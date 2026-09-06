"""Citation verification (Architecture.md §3 step 5): every (sentence, cited block) pair goes
to a judge from a different model family; unsupported citations are flagged, never silently
kept. One judge call per answer — free tiers are too small for a call per pair."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from librarian.errors import RetryableError
from librarian.llm.providers import Chat
from librarian.types import Citation, SearchHit

_CITE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Models cite in whatever brackets their tokenizer favours — seen live: gpt-oss wrote 【1】.
_BRACKET_VARIANTS = str.maketrans({"【": "[", "】": "]", "［": "[", "］": "]"})  # noqa: RUF001


def normalize_citation_brackets(text: str) -> str:
    return text.translate(_BRACKET_VARIANTS)

JUDGE_PROMPT = """You are a strict fact-checking judge. You will get a question, numbered
source passages, and a list of claims; each claim cites one passage by number.

For every claim decide: does the cited passage actually support the claim as written?
Judge only against the cited passage, not your own knowledge. Also rate, 1-5, how completely
the full answer addresses the question (5 = every part addressed).

Respond with JSON only, exactly this shape:
{"pairs": [{"index": 0, "supported": true, "note": "short reason"}], "completeness": 4}
One entry per claim, `index` copied from the input."""


class _JudgePair(BaseModel):
    index: int
    supported: bool
    note: str = ""


class _JudgeOutput(BaseModel):
    pairs: list[_JudgePair] = Field(default_factory=list)
    completeness: int = Field(default=3, ge=1, le=5)


def extract_citations(answer: str, hits: list[SearchHit]) -> list[Citation]:
    """Every [n] in the answer, sentence by sentence. An n outside the context blocks is
    immediately flagged as unsupported — the model cited a passage it was never given."""
    citations: list[Citation] = []
    for sentence in _SENTENCE_SPLIT.split(normalize_citation_brackets(answer).strip()):
        for m in _CITE.finditer(sentence):
            n = int(m.group(1))
            clean = sentence.strip()
            if 1 <= n <= len(hits):
                h = hits[n - 1]
                citations.append(
                    Citation(
                        n=n,
                        sentence=clean,
                        chunk_id=h.chunk_id,
                        source=h.source,
                        heading_path=h.heading_path,
                        page=h.page,
                    )
                )
            else:
                citations.append(
                    Citation(n=n, sentence=clean, supported=False, note="no such context block")
                )
    return citations


def _strip_fences(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def verify(
    judge: Chat,
    question: str,
    answer: str,
    hits: list[SearchHit],
    citations: list[Citation],
) -> tuple[list[Citation], int]:
    """Returns (citations with verdicts, completeness 1-5).

    If the judge chain is exhausted, verdicts stay None ("unverified") — the confidence gate
    then counts them as unsupported, which pushes toward refusal: fail toward honesty.
    """
    judgeable = [(i, c) for i, c in enumerate(citations) if c.supported is None]
    if not judgeable:
        return citations, 3
    passages = "\n\n".join(f"[{i}] {h.text.strip()[:1500]}" for i, h in enumerate(hits, 1))
    claims = "\n".join(
        f'{{"index": {i}, "cites": {c.n}, "claim": {json.dumps(c.sentence)}}}'
        for i, c in judgeable
    )
    user = (
        f"Question: {question}\n\nFull answer:\n{answer}\n\n"
        f"Source passages:\n{passages}\n\nClaims to judge:\n{claims}"
    )
    try:
        raw = judge.chat(
            [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user}],
            max_tokens=1500,
            json_object=True,
        )
        parsed = _JudgeOutput.model_validate_json(_strip_fences(raw))
    except (RetryableError, ValidationError, json.JSONDecodeError):
        return citations, 3  # verdicts stay None — shown as unverified, scored as unsupported
    by_index = {p.index: p for p in parsed.pairs}
    out = list(citations)
    for i, c in judgeable:
        p = by_index.get(i)
        if p is not None:
            out[i] = c.model_copy(update={"supported": p.supported, "note": p.note})
    return out, parsed.completeness
