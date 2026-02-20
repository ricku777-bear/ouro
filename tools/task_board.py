"""Task board tool (v1) for round-based orchestration.

Design goals:
- Extremely small API surface (create/list/get/update/runnable).
- Session-scoped in-memory state with optional hydration/sync to a persistent `tasks.md`.
- Deterministic, machine-readable `tasks.md` via a single fenced JSON block.

This intentionally mirrors the "Tasks as state" pattern: the LLM can plan by mutating a task table,
while the runtime only needs mechanical scheduling rules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import BaseTool

_DEFAULT_TASKS_PATH = "tasks.md"


@dataclass
class TaskRecord:
    id: str
    status: str = "pending"  # pending|in_progress|completed|failed
    owner: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    subject: str = ""
    description: str = ""
    active_form: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    errors: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "owner": self.owner,
            "blocked_by": list(self.blocked_by),
            "subject": self.subject,
            "description": self.description,
            "active_form": self.active_form,
            "metadata": dict(self.metadata or {}),
            "summary": self.summary,
            "artifacts": list(self.artifacts or []),
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        return cls(
            id=str(data.get("id", "")).strip(),
            status=str(data.get("status", "pending")).strip() or "pending",
            owner=(str(data.get("owner")).strip() if data.get("owner") is not None else None),
            blocked_by=[str(x).strip() for x in (data.get("blocked_by") or []) if str(x).strip()],
            subject=str(data.get("subject", "")).strip(),
            description=str(data.get("description", "")).strip(),
            active_form=str(data.get("active_form", "")).strip(),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            summary=str(data.get("summary", "")).strip(),
            artifacts=[str(x).strip() for x in (data.get("artifacts") or []) if str(x).strip()],
            errors=str(data.get("errors", "")).strip(),
        )


@dataclass
class TaskPlan:
    version: int = 1
    goal: str = ""
    tasks: dict[str, TaskRecord] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "goal": self.goal,
            "tasks": [t.to_dict() for _id, t in sorted(self.tasks.items(), key=lambda kv: kv[0])],
        }


class TaskBoardTool(BaseTool):
    """A minimal task board tool for orchestration."""

    @property
    def name(self) -> str:
        return "task_board"

    @property
    def description(self) -> str:
        return (
            "Maintain a task board for round-based orchestration.\n\n"
            "Use this for dependent workflows and map-reduce:\n"
            "- create tasks with blocked_by dependencies\n"
            "- list/get/update task state and attach artifacts/summaries\n"
            "- query runnable tasks\n\n"
            "Optionally persists to tasks.md via a single fenced JSON block."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "operation": {
                "type": "string",
                "description": "create|list|get|update|runnable|hydrate|sync",
            },
            "path": {
                "type": "string",
                "description": f"Path to tasks.md (default: { _DEFAULT_TASKS_PATH })",
                "default": _DEFAULT_TASKS_PATH,
            },
            "goal": {"type": "string", "description": "Plan goal (hydrate/create)"},
            "id": {"type": "string", "description": "Task id (get/update)"},
            "subject": {"type": "string", "description": "Task subject (create)"},
            "description": {"type": "string", "description": "Task description (create)"},
            "active_form": {"type": "string", "description": "Active form (create/update)"},
            "blocked_by": {
                "type": "array",
                "description": "Dependency task ids",
                "items": {"type": "string"},
            },
            "status": {"type": "string", "description": "pending|in_progress|completed|failed"},
            "owner": {
                "type": "string",
                "description": "Owner label (agent name/worktree path/etc)",
            },
            "summary": {"type": "string", "description": "Short summary to attach"},
            "artifacts": {
                "type": "array",
                "description": "Artifact paths",
                "items": {"type": "string"},
            },
            "errors": {"type": "string", "description": "Error text to attach"},
            "metadata": {"type": "object", "description": "Arbitrary metadata"},
            "limit": {
                "type": "integer",
                "description": "Limit results (list/runnable)",
                "minimum": 1,
                "default": 50,
            },
        }

    def __init__(self) -> None:
        self._plan = TaskPlan()
        self._path: str = _DEFAULT_TASKS_PATH

    async def execute(self, operation: str, path: str = _DEFAULT_TASKS_PATH, **kwargs) -> str:
        op = (operation or "").strip().lower()
        if not op:
            return "Error: operation is required"

        self._path = (path or _DEFAULT_TASKS_PATH).strip() or _DEFAULT_TASKS_PATH

        if op == "hydrate":
            goal = (kwargs.get("goal") or "").strip()
            return self._hydrate(goal=goal)

        if op == "sync":
            return self._sync()

        if op == "create":
            subject = (kwargs.get("subject") or "").strip()
            description = (kwargs.get("description") or "").strip()
            active_form = (kwargs.get("active_form") or "").strip()
            blocked_by = kwargs.get("blocked_by") or []
            metadata = kwargs.get("metadata") or {}
            if not subject or not description:
                return "Error: create requires subject and description"
            task_id = self._alloc_id()
            rec = TaskRecord(
                id=task_id,
                subject=subject,
                description=description,
                active_form=active_form,
                blocked_by=[str(x).strip() for x in blocked_by if str(x).strip()],
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            self._plan.tasks[task_id] = rec
            self._sync_best_effort()
            return json.dumps({"id": task_id}, ensure_ascii=True)

        if op == "list":
            limit = _to_pos_int(kwargs.get("limit"), default=50)
            items = []
            for _id, t in sorted(self._plan.tasks.items(), key=lambda kv: kv[0])[:limit]:
                items.append(
                    {
                        "id": t.id,
                        "status": t.status,
                        "owner": t.owner,
                        "blocked_by": t.blocked_by,
                        "subject": t.subject,
                    }
                )
            return json.dumps({"goal": self._plan.goal, "tasks": items}, ensure_ascii=True)

        if op == "get":
            task_id = (kwargs.get("id") or "").strip()
            if not task_id:
                return "Error: get requires id"
            t = self._plan.tasks.get(task_id)
            if not t:
                return "Error: task not found"
            return json.dumps(t.to_dict(), ensure_ascii=True)

        if op == "update":
            task_id = (kwargs.get("id") or "").strip()
            if not task_id:
                return "Error: update requires id"
            t = self._plan.tasks.get(task_id)
            if not t:
                return "Error: task not found"

            for field_name in [
                "status",
                "owner",
                "subject",
                "description",
                "active_form",
                "summary",
                "errors",
            ]:
                if field_name in kwargs and kwargs[field_name] is not None:
                    setattr(t, field_name, str(kwargs[field_name]).strip())

            if "blocked_by" in kwargs and kwargs["blocked_by"] is not None:
                raw = kwargs["blocked_by"]
                if isinstance(raw, list):
                    t.blocked_by = [str(x).strip() for x in raw if str(x).strip()]

            if "artifacts" in kwargs and kwargs["artifacts"] is not None:
                raw = kwargs["artifacts"]
                if isinstance(raw, list):
                    t.artifacts = [str(x).strip() for x in raw if str(x).strip()]

            if "metadata" in kwargs and kwargs["metadata"] is not None:
                raw = kwargs["metadata"]
                if isinstance(raw, dict):
                    t.metadata = raw

            self._sync_best_effort()
            return "OK"

        if op == "runnable":
            limit = _to_pos_int(kwargs.get("limit"), default=50)
            runnable = self._runnable_ids()
            return json.dumps({"runnable": runnable[:limit]}, ensure_ascii=True)

        return f"Error: unknown operation '{op}'"

    # ------------------------------------------------------------------
    # Internal state / persistence
    # ------------------------------------------------------------------

    def _hydrate(self, *, goal: str) -> str:
        path = Path(self._path)
        if not path.exists():
            self._plan = TaskPlan(goal=goal or "")
            self._sync_best_effort()
            return "OK"

        try:
            data = path.read_text(encoding="utf-8")
        except OSError:
            return "Error: failed to read tasks file"

        payload = _extract_json_block(data)
        if payload is None:
            return "Error: tasks.md missing JSON block"

        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return "Error: invalid JSON in tasks.md"

        plan = TaskPlan(
            version=int(obj.get("version", 1) or 1),
            goal=str(obj.get("goal", "")).strip(),
            tasks={},
        )
        for item in obj.get("tasks") or []:
            if not isinstance(item, dict):
                continue
            rec = TaskRecord.from_dict(item)
            if rec.id:
                plan.tasks[rec.id] = rec

        if goal:
            plan.goal = goal
        self._plan = plan
        return "OK"

    def _sync(self) -> str:
        try:
            Path(self._path).write_text(_render_tasks_md(self._plan), encoding="utf-8")
        except OSError:
            return "Error: failed to write tasks file"
        return "OK"

    def _sync_best_effort(self) -> None:
        # Best-effort persistence so the manager doesn't need to remember to call sync.
        try:
            Path(self._path).write_text(_render_tasks_md(self._plan), encoding="utf-8")
        except OSError:
            return

    def _alloc_id(self) -> str:
        max_n = -1
        for task_id in self._plan.tasks:
            m = re.match(r"^T([0-9]+)$", task_id)
            if not m:
                continue
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            max_n = max(max_n, n)
        return f"T{max_n + 1}"

    def _runnable_ids(self) -> list[str]:
        def is_completed(tid: str) -> bool:
            t = self._plan.tasks.get(tid)
            return bool(t and t.status == "completed")

        out: list[str] = []
        for _id, t in sorted(self._plan.tasks.items(), key=lambda kv: kv[0]):
            if t.status != "pending":
                continue
            if t.owner:
                continue
            # Static deps: all blocked_by completed.
            if any((dep and not is_completed(dep)) for dep in (t.blocked_by or [])):
                continue
            out.append(t.id)
        return out


def _to_pos_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _extract_json_block(text: str) -> str | None:
    # Single fenced JSON block. Keep parsing deterministic and avoid fragile regex escaping.
    if not text:
        return None
    lower = text.lower()
    start = lower.find("```json")
    if start < 0:
        return None
    start_nl = text.find("\n", start)
    if start_nl < 0:
        return None
    end = text.find("```", start_nl + 1)
    if end < 0:
        return None
    payload = text[start_nl + 1 : end].strip()
    if not payload.startswith("{"):
        return None
    return payload


def _render_tasks_md(plan: TaskPlan) -> str:
    payload = json.dumps(plan.to_dict(), indent=2, ensure_ascii=True) + "\n"
    return "\n".join(
        [
            "# Task Plan",
            "",
            "This file is machine-written. Edit the JSON block only if you know what you're doing.",
            "",
            "```json",
            payload.rstrip("\n"),
            "```",
            "",
        ]
    )
