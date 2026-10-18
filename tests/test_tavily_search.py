"""Offline tests for Tavily search, extract normalisation, and the disk cache."""
import json

import pytest

from pipeline import tavily_search
from pipeline.tavily_search import TavilyError, normalize_date, search, tavily_key


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _install_post(monkeypatch, responses):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        responder = responses[len(calls) - 1] if len(calls) <= len(responses) else responses[-1]
        return responder

    monkeypatch.setattr(tavily_search.requests, "post", fake_post)
    monkeypatch.setattr(tavily_search.time, "sleep", lambda delay: None)
    return calls


def test_tavily_key_reads_desktop_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    key_file = tmp_path / "tavily-key.txt"
    key_file.write_text("tvly-from-file\nignored\n", encoding="utf-8")
    monkeypatch.setattr(tavily_search, "KEY_FILE", str(key_file))
    assert tavily_key() == "tvly-from-file"

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-from-env")
    assert tavily_key() == "tvly-from-env"


def test_search_request_shape(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    calls = _install_post(monkeypatch, [FakeResponse(200, {"results": [], "usage": {"credits": 1}})])
    search("palm oil concession Ketapang", country="indonesia", topic="general")
    sent = calls[0]
    assert sent["url"] == "https://api.tavily.com/search"
    assert sent["headers"]["Authorization"] == "Bearer tvly-test"
    body = sent["json"]
    assert body["include_published_date"] is True
    assert body["include_usage"] is True
    assert body["include_answer"] is False
    assert body["include_raw_content"] is False
    assert body["country"] == "indonesia"

    news_calls = _install_post(
        monkeypatch,
        [FakeResponse(200, {"results": [], "usage": {"credits": 1}})],
    )
    search("palm oil news Ketapang", country="indonesia", topic="news")
    assert "country" not in news_calls[0]["json"]


def test_result_normalisation(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    long_excerpt = "x" * 800
    payload = {
        "results": [
            {
                "title": "Clearing",
                "url": "https://news.example/story",
                "content": long_excerpt,
                "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
                "score": 0.8,
            },
            {
                "title": "Undated",
                "url": "https://news.example/undated",
                "content": "short",
                "published_date": None,
                "score": 0.2,
            },
        ],
        "usage": {"credits": 1},
    }
    _install_post(monkeypatch, [FakeResponse(200, payload)])
    found = search("dated and undated clearing")
    assert found["results"][0]["published"] == "2025-03-11"
    assert found["results"][1]["published"] == ""
    assert len(found["results"][0]["excerpt"]) <= 600
    assert normalize_date(None) == ""
    assert normalize_date("not a date") == ""


@pytest.mark.parametrize("status", [401, 432, 433])
def test_fatal_tavily_status_is_not_retried(monkeypatch, status):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    calls = _install_post(monkeypatch, [FakeResponse(status, {"error": "no"})])
    with pytest.raises(TavilyError) as caught:
        search(f"fatal status {status} query")
    assert caught.value.retryable is False
    assert caught.value.status == status
    assert len(calls) == 1


def test_rate_limit_retries_once(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    calls = _install_post(
        monkeypatch,
        [
            FakeResponse(429, {"error": "slow"}, {"Retry-After": "1"}),
            FakeResponse(200, {"results": [], "usage": {"credits": 1}}),
        ],
    )
    found = search("retry after rate limit")
    assert len(calls) == 2
    assert found["cached"] is False
    assert found["credits"] == 1


def test_search_cache_skips_second_http_call(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    calls = _install_post(
        monkeypatch,
        [FakeResponse(200, {"results": [{"title": "A", "url": "https://a.example", "content": "palm", "score": 1}], "usage": {"credits": 1}})],
    )
    first = search("cached plantation query")
    second = search("cached plantation query")
    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["credits"] == 0
    assert second["results"][0]["url"] == "https://a.example"
