"""Builders for the OpenAI-format message list the loop maintains."""

from __future__ import annotations

import json
from typing import Any

from packages.shared.types.llm import LLMMessage, LLMResponse
from packages.shared.types.subtask import Subtask
from packages.shared.types.tools import ToolResult

MAX_TOOL_RESULT_CHARS = 24_000


def system_message(text: str) -> LLMMessage:
    return {"role": "system", "content": text}


def user_message(text: str) -> LLMMessage:
    return {"role": "user", "content": text}


def render_subtask(subtask: Subtask) -> str:
    parts = [
        f"## Subtask {subtask.id}",
        subtask.description.strip(),
    ]
    if subtask.inputs:
        parts.append(
            "## Inputs from earlier steps\n```json\n"
            + json.dumps(subtask.inputs, indent=2, default=str)
            + "\n```"
        )
    if subtask.expected_output:
        parts.append("## Expected output\n" + subtask.expected_output.strip())
    parts.append(
        "When you are done, call `submit_result` exactly once with your deliverable. "
        "Do not answer in plain text."
    )
    return "\n\n".join(parts)


def assistant_message(response: LLMResponse) -> LLMMessage:
    """Append the assistant turn exactly as returned so tool_call ids line up."""
    msg: dict[str, Any] = dict(response.raw_assistant_message) or {"role": "assistant"}
    msg["role"] = "assistant"
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in response.tool_calls
        ]
        msg.setdefault("content", None)
    else:
        msg["content"] = response.content or ""
        msg.pop("tool_calls", None)
    return msg


def tool_messages(results: list[ToolResult]) -> list[LLMMessage]:
    """One `tool` message per result; all appended together before the next model call."""
    out: list[LLMMessage] = []
    for r in results:
        content = r.content
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = (
                content[:MAX_TOOL_RESULT_CHARS]
                + f"\n…[truncated {len(r.content) - MAX_TOOL_RESULT_CHARS} chars]"
            )
        if r.is_error:
            content = f"ERROR: {content}"
        out.append(
            {"role": "tool", "tool_call_id": r.tool_call_id, "name": r.name, "content": content}
        )
    return out
