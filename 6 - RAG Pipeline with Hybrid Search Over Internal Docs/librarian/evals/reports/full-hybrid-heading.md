# Eval report full-hybrid-heading

50 runs · k=1 · mode=hybrid · strategy=heading · 2026-09-06T17:36:20+00:00 → 2026-09-06T17:39:40+00:00

| metric | value | target | met |
|---|---|---|---|
| refusal_honesty | 1.0 | ≥ 1.0 | yes |
| success_rate_answerable | 0.8 | ≥ 0.8 | yes |
| retrieval_recall | 0.8375 | ≥ 0.85 | NO |
| citation_accuracy | 0.8995 | ≥ 0.85 | yes |
| faithfulness_rate | 0.9412 | ≥ 0.9 | yes |
| success_rate (all) | 0.84 | — | |
| correctness_mean | 4.6471 | — | |
| latency p50 / p95 s | 7.25 / 17.75 | — | |

## Per category

| category | runs | success | failure reasons seen |
|---|---|---|---|
| ambiguous | 6 | 0.6667 | correctness 3 < 4; refused an answerable question |
| exact_token | 8 | 0.875 | refused an answerable question |
| lookup | 18 | 0.8889 | correctness 1 < 4; missing required content: CORSMiddleware; refused an answerable question |
| multi_hop | 8 | 0.625 | refused an answerable question |
| no_answer | 10 | 1.0 | — |

## Failed runs

- `ambig_validate_data` (run 0): correctness 3 < 4
- `ambig_deploy` (run 0): refused an answerable question
- `token_http_404_not_found` (run 0): refused an answerable question
- `lookup_status_code_decorator` (run 0): refused an answerable question
- `lookup_cors_setup` (run 0): missing required content: CORSMiddleware; correctness 1 < 4
- `hop_error_vs_response` (run 0): refused an answerable question
- `hop_path_and_query_validation` (run 0): refused an answerable question
- `hop_secure_plus_background` (run 0): refused an answerable question
