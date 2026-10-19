"""Offline tests for the Stage B investigation loop."""
import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from perceptron.chat import ChatCompletionMessage, Choice, FunctionCall, ToolCall, Usage
from perceptron.errors import BadRequestError, RateLimitError

from pipeline import investigate
from pipeline.investigate import (
    UNCITED_ERROR,
    Budget,
    LoopState,
    build_user_message,
    cited_sources,
    complete,
    downgrade_uncited,
    execute_tool,
    final_report,
    load_stage_a,
    main,
    run_tool_loop,
    should_investigate,
    validate_report,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Completions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class FakeClient:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=Completions(script))


def tool_call(call_id, name, arguments):
    return ToolCall(id=call_id, function=FunctionCall(name=name, arguments=json.dumps(arguments)))


def completion(finish, content="", tool_calls=None, reasoning=None):
    message = ChatCompletionMessage(
        role="assistant",
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    choice = Choice(index=0, message=message, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice],
        usage=Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
    )


def blank_report(**overrides):
    report = {
        "status": "not_found",
        "cause": "unknown",
        "cause_detail": "Nothing specific was reported.",
        "replacement_land_use": "unknown",
        "plantation_name": "unknown",
        "company": "unknown",
        "event_period": "unknown",
        "confidence": "low",
        "evidence": [],
        "inferences": [],
        "source_ids": [],
        "caveats": [],
    }
    report.update(overrides)
    return report


def search_call(call_id, query):
    return {
        "id": call_id,
        "function": {"name": "web_search", "arguments": json.dumps({"query": query})},
    }


def test_should_investigate_fixtures():
    flagged_ok, flagged_reason = should_investigate(load_stage_a(FIXTURES / "flagged"))
    intact_ok, intact_reason = should_investigate(load_stage_a(FIXTURES / "intact"))
    assert flagged_ok is True
    assert flagged_reason == ""
    assert intact_ok is False
    assert intact_reason

    window_only = {
        "confidence": {
            "model": "perceptron-mk1.5",
            "mean_score": 0.1,
            "windows": [{"eligible": True, "score": 0.6}],
        }
    }
    assert should_investigate(window_only)[0] is True
    ineligible = {
        "confidence": {
            "model": "perceptron-mk1.5",
            "mean_score": 0.1,
            "windows": [{"eligible": False, "score": 0.9}],
        }
    }
    assert should_investigate(ineligible)[0] is False


def test_build_user_message_is_text():
    stage = load_stage_a(FIXTURES / "flagged")
    place = {
        "village": "Desa Hulu",
        "district": "Sandai",
        "regency": "Ketapang",
        "province": "West Kalimantan",
        "country": "Indonesia",
    }
    message = build_user_message(stage, place)
    assert message["role"] == "user"
    assert isinstance(message["content"], str)
    text = message["content"]
    assert "-0.502" in text
    assert "109.489" in text
    assert "Desa Hulu" in text
    assert "Ketapang" in text
    assert "2021-09-24" in text
    assert "2025-08-01" in text
    assert "0.78" in text
    assert "latitude -0.502" in text


def test_execute_tool_validation():
    state = LoopState()
    deadline = time.monotonic() + 30
    cases = [
        ({"id": "a", "function": {"name": "browse", "arguments": "{}"}}, "Unknown tool"),
        ({"id": "b", "function": {"name": "web_search", "arguments": "{"}}, "not valid JSON"),
        (
            {
                "id": "c",
                "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "palm oil kalimantan", "country": "indonesia"}),
                },
            },
            "Unexpected argument",
        ),
        ({"id": "d", "function": {"name": "web_search", "arguments": json.dumps({"query": "ab"})}}, "3-300"),
        (
            {
                "id": "e",
                "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "palm oil kalimantan", "start_date": "yesterday"}),
                },
            },
            "YYYY-MM-DD",
        ),
        ({"id": "f", "function": {"name": "read_page", "arguments": json.dumps({"result_id": "S9"})}}, "Unknown result id"),
    ]
    for call, needle in cases:
        message = execute_tool(call, state, deadline)
        assert message["role"] == "tool"
        assert message["tool_call_id"] == call["id"]
        payload = json.loads(message["content"])
        assert needle in payload["error"]


def test_result_ids_stay_stable(monkeypatch):
    def fake_search(query, **kwargs):
        if "first" in query:
            rows = [
                {"title": "A", "url": "https://a.example/1", "published": "2023-05-02", "excerpt": "a", "score": 1},
                {"title": "B", "url": "https://b.example/2", "published": "", "excerpt": "b", "score": 1},
            ]
        else:
            rows = [
                {"title": "B again", "url": "https://b.example/2", "published": "", "excerpt": "b2", "score": 1},
                {"title": "C", "url": "https://c.example/3", "published": "", "excerpt": "c", "score": 1},
            ]
        return {"results": rows, "credits": 1, "cached": False}

    monkeypatch.setattr(investigate.tavily_search, "search", fake_search)
    state = LoopState()
    deadline = time.monotonic() + 30
    execute_tool(search_call("1", "first palm kalimantan"), state, deadline)
    second = execute_tool(search_call("2", "second palm kalimantan"), state, deadline)
    ids = [row["id"] for row in json.loads(second["content"])["results"]]
    assert ids == ["S2", "S3"]
    assert list(state.results_by_id) == ["S1", "S2", "S3"]


