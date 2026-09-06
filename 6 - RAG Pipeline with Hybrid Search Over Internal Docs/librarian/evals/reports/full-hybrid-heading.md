# Eval report full-hybrid-heading

50 runs · k=1 · mode=hybrid · strategy=heading · 2026-09-06T17:18:35+00:00 → 2026-09-06T17:34:21+00:00

| metric | value | target | met |
|---|---|---|---|
| refusal_honesty | 1.0 | ≥ 1.0 | yes |
| success_rate_answerable | 0.5 | ≥ 0.8 | NO |
| retrieval_recall | 0.8375 | ≥ 0.85 | NO |
| citation_accuracy | 0.9077 | ≥ 0.85 | yes |
| faithfulness_rate | 1.0 | ≥ 0.9 | yes |
| success_rate (all) | 0.6 | — | |
| correctness_mean | 4.7619 | — | |
| latency p50 / p95 s | 8.52 / 50.1 | — | |

## Per category

| category | runs | success | failure reasons seen |
|---|---|---|---|
| ambiguous | 6 | 0.0 | judge: no score; refused an answerable question |
| exact_token | 8 | 0.625 | correctness 3 < 4; judge: no score; refused an answerable question |
| lookup | 18 | 0.6667 | judge: no score; missing required content: CORSMiddleware; refused an answerable question |
| multi_hop | 8 | 0.375 | judge: no score; refused an answerable question |
| no_answer | 10 | 1.0 | — |

## Failed runs

- `ambig_handle_errors` (run 0): judge: no score
- `ambig_add_auth` (run 0): refused an answerable question
- `ambig_document_api` (run 0): refused an answerable question
- `ambig_validate_data` (run 0): judge: no score
- `ambig_return_files` (run 0): judge: no score
- `ambig_deploy` (run 0): refused an answerable question
- `token_http_404_not_found` (run 0): refused an answerable question
- `token_oauth2passwordrequestform` (run 0): judge: no score
- `token_uploadfile_read` (run 0): correctness 3 < 4
- `lookup_status_code_decorator` (run 0): judge: no score
- `lookup_cors_setup` (run 0): refused an answerable question; missing required content: CORSMiddleware
- `lookup_upload_file` (run 0): refused an answerable question
- `lookup_static_files` (run 0): refused an answerable question
- `lookup_api_router` (run 0): refused an answerable question
- `lookup_docs_urls` (run 0): refused an answerable question
- `hop_error_vs_response` (run 0): judge: no score
- `hop_path_and_query_validation` (run 0): refused an answerable question
- `hop_secure_plus_background` (run 0): refused an answerable question
- `hop_static_and_html` (run 0): refused an answerable question
- `hop_router_shared_dependency` (run 0): judge: no score
