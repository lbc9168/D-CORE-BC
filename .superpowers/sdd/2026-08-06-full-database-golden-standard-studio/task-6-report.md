# Task 6 report — Candidate Cache and Four-Source Orchestration

## Scope delivered

- Added `candidate_cache.py`, an atomic content-addressed JSON cache for validated `CandidateValue` records.
- Added `candidate_builder.py`, which builds one `CandidateBundle` from database, production, review, and deterministic sources without pipeline or production-database writes.
- Added `review_prompt.py`, which constructs an article-only review prompt with a stable prompt version hash.
- Added focused contract tests in `test_candidate_builder.py`.

## Interfaces and safety properties

- `CandidateCacheKey` contains the immutable article SHA-256, source name, source/model version, prompt hash, and parser/policy version. Its canonical JSON digest names the cache record.
- Cache writes create a process- and UUID-unique temporary file, flush and `fsync` it, then atomically replace the destination. Cache reads validate both the embedded key and the Pydantic candidate contract.
- `CandidateBuilder` always gives every runner an article-only `CandidateRunRequest`; neither database nor production candidate data is passed to the review runner.
- `ReviewCandidateRunner` rejects an accidental cross-candidate request as `independence_violation` and represents malformed provider JSON as `invalid_json`, never as an empty successful response.
- A failed provider is represented in the bundle with `status="error"`, `error_code`, and `error_detail`; successful sources remain available.

## Existing-contract reuse

- The production adapter calls its supplied staged extractor with `(title, content, publish_date)` and normalizes its raw output through `eval.predict.flat_prediction` before translating it to the Studio schema.
- The deterministic adapter uses the existing `parse_capacity_mw`, `extract_energy_facts`, and `classify_energy` implementation, recording the existing `energy_taxonomy_v1` policy version in its cache identity and metadata.
- No reusable production amount/date parser was present in the inspected extractor interfaces. The adapter therefore does not invent a competing amount/date normalizer; provider-specific deterministic parsers can remain source-local runners under the same protocol.
- Repository imports are resolved at runtime from the checkout and its `s01b_project_extractor/src` root, preserving the Studio's independent `uv` project while reusing the canonical code rather than copying it.

## TDD evidence

1. Added the new candidate-builder tests before candidate modules existed.
2. Ran the requested focused command and observed collection fail with `ModuleNotFoundError: gold_standard_studio.candidate_builder`.
3. Implemented the minimum cache/orchestrator/adapters to satisfy the contracts.
4. A focused run then found the real capacity-parser boundary: passing the whole article containing `50 MWh` causes `parse_capacity_mw` to reject it as non-installed capacity. The deterministic adapter now selects an installed-capacity phrase first and delegates unit semantics to the existing parser.

## Verification

```text
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_candidate_builder.py -q
8 passed in 0.16s

uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests -q
51 passed in 1.68s

uv run --project external-tools/gold-standard-studio python -m compileall -q external-tools/gold-standard-studio/src external-tools/gold-standard-studio/tests
exit 0

git diff --check
exit 0
```

## Concerns / follow-up

- Production and review runner versions must be supplied by the caller whenever provider, model, or code behavior changes; the cache deliberately treats that version string as part of its stable public key.
- Candidate generation has no side effect on production databases, but it does write Studio-local cache files only when invoked by a caller with a configured cache directory.

## Review follow-up — cache and contract hardening

- Cache loading now checks the decoded JSON is a mapping before touching mapping methods. Non-object JSON (`[]`, `null`, strings and scalars), key mismatches and schema-invalid records are quarantined beside the cache as unique `.corrupt` files and returned as cache misses.
- The cache writes only successful (`status="ok"`) candidate values. Errors are intentionally non-cacheable for every source, including deterministic contract errors, because retrying a corrected provider/configuration is safer than preserving a stale failed result. Legacy cached error records are also ignored.
- The reviewer accepts exactly `{"projects": [...]}` at the provider boundary. Missing or unknown top-level keys, non-list projects and invalid nested projects produce `CandidateValue(status="error", error_code="invalid_json")` rather than an ambiguous successful empty bundle.
- The static review prompt material is now explicit and reused by the rendered prompt/hash. The hash includes the version, reviewer instructions and response schema, but deliberately excludes article-specific values because the candidate key already includes the article SHA-256.
- CandidateBuilder validates source names on construction, requiring one each of database, production, review and deterministic before any source invocation.

### Follow-up verification

```text
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_candidate_builder.py -q
20 passed in 0.17s
```
