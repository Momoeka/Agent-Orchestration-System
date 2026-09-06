"""Eval data shapes: golden tasks, per-run results, the report."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvalCategory(StrEnum):
    LOOKUP = "lookup"          # one fact, one page
    EXACT_TOKEN = "exact_token"  # the query contains an identifier BM25 should nail
    MULTI_HOP = "multi_hop"    # needs two documents combined
    NO_ANSWER = "no_answer"    # not in the corpus — must refuse, never fabricate
    AMBIGUOUS = "ambiguous"    # under-specified — must stay grounded while covering readings


class GoldenQA(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    category: EvalCategory
    difficulty: str = "medium"
    question: str = Field(min_length=10)
    # substring match against hit.source (path separators normalised); recall@k = share covered
    expected_sources: list[str] = Field(default_factory=list)
    expect_refusal: bool = False
    must_contain: list[str] = Field(default_factory=list)  # case-insensitive, in answer text
    must_not_contain: list[str] = Field(default_factory=list)
    golden_answer: str = ""
    notes: str = ""


class EvalRunResult(BaseModel):
    golden_id: str
    category: EvalCategory
    mode: str
    strategy: str
    run_index: int
    refusal: bool
    answer_text: str = ""
    retrieval_recall: float | None = None
    citation_accuracy: float | None = None
    composite_confidence: float = 0.0
    correctness: int | None = None   # judge, 1-5, vs the golden answer
    faithfulness: int | None = None  # judge, 1-5, vs the retrieved context
    judge_note: str = ""
    generator: str = ""
    judge_model: str = ""
    elapsed_s: float = 0.0
    success: bool = False
    failure_reasons: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    run_id: str
    started_at: str
    finished_at: str
    k: int
    mode: str
    strategy: str
    task_count: int
    run_count: int
    results: list[EvalRunResult] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    per_category: dict[str, dict[str, Any]] = Field(default_factory=dict)
    baseline_id: str | None = None
    diff: dict[str, Any] | None = None
