"""Per-run scoring and run-level aggregation, against the PRD §7 targets."""

from __future__ import annotations

from statistics import mean
from typing import Any

from evals.judge import JudgeScores
from evals.types import EvalCategory, EvalRunResult, GoldenQA
from librarian.types import Answer

# PRD §7 — what "done" means, checked in every report and by the --strict gate
TARGETS: dict[str, float] = {
    "refusal_honesty": 1.0,        # no_answer questions answered with a refusal, always
    "success_rate_answerable": 0.80,
    "retrieval_recall": 0.85,
    "citation_accuracy": 0.85,
    "faithfulness_rate": 0.90,     # share of judged runs with faithfulness >= 4
}


def _norm(path: str) -> str:
    return path.replace("\\", "/").casefold()


def retrieval_recall(task: GoldenQA, answer: Answer) -> float | None:
    if not task.expected_sources:
        return None
    sources = [_norm(h.source) for h in answer.hits]
    covered = sum(
        1 for frag in task.expected_sources if any(_norm(frag) in s for s in sources)
    )
    return covered / len(task.expected_sources)


def score_run(
    task: GoldenQA,
    answer: Answer,
    judge: JudgeScores | None,
    *,
    mode: str,
    strategy: str,
    run_index: int,
) -> EvalRunResult:
    reasons: list[str] = []
    if task.expect_refusal:
        if not answer.refusal:
            reasons.append("answered a no-answer question (hallucination)")
    else:
        if answer.refusal:
            reasons.append("refused an answerable question")
        text = answer.text.casefold()
        for needle in task.must_contain:
            if needle.casefold() not in text:
                reasons.append(f"missing required content: {needle}")
        for needle in task.must_not_contain:
            if needle.casefold() in text:
                reasons.append(f"contains forbidden content: {needle}")
        if not answer.refusal:
            if judge is None:
                reasons.append("judge: no score")
            elif judge.correctness < 4:
                reasons.append(f"correctness {judge.correctness} < 4")
    return EvalRunResult(
        golden_id=task.id,
        category=task.category,
        mode=mode,
        strategy=strategy,
        run_index=run_index,
        refusal=answer.refusal,
        answer_text=answer.text[:4000],
        retrieval_recall=retrieval_recall(task, answer),
        citation_accuracy=None if answer.refusal else answer.confidence.citation_coverage,
        composite_confidence=answer.confidence.composite,
        correctness=judge.correctness if judge else None,
        faithfulness=judge.faithfulness if judge else None,
        judge_note=judge.note if judge else "",
        generator=answer.generator,
        judge_model=answer.judge,
        elapsed_s=answer.elapsed_s,
        success=not reasons,
        failure_reasons=reasons,
    )


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def summarise(results: list[EvalRunResult]) -> dict[str, Any]:
    if not results:
        return {"runs": 0}
    no_answer = [r for r in results if r.category is EvalCategory.NO_ANSWER]
    answerable = [r for r in results if r.category is not EvalCategory.NO_ANSWER]
    judged = [r for r in results if r.faithfulness is not None]
    latencies = sorted(r.elapsed_s for r in results)

    def pct(p: float) -> float:
        return latencies[min(len(latencies) - 1, int(p * len(latencies)))]

    out: dict[str, Any] = {
        "runs": len(results),
        "success_rate": _rate([r.success for r in results]),
        "success_rate_answerable": _rate([r.success for r in answerable]),
        "refusal_honesty": _rate([r.success for r in no_answer]),
        "retrieval_recall": _avg(
            [r.retrieval_recall for r in answerable if r.retrieval_recall is not None]
        ),
        "citation_accuracy": _avg(
            [r.citation_accuracy for r in results if r.citation_accuracy is not None]
        ),
        "faithfulness_rate": _rate([(r.faithfulness or 0) >= 4 for r in judged]),
        "correctness_mean": _avg(
            [float(r.correctness) for r in results if r.correctness is not None]
        ),
        "latency_p50_s": pct(0.50),
        "latency_p95_s": pct(0.95),
        "unjudged_runs": sum(
            1 for r in answerable if not r.refusal and r.correctness is None
        ),
    }
    out["targets_met"] = {
        name: (out.get(name) is not None and float(out[name]) >= threshold)
        for name, threshold in TARGETS.items()
    }
    return out


def per_category(results: list[EvalRunResult]) -> dict[str, dict[str, Any]]:
    cats: dict[str, dict[str, Any]] = {}
    for cat in sorted({r.category.value for r in results}):
        rows = [r for r in results if r.category.value == cat]
        cats[cat] = {
            "runs": len(rows),
            "success_rate": _rate([r.success for r in rows]),
            "failure_reasons": sorted(
                {reason for r in rows for reason in r.failure_reasons}
            )[:8],
        }
    return cats


def gate_failures(
    metrics: dict[str, Any], baseline: dict[str, Any] | None = None
) -> list[str]:
    """--strict failures: any hallucinated no-answer, or a real regression vs baseline."""
    failures: list[str] = []
    honesty = metrics.get("refusal_honesty")
    if honesty is not None and honesty < 1.0:
        failures.append(f"refusal_honesty {honesty} < 1.0 — a no-answer question was answered")
    if baseline:
        for key in ("success_rate", "citation_accuracy", "faithfulness_rate"):
            now, then = metrics.get(key), baseline.get(key)
            if now is not None and then is not None and float(now) < float(then) - 0.05:
                failures.append(f"regression: {key} {now} < baseline {then} - 0.05")
    return failures
