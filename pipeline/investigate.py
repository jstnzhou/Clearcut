"""Stage B: look up what replaced the forest after Perceptron Mk1.5 flags a plantation.

Usage:
    python pipeline/investigate.py data/plots/<name> [--force]

Reads evidence.json and confidence.json. When the plot looks like a post-2020
plantation conversion, asks Perceptron Mk1.5 to search the web (Tavily) and
writes investigation.json. A failure here is recorded in that file and the
process still exits 0. Stage A is not modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import requests

from perceptron import Client
from perceptron.errors import (
    QuotaExceededError,
    SDKError,
    TimeoutError as PerceptronTimeout,
    TransportError,
)

if __package__:
    from . import tavily_search
    from .investigate_prompts import PROMPT_VERSION, REPORT_SCHEMA, SYSTEM_PROMPT, TOOLS
else:
    import tavily_search
    from investigate_prompts import PROMPT_VERSION, REPORT_SCHEMA, SYSTEM_PROMPT, TOOLS

MODEL = "perceptron-mk1.5"
KEY_FILE = os.path.expanduser("~/Desktop/perceptron-key.txt")
GATE_MEAN = 0.5
GATE_WINDOW = 0.6
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
FATAL_TAVILY = {401, 432, 433}
SEARCH_KEYS = {"query", "topic", "max_results", "start_date", "end_date"}
READ_KEYS = {"result_id", "focus"}
UNCITED_ERROR = "plantation_name or company is set without a cited source"


@dataclass
class Budget:
    max_rounds: int = 4
    max_tool_calls: int = 6
    max_parallel_tools: int = 2
    deadline_s: float = 90.0
    max_completion_tokens: int = 2048
    max_total_tokens: int = 60_000


@dataclass
class LoopState:
    results_by_id: dict = field(default_factory=dict)
    url_to_id: dict = field(default_factory=dict)
    queries: list = field(default_factory=list)
    rounds: int = 0
    calls_used: int = 0
    credits: int = 0
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    errors: list = field(default_factory=list)
    country: str = ""
    lat: float | None = None
    lon: float | None = None
    fatal_search_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


def api_key() -> str:
    if os.environ.get("PERCEPTRON_API_KEY"):
        return os.environ["PERCEPTRON_API_KEY"]
    try:
        with open(KEY_FILE) as f:
            return f.readline().strip()
    except OSError as exc:
        raise SDKError("PERCEPTRON_API_KEY not set") from exc


def load_stage_a(plot_dir: str) -> dict:
    plot = Path(plot_dir)
    evidence = json.loads((plot / "evidence.json").read_text(encoding="utf-8"))
    confidence = json.loads((plot / "confidence.json").read_text(encoding="utf-8"))
    loaded = {"evidence": evidence, "confidence": confidence, "plot_dir": str(plot)}
    for name in ("chip_stats", "verdict"):
        path = plot / f"{name}.json"
        if path.exists():
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def should_investigate(stage_a: dict) -> tuple[bool, str]:
    """Run Stage B when Perceptron reports a likely plantation conversion."""
    confidence = stage_a.get("confidence") or {}
    if confidence.get("maps_only") or not confidence.get("model"):
        return False, "Perceptron did not score this plot for plantation conversion."
    mean = confidence.get("mean_score")
    if isinstance(mean, (int, float)) and not isinstance(mean, bool) and mean >= GATE_MEAN:
        return True, ""
    for window in confidence.get("windows") or []:
        if not isinstance(window, dict) or not window.get("eligible"):
            continue
        score = window.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= GATE_WINDOW:
            return True, ""
    return False, "Perceptron did not flag this plot as a plantation conversion."


def _window_center(window: dict):
    polygon = window.get("polygon") or []
    points = list(polygon)
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if not points:
        return None
    try:
        lon = sum(float(point[0]) for point in points) / len(points)
        lat = sum(float(point[1]) for point in points) / len(points)
    except (TypeError, ValueError, IndexError):
        return None
    return lat, lon


def _average(values) -> str:
    if not values:
        return "n/a"
    return str(round(sum(values) / len(values), 3))


def build_user_message(stage_a: dict, place: dict) -> dict:
    evidence = stage_a.get("evidence") or {}
    confidence = stage_a.get("confidence") or {}
    lat = evidence.get("lat")
    lon = evidence.get("lon")
    bbox = evidence.get("bbox")
    before = evidence.get("before") or {}
    after = evidence.get("after") or {}
    scenes = evidence.get("scenes") or []
    before_date = before.get("date") or (scenes[0].get("date") if scenes else "")
    after_date = after.get("date") or (scenes[-1].get("date") if scenes else "")
    windows = confidence.get("windows") or []
    hot = []
    for window in windows:
        score = window.get("score") if isinstance(window, dict) else None
        if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= GATE_WINDOW:
            hot.append(window)
    centers = [center for center in (_window_center(window) for window in hot) if center]
    if centers:
        centroid = (
            round(sum(item[0] for item in centers) / len(centers), 5),
            round(sum(item[1] for item in centers) / len(centers), 5),
        )
    else:
        centroid = (lat, lon)
    loss_values = [
        window["loss_share"]
        for window in windows
        if isinstance(window, dict) and window.get("eligible") and isinstance(window.get("loss_share"), (int, float))
    ]
    forest_values = [
        window["forest_share"]
        for window in windows
        if isinstance(window, dict) and window.get("eligible") and isinstance(window.get("forest_share"), (int, float))
    ]
    plot_name = Path(stage_a.get("plot_dir") or "plot").name
    place = place or {}
    place_text = ", ".join(str(place.get(part) or "") for part in ("village", "district", "regency", "province", "country"))
    before_year = str(before_date)[:4] if before_date else "2020"
    lines = [
        f"Plot folder: data/plots/{plot_name}",
        f"Centre: lat {lat}, lon {lon}. Bounding box [minLon,minLat,maxLon,maxLat]: {bbox}",
        f"Place (reverse geocode, may be imprecise): {place_text}",
        f"Imagery: one Sentinel-2 scene per year from {before_date} to {after_date}",
        (
            "Stage A (Perceptron Mk1.5 on yearly tiles): "
            f"loss-weighted mean conversion confidence {confidence.get('mean_score')};"
        ),
        (
            f"{len(hot)} of {len(windows)} tiles scored >= 0.6 "
            f"(centroid of those tiles: lat {centroid[0]}, lon {centroid[1]})"
        ),
        (
            "Maps: Hansen post-2020 tree-cover loss in eligible tiles averages "
            f"{_average(loss_values)}; JRC 2020 forest share {_average(forest_values)}"
        ),
    ]
    loss_years = (stage_a.get("verdict") or {}).get("loss_years")
    if loss_years:
        lines.append("Loss years: " + ", ".join(str(year) for year in loss_years))
    lines.append(f"Search window for news: {before_year}-01-01 to {date.today().isoformat()}")
    lines.append("Task: find public reporting that names who cleared this land or what it became.")
    lines.append(
        f"Include these coordinates in every web_search query: latitude {lat}, longitude {lon}."
    )
    return {"role": "user", "content": "\n".join(lines)}


def _tool_message(call_id: str, payload: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _query_with_coordinates(query: str, state: LoopState) -> str:
    if state.lat is None or state.lon is None:
        return query
    lat_s = f"{float(state.lat):.5f}"
    lon_s = f"{float(state.lon):.5f}"
    short_lat = f"{float(state.lat):.3f}"
    short_lon = f"{float(state.lon):.3f}"
    if (lat_s in query and lon_s in query) or (short_lat in query and short_lon in query):
        return query
    suffix = f" near {lat_s}, {lon_s}"
    if len(query) + len(suffix) > 1500:
        return query
    return query + suffix


def _assign_results(state: LoopState, query: str, rows: list[dict]) -> list[dict]:
    assigned = []
    with state.lock:
        for row in rows:
            url = row.get("url") or ""
            if not url:
                continue
            if url in state.url_to_id:
                result_id = state.url_to_id[url]
            else:
                result_id = f"S{len(state.url_to_id) + 1}"
                state.url_to_id[url] = result_id
                state.results_by_id[result_id] = {
                    "title": row.get("title") or "",
                    "url": url,
                    "published": row.get("published") or "",
                    "excerpt": row.get("excerpt") or "",
                    "query": query,
                }
            stored = state.results_by_id[result_id]
            assigned.append({
                "id": result_id,
                "title": stored.get("title") or "",
                "url": url,
                "published": stored.get("published") or "",
                "excerpt": stored.get("excerpt") or "",
            })
    return assigned


def _remember_error(state: LoopState, message: str) -> None:
    with state.lock:
        if message not in state.errors:
            state.errors.append(message)


def _web_search(args: dict, state: LoopState, remaining: float) -> dict:
    if state.fatal_search_error:
        return {"error": state.fatal_search_error}
    query = args.get("query")
    if not isinstance(query, str):
        return {"error": "Query must be a string of 3-300 characters."}
    query = query.strip()
    if not 3 <= len(query) <= 300:
        return {"error": "Query must be 3-300 characters."}
    topic = args.get("topic", "general")
    if topic not in ("general", "news"):
        return {"error": "topic must be general or news."}
    max_results = args.get("max_results", 5)
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 8:
        return {"error": "max_results must be an integer from 1 to 8."}
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    for label, value in (("start_date", start_date), ("end_date", end_date)):
        if value is None:
            continue
        if not isinstance(value, str) or not DATE_RE.match(value):
            return {"error": "Dates must be YYYY-MM-DD."}
    query = _query_with_coordinates(query, state)
    country = state.country if topic == "general" and state.country else None
    timeout_s = min(10.0, remaining - 5)
    if timeout_s <= 0:
        return {"error": "Out of time; not run."}
    try:
        found = tavily_search.search(
            query,
            max_results=max_results,
            topic=topic,
            start_date=start_date,
            end_date=end_date,
            country=country,
            search_depth="basic",
            timeout_s=timeout_s,
        )
    except tavily_search.TavilyError as exc:
        if exc.status in FATAL_TAVILY:
            state.fatal_search_error = str(exc)
            _remember_error(state, str(exc))
        return {"error": str(exc)}
    except requests.RequestException:
        return {"error": "Search timed out; no results were retrieved."}
    with state.lock:
        state.credits += int(found.get("credits") or 0)
        state.queries.append(query)
    rows = _assign_results(state, query, found.get("results") or [])
    if not rows:
        return {"results": [], "note": "No results for this query."}
    return {"results": rows, "note": ""}


def _read_page(args: dict, state: LoopState, remaining: float) -> dict:
    result_id = args.get("result_id")
    if not isinstance(result_id, str) or not result_id:
        return {"error": "result_id must be a search result id such as S3."}
    focus = args.get("focus")
    if focus is not None and not isinstance(focus, str):
        return {"error": "focus must be a string."}
    with state.lock:
        known = state.results_by_id.get(result_id)
    if not known:
        return {"error": f"Unknown result id {result_id}."}
    timeout_s = min(15.0, remaining - 5)
    if timeout_s <= 0:
        return {"error": "Out of time; not run."}
    try:
        page = tavily_search.extract(known["url"], focus=focus, timeout_s=timeout_s)
    except tavily_search.TavilyError as exc:
        if exc.status in FATAL_TAVILY:
            state.fatal_search_error = str(exc)
            _remember_error(state, str(exc))
        return {"error": str(exc)}
    except requests.RequestException:
        return {"error": "Page read timed out."}
    with state.lock:
        state.credits += int(page.get("credits") or 0)
    return {"id": result_id, "url": page.get("url") or known["url"], "text": page.get("text") or ""}


def execute_tool(call: dict, state: LoopState, deadline: float) -> dict:
    """Run one tool call. Always returns a role:tool message and never raises."""
    call_id = str(call.get("id") or "")
    try:
        function = call.get("function") or {}
        name = function.get("name")
        if name not in ("web_search", "read_page"):
            return _tool_message(call_id, {"error": "Unknown tool. Use web_search or read_page."})
        raw = function.get("arguments")
        if not isinstance(raw, str):
            return _tool_message(call_id, {"error": "Arguments are not valid JSON."})
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            return _tool_message(call_id, {"error": "Arguments are not valid JSON."})
        if not isinstance(args, dict):
            return _tool_message(call_id, {"error": "Arguments must be a JSON object."})
        allowed = SEARCH_KEYS if name == "web_search" else READ_KEYS
        extra = sorted(set(args) - allowed)
        if extra:
            return _tool_message(call_id, {"error": f"Unexpected argument: {extra[0]}."})
        remaining = deadline - time.monotonic()
        if remaining < 8:
            return _tool_message(call_id, {"error": "Out of time; not run."})
        payload = _web_search(args, state, remaining) if name == "web_search" else _read_page(args, state, remaining)
        return _tool_message(call_id, payload)
    except Exception as exc:  # a tool bug must not kill the loop
        return _tool_message(call_id, {"error": f"Tool failed: {type(exc).__name__}."})


def _run_concurrently(calls: list, state: LoopState, deadline: float, workers: int) -> list:
    if not calls:
        return []
    if len(calls) == 1 or workers <= 1:
        return [execute_tool(call, state, deadline) for call in calls]
    with ThreadPoolExecutor(max_workers=min(workers, len(calls))) as pool:
        return list(pool.map(lambda call: execute_tool(call, state, deadline), calls))


def _add_usage(state: LoopState, response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        total = usage.get("total_tokens") or 0
    else:
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        total = getattr(usage, "total_tokens", 0) or 0
    state.usage["prompt_tokens"] += int(prompt)
    state.usage["completion_tokens"] += int(completion)
    state.usage["total_tokens"] += int(total)


def _retryable_sdk(err: Exception) -> bool:
    if isinstance(err, QuotaExceededError):
        return False
    status = getattr(err, "status_code", None)
    if status in {400, 401, 403}:
        return False
    if status in RETRY_STATUS:
        return True
    return isinstance(err, (TransportError, PerceptronTimeout, ConnectionError, TimeoutError))


def complete(client, messages: list, budget: Budget, deadline: float, **kwargs):
    """One Perceptron request, with retries for rate limits and transient server errors."""
    attempts = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise PerceptronTimeout("Investigation deadline reached")
        attempts += 1
        request = dict(kwargs)
        request.setdefault("max_completion_tokens", budget.max_completion_tokens)
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                n=1,
                timeout=max(1.0, min(60.0, remaining)),
                **request,
            )
        except QuotaExceededError:
            raise
        except SDKError as err:
            if not _retryable_sdk(err) or attempts >= 3:
                raise
            delay = err.retry_after if getattr(err, "retry_after", None) is not None else min(2**attempts + random.random(), 10)
            if delay > (deadline - time.monotonic()) - 20:
                raise
            time.sleep(delay)


def _message_dict(message) -> dict:
    if hasattr(message, "to_dict"):
        return message.to_dict()
    if isinstance(message, dict):
        return message
    return {"role": "assistant", "content": getattr(message, "content", "") or ""}


def run_tool_loop(client, messages: list, budget: Budget, deadline: float, *, lat=None, lon=None, country: str = "") -> LoopState:
    state = LoopState(lat=lat, lon=lon, country=country or "")
    concluded = False
    while (
        state.rounds < budget.max_rounds
        and deadline - time.monotonic() > 20
        and state.usage["total_tokens"] < budget.max_total_tokens
    ):
        response = complete(
            client,
            messages,
            budget,
            deadline,
            tools=TOOLS,
            tool_choice="auto",
            parallel_tool_calls=True,
            reasoning_effort="high",
            max_completion_tokens=budget.max_completion_tokens,
        )
        state.rounds += 1
        _add_usage(state, response)
        choice = response.choices[0]
        message = _message_dict(choice.message)
        calls = message.get("tool_calls") or []
        if choice.finish_reason == "stop" and not calls:
            messages.append(message)
            concluded = True
            break
        if choice.finish_reason != "tool_calls" or not calls:
            state.errors.append(f"incomplete turn: {choice.finish_reason}")
            break
        messages.append(message)
        room = max(0, budget.max_tool_calls - state.calls_used)
        allowed = calls[:room]
        results = _run_concurrently(allowed, state, deadline, budget.max_parallel_tools)
        results += [
            _tool_message(
                call.get("id") or "",
                {"error": "Tool-call budget reached; this call was not run. Use results you already have."},
            )
            for call in calls[len(allowed):]
        ]
        messages.extend(results)
        state.calls_used += len(allowed)
    else:
        concluded = False
    if concluded:
        return state
    messages.append({
        "role": "user",
        "content": "The search budget is used up. Do not call tools. Summarise what the results show, citing result ids.",
    })
    response = complete(
        client,
        messages,
        budget,
        deadline,
        tools=TOOLS,
        tool_choice="none",
        reasoning_effort="low",
        max_completion_tokens=budget.max_completion_tokens,
    )
    _add_usage(state, response)
    message = _message_dict(response.choices[0].message)
    if message.get("tool_calls"):
        state.errors.append("model requested tools after budget")
        message = {"role": "assistant", "content": "Budget reached."}
    messages.append(message)
    return state


def _cited_id_list(report: dict) -> list[str]:
    found = []
    for source_id in report.get("source_ids") or []:
        if isinstance(source_id, str):
            found.append(source_id)
    for item in report.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        for source_id in item.get("source_ids") or []:
            if isinstance(source_id, str):
                found.append(source_id)
    return found


def validate_report(report: dict, state: LoopState) -> list[str]:
    import jsonschema

    errors = []
    try:
        jsonschema.validate(report, REPORT_SCHEMA)
    except jsonschema.ValidationError as exc:
        errors.append(exc.message)
    if not isinstance(report, dict):
        return errors or ["Report was not an object."]
    known = set(state.results_by_id)
    for source_id in _cited_id_list(report):
        if source_id not in known:
            errors.append(f"Unknown source id: {source_id}")
    names_set = False
    for field_name in ("plantation_name", "company"):
        value = report.get(field_name)
        if isinstance(value, str) and value.strip().lower() not in ("", "unknown"):
            names_set = True
    cited = False
    for item in report.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        ids = [source_id for source_id in (item.get("source_ids") or []) if isinstance(source_id, str)]
        if ids and all(source_id in known for source_id in ids):
            cited = True
            break
    if names_set and not cited:
        errors.append(UNCITED_ERROR)
    return errors


def cited_sources(report: dict, state: LoopState) -> list[dict]:
    if not isinstance(report, dict):
        return []
    ordered = []
    for source_id in _cited_id_list(report):
        if source_id in state.results_by_id and source_id not in ordered:
            ordered.append(source_id)

    def sort_key(source_id: str):
        suffix = source_id[1:]
        return int(suffix) if source_id.startswith("S") and suffix.isdigit() else 10**9

    ordered.sort(key=sort_key)
    sources = []
    for source_id in ordered:
        row = state.results_by_id[source_id]
        sources.append({
            "id": source_id,
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "published": row.get("published") or "",
        })
    return sources


def downgrade_uncited(report: dict | None, errors: list[str]) -> dict | None:
    if not isinstance(report, dict):
        return report
    if any(UNCITED_ERROR in error for error in errors):
        report["status"] = "partially_identified"
        report["confidence"] = "low"
    return report


def final_report(client, messages: list, state: LoopState, budget: Budget, deadline: float):
    history = list(messages)
    history.append({
        "role": "user",
        "content": "Write the final report as JSON per the schema, using only the results above. Use 'unknown' where not reported.",
    })
    report = None
    errors: list[str] = []
    for attempt in range(2):
        response = complete(
            client,
            history,
            budget,
            deadline,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "clearcut_investigation", "strict": True, "schema": REPORT_SCHEMA},
            },
            reasoning_effort="low",
            max_completion_tokens=2048,
        )
        _add_usage(state, response)
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            errors = [f"report finish_reason {choice.finish_reason}"]
            history.append({"role": "user", "content": "The report did not finish. Return only the JSON report. " + errors[0]})
            continue
        content = choice.message.content if hasattr(choice.message, "content") else choice.message.get("content")
        try:
            report = json.loads(content or "")
        except (json.JSONDecodeError, TypeError) as exc:
            errors = [f"invalid JSON: {exc}"]
            history.append({"role": "user", "content": "The report was not valid JSON. " + errors[0]})
            continue
        errors = validate_report(report, state)
        if not errors:
            return report, []
        if attempt == 0:
            history.append({
                "role": "user",
                "content": "Fix these validation errors and return the report again:\n" + "\n".join(errors),
            })
    return report, errors


def _input_hash(plot: Path) -> str:
    digest = hashlib.sha256()
    digest.update((plot / "evidence.json").read_bytes())
    digest.update((plot / "confidence.json").read_bytes())
    digest.update(PROMPT_VERSION.encode("utf-8"))
    return digest.hexdigest()


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _envelope(
    *,
    status: str,
    input_hash: str,
    t0: float,
    place: dict | None = None,
    report: dict | None = None,
    sources: list | None = None,
    queries: list | None = None,
    rounds: int = 0,
    tool_calls: int = 0,
    credits: int = 0,
    usage: dict | None = None,
    errors: list | None = None,
    validation_errors: list | None = None,
    reason: str = "",
) -> dict:
    document = {
        "status": status,
        "input_hash": input_hash,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "place": place or {},
        "report": report,
        "sources": sources or [],
        "queries": queries or [],
        "rounds": rounds,
        "tool_calls": tool_calls,
        "tavily_credits": credits,
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "elapsed_s": round(time.monotonic() - t0, 2),
        "errors": errors or [],
        "validation_errors": validation_errors or [],
        "created_at": _stamp(),
    }
    if reason:
        document["reason"] = reason
    return document


def run_investigation(plot_dir: str, *, budget: Budget | None = None, client=None, force: bool = False) -> dict:
    budget = budget or Budget()
    t0 = time.monotonic()
    deadline = t0 + budget.deadline_s
    plot = Path(plot_dir)
    stage_a = load_stage_a(plot_dir)
    input_hash = _input_hash(plot)
    destination = plot / "investigation.json"
    if not force and destination.exists():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, dict) and previous.get("input_hash") == input_hash and previous.get("status") != "error":
            return previous

    ok, reason = should_investigate(stage_a)
    if not ok:
        document = _envelope(status="not_applicable", input_hash=input_hash, t0=t0, reason=reason)
        _write_json(destination, document)
        return document
    try:
        tavily_search.tavily_key()
    except tavily_search.TavilyError as exc:
        document = _envelope(
            status="error",
            input_hash=input_hash,
            t0=t0,
            errors=[str(exc)],
        )
        _write_json(destination, document)
        return document

    evidence = stage_a.get("evidence") or {}
    lat = evidence.get("lat")
    lon = evidence.get("lon")
    try:
        place = tavily_search.reverse_geocode(float(lat), float(lon), timeout_s=min(8.0, max(1.0, deadline - time.monotonic())))
    except (TypeError, ValueError):
        place = tavily_search._blank_place()
    country = tavily_search.country_code(place.get("country")) or ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        build_user_message(stage_a, place),
    ]
    try:
        client = client or Client(api_key=api_key(), timeout=60, retries=0)
        state = run_tool_loop(client, messages, budget, deadline, lat=lat, lon=lon, country=country)
        report, validation_errors = final_report(client, messages, state, budget, deadline)
    except SDKError as exc:
        document = _envelope(
            status="error",
            input_hash=input_hash,
            t0=t0,
            place=place,
            errors=[str(exc)[:300] or "Perceptron request failed"],
        )
        _write_json(destination, document)
        return document

    report = downgrade_uncited(report, validation_errors)
    if isinstance(report, dict) and report.get("status"):
        status = report["status"]
    else:
        status = "error"
    document = _envelope(
        status=status,
        input_hash=input_hash,
        t0=t0,
        place=place,
        report=report,
        sources=cited_sources(report, state) if report else [],
        queries=state.queries,
        rounds=state.rounds,
        tool_calls=state.calls_used,
        credits=state.credits,
        usage=state.usage,
        errors=state.errors,
        validation_errors=validation_errors,
    )
    _write_json(destination, document)
    return document


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    args = [arg for arg in argv if arg != "--force"]
    if len(args) != 1:
        print("Usage: python pipeline/investigate.py <plot_dir> [--force]", file=sys.stderr)
        return 2
    try:
        run_investigation(args[0], force=force)
    except Exception as exc:
        print(f"investigation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
