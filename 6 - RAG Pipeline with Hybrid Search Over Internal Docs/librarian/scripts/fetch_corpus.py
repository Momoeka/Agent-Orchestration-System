"""Fetch the demo corpus: the FastAPI docs (public markdown), into CORPUS_DIR.

Shallow-clones the FastAPI repo to a temp dir and copies ``docs/en/docs/**/*.md``. The corpus
is never committed (Rules.md §5); run this once per clone.

Usage:  uv run python scripts/fetch_corpus.py [--limit N]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from librarian.config import get_settings

REPO = "https://github.com/fastapi/fastapi.git"
DOCS_SUBDIR = Path("docs/en/docs")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="copy at most N files (0 = all)")
    args = ap.parse_args()

    corpus = get_settings().corpus_dir
    corpus.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="librarian-corpus-") as tmp:
        print(f"cloning {REPO} (shallow)...")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", REPO, tmp],
            check=True,
        )
        src = Path(tmp) / DOCS_SUBDIR
        files = sorted(p for p in src.rglob("*.md") if p.is_file())
        if args.limit:
            files = files[: args.limit]
        for f in files:
            rel = f.relative_to(src)
            target = corpus / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    print(f"copied {len(files)} markdown files to {corpus}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
