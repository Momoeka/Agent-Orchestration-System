"""Phase 4: golden set consistency, per-run scoring, aggregation, the strict gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.judge import JudgeScores, judge_answer
from evals.loader import load_golden
from evals.metrics import gate_failures, retrieval_recall, score_run, summarise
from evals.runner import done_keys, read_jsonl
from evals.types import EvalCategory, EvalRunResult, GoldenQA
from librarian.types import Answer, Confidence, SearchHit
from tests.test_answer import ScriptedChat


def golden(**kw: object) -> GoldenQA:
    base: dict[str, object] = {
        "id": "t_one",
        "category": "lookup",
        "question": "what is alpha exactly?",
        "expected_sources": ["alpha"],
        "golden_answer": "alpha is a thing",
    }
    return GoldenQA.model_validate({**base, **kw})


def answer(**kw: object) -> Answer:
    base: dict[str, object] = {
        "question": "q",
        "text": "Alpha is a thing [1].",
        "refusal": False,
        "confidence": Confidence(
            retrieval=1.0, citation_coverage=1.0, completeness=1.0, composite=1.0
        ),
        "hits": [
            SearchHit(chunk_id="c1", doc_id="d", source="data\\corpus\\alpha\\intro.md", text="x")
        ],
        "elapsed_s": 2.0,
    }
    return Answer.model_validate({**base, **kw})


def test_golden_set_is_consistent() -> None:
    tasks = load_golden()
    assert len(tasks) >= 50
    assert {t.category for t in tasks} == set(EvalCategory)
    for t in tasks:
        if t.category is EvalCategory.NO_ANSWER:
            assert t.expect_refusal and not t.must_contain
        else:
            assert t.expected_sources and t.golden_answer


def test_loader_rejects_bad_tasks(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text(
        "- {id: broken_one, category: no_answer, question: 'is this in the corpus at all?'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expect_refusal"):
        load_golden(tmp_path)
    f.write_text(
        "- {id: broken_two, category: lookup, question: 'where is the thing documented?'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_sources"):
        load_golden(tmp_path)


def test_retrieval_recall_normalises_separators() -> None:
    t = golden(expected_sources=["alpha/intro", "beta"])
    assert retrieval_recall(t, answer()) == 0.5  # alpha matched despite backslashes, beta not
    assert retrieval_recall(golden(expected_sources=[]), answer()) is None


def scored(task: GoldenQA, ans: Answer, judge: JudgeScores | None) -> EvalRunResult:
    return score_run(task, ans, judge, mode="hybrid", strategy="heading", run_index=0)


def test_score_run_rules() -> None:
    good = JudgeScores(correctness=5, faithfulness=5)
    ok = scored(golden(must_contain=["alpha"]), answer(), good)
    assert ok.success and ok.retrieval_recall == 1.0 and ok.citation_accuracy == 1.0

    hallucinated = scored(
        golden(category="no_answer", expect_refusal=True, expected_sources=[], golden_answer=""),
        answer(),
        None,
    )
    assert not hallucinated.success and "hallucination" in hallucinated.failure_reasons[0]

    honest = scored(
        golden(category="no_answer", expect_refusal=True, expected_sources=[], golden_answer=""),
        answer(refusal=True, text="NOT IN CORPUS\nnothing"),
        None,
    )
    assert honest.success

    refused = scored(golden(), answer(refusal=True, text="NOT IN CORPUS\n..."), None)
    assert not refused.success and refused.failure_reasons == ["refused an answerable question"]
    assert refused.citation_accuracy is None  # refusals do not count toward citation accuracy

    weak = scored(golden(), answer(), JudgeScores(correctness=3, faithfulness=5))
    assert not weak.success and weak.failure_reasons == ["correctness 3 < 4"]

    unjudged = scored(golden(), answer(), None)
    assert not unjudged.success and unjudged.failure_reasons == ["judge: no score"]

    missing = scored(golden(must_contain=["gamma"]), answer(), good)
    assert not missing.success and "missing required content: gamma" in missing.failure_reasons


def test_summarise_and_gate() -> None:
    rows = [
        scored(golden(), answer(), JudgeScores(correctness=5, faithfulness=5)),
        scored(golden(id="t_two"), answer(), JudgeScores(correctness=4, faithfulness=3)),
        scored(
            golden(
                id="t_no",
                category="no_answer",
                expect_refusal=True,
                expected_sources=[],
                golden_answer="",
            ),
            answer(),  # answered → hallucination
            None,
        ),
    ]
    m = summarise(rows)
    assert m["runs"] == 3 and m["refusal_honesty"] == 0.0
    assert m["success_rate_answerable"] == 1.0
    assert m["faithfulness_rate"] == 0.5  # one of two judged runs >= 4
    assert m["correctness_mean"] == 4.5
    assert m["targets_met"]["refusal_honesty"] is False
    failures = gate_failures(m)
    assert failures and "no-answer question was answered" in failures[0]

    regressed = gate_failures(
        {"refusal_honesty": 1.0, "success_rate": 0.7, "citation_accuracy": 0.9},
        baseline={"success_rate": 0.9, "citation_accuracy": 0.9},
    )
    assert regressed == ["regression: success_rate 0.7 < baseline 0.9 - 0.05"]
    assert summarise([]) == {"runs": 0}


def test_judge_answer_parses_and_survives_garbage() -> None:
    good = ScriptedChat(['{"correctness": 4, "faithfulness": 5, "note": "solid"}'])
    scores = judge_answer(good, "q", "gold", "ans", [])
    assert scores is not None and (scores.correctness, scores.faithfulness) == (4, 5)
    assert judge_answer(ScriptedChat(["nonsense"]), "q", "g", "a", []) is None


def test_jsonl_roundtrip_and_done_keys(tmp_path: Path) -> None:
    rows = [
        scored(golden(), answer(), JudgeScores(correctness=5, faithfulness=5)),
        scored(golden(id="t_two"), answer(), None),
    ]
    f = tmp_path / "run.jsonl"
    f.write_text("".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
    back = read_jsonl(f)
    assert [r.golden_id for r in back] == ["t_one", "t_two"]
    assert done_keys(back) == {("t_one", 0), ("t_two", 0)}
    assert read_jsonl(tmp_path / "absent.jsonl") == []