def test_search_query_includes_coordinates(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured["query"] = query
        captured["country"] = kwargs.get("country")
        return {"results": [], "credits": 0, "cached": False}

    monkeypatch.setattr(investigate.tavily_search, "search", fake_search)
    state = LoopState(lat=-0.502, lon=109.489, country="indonesia")
    execute_tool(search_call("1", "kebun sawit ketapang"), state, time.monotonic() + 30)
    assert "kebun sawit ketapang" in captured["query"]
    assert "-0.50200" in captured["query"]
    assert "109.48900" in captured["query"]
    assert captured["country"] == "indonesia"


def test_loop_concludes_without_finalise(monkeypatch):
    def fake_search(query, **kwargs):
        return {
            "results": [{
                "title": "Palm",
                "url": "https://news.example/p",
                "published": "2024-01-02",
                "excerpt": "oil palm",
                "score": 0.8,
            }],
            "credits": 1,
            "cached": False,
        }

    monkeypatch.setattr(investigate.tavily_search, "search", fake_search)
    client = FakeClient([
        completion(
            "tool_calls",
            reasoning="look it up",
            tool_calls=[tool_call("c1", "web_search", {"query": "oil palm ketapang"})],
        ),
        completion("stop", content="S1 mentions oil palm.", reasoning="done"),
    ])
    messages = [{"role": "user", "content": "find it"}]
    run_tool_loop(client, messages, Budget(), time.monotonic() + 60)
    assert len(client.chat.completions.calls) == 2
    assert all(call.get("tool_choice") != "none" for call in client.chat.completions.calls)
    assistant = next(item for item in messages if item.get("reasoning_content") == "look it up")
    assert assistant["tool_calls"][0]["id"] == "c1"
    tool_messages = [item for item in messages if item.get("role") == "tool"]
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "S1" in tool_messages[0]["content"]


def test_parallel_calls_respect_budget(monkeypatch):
    searches = []

    def fake_search(query, **kwargs):
        searches.append(query)
        return {"results": [], "credits": 1, "cached": False}

    monkeypatch.setattr(investigate.tavily_search, "search", fake_search)
    calls = [
        tool_call("c1", "web_search", {"query": "one palm kalimantan"}),
        tool_call("c2", "web_search", {"query": "two palm kalimantan"}),
        tool_call("c3", "web_search", {"query": "three palm kalimantan"}),
    ]
    client = FakeClient([
        completion("tool_calls", tool_calls=calls),
        completion("stop", content="nothing specific"),
    ])
    messages = [{"role": "user", "content": "find it"}]
    run_tool_loop(client, messages, Budget(max_tool_calls=2), time.monotonic() + 60)
    assert len(searches) == 2
    assert len(client.chat.completions.calls) == 2
    budget_hits = [
        item for item in messages
        if item.get("role") == "tool" and "Tool-call budget reached" in item["content"]
    ]
    assert len(budget_hits) == 1
    assert budget_hits[0]["tool_call_id"] == "c3"


def test_rounds_exhausted_finalise_disables_tools(monkeypatch):
    monkeypatch.setattr(
        investigate.tavily_search,
        "search",
        lambda *args, **kwargs: {"results": [], "credits": 0, "cached": False},
    )
    client = FakeClient([
        completion("tool_calls", tool_calls=[tool_call("c1", "web_search", {"query": "palm oil ketapang"})]),
        completion("stop", content="No named plantation."),
    ])
    messages = [{"role": "user", "content": "find it"}]
    run_tool_loop(client, messages, Budget(max_rounds=1), time.monotonic() + 60)
    finalise = client.chat.completions.calls[-1]
    assert finalise["tool_choice"] == "none"
    assert "tools" in finalise
    assert any(item.get("role") == "user" and "search budget is used up" in item["content"] for item in messages)


def test_length_finish_does_not_run_tools(monkeypatch):
    def fail_search(*args, **kwargs):
        raise AssertionError("partial tool calls must not run")

    monkeypatch.setattr(investigate.tavily_search, "search", fail_search)
    client = FakeClient([
        completion(
            "length",
            tool_calls=[tool_call("c1", "web_search", {"query": "palm oil ketapang"})],
        ),
        completion("stop", content="Could not finish the search."),
    ])
    messages = [{"role": "user", "content": "find it"}]
    state = run_tool_loop(client, messages, Budget(), time.monotonic() + 60)
    assert state.calls_used == 0
    assert any("incomplete turn: length" in error for error in state.errors)
    assert client.chat.completions.calls[-1]["tool_choice"] == "none"


def test_final_report_uses_schema_without_tools():
    report = blank_report()
    client = FakeClient([completion("stop", content=json.dumps(report))])
    got, errors = final_report(
        client,
        [{"role": "user", "content": "context"}],
        LoopState(),
        Budget(),
        time.monotonic() + 60,
    )
    assert errors == []
    assert got["status"] == "not_found"
    sent = client.chat.completions.calls[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["name"] == "clearcut_investigation"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in sent
    assert "tool_choice" not in sent


def test_hallucinated_source_is_dropped_and_status_downgraded():
    state = LoopState()
    state.results_by_id["S1"] = {
        "title": "A real page",
        "url": "https://a.example/a",
        "published": "2023-05-02",
        "excerpt": "mentions a different district",
    }
    report = blank_report(
        status="identified",
        cause="oil_palm_plantation",
        plantation_name="Kebun Satu",
        company="PT Example",
        confidence="high",
        replacement_land_use="oil palm",
        source_ids=["S99"],
        evidence=[{"claim": "This plot is Kebun Satu.", "source_ids": ["S99"]}],
    )
    errors = validate_report(report, state)
    assert any("S99" in error for error in errors)
    assert UNCITED_ERROR in errors
    downgrade_uncited(report, errors)
    assert report["status"] == "partially_identified"
    assert report["confidence"] == "low"
    assert cited_sources(report, state) == []


def test_perceptron_retry(monkeypatch):
    monkeypatch.setattr(investigate.time, "sleep", lambda delay: None)
    retrying = FakeClient([
        RateLimitError("slow down", retry_after=1, status_code=429),
        completion("stop", content="ok"),
    ])
    complete(retrying, [], Budget(), time.monotonic() + 60)
    assert len(retrying.chat.completions.calls) == 2

    rejected = FakeClient([BadRequestError("bad request", status_code=400)])
    with pytest.raises(BadRequestError):
        complete(rejected, [], Budget(), time.monotonic() + 60)
    assert len(rejected.chat.completions.calls) == 1

    late = FakeClient([RateLimitError("slow down", retry_after=1, status_code=429)])
    with pytest.raises(RateLimitError):
        complete(late, [], Budget(), time.monotonic() + 10)
    assert len(late.chat.completions.calls) == 1


def test_missing_tavily_key_writes_error(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(investigate.tavily_search, "KEY_FILE", str(tmp_path / "missing-tavily-key.txt"))
    plot = tmp_path / "flagged"
    plot.mkdir()
    shutil.copy(FIXTURES / "flagged" / "evidence.json", plot / "evidence.json")
    shutil.copy(FIXTURES / "flagged" / "confidence.json", plot / "confidence.json")
    assert main([str(plot)]) == 0
    written = json.loads((plot / "investigation.json").read_text(encoding="utf-8"))
    assert written["status"] == "error"
    assert written["errors"] == ["TAVILY_API_KEY not set"]


def test_stage_b_failure_does_not_fail_the_request(capsys):
    import web.server as server

    def runner(step, **kwargs):
        if step == ["stage-b"]:
            return SimpleNamespace(returncode=1, stderr="lookup failed", stdout="")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    ok, detail = server.run_pipeline([["stage-a"], ["stage-b"]], {}, runner=runner)
    assert ok is True
    assert detail == ""
    assert "lookup failed" in capsys.readouterr().err

    def fail_first(step, **kwargs):
        return SimpleNamespace(returncode=1, stderr="satellite failed", stdout="")

    ok, detail = server.run_pipeline([["stage-a"], ["stage-b"]], {}, runner=fail_first)
    assert ok is False
    assert "satellite failed" in detail


@pytest.mark.live
def test_live_investigation(tmp_path):
    tavily_ready = bool(os.environ.get("TAVILY_API_KEY")) or Path.home().joinpath("Desktop", "tavily-key.txt").exists()
    perceptron_ready = bool(os.environ.get("PERCEPTRON_API_KEY")) or Path.home().joinpath("Desktop", "perceptron-key.txt").exists()
    if not tavily_ready or not perceptron_ready:
        pytest.skip("needs a Perceptron key and a Tavily key")
    plot = tmp_path / "live"
    plot.mkdir()
    shutil.copy(FIXTURES / "flagged" / "evidence.json", plot / "evidence.json")
    shutil.copy(FIXTURES / "flagged" / "confidence.json", plot / "confidence.json")
    document = investigate.run_investigation(str(plot), force=True)
    assert document["status"] in {"identified", "partially_identified", "not_found", "error"}
    if document.get("report"):
        validate_report(document["report"], LoopState(results_by_id={
            source["id"]: source for source in document.get("sources") or []
        }))
        known_urls = {source["url"] for source in document.get("sources") or []}
        for source in document.get("sources") or []:
            assert source["url"] in known_urls
            assert source["url"].startswith("http")
