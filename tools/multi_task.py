"""Fanout-only multi-task tool for parallel sub-agent execution (v1).

Contract:
- Executes N independent tasks concurrently (fanout).
- No dependencies/DAG semantics inside this tool.
- Each sub-agent runs a full ReAct loop (tool calling), but recursion into multi_task is disallowed.
- Produces bounded structured fields (SUMMARY / KEY_FINDINGS / ERRORS) and writes full artifacts to disk.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm import LLMMessage

from .base import BaseTool

if TYPE_CHECKING:
    from agent.base import BaseAgent


@dataclass
class TaskExecutionResult:
    status: str
    output: str
    summary: str
    key_findings: str
    errors: str
    artifact_path: str


@dataclass
class MultiTaskExecution:
    tasks: list[str]
    results: dict[int, TaskExecutionResult]
    artifact_root: Path | None
    dag_path: str
    violations: list[str] | None = None


class MultiTaskTool(BaseTool):
    """Execute independent tasks concurrently using sub-agent ReAct loops."""

    MAX_PARALLEL = 4
    MAX_TASKS = 12
    SUBTASK_TIMEOUT_SECONDS = 300
    SUMMARY_MAX_CHARS = 300
    NON_CONFORMANT_PREVIEW_CHARS = 500

    def __init__(self, agent: BaseAgent):
        self.agent = agent

    @property
    def name(self) -> str:
        return "multi_task"

    @property
    def description(self) -> str:
        return (
            "Execute multiple independent tasks in parallel using sub-agents (fanout only).\n\n"
            "Input: tasks[] (strings), max_parallel.\n"
            "Output: per-task summaries plus artifact paths.\n"
            "Use this to speed up independent work; encode dependencies as multiple rounds in the manager."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "tasks": {
                "type": "array",
                "description": "List of independent task descriptions to execute (fanout only)",
                "items": {"type": "string"},
            },
            "max_parallel": {
                "type": "integer",
                "description": f"Max concurrent subtasks (default: {self.MAX_PARALLEL})",
                "minimum": 1,
                "default": self.MAX_PARALLEL,
            },
        }

    async def execute(
        self,
        tasks: list[str] | None = None,
        max_parallel: int | None = None,
        timeout: float | None = None,
    ) -> str:
        del timeout
        execution = await self.execute_structured(
            tasks=tasks or [],
            max_parallel=max_parallel,
            artifact_root=None,
            cleanup=None,
        )
        if execution.violations:
            return self._format_preflight_failure(execution.violations)
        return self._format_results(execution.tasks, execution.results, dag_path=execution.dag_path)

    async def execute_structured(
        self,
        *,
        tasks: list[str],
        max_parallel: int | None,
        artifact_root: Path | None,
        cleanup: bool | None,
    ) -> MultiTaskExecution:
        normalized = [str(t).strip() for t in (tasks or []) if str(t).strip()]
        if not normalized:
            return MultiTaskExecution(
                tasks=[],
                results={},
                artifact_root=None,
                dag_path="UNAVAILABLE",
                violations=["No tasks provided."],
            )
        if len(normalized) > self.MAX_TASKS:
            return MultiTaskExecution(
                tasks=[],
                results={},
                artifact_root=None,
                dag_path="UNAVAILABLE",
                violations=[f"Too many tasks: {len(normalized)} > MAX_TASKS({self.MAX_TASKS})."],
            )

        parallel_limit = _to_pos_int(max_parallel, default=self.MAX_PARALLEL)
        run_dir = artifact_root or self._prepare_artifact_run_dir()
        if run_dir is None:
            # Still run but without artifacts.
            run_dir = None

        tools = self._get_subtask_tools()
        results = await self._execute_fanout(
            normalized,
            tools,
            artifact_root=run_dir,
            max_parallel=parallel_limit,
        )

        dag_path = self._write_dag_visualization(normalized, results, run_dir)

        should_cleanup = cleanup
        if should_cleanup is None:
            should_cleanup = not self._should_keep_artifacts()
        if should_cleanup:
            self._cleanup_artifact_run_dir(run_dir)
            dag_path = "CLEANED" if dag_path != "UNAVAILABLE" else "UNAVAILABLE"

        return MultiTaskExecution(
            tasks=normalized,
            results=results,
            artifact_root=run_dir,
            dag_path=dag_path,
            violations=None,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute_fanout(
        self,
        tasks: list[str],
        tools: list[dict[str, Any]],
        *,
        artifact_root: Path | None,
        max_parallel: int,
    ) -> dict[int, TaskExecutionResult]:
        semaphore = asyncio.Semaphore(max_parallel)
        timeout_seconds = float(
            os.getenv("OURO_SUBTASK_TIMEOUT_SECONDS", str(self.SUBTASK_TIMEOUT_SECONDS))
        )
        results: dict[int, TaskExecutionResult] = {}

        async def run_one(idx: int) -> None:
            async with semaphore:
                try:
                    output = await asyncio.wait_for(
                        self._run_subtask(idx, tasks[idx], tools),
                        timeout=timeout_seconds,
                    )
                    results[idx] = self._build_result(idx, tasks[idx], output, artifact_root)
                except asyncio.TimeoutError:
                    msg = f"Task timed out after {timeout_seconds}s."
                    results[idx] = self._build_result(
                        idx, tasks[idx], msg, artifact_root, status="failed"
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    msg = f"Task failed: {str(e)}"
                    results[idx] = self._build_result(
                        idx, tasks[idx], msg, artifact_root, status="failed"
                    )

        async with asyncio.TaskGroup() as tg:
            for i in range(len(tasks)):
                tg.create_task(run_one(i))

        return results

    async def _run_subtask(self, idx: int, task_desc: str, tools: list[dict[str, Any]]) -> str:
        prompt = "\n".join(
            [
                "<role>",
                "You are a sub-agent executing one independent work unit.",
                "</role>",
                "",
                "<instructions>",
                "- You may use tools as needed to complete the task.",
                "- Do NOT call multi_task or task_board.",
                "- Final response MUST follow this exact structure:",
                f"  SUMMARY: <concise summary, max {self.SUMMARY_MAX_CHARS} chars>",
                "  KEY_FINDINGS:",
                "  - <finding 1>",
                "  - <finding 2>",
                "  ERRORS:",
                "  - none (if no errors) OR list concrete errors",
                "</instructions>",
                "",
                "<task>",
                f"Task #{idx}: {task_desc}",
                "</task>",
                "",
                "Execute the task now:",
            ]
        )
        messages = [LLMMessage(role="user", content=prompt)]
        return await self.agent._react_loop(
            messages=messages, tools=tools, use_memory=False, save_to_memory=False
        )

    # ------------------------------------------------------------------
    # Tools / artifacts
    # ------------------------------------------------------------------

    def _get_subtask_tools(self) -> list[dict[str, Any]]:
        schemas = self.agent.tool_executor.get_tool_schemas()
        excluded = {"multi_task", "task_board", "manage_todo_list"}
        filtered: list[dict[str, Any]] = []
        for schema in schemas:
            name = schema.get("name") or schema.get("function", {}).get("name") or ""
            if name in excluded:
                continue
            filtered.append(schema)
        return filtered

    def _prepare_artifact_run_dir(self) -> Path | None:
        run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = Path.cwd() / ".ouro_artifacts" / run_id
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return path

    def _cleanup_artifact_run_dir(self, run_dir: Path | None) -> None:
        if run_dir is None:
            return
        try:
            shutil.rmtree(run_dir)
        except OSError:
            return

    def _build_result(
        self,
        idx: int,
        task_desc: str,
        output: str,
        artifact_root: Path | None,
        *,
        status: str = "success",
    ) -> TaskExecutionResult:
        summary, key_findings, errors = _extract_sections(output)
        if not summary:
            summary = _fallback_summary(output, max_chars=self.SUMMARY_MAX_CHARS)
        if not key_findings:
            key_findings = "- (not provided)"
        if not errors:
            errors = "- none"

        artifact_path = self._write_task_artifact(
            artifact_root=artifact_root,
            idx=idx,
            task_desc=task_desc,
            output=output,
            summary=summary,
            key_findings=key_findings,
            errors=errors,
            status=status,
        )

        return TaskExecutionResult(
            status=status,
            output=output,
            summary=summary,
            key_findings=key_findings,
            errors=errors,
            artifact_path=artifact_path,
        )

    def _write_task_artifact(
        self,
        *,
        artifact_root: Path | None,
        idx: int,
        task_desc: str,
        output: str,
        summary: str,
        key_findings: str,
        errors: str,
        status: str,
    ) -> str:
        if artifact_root is None:
            return "UNAVAILABLE"
        path = artifact_root / f"task_{idx}.md"
        content = "\n".join(
            [
                "# Task Artifact",
                "",
                f"task_idx: {idx}",
                f"status: {status}",
                f"task_desc: {task_desc}",
                "",
                "## SUMMARY",
                summary or "(missing)",
                "",
                "## KEY_FINDINGS",
                key_findings or "(missing)",
                "",
                "## ERRORS",
                errors or "(missing)",
                "",
                "## RAW_OUTPUT",
                output or "",
                "",
            ]
        )
        try:
            path.write_text(content, encoding="utf-8")
        except OSError:
            return "UNAVAILABLE"
        return str(path)

    def _write_dag_visualization(
        self,
        tasks: list[str],
        results: dict[int, TaskExecutionResult],
        artifact_root: Path | None,
    ) -> str:
        if artifact_root is None:
            return "UNAVAILABLE"
        path = artifact_root / "dag.mmd"
        lines = ["flowchart TD"]
        for idx, task in enumerate(tasks):
            r = results.get(idx)
            status = r.status if r else "not_executed"
            label = _sanitize_mermaid_label(task)[:80]
            lines.append(f'    T{idx}["#{idx} {label}<br/>status={status}"]')
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            return "UNAVAILABLE"
        return str(path)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_preflight_failure(self, violations: list[str]) -> str:
        lines = ["# Multi-Task", "STATUS: PLAN_REJECTED", "VIOLATIONS:"]
        lines.extend(f"- {v}" for v in violations)
        return "\n".join(lines)

    def _format_results(
        self, tasks: list[str], results: dict[int, TaskExecutionResult], *, dag_path: str
    ) -> str:
        parts = ["# Multi-Task Results", f"DAG_PATH: {dag_path}", ""]
        for idx in range(len(tasks)):
            r = results.get(idx)
            if r is None:
                parts.append(f"## Task {idx}\nStatus: not_executed\n")
                continue
            parts.append(
                "\n".join(
                    [
                        f"## Task {idx}",
                        f"Status: {r.status}",
                        f"SUMMARY: {r.summary}",
                        "KEY_FINDINGS:",
                        r.key_findings,
                        "ERRORS:",
                        r.errors,
                        f"ARTIFACT_PATH: {r.artifact_path}",
                        "",
                    ]
                )
            )
        return "\n".join(parts).strip() + "\n"

    def _should_keep_artifacts(self) -> bool:
        keep_flag = os.getenv("OURO_KEEP_MULTITASK_ARTIFACTS")
        if keep_flag is not None:
            return keep_flag.strip().lower() in {"1", "true", "yes", "on"}
        verbose_flag = os.getenv("OURO_VERBOSE", "0")
        return verbose_flag.strip().lower() in {"1", "true", "yes", "on"}


def _to_pos_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _sanitize_mermaid_label(value: str) -> str:
    text = (value or "").replace("\n", " ").replace('"', "'").strip()
    text = text.replace("[", "(").replace("]", ")")
    return text if text else "(empty task)"


def _extract_sections(output: str) -> tuple[str | None, str | None, str | None]:
    aliases = {"summary": "SUMMARY:", "key_findings": "KEY_FINDINGS:", "errors": "ERRORS:"}
    sections: dict[str, list[str]] = {k: [] for k in aliases}
    active: str | None = None
    for raw in (output or "").splitlines():
        stripped = raw.strip()
        upper = stripped.upper()
        matched = None
        for name, prefix in aliases.items():
            if upper.startswith(prefix):
                matched = name
                active = name
                inline = stripped[len(prefix) :].strip()
                if inline:
                    sections[name].append(inline)
                break
        if matched is not None:
            continue
        if active is not None:
            sections[active].append(stripped)

    def norm(lines: list[str]) -> str | None:
        cleaned = [ln for ln in lines if ln.strip()]
        if not cleaned:
            return None
        return "\n".join(cleaned).strip()

    return norm(sections["summary"]), norm(sections["key_findings"]), norm(sections["errors"])


def _fallback_summary(output: str, *, max_chars: int) -> str:
    text = (output or "").strip()
    if not text:
        return "(empty)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    joined = " ".join(lines) if lines else text
    return joined if len(joined) <= max_chars else joined[:max_chars].rstrip() + "..."
