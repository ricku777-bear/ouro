# RFC 012: Sub-Agent Artifacts and Required-Fetch Hints for `multi_task`

- Status: Proposed
- Authors: Li0k, Codex
- Date: 2026-02-19

## Summary

Improve `multi_task` context propagation by combining structured sub-agent summaries with optional markdown artifacts. Downstream tasks should consume compact summaries by default and fetch full artifact content only when required fields are missing.

## Problem

Current dependency context handling has improved from prefix truncation, but it still relies on a single in-memory text path. For long sub-agent outputs, the system needs both:
- compact, reliable context for downstream execution;
- full-fidelity output for audit/debug/follow-up.

A concrete failure mode is when key evidence is present in full output but absent in a compact dependency summary, and downstream tasks need that evidence to proceed safely.

## Goals

- Preserve `multi_task` as a simple LLM-first orchestration tool.
- Keep downstream context compact by default.
- Preserve full sub-agent output as per-task artifacts (`.md`) for on-demand retrieval.
- Add a deterministic, minimal fetch hint so retrieval behavior is predictable.
- Keep the policy simple enough to avoid a scoring engine.

## Non-goals

- Introduce a new global workflow engine or heavy DAG runtime.
- Replace existing ReAct loop architecture.
- Add distributed workers or persistent artifact indexing.
- Force all downstream steps to read artifacts.

## Proposed Behavior (User-Facing)

Describe the observable behavior.

- CLI / UX changes:
  - No new top-level CLI command.
  - `multi_task` outputs include artifact path metadata per task when available.
- Config changes:
  - No global config required in v1.
  - Add optional `multi_task` argument: `artifact_dir` (default local project path).
  - Default artifact root: `<cwd>/.ouro_artifacts/<run_id>/`.
- Output / logging changes:
  - Each task result contains: `summary`, `key_findings`, `errors`, `artifact_path`, `fetch_hint`, and `missing_fields`.
  - Dependency context passed to downstream subtasks contains compact fields and a retrieval hint:
    - `FETCH_HINT: NONE | REQUIRED`.

## Invariants (Must Not Regress)

- Non-`multi_task` execution paths remain unchanged.
- Dependency gating remains strict (`success` only).
- Default `max_parallel` remains `4` unless explicitly overridden.
- Missing artifacts must not fail the whole run; execution continues with summary-first context.

## Design Sketch (Minimal)

1. Extend `TaskExecutionResult` with:
   - `artifact_path: str`
   - `fetch_hint: str`
   - `missing_fields: list[str]`
2. Keep current structured sub-agent response contract (`SUMMARY`, `KEY_FINDINGS`, `ERRORS`).
3. Parse structured sections and compute missing fields:
   - Expected fields: `SUMMARY`, `KEY_FINDINGS`, `ERRORS`
   - Required fields (v1): `SUMMARY`
   - `missing_fields` contains expected sections that were not parsed
4. Compute fetch hint with a strict minimal rule:
   - `FETCH_HINT=REQUIRED` when any required field is missing
   - otherwise `FETCH_HINT=NONE`
5. Write task artifact markdown:
   - path: `<cwd>/.ouro_artifacts/<run_id>/task_<idx>.md` by default, or `<artifact_dir>/<run_id>/task_<idx>.md` when provided
   - content: task metadata + structured fields + raw output.
6. Build downstream dependency context as:
   - `SUMMARY`
   - `ARTIFACT_PATH`
   - `FETCH_HINT`
   - `MISSING_FIELDS`
   - (no full raw output by default)
7. `FETCH_HINT` is advisory (LLM-facing guidance), not a hard runtime gate in v1.

## Alternatives Considered

- Option A: Keep compact summary only (no artifact path).
  - Simpler, but loses full-fidelity traceability.
- Option B: Always pass full raw output in dependency context.
  - Better recall, but worse latency/cost and context bloat.
- Option C: Confidence-scored retrieval hints.
  - More flexible, but over-engineered for v1 and harder to tune.

## Test Plan

- Unit tests:
  - Parse structured sections and compute `missing_fields`.
  - `FETCH_HINT` classification (`NONE` vs `REQUIRED`).
  - Artifact writing success/failure behavior.
- Targeted tests to run locally:
  - `./scripts/dev.sh test -q test/test_multi_task.py`
  - `./scripts/dev.sh test -q test/test_parallel_tools.py`
- Smoke run (one real CLI run):
  - `python main.py --task "Analyze flight options then produce dependent trip plan using multi_task" --verify`

## Rollout / Migration

- Backward compatibility:
  - Not guaranteed. This is a behavior-level breaking change for result formatting and retrieval metadata.
  - Existing automation/scripts parsing legacy free-form result text must be updated.
- Migration steps (if any):
  - Update downstream parsers/prompts to consume `summary/fetch_hint/missing_fields/artifact_path`.
  - Docs update in README/examples after behavior lands.

## Risks & Mitigations

- Risk: Artifact file growth over time.
  - Mitigation: keep artifacts under run-scoped directory; add cleanup strategy in follow-up.
- Risk: Required-field policy is too strict or too loose, causing extra/insufficient retrieval.
  - Mitigation: start with `SUMMARY`-only required fields in v1 and validate with focused tests.
- Risk: Model output format drift.
  - Mitigation: local parser + fallback path already in place.

## Open Questions

- Should we add a lightweight cleanup command/retention policy in the same RFC or defer?
- Should required fields remain `SUMMARY`-only in v1, or include `KEY_FINDINGS` in v2?
