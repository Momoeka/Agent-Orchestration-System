"""The eval runner: real questions through the real pipeline, JSONL-resumable.

  uv run python -m evals.runner                       # full set, k=1, hybrid, default strategy
  uv run python -m evals.runner --category no_answer  # one category
  uv run python -m evals.runner --mode dense          # the hybrid-vs-dense comparison arm
  uv run python -m evals.runner --strategy fixed      # the chunking bake-off arm
  uv run python -m evals.runner --resume <run_id>     # continue a rate-limited run
  uv run python -m evals.runner --baseline            # save this run as the baseline
  uv run python -m evals.runner --strict              # exit 1 on hallucination or regression
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evals.judge import judge_answer
from evals.loader import load_golden
from evals.metrics import TARGETS, gate_failures, per_category, score_run, summarise
from evals.types import EvalReport, EvalRunResult
from librarian.answer.pipeline import build_pipeline
from librarian.config import get_settings
from librarian.llm.providers import build_chat
from librarian.retrieve.retriever import Mode

REPORTS_DIR = Path(__file__).parent / "reports"


def read_jsonl(path: Path) -> list[EvalRunResult]:
    if not path.exists():
        return []
    return [
        EvalRunResult.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def done_keys(results: list[EvalRunResult]) -> set[tuple[str, int]]:
    return {(r.golden_id, r.run_index) for r in results}


def write_markdown(report: EvalReport, path: Path) -> None:
    m = report.metrics
    lines = [
        f"# Eval report {report.run_id}",
        "",
        f"{report.run_count} runs · k={report.k} · mode={report.mode} · "
        f"strategy={report.strategy} · {report.started_at} → {report.finished_at}",
        "",
        "| metric | value | target | met |",
        "|---|---|---|---|",
    ]
    for name, target in TARGETS.items():
        value = m.get(name)
        met = m.get("targets_met", {}).get(name)
        lines.append(f"| {name} | {value} | ≥ {target} | {'yes' if met else 'NO'} |")
    lines += [
        f"| success_rate (all) | {m.get('success_rate')} | — | |",
        f"| correctness_mean | {m.get('correctness_mean')} | — | |",
        f"| latency p50 / p95 s | {m.get('latency_p50_s')} / {m.get('latency_p95_s')} | — | |",
        "",
        "## Per category",
        "",
        "| category | runs | success | failure reasons seen |",
        "|---|---|---|---|",
    ]
    for cat, row in report.per_category.items():
        reasons = "; ".join(row["failure_reasons"]) or "—"
        lines.append(f"| {cat} | {row['runs']} | {row['success_rate']} | {reasons} |")
    if report.diff:
        lines += ["", "## Vs baseline", "", "```json", json.dumps(report.diff, indent=2), "```"]
    failures = [r for r in report.results if not r.success]
    if failures:
        lines += ["", "## Failed runs", ""]
        for r in failures:
            lines.append(f"- `{r.golden_id}` (run {r.run_index}): {'; '.join(r.failure_reasons)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "dense", "sparse"])
    ap.add_argument("--strategy", default="", help="chunking strategy arm (default: settings)")
    ap.add_argument("--only", default="", help="run a single golden id")
    ap.add_argument("--category", default="", help="run one category")
    ap.add_argument("--resume", default="", help="run id to continue")
    ap.add_argument("--baseline", action="store_true", help="save this run as baseline.json")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    strategy = args.strategy or settings.chunk_strategy
    tasks = load_golden()
    if args.only:
        tasks = [t for t in tasks if t.id == args.only]
    if args.category:
        tasks = [t for t in tasks if t.category.value == args.category]
    if not tasks:
        print("no tasks selected")
        return 1

    REPORTS_DIR.mkdir(exist_ok=True)
    run_id = args.resume or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    jsonl = REPORTS_DIR / f"{run_id}.jsonl"
    results = read_jsonl(jsonl)
    skip = done_keys(results)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")

    pipeline = build_pipeline(settings, strategy=strategy)
    eval_judge = build_chat(settings, "judge")
    mode: Mode = args.mode

    total = len(tasks) * args.k
    with jsonl.open("a", encoding="utf-8") as sink:
        for run_index in range(args.k):
            for t in tasks:
                if (t.id, run_index) in skip:
                    continue
                answer = pipeline.ask(t.question, mode=mode)
                judge = None
                if not answer.refusal and not t.expect_refusal:
                    judge = judge_answer(
                        eval_judge, t.question, t.golden_answer, answer.text, answer.hits
                    )
                result = score_run(
                    t, answer, judge, mode=mode, strategy=strategy, run_index=run_index
                )
                if judge is not None:
                    result.judge_model = eval_judge.last_used
                results.append(result)
                sink.write(result.model_dump_json() + "\n")
                sink.flush()
                mark = "ok " if result.success else "FAIL"
                print(
                    f"  [{len(results)}/{total}] {mark} {t.id} ({answer.elapsed_s}s)"
                    + (f" — {'; '.join(result.failure_reasons)}" if not result.success else ""),
                    flush=True,
                )
                time.sleep(1)  # be gentle to free tiers

    metrics = summarise(results)
    baseline_path = REPORTS_DIR / "baseline.json"
    baseline = None
    diff = None
    if baseline_path.exists() and not args.baseline:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8")).get("metrics", {})
        diff = {
            key: {"run": metrics.get(key), "baseline": baseline.get(key)}
            for key in TARGETS
            if metrics.get(key) != baseline.get(key)
        }
    report = EvalReport(
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        k=args.k,
        mode=args.mode,
        strategy=strategy,
        task_count=len(tasks),
        run_count=len(results),
        results=results,
        metrics=metrics,
        per_category=per_category(results),
        baseline_id="baseline" if baseline else None,
        diff=diff,
    )
    (REPORTS_DIR / f"{run_id}.json").write_text(report.model_dump_json(indent=2), "utf-8")
    write_markdown(report, REPORTS_DIR / f"{run_id}.md")
    (REPORTS_DIR / "latest.json").write_text(
        json.dumps({"run_id": run_id, "metrics": metrics}, indent=2), "utf-8"
    )
    if args.baseline:
        baseline_path.write_text(json.dumps({"run_id": run_id, "metrics": metrics}, indent=2))
        print("saved as baseline.json")

    print(json.dumps(metrics, indent=2))
    if args.strict:
        failures = gate_failures(metrics, baseline)
        if failures:
            print("GATE FAILED:\n  " + "\n  ".join(failures))
            return 1
        print("GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
