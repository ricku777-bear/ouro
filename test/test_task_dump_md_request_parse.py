from agent.task_policy import TaskPolicy


def _parse(task: str):
    return TaskPolicy(agent=object()).extract_task_dump_md_request(task)


def test_extract_task_dump_md_request_parses_path_and_debug() -> None:
    task = '请在最后调用 TaskDumpMd(path=".tmp/tasks.md", includeDebug=true)。'
    assert _parse(task) == (".tmp/tasks.md", True)


def test_extract_task_dump_md_request_parses_path_without_debug() -> None:
    task = "最后调用 TaskDumpMd(path='.tmp/tasks.md')"
    assert _parse(task) == (".tmp/tasks.md", False)


def test_extract_task_dump_md_request_requires_path() -> None:
    assert _parse("最后调用 TaskDumpMd(includeDebug=true)") is None
