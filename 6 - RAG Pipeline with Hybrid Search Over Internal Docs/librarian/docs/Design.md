# Design — Librarian dashboard

Streamlit, one job: make retrieval quality *visible*. Evidence first, chrome last.

## 1. Principles

1. **No answer without its evidence.** The answer never renders without its citations, and
   every citation expands to the exact chunk with source, heading path, and page.
2. **Confidence is always on screen** — the composite and its three parts (retrieval,
   citation coverage, completeness), never just a green tick.
3. **Comparison is a first-class control**: hybrid vs dense-only side by side is how the
   project's thesis is demonstrated, so it is a toggle, not a hidden mode.
4. A refusal renders as a designed state (what was found / not found / where to look next),
   visually distinct from an error.

## 2. Tokens

Match Foreman's console so the portfolio feels like one hand built it: Streamlit defaults,
light theme, system font stack. Semantic colours only — green `#16a34a` (verified citation,
high confidence), amber `#d97706` (unverified citation, mid confidence), red `#dc2626`
(flagged citation, refusal threshold), neutral grey elsewhere. Monospace for chunk text,
scores to two decimals. No custom CSS beyond Streamlit theming.

## 3. Screens

| Page | Contents |
|---|---|
| **Ask** | question box; mode toggle (hybrid / dense-only / side-by-side); answer with inline `[n]` badges coloured by verification; citations panel (chunk text, source, heading path, page, scores at each stage: dense/sparse rank → RRF → rerank); confidence breakdown; timings footer |
| **Documents** | indexed documents table (title, format, chunks per strategy, ingested at); upload/ingest form; dedup-skip counts |
| **Evals** | latest report headline (the PRD §7 table with pass/fail), per-category table, chunking bake-off comparison, link to the markdown report |

## 4. Components (reusable)

`citation_badge(n, status)` · `chunk_card(hit)` (text + provenance + stage scores) ·
`confidence_bar(parts)` · `metric_row(name, value, target)` — one file per component under
`apps/dashboard/components/`.

## 5. Accessibility

Colour never carries meaning alone (badges also show ✓/?/✗); chunk text is real text, not
images; every score has a label; keyboard-only operation works (native Streamlit widgets only).

## 6. Out of scope for v1

Chat history, streaming tokens, dark theme, auth, mobile layout.
