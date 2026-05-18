import pytest

from core.exceptions import RetryableError
from core.workflow import Workflow, WorkflowConfig
from utils.parser import parse_response


def test_orphan_file_block_requests_file_write_retry() -> None:
    workflow = Workflow.__new__(Workflow)
    parsed = parse_response('<file path="app.py">print("hi")</file>', require_block=False)

    with pytest.raises(RetryableError, match="file_write"):
        workflow._assert_no_orphan_file_blocks(parsed)


def test_file_block_with_tool_call_is_allowed() -> None:
    workflow = Workflow.__new__(Workflow)
    parsed = parse_response(
        '<file path="app.py">print("hi")</file>\n'
        '<tool name="file_write">{"path":"app.py","content":"print(\\"hi\\")"}</tool>',
        require_block=False,
    )

    workflow._assert_no_orphan_file_blocks(parsed)


def test_workflow_config_infer_done_after_tool_reply_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKFLOW_INFER_DONE_AFTER_TOOL_REPLY", "0")

    cfg = WorkflowConfig.from_env()

    assert cfg.infer_done_after_tool_reply is False

