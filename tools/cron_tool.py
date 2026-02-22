"""Cron management tool for agents to schedule recurring tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.base import BaseTool

if TYPE_CHECKING:
    from bot.proactive import CronScheduler


class CronTool(BaseTool):
    """Tool for managing cron-scheduled tasks during conversation."""

    def __init__(self, cron_scheduler: CronScheduler):
        self._scheduler = cron_scheduler

    @property
    def name(self) -> str:
        return "manage_cron"

    @property
    def description(self) -> str:
        return """Manage cron-scheduled recurring tasks.

WHEN TO USE:
- User asks to schedule a recurring task (e.g. "每天早上9点给我发日报")
- User wants to list, modify, or remove scheduled jobs

OPERATIONS:
- add: Create a new cron job (requires schedule and prompt)
- remove: Delete an existing job (requires job_id)
- list: View all scheduled jobs

SCHEDULE FORMAT:
- Cron expression: "0 9 * * *" (daily at 9am), "*/30 * * * *" (every 30 min)
- Interval in seconds: "3600" (every hour), "300" (every 5 min)

EXAMPLES:
- add: {"schedule": "0 9 * * *", "prompt": "生成今日工作日报", "name": "Daily report"}
- list: {} (no parameters needed)
- remove: {"job_id": "abc123def456"}"""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "operation": {
                "type": "string",
                "description": "Operation to perform: add, remove, or list",
            },
            "schedule": {
                "type": "string",
                "description": "Cron expression (e.g. '0 9 * * *') or interval in seconds (for add)",
                "default": "",
            },
            "prompt": {
                "type": "string",
                "description": "Task prompt that the agent will execute on each run (for add)",
                "default": "",
            },
            "name": {
                "type": "string",
                "description": "Optional human-readable name for the job (for add)",
                "default": "",
            },
            "job_id": {
                "type": "string",
                "description": "Job ID to remove (for remove)",
                "default": "",
            },
        }

    async def execute(
        self,
        operation: str,
        schedule: str = "",
        prompt: str = "",
        name: str = "",
        job_id: str = "",
        **kwargs: Any,
    ) -> str:
        try:
            if operation == "add":
                return self._add(schedule, prompt, name)
            elif operation == "remove":
                return self._remove(job_id)
            elif operation == "list":
                return self._list()
            else:
                return f"Error: Unknown operation '{operation}'. Supported: add, remove, list"
        except Exception as e:
            return f"Error executing cron operation: {e}"

    def _add(self, schedule: str, prompt: str, name: str) -> str:
        if not schedule:
            return "Error: 'schedule' is required for add operation"
        if not prompt:
            return "Error: 'prompt' is required for add operation"
        try:
            job = self._scheduler.add_job(schedule, prompt, name=name)
        except (ValueError, KeyError) as exc:
            return f"Error: Invalid schedule '{schedule}': {exc}"
        return (
            f"Created cron job {job.id}"
            f" (name={job.name!r},"
            f" schedule={job.schedule_type}={job.schedule_value},"
            f" next_run={job.next_run_at})"
        )

    def _remove(self, job_id: str) -> str:
        if not job_id:
            return "Error: 'job_id' is required for remove operation"
        if self._scheduler.remove_job(job_id):
            return f"Removed cron job {job_id}."
        return f"Error: No cron job with id '{job_id}'."

    def _list(self) -> str:
        jobs = self._scheduler.jobs
        if not jobs:
            return "No cron jobs scheduled."
        lines = []
        for j in jobs:
            status = "enabled" if j.enabled else "disabled"
            lines.append(
                f"- {j.id}: {j.name}"
                f" [{j.schedule_type}={j.schedule_value}]"
                f" ({status}, next_run={j.next_run_at})"
            )
        return "Cron jobs:\n" + "\n".join(lines)
