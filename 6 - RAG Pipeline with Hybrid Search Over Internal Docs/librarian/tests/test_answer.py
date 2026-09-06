"""Phase 3: role chains, grounded generation, citation verification, confidence, ask()."""

from __future__ import annotations

import uuid
from pathlib import Path

import chromadb
import pytest

from librarian.answer.confidence import build_confidence, citation_coverage, retrieval_confidence
from librarian.answer.generate import context_blocks, generate, is_refusal
from librarian.answer.pipeline import AnswerPipeline
from librarian.answer.verify import extract_citations, verify
from librarian.errors import RetryableError
from librarian.index.dense import DenseIndex
from librarian.index.indexer import Indexer
from librarian.index.sparse import SparseIndex
from librarian.index.store import ChunkStore
from librarian.llm.roles import load_models_config
from librarian.retrieve.rerank import NoReranker
from librarian.retrieve.retriever import Retriever
from librarian.types import Citation, Document, Format, SearchHit, Strategy
from tests.test_retrieve import HashEmbedder


class ScriptedChat:
    """Satisfies the Chat protocol; returns queued replies or raises queued errors."""

    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)
        self.last_used = "fake/model"
        self.calls: list[list[dict[str, str]]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1200,
        json_object: bool = False,
    ) -> str:
        self.calls.append(messages)
        item = self._replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def hit(n: int, text: str, *, both: bool = True) -> SearchHit:
    return SearchHit(
        chunk_id=f"c{n}",
        doc_id="d",
        source="doc.md",
        text=text,
        heading_path=["Guide", f"S{n}"],
        page=n,
        dense_rank=n,
        sparse_rank=n if both else None,
    )


