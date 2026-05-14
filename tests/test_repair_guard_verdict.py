"""extract_json_verdict：围栏、裸 JSON、配平花括号等解析路径。"""

from core.repair_guard import extract_json_verdict


def test_fence_json() -> None:
    raw = """好的。
```json
{"allow_session_done": true, "assistant_replacement": "", "feedback": ""}
```
"""
    v = extract_json_verdict(raw)
    assert v is not None
    assert v["allow_session_done"] is True


def test_whole_document_json() -> None:
    raw = '{"allow_session_done": false, "assistant_replacement": "", "feedback": "缺工具"}'
    v = extract_json_verdict(raw)
    assert v is not None
    assert v["allow_session_done"] is False


def test_prefix_noise_then_brace_object() -> None:
    raw = """Here is the verdict:
{"allow_session_done": true, "assistant_replacement": "", "feedback": "ok"} trailing ignored"""
    v = extract_json_verdict(raw)
    assert v is not None
    assert v["allow_session_done"] is True


def test_nested_json_object() -> None:
    raw = '{"meta": {"x": 1}, "allow_session_done": true, "assistant_replacement": "", "feedback": ""}'
    v = extract_json_verdict(raw)
    assert v is not None
    assert v["allow_session_done"] is True
    assert v["meta"] == {"x": 1}


def test_invalid_returns_none() -> None:
    assert extract_json_verdict("not json at all") is None
