"""Load and validate the golden set from evals/golden_tasks/*.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

from evals.types import EvalCategory, GoldenQA

GOLDEN_DIR = Path(__file__).parent / "golden_tasks"


def load_golden(directory: Path = GOLDEN_DIR) -> list[GoldenQA]:
    tasks: list[GoldenQA] = []
    for f in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            raise ValueError(f"{f.name}: expected a YAML list of tasks")
        tasks.extend(GoldenQA.model_validate(item) for item in raw)
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate golden ids: {sorted(dupes)}")
    for t in tasks:
        if t.category is EvalCategory.NO_ANSWER and not t.expect_refusal:
            raise ValueError(f"{t.id}: no_answer tasks must expect_refusal")
        if t.category is not EvalCategory.NO_ANSWER:
            if not t.expected_sources:
                raise ValueError(f"{t.id}: answerable tasks need expected_sources")
            if not t.golden_answer:
                raise ValueError(f"{t.id}: answerable tasks need a golden_answer")
    return tasks