def test_models_config_loads_and_drops_paid(tmp_path: Path) -> None:
    real = load_models_config(Path("config/models.yaml"), enable_paid=False)
    for role in ("generator", "judge"):
        assert real.role(role).chain, role
        for entry in real.role(role).chain:
            assert real.providers[entry.provider].free_tier, f"{role}: {entry.provider}"
    with_paid = load_models_config(Path("config/models.yaml"), enable_paid=True)
    assert with_paid.role("generator").chain[0].provider == "explabs"

    bad = tmp_path / "m.yaml"
    bad.write_text(
        "providers:\n  p: {base_url_env: X, paid: true}\n"
        "roles:\n  generator:\n    chain: [{provider: p, model: m}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="only paid"):
        load_models_config(bad, enable_paid=False)
    bad.write_text(
        "providers: {}\nroles:\n  generator:\n    chain: [{provider: ghost, model: m}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown provider"):
        load_models_config(bad, enable_paid=False)


def test_context_blocks_and_refusal_marker() -> None:
    blocks = context_blocks([hit(1, "alpha text"), hit(2, "beta text")])
    assert "[1] Guide > S1 · page 1\nalpha text" in blocks
    assert "[2] Guide > S2 · page 2\nbeta text" in blocks
    assert is_refusal("NOT IN CORPUS\nnothing here") and is_refusal("  not in corpus …")
    assert not is_refusal("The answer is 42 [1].")


def test_generate_sends_question_and_blocks() -> None:
    chat = ScriptedChat(["The answer [1]."])
    out = generate(chat, "what is alpha?", [hit(1, "alpha text")])
    assert out == "The answer [1]."
    user = chat.calls[0][1]["content"]
    assert "what is alpha?" in user and "alpha text" in user


def test_extract_citations_maps_and_flags() -> None:
    hits = [hit(1, "a"), hit(2, "b")]
    # 【1】 is what groq/gpt-oss wrote live — bracket variants must parse like [1]
    text = "Alpha does X 【1】. Beta does Y [1][2]. Ghost claim [7]."
    cites = extract_citations(text, hits)
    assert [(c.n, c.supported) for c in cites] == [
        (1, None),
        (1, None),
        (2, None),
        (7, False),
    ]
    assert cites[3].note == "no such context block"
    assert cites[0].sentence == "Alpha does X [1]."  # variant brackets normalised
    assert cites[1].sentence == "Beta does Y [1][2]."
    assert cites[0].chunk_id == "c1" and cites[2].page == 2


def test_verify_applies_judge_verdicts_and_survives_judge_outage() -> None:
    hits = [hit(1, "alpha supports X")]
    cites = extract_citations("X is true [1]. Y is false [1].", hits)
    judge = ScriptedChat(
        [
            '```json\n{"pairs": [{"index": 0, "supported": true, "note": "says X"},'
            ' {"index": 1, "supported": false, "note": "never says Y"}],'
            ' "completeness": 4}\n```'
        ]
    )
    verified, completeness = verify(judge, "q", "answer", hits, cites)
    assert [c.supported for c in verified] == [True, False]
    assert verified[1].note == "never says Y" and completeness == 4

    down = ScriptedChat([RetryableError("chain exhausted")])
    unverified, completeness = verify(down, "q", "answer", hits, cites)
    assert [c.supported for c in unverified] == [None, None] and completeness == 3

    garbled = ScriptedChat(["not json at all"])
    still_none, _ = verify(garbled, "q", "answer", hits, cites)
    assert [c.supported for c in still_none] == [None, None]


def test_confidence_math() -> None:
    both = [hit(1, "a"), hit(2, "b")]
    one_sided = [hit(1, "a", both=False)]
    assert retrieval_confidence(both) == 1.0
    assert retrieval_confidence(one_sided) == 0.6
    assert retrieval_confidence([]) == 0.0

    cites = [
        Citation(n=1, sentence="s", supported=True),
        Citation(n=1, sentence="s", supported=False),
        Citation(n=1, sentence="s", supported=None),  # unverified counts as unsupported
    ]
    assert citation_coverage(cites) == pytest.approx(1 / 3)
    assert citation_coverage([]) == 0.0

    conf = build_confidence(both, [Citation(n=1, sentence="s", supported=True)], 5)
    assert conf.composite == pytest.approx(0.35 * 1.0 + 0.45 * 1.0 + 0.20 * 1.0)


def build_pipeline_with(
    generator: ScriptedChat, judge: ScriptedChat, corpus: dict[str, str]
) -> AnswerPipeline:
    store = ChunkStore()
    dense = DenseIndex(chromadb.EphemeralClient(), space_id=f"a-{uuid.uuid4().hex[:8]}")
    embedder = HashEmbedder()
    indexer = Indexer(store, dense, embedder)
    for name, text in corpus.items():
        doc = Document(doc_id=name, source=f"{name}.md", format=Format.MARKDOWN, text=text)
        indexer.ingest(doc, strategy=Strategy.HEADING, size=500)
    retriever = Retriever(store, dense, SparseIndex(store.all_chunks()), embedder, NoReranker())
    return AnswerPipeline(retriever, generator, judge, context_k=3, threshold=0.55)


CORPUS = {
    "api": "# API\n\nThe response_model parameter controls the output schema shape.\n",
    "cats": "# Cats\n\nThe cat sat on the mat and napped all afternoon.\n",
}


def test_ask_happy_path() -> None:
    generator = ScriptedChat(["The response_model parameter controls the output schema [1]."])
    judge = ScriptedChat(
        ['{"pairs": [{"index": 0, "supported": true, "note": "ok"}], "completeness": 5}']
    )
    answer = build_pipeline_with(generator, judge, CORPUS).ask("what does response_model do?")
    assert not answer.refusal
    assert answer.citations[0].supported is True and answer.citations[0].n == 1
    assert answer.confidence.composite > 0.55
    assert answer.generator == "fake/model" and len(answer.hits) >= 1


def test_ask_model_refusal_and_low_confidence_refusal() -> None:
    generator = ScriptedChat(["NOT IN CORPUS\nThe blocks cover response_model, not billing."])
    judge = ScriptedChat([])  # never called on a model refusal
    answer = build_pipeline_with(generator, judge, CORPUS).ask("how does billing work?")
    assert answer.refusal and answer.citations == [] and judge.calls == []

    uncited = ScriptedChat(["Billing is monthly and automatic."])  # no citations at all
    judge2 = ScriptedChat([])  # no citations -> judge not needed
    low = build_pipeline_with(uncited, judge2, CORPUS).ask("how does billing work?")
    assert low.refusal and low.confidence.citation_coverage == 0.0


def test_ask_empty_index_and_generator_outage_refuse() -> None:
    generator = ScriptedChat([])
    empty = build_pipeline_with(generator, ScriptedChat([]), {})
    answer = empty.ask("anything")
    assert answer.refusal and "Retrieval returned nothing" in answer.text

    down = ScriptedChat([RetryableError("chain exhausted")])
    outage = build_pipeline_with(down, ScriptedChat([]), CORPUS).ask("what is response_model?")
    assert outage.refusal and "unavailable" in outage.text and outage.hits
