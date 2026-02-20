"""Tests for fanout-only MultiTaskTool (v1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.multi_task import MultiTaskTool


def _make_agent(*, outputs: list[str]) -> MagicMock:
    agent = MagicMock()
    agent._react_loop = AsyncMock(side_effect=list(outputs))
    agent.tool_executor = MagicMock()
    agent.tool_executor.get_tool_schemas.return_value = [
        {"name": "read_file"},
        {"name": "shell"},
        {"name": "manage_todo_list"},
        {"name": "task_board"},
        {"name": "multi_task"},
    ]
    return agent


class TestMultiTaskToolV1:
    def test_tool_schema(self):
        agent = _make_agent(outputs=["SUMMARY: ok\nKEY_FINDINGS:\n- x\nERRORS:\n- none\n"])
        tool = MultiTaskTool(agent)

        assert tool.name == "multi_task"
        params = tool.parameters
        assert set(params.keys()) == {"tasks", "max_parallel"}
        assert params["tasks"]["type"] == "array"
        assert params["max_parallel"]["type"] == "integer"

    def test_get_subtask_tools_excludes_recursive_tools(self):
        agent = _make_agent(outputs=["SUMMARY: ok\nKEY_FINDINGS:\n- x\nERRORS:\n- none\n"])
        tool = MultiTaskTool(agent)

        schemas = tool._get_subtask_tools()
        names = {s.get("name") for s in schemas}
        assert "read_file" in names
        assert "shell" in names
        assert "multi_task" not in names
        assert "task_board" not in names
        assert "manage_todo_list" not in names

    @pytest.mark.asyncio
    async def test_execute_structured_rejects_empty(self, tmp_path):
        agent = _make_agent(outputs=[])
        tool = MultiTaskTool(agent)

        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        execution = await tool.execute_structured(
            tasks=[],
            max_parallel=2,
            artifact_root=run_dir,
            cleanup=False,
        )
        assert execution.violations
        assert "No tasks" in execution.violations[0]

    @pytest.mark.asyncio
    async def test_execute_structured_runs_and_writes_artifacts(self, tmp_path):
        outputs = [
            "SUMMARY: A\nKEY_FINDINGS:\n- fa\nERRORS:\n- none\n",
            "SUMMARY: B\nKEY_FINDINGS:\n- fb\nERRORS:\n- none\n",
        ]
        agent = _make_agent(outputs=outputs)
        tool = MultiTaskTool(agent)

        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        execution = await tool.execute_structured(
            tasks=["do a", "do b"],
            max_parallel=1,
            artifact_root=run_dir,
            cleanup=False,
        )

        assert execution.violations is None
        assert set(execution.results.keys()) == {0, 1}
        assert (run_dir / "task_0.md").exists()
        assert (run_dir / "task_1.md").exists()
        assert (run_dir / "dag.mmd").exists()

        r0 = execution.results[0]
        assert r0.summary == "A"
        assert "fa" in r0.key_findings
        assert "none" in r0.errors.lower()

    @pytest.mark.asyncio
    async def test_fallback_summary_when_sections_missing(self, tmp_path):
        agent = _make_agent(outputs=["just raw output without structure\n" * 5])
        tool = MultiTaskTool(agent)

        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        execution = await tool.execute_structured(
            tasks=["x"],
            max_parallel=1,
            artifact_root=run_dir,
            cleanup=False,
        )
        r0 = execution.results[0]
        assert r0.summary  # fallback summary present
        assert r0.key_findings.strip()  # default is non-empty
        assert r0.errors.strip()  # default is non-empty
