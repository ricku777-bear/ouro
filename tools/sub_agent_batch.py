"""Parallel sub-agent execution for Tasks-based orchestration.

This tool is intentionally minimal: it runs multiple fresh ReAct loops (sub-agents)
concurrently, each focused on a single TaskStore task ID. Sub-agents are expected
to update their own task status via TaskGet/TaskList/TaskUpdate.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List

from llm import LLMMessage

from .base import BaseTool

if TYPE_CHECKING:
    from agent.base import BaseAgent


def _json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"


class SubAgentBatchTool(BaseTool):
    """Run multiple sub-agents in parallel for task IDs."""

    MAX_PARALLEL_CAP = 8
    MAX_OUTPUT_CHARS = 4000

    def __init__(self, agent: "BaseAgent"):
        self.agent = agent

    @property
    def name(self) -> str:
        return "sub_agent_batch"

    @property
    def description(self) -> str:
        return (
            "Run multiple sub-agents in parallel, each responsible for one Task ID. "
            "Each sub-agent is a fresh ReAct loop (no memory) and should use TaskGet/TaskList "
            "to self-check availability, then TaskUpdate to mark in_progress/completed."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "runs": {
                "type": "array",
                "description": "Sub-agent runs to execute in parallel",
                "items": {
                    "type": "object",
                    "properties": {
                        "taskId": {"type": "string", "description": "Task ID to execute"},
                        "notes": {
                            "type": "string",
                            "description": "Optional additional constraints for this run",
                            "default": "",
                        },
                    },
                    "required": ["taskId"],
                },
            },
            "maxParallel": {
                "type": "integer",
                "description": f"Max concurrent sub-agents (default: 4, cap: {MAX_PARALLEL_CAP})",
                "default": 4,
            },
        }

    def to_anthropic_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters,
                "required": ["runs"],
            },
        }

    def _get_subagent_tools(self) -> List[Dict[str, Any]]:
        all_tools = self.agent.tool_executor.get_tool_schemas()

        def _tool_name(schema: Dict[str, Any]) -> str | None:
            return schema.get("name") or schema.get("function", {}).get("name")

        excluded = {self.name, "multi_task"}
        return [t for t in all_tools if (_tool_name(t) not in excluded)]

    def _build_worker_prompt(self, *, task_id: str, notes: str) -> str:
        extra = notes.strip()
        notes_block = f"\n\n<constraints>\n{extra}\n</constraints>\n" if extra else ""

        return f"""<role>
You are a sub-agent executing exactly one task from a shared task graph.
You must focus ONLY on this task ID and update its status yourself.
</role>

<task_id>{task_id}</task_id>
{notes_block}
<contract>
1. Self-check:
   - Call TaskGet(id) to read the task.
   - Call TaskList() and ensure this id is in available. If not available or already completed, STOP.
2. Start:
   - Call TaskUpdate(id, status="in_progress") before doing work.
3. Execute:
   - Do only the work needed for this task.
   - Avoid unrelated edits and avoid creating scratch artifacts unless necessary.
4. Finish:
   - On success: TaskUpdate(id, status="completed")
   - On failure/blockers: reset to pending and explain why.
5. Output:
   - Return a short summary of what you did and any follow-ups for the main agent.
</contract>

Execute now."""

    async def execute(
        self,
        runs: List[Dict[str, Any]],
        maxParallel: int = 4,
        **kwargs,
    ) -> str:
        if not runs:
            return _json({"ok": False, "error": "runs must be a non-empty array"})

        try:
            max_parallel = int(maxParallel)
        except (TypeError, ValueError):
            max_parallel = 4
        max_parallel = max(1, min(max_parallel, self.MAX_PARALLEL_CAP))

        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for r in runs:
            task_id = str(r.get("taskId", "")).strip()
            if not task_id:
                return _json({"ok": False, "error": "Each run must include a non-empty taskId"})
            if task_id in seen:
                return _json({"ok": False, "error": f"Duplicate taskId in runs: {task_id}"})
            seen.add(task_id)
            normalized.append({"taskId": task_id, "notes": str(r.get("notes", "") or "")})

        tools = self._get_subagent_tools()
        semaphore = asyncio.Semaphore(max_parallel)
        results: list[dict[str, Any]] = []

        async def _run_one(task_id: str, notes: str) -> dict[str, Any]:
            prompt = self._build_worker_prompt(task_id=task_id, notes=notes)
            messages = [LLMMessage(role="user", content=prompt)]
            async with semaphore:
                try:
                    out = await self.agent._react_loop(
                        messages=messages,
                        tools=tools,
                        use_memory=False,
                        save_to_memory=False,
                        task=f"sub_agent:{task_id}",
                    )
                    return {
                        "taskId": task_id,
                        "ok": True,
                        "output": _truncate(str(out), self.MAX_OUTPUT_CHARS),
                    }
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    return {"taskId": task_id, "ok": False, "error": str(e)}

        async with asyncio.TaskGroup() as tg:
            task_list = [
                tg.create_task(_run_one(r["taskId"], r["notes"])) for r in normalized
            ]

        for t in task_list:
            results.append(t.result())

        return _json({"ok": True, "maxParallel": max_parallel, "results": results})

