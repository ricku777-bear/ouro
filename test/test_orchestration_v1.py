"""Tests for round-based orchestration runner (v1)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from orchestration.v1 import RoundOrchestratorV1
from tools.multi_task import MultiTaskExecution, TaskExecutionResult
from tools.task_board import TaskBoardTool


@dataclass
class FakeMultiTask:
    """Deterministic multi_task for orchestration tests (no sub-agents)."""

    def __init__(self, round_to_results):
        self.round_to_results = round_to_results
        self.calls = 0

    async def execute_structured(self, *, tasks, max_parallel, artifact_root, cleanup):
        _ = (tasks, max_parallel, artifact_root, cleanup)
        results_for_round = self.round_to_results[self.calls]
        self.calls += 1
        return MultiTaskExecution(
            tasks=list(tasks),
            results=results_for_round,
            artifact_root=artifact_root,
            dag_path=str(artifact_root / "dag.mmd"),
            violations=None,
        )


@pytest.mark.asyncio
async def test_map_reduce_two_rounds(tmp_path):
    board = TaskBoardTool()
    tasks_path = tmp_path / "tasks.md"
    await board.execute(operation="hydrate", path=str(tasks_path), goal="G")

    # MAP tasks
    t0 = json.loads(
        await board.execute(
            operation="create", path=str(tasks_path), subject="M0", description="D0"
        )
    )["id"]
    t1 = json.loads(
        await board.execute(
            operation="create", path=str(tasks_path), subject="M1", description="D1"
        )
    )["id"]
    # REDUCE task depends on both
    t2 = json.loads(
        await board.execute(
            operation="create",
            path=str(tasks_path),
            subject="R",
            description="DR",
            blocked_by=[t0, t1],
        )
    )["id"]

    fake = FakeMultiTask(
        round_to_results=[
            {
                0: TaskExecutionResult(
                    status="success",
                    output="o0",
                    summary="s0",
                    key_findings="- k0",
                    errors="- none",
                    artifact_path=str(tmp_path / "a0.md"),
                ),
                1: TaskExecutionResult(
                    status="success",
                    output="o1",
                    summary="s1",
                    key_findings="- k1",
                    errors="- none",
                    artifact_path=str(tmp_path / "a1.md"),
                ),
            },
            {
                0: TaskExecutionResult(
                    status="success",
                    output="o2",
                    summary="s2",
                    key_findings="- k2",
                    errors="- none",
                    artifact_path=str(tmp_path / "a2.md"),
                )
            },
        ]
    )

    orch = RoundOrchestratorV1(
        task_board=board,
        multi_task=fake,
        tasks_path=str(tasks_path),
        artifact_root=str(tmp_path / "artifacts"),
    )
    run = await orch.run(max_rounds=10, max_parallel=4, cleanup_artifacts=False)

    assert run.deadlocked is False
    assert set(run.completed) == {t0, t1, t2}
    assert run.failed == []
    assert fake.calls == 2
