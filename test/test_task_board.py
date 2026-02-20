"""Tests for TaskBoardTool (v1)."""

from __future__ import annotations

import json

import pytest

from tools.task_board import TaskBoardTool


@pytest.mark.asyncio
async def test_hydrate_creates_tasks_md_when_missing(tmp_path):
    tool = TaskBoardTool()
    tasks_path = tmp_path / "tasks.md"

    out = await tool.execute(operation="hydrate", path=str(tasks_path), goal="G")
    assert out == "OK"
    assert tasks_path.exists()
    text = tasks_path.read_text(encoding="utf-8")
    assert "```json" in text
    assert '"goal": "G"' in text


@pytest.mark.asyncio
async def test_create_list_get_update_and_runnable(tmp_path):
    tool = TaskBoardTool()
    tasks_path = tmp_path / "tasks.md"
    await tool.execute(operation="hydrate", path=str(tasks_path), goal="Goal")

    t0 = json.loads(
        await tool.execute(
            operation="create",
            path=str(tasks_path),
            subject="S0",
            description="D0",
        )
    )["id"]
    t1 = json.loads(
        await tool.execute(
            operation="create",
            path=str(tasks_path),
            subject="S1",
            description="D1",
            blocked_by=[t0],
        )
    )["id"]

    listed = json.loads(await tool.execute(operation="list", path=str(tasks_path)))
    assert [t["id"] for t in listed["tasks"]] == [t0, t1]

    runnable = json.loads(await tool.execute(operation="runnable", path=str(tasks_path)))
    assert runnable["runnable"] == [t0]

    await tool.execute(
        operation="update", path=str(tasks_path), id=t0, status="completed", summary="ok"
    )
    runnable2 = json.loads(await tool.execute(operation="runnable", path=str(tasks_path)))
    assert runnable2["runnable"] == [t1]

    got = json.loads(await tool.execute(operation="get", path=str(tasks_path), id=t1))
    assert got["blocked_by"] == [t0]


@pytest.mark.asyncio
async def test_hydrate_round_trip(tmp_path):
    t1 = TaskBoardTool()
    tasks_path = tmp_path / "tasks.md"
    await t1.execute(operation="hydrate", path=str(tasks_path), goal="Goal")
    created = json.loads(
        await t1.execute(
            operation="create",
            path=str(tasks_path),
            subject="S",
            description="D",
        )
    )["id"]
    await t1.execute(
        operation="update", path=str(tasks_path), id=created, status="completed", summary="done"
    )

    t2 = TaskBoardTool()
    await t2.execute(operation="hydrate", path=str(tasks_path), goal="")
    got = json.loads(await t2.execute(operation="get", path=str(tasks_path), id=created))
    assert got["status"] == "completed"
    assert got["summary"] == "done"


@pytest.mark.asyncio
async def test_dir_store_round_trip(tmp_path):
    tool = TaskBoardTool()
    store_dir = tmp_path / "task_list"

    out = await tool.execute(
        operation="hydrate",
        path=str(store_dir),
        store="dir",
        goal="G",
    )
    assert out == "OK"

    t0 = json.loads(
        await tool.execute(
            operation="create",
            path=str(store_dir),
            store="dir",
            subject="S0",
            description="D0",
        )
    )["id"]
    t1 = json.loads(
        await tool.execute(
            operation="create",
            path=str(store_dir),
            store="dir",
            subject="S1",
            description="D1",
            blocked_by=[t0],
        )
    )["id"]
    await tool.execute(
        operation="update",
        path=str(store_dir),
        store="dir",
        id=t0,
        status="completed",
        summary="done0",
    )

    # Verify per-task JSON exists with Claude-like keys.
    t1_path = store_dir / f"{t1}.json"
    assert t1_path.exists()
    obj = json.loads(t1_path.read_text(encoding="utf-8"))
    assert obj["id"] == t1
    assert obj["blockedBy"] == [t0]
    assert "activeForm" in obj

    tool2 = TaskBoardTool()
    out2 = await tool2.execute(operation="hydrate", path=str(store_dir), store="dir", goal="")
    assert out2 == "OK"
    got1 = json.loads(await tool2.execute(operation="get", path=str(store_dir), store="dir", id=t1))
    assert got1["blocked_by"] == [t0]
