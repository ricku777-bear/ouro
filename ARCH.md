# Architecture v1: Task Board + Fanout

Ouro is built around **ReAct loops**: the agent reasons, uses tools, observes results, and repeats.

v1 orchestration is intentionally minimal:
- LLM does planning and synthesis.
- Runtime does only mechanical scheduling + artifact persistence.

## Primitives

Orchestration should be composed from small primitives in the manager ReAct loop:

- `task_board`: create/list/get/update tasks; encode dependencies; persist to `tasks.md`.
- `multi_task` (fanout): run N worker ReAct loops in parallel; return `summary + artifacts` per task.

Avoid a monolithic `orchestrate` tool that runs a second "manager agent loop" inside a tool.

## `tasks.md` (Minimal IR)

`tasks.md` is the persistent blueprint and audit trail.
For deterministic parsing, it SHOULD contain a single fenced JSON block:

```json
{
  "version": 1,
  "goal": "One sentence goal",
  "tasks": [
    {
      "id": "T0",
      "status": "pending",
      "owner": null,
      "blocked_by": [],
      "subject": "Extract section headings",
      "description": "From user:/path/to/paper.pdf, extract up to 8 top-level headings.",
      "active_form": "Extracting section headings",
      "metadata": {"source": "paper.pdf"},
      "summary": "",
      "artifacts": [],
      "errors": ""
    }
  ]
}
```

Allowed `status`: `pending | in_progress | completed | failed`.

We store only `blocked_by` edges. The inverse (`blocks`) is derivable.

## Scheduling (Round-Based)

A "round" is one fanout barrier (typically one `multi_task` call).

A task is runnable when:

- `status == "pending"`
- `owner` is empty/unset
- all referenced `blocked_by` tasks are `completed`

Minimal state transitions:

- `pending` -> `in_progress` (manager assigns `owner`, schedules execution)
- `in_progress` -> `completed` (worker succeeded)
- `in_progress` -> `failed` (worker failed)

## Map-Reduce

Map-reduce is expressed in the task graph:

- `MAP`: multiple independent tasks in the same round (fanout N)
- `REDUCE`: one task that depends on all map tasks (fanout 1)

## Artifacts

Artifacts are the source of truth for "full context"; summaries are compact carry-forward signals.

Suggested layout:

```
tasks.md
.ouro_artifacts/orchestrations/<run_id>/
  tasks.snapshot.md
  round_0/
    task_T0.md
    task_T1.md
```

Downstream steps should prefer `summary`, and open full artifacts on demand.

## Parallel Writes (Optional): Lazy Worktree Isolation

If workers are allowed to write/edit/run arbitrary commands in a shared workspace, concurrency is non-deterministic.
A pragmatic compromise is **write-on-demand worktree isolation**:

- Workers start in the main workspace for read-only exploration.
- When a worker decides it must write, it first creates/acquires a dedicated git worktree and writes there.
- The worker reports back `WORKTREE_PATH + diff/commit` for the manager to merge.

Even with "all nodes are full ReAct", we still need small hard budgets (`max_parallel`, per-worker timeouts).
