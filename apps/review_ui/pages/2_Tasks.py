"""Task lookup: plan, subtasks with verdicts, deliverable, ledger counts (docs/Design.md §3.3–3.4)."""

from __future__ import annotations

import json

import httpx
import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import inject_css, status_badge
from packages.shared.config import get_settings

st.set_page_config(page_title="Foreman — tasks", page_icon="🗂️", layout="wide")
inject_css()


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


c = client()
st.title("Tasks")

with st.expander("Submit a new task"):
    req = st.text_area("Request", height=100, key="new-request")
    user = st.text_input("User id", value="u_42", key="new-user")
    review = st.checkbox("Require plan approval before any work (L3)", key="new-review")
    if st.button("Submit", type="primary"):
        try:
            out = c.create_task(req, user, require_human_review=review)
            st.success(f"queued: {out['task_id']}")
            st.session_state["task_id"] = out["task_id"]
        except httpx.HTTPStatusError as e:
            st.error(f"{e.response.status_code}: {e.response.text[:300]}")

task_id = st.text_input("Task id", value=st.session_state.get("task_id", ""), key="task-id-input")
if not task_id.strip():
    st.stop()
try:
    v = c.task(task_id.strip())
except httpx.HTTPStatusError as e:
    st.error(f"{e.response.status_code}: {e.response.text[:300]}")
    st.stop()

st.markdown(
    f"{status_badge(v['status'])} &nbsp; user `{v['user_id']}` · LLM calls {v['llm_calls']} · "
    f"tokens {v['tokens']} · tool calls {v['tool_calls']} ({v['tool_calls_not_executed']} not executed) · "
    f"cost {v['cost_usd'] if v['cost_usd'] is not None else '$0 (free tiers)'}",
    unsafe_allow_html=True,
)
if v.get("pending_approval_id"):
    st.warning(f"Waiting on approval #{v['pending_approval_id']} — open the Approvals page.")
if v.get("error"):
    st.error(v["error"])

st.subheader("Request")
st.markdown(f'<div class="card">{v["request"]}</div>', unsafe_allow_html=True)

plan = v.get("plan") or {}
if plan:
    st.subheader(f"Plan · confidence {plan.get('confidence')}")
    for s in plan.get("subtasks", []):
        st.markdown(
            f"**{s['id']}** ({s['specialist']}) ← {', '.join(s['depends_on']) or '—'} — {s['description']}"
        )
    if plan.get("sensitive_actions"):
        st.caption("sensitive actions: " + ", ".join(plan["sensitive_actions"]))

if v.get("subtasks"):
    st.subheader("Subtasks")
    for s in v["subtasks"]:
        verdict = s.get("verdict") or {}
        result = s.get("result") or {}
        with st.expander(
            f"{s['id']} · {s['specialist']} · attempt {s['attempt']} · {s['status']}"
            + (f" · score {verdict.get('score')}" if verdict else "")
            + (" · human" if result.get("human_authored") else "")
        ):
            st.markdown(status_badge(s["status"]), unsafe_allow_html=True)
            if result:
                st.markdown(result.get("output", ""))
                st.caption(
                    "sources: "
                    + ", ".join(result.get("sources") or [])
                    + " · tools: "
                    + ", ".join(result.get("tools_used") or [])
                )
            if verdict:
                st.json(
                    {
                        k: verdict.get(k)
                        for k in ("accept", "score", "issues", "feedback", "reviewer_model")
                    }
                )

if v.get("approvals"):
    st.subheader("Approvals on this task")
    st.dataframe(v["approvals"], use_container_width=True, hide_index=True)

if v.get("final_output"):
    fo = v["final_output"]
    st.subheader(("🧑 " if fo.get("human_authored") else "") + fo.get("title", "Deliverable"))
    st.caption(f"confidence {fo.get('confidence')} · sources: {', '.join(fo.get('sources') or [])}")
    st.markdown(fo.get("body", ""))
    with st.expander("raw JSON"):
        st.code(json.dumps(fo, indent=2), language="json")
