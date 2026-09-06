"""Ask the indexed corpus a question — the Phase 3 demo.

Usage:  uv run python scripts/ask.py "how do I set a response_model" [--mode hybrid]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.stdout.encoding.lower() not in ("utf-8", "utf8"):  # Windows console default is cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from librarian.answer.pipeline import build_pipeline
from librarian.config import get_settings
from librarian.retrieve.retriever import Mode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "dense", "sparse"])
    args = ap.parse_args()

    answer = build_pipeline(get_settings()).ask(args.question, mode=cast(Mode, args.mode))

    print("\n" + ("=" * 72))
    print("REFUSAL" if answer.refusal else "ANSWER", f"· {answer.elapsed_s}s")
    print("=" * 72)
    print(answer.text)
    if answer.citations:
        print("\nCitations:")
        for c in answer.citations:
            mark = {True: "ok", False: "UNSUPPORTED", None: "unverified"}[c.supported]
            where = " > ".join(c.heading_path) or c.source
            note = f" — {c.note}" if c.note else ""
            print(f"  [{c.n}] {mark:<12} {where}{note}")
    conf = answer.confidence
    print(
        f"\nConfidence: composite {conf.composite}  (retrieval {conf.retrieval} · "
        f"citations {conf.citation_coverage} · completeness {conf.completeness})"
    )
    if answer.generator:
        print(f"Models: generator {answer.generator} · judge {answer.judge or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
