"""The eval judge: correctness vs the golden answer AND faithfulness vs the retrieved context,
in one call per run — free judge tiers cannot afford more (Foreman's lesson)."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

from librarian.errors import RetryableError
from librarian.llm.providers import Chat
from librarian.types import SearchHit

JUDGE_PROMPT = """You are a strict evaluation judge for a documentation Q&A system.
You get: a question, a reference answer (ground truth), the system's answer, and the context
passages the system retrieved.

Score two things, each 1-5:
- correctness: does the system's answer agree with the reference answer on the facts the
  question asked about? (5 = fully correct, 3 = partly, 1 = wrong or off-topic)
- faithfulness: is every claim in the system's answer supported by the retrieved passages?
  (5 = fully grounded, 1 = substantial unsupported content)

Judge only what is written. Respond with JSON only, exactly:
{"correctness": 4, "faithfulness": 5, "note": "one short sentence"}"""


class JudgeScores(BaseModel):
    correctness: int = Field(ge=1, le=5)
    faithfulness: int = Field(ge=1, le=5)
    note: str = ""


def _strip_fences(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def judge_answer(
    judge: Chat, question: str, golden_answer: str, answer_text: str, hits: list[SearchHit]
) -> JudgeScores | None:
    """None when the judge chain is exhausted or returns garbage — recorded as unscored,
    which fails the run (a run nobody could judge must not count as a pass)."""
    context = "\n\n".join(f"[{i}] {h.text.strip()[:1200]}" for i, h in enumerate(hits, 1))
    user = (
        f"Question: {question}\n\nReference answer:\n{golden_answer}\n\n"
        f"System answer:\n{answer_text}\n\nRetrieved passages:\n{context}"
    )
    try:
        raw = judge.chat(
            [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user}],
            max_tokens=400,
            json_object=True,
        )
        return JudgeScores.model_validate_json(_strip_fences(raw))
    except (RetryableError, ValidationError, json.JSONDecodeError):
        return None
