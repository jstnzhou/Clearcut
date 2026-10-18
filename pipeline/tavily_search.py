"""Tavily search and extract, plus Nominatim reverse geocoding.

Uses requests so each call can take a deadline-derived timeout and so tests can
mock the HTTP response directly. Results are cached under data/cache/.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

TAVILY_URL = "https://api.tavily.com"
KEY_FILE = os.path.expanduser("~/Desktop/tavily-key.txt")
SNIPPET_CHARS = 600
PAGE_CHARS = 3000
SEARCH_TTL_S = 7 * 24 * 3600
EXTRACT_TTL_S = 30 * 24 * 3600
ROOT = Path(__file__).resolve().parents[1]

# Lowercase English names accepted by Tavily's country boost (topic "general" only).
TAVILY_COUNTRIES = frozenset({
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina", "armenia", "australia",
    "austria", "azerbaijan", "bahamas", "bahrain", "bangladesh", "barbados", "belarus", "belgium",
    "belize", "benin", "bhutan", "bolivia", "bosnia and herzegovina", "botswana", "brazil", "brunei",
    "bulgaria", "burkina faso", "burundi", "cambodia", "cameroon", "canada", "cape verde",
    "central african republic", "chad", "chile", "china", "colombia", "comoros", "congo", "costa rica",
    "croatia", "cuba", "cyprus", "czech republic", "denmark", "djibouti", "dominican republic",
    "ecuador", "egypt", "el salvador", "equatorial guinea", "eritrea", "estonia", "ethiopia", "fiji",
    "finland", "france", "gabon", "gambia", "georgia", "germany", "ghana", "greece", "guatemala",
    "guinea", "haiti", "honduras", "hungary", "iceland", "india", "indonesia", "iran", "iraq",
    "ireland", "israel", "italy", "jamaica", "japan", "jordan", "kazakhstan", "kenya", "kuwait",
    "kyrgyzstan", "latvia", "lebanon", "lesotho", "liberia", "libya", "liechtenstein", "lithuania",
    "luxembourg", "madagascar", "malawi", "malaysia", "maldives", "mali", "malta", "mauritania",
    "mauritius", "mexico", "moldova", "monaco", "mongolia", "montenegro", "morocco", "mozambique",
    "myanmar", "namibia", "nepal", "netherlands", "new zealand", "nicaragua", "niger", "nigeria",
    "north korea", "north macedonia", "norway", "oman", "pakistan", "panama", "papua new guinea",
    "paraguay", "peru", "philippines", "poland", "portugal", "qatar", "romania", "russia", "rwanda",
    "saudi arabia", "senegal", "serbia", "singapore", "slovakia", "slovenia", "somalia", "south africa",
    "south korea", "south sudan", "spain", "sri lanka", "sudan", "sweden", "switzerland", "syria",
    "taiwan", "tajikistan", "tanzania", "thailand", "togo", "trinidad and tobago", "tunisia", "turkey",
    "turkmenistan", "uganda", "ukraine", "united arab emirates", "united kingdom", "united states",
    "uruguay", "uzbekistan", "venezuela", "vietnam", "yemen", "zambia", "zimbabwe",
})

COUNTRY_ALIASES = {
    "united states of america": "united states",
    "usa": "united states",
    "us": "united states",
    "uk": "united kingdom",
    "great britain": "united kingdom",
    "russian federation": "russia",
    "republic of korea": "south korea",
    "czechia": "czech republic",
    "viet nam": "vietnam",
    "bolivia (plurinational state of)": "bolivia",
    "tanzania, united republic of": "tanzania",
    "iran (islamic republic of)": "iran",
    "venezuela (bolivarian republic of)": "venezuela",
    "laos": "laos",
}


class TavilyError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def tavily_key() -> str:
    """TAVILY_API_KEY if set, otherwise the first line of ~/Desktop/tavily-key.txt."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(KEY_FILE) as handle:
            key = handle.readline().strip()
    except OSError:
        key = ""
    if not key:
        raise TavilyError("TAVILY_API_KEY not set")
    return key


def country_code(name: str | None) -> str | None:
    """Return a Tavily country boost value, or None when the name is not in their list."""
    if not name:
        return None
    text = " ".join(name.lower().replace(",", " ").split())
    text = COUNTRY_ALIASES.get(text, text)
    if text in TAVILY_COUNTRIES:
        return text
    return None


def cache_root() -> Path:
    override = os.environ.get("CLEARCUT_CACHE_DIR", "").strip()
    return Path(override) if override else ROOT / "data" / "cache"


def _cache_path(kind: str, key: dict) -> Path:
    raw = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return cache_root() / kind / f"{digest}.json"


def cache_get(kind: str, key: dict, ttl_s: float | None):
    path = _cache_path(kind, key)
    if not path.exists():
        return None
    if ttl_s is not None:
        age = time.time() - path.stat().st_mtime
        if age > ttl_s:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(kind: str, key: dict, value: object) -> None:
    path = _cache_path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def normalize_date(value: str | None) -> str:
    """Turn an RFC-1123 or ISO timestamp into YYYY-MM-DD. Unparseable values become ''."""
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _retry_after(resp, cap: float) -> float:
    raw = resp.headers.get("Retry-After") if resp.headers else None
    try:
        delay = float(raw) if raw else 1.0
    except (TypeError, ValueError):
        delay = 1.0
    if delay < 0:
        delay = 0.0
    return min(delay, max(0.0, cap))


def _status_error(status: int) -> TavilyError:
    if status == 401:
        return TavilyError("Tavily key invalid", status=status, retryable=False)
    if status == 432:
        return TavilyError("Tavily plan credit limit reached", status=status, retryable=False)
    if status == 433:
        return TavilyError("Tavily pay-as-you-go limit exceeded", status=status, retryable=False)
    if status == 400:
        return TavilyError("Tavily rejected the request", status=status, retryable=False)
    if status == 422:
        return TavilyError("Tavily rejected the request", status=status, retryable=False)
    retryable = status == 429 or 500 <= status <= 504
    return TavilyError(f"Tavily request failed ({status})", status=status, retryable=retryable)


def _post(path: str, body: dict, timeout_s: float) -> dict:
    key = tavily_key()
    url = f"{TAVILY_URL}{path}"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    timeout_s = max(1.0, float(timeout_s))
    last_error = None
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout_s)
        except requests.Timeout as exc:
            last_error = TavilyError("Tavily request timed out", retryable=True)
            if attempt == 0:
                continue
            raise last_error from exc
        except requests.ConnectionError as exc:
            last_error = TavilyError("Tavily connection failed", retryable=True)
            if attempt == 0:
                continue
            raise last_error from exc
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise TavilyError("Tavily returned a non-JSON response", status=200, retryable=False) from exc
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            if resp.status_code == 429:
                time.sleep(_retry_after(resp, timeout_s))
            continue
        raise _status_error(resp.status_code)
    raise last_error or TavilyError("Tavily request failed", retryable=True)


def _credits(payload: dict) -> int:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0
    try:
        return int(usage.get("credits") or 0)
    except (TypeError, ValueError):
        return 0


def search(
    query: str,
    *,
    max_results: int = 5,
    topic: str = "general",
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    search_depth: str = "basic",
    timeout_s: float = 10.0,
) -> dict:
    """Search Tavily. Country is sent only for topic 'general'."""
    cache_key = {
        "query": query,
        "max_results": max_results,
        "topic": topic,
        "start_date": start_date,
        "end_date": end_date,
        "country": country if topic == "general" else None,
        "search_depth": search_depth,
    }
    cached = cache_get("tavily_search", cache_key, SEARCH_TTL_S)
    if isinstance(cached, dict) and "results" in cached:
        return {"results": cached["results"], "credits": 0, "cached": True}

    body = {
        "query": query,
        "search_depth": search_depth,
        "chunks_per_source": 3,
        "max_results": max_results,
        "topic": topic,
        "include_published_date": True,
        "filter_by_published_date": False,
        "include_answer": False,
        "include_raw_content": False,
        "include_usage": True,
    }
    if start_date:
        body["start_date"] = start_date
    if end_date:
        body["end_date"] = end_date
    if country and topic == "general":
        body["country"] = country

    payload = _post("/search", body, timeout_s)
    results = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        excerpt = item.get("content") or ""
        if not isinstance(excerpt, str):
            excerpt = str(excerpt)
        results.append({
            "title": item.get("title") or "",
            "url": item["url"],
            "published": normalize_date(item.get("published_date")),
            "excerpt": excerpt[:SNIPPET_CHARS],
            "score": item.get("score"),
        })
    out = {"results": results, "credits": _credits(payload), "cached": False}
    cache_put("tavily_search", cache_key, {"results": results, "credits": out["credits"]})
    return out


def extract(url: str, *, focus: str | None = None, timeout_s: float = 10.0) -> dict:
    """Extract text from one URL. Raises TavilyError when the URL fails or nothing comes back."""
    cache_key = {"url": url, "focus": focus or "", "extract_depth": "basic", "format": "text"}
    cached = cache_get("tavily_extract", cache_key, EXTRACT_TTL_S)
    if isinstance(cached, dict) and cached.get("text") is not None:
        return {"url": cached.get("url") or url, "text": cached["text"], "credits": 0, "cached": True}

    body = {
        "urls": [url],
        "extract_depth": "basic",
        "format": "text",
        "timeout": max(1, min(int(timeout_s), 60)),
        "include_usage": True,
    }
    if focus:
        body["query"] = focus
        body["chunks_per_source"] = 3

    payload = _post("/extract", body, timeout_s)
    failed = payload.get("failed_results") or []
    if any(isinstance(item, dict) and item.get("url") == url for item in failed):
        message = "Tavily could not read that page"
        for item in failed:
            if isinstance(item, dict) and item.get("url") == url and item.get("error"):
                message = str(item["error"])[:200]
                break
        raise TavilyError(message, retryable=False)
    results = payload.get("results") or []
    match = None
    for item in results:
        if isinstance(item, dict) and item.get("url") == url:
            match = item
            break
    if match is None and results and isinstance(results[0], dict):
        match = results[0]
    if not match:
        raise TavilyError("Tavily returned no page text", retryable=False)
    text = match.get("raw_content") or ""
    if not isinstance(text, str):
        text = str(text)
    out = {"url": match.get("url") or url, "text": text[:PAGE_CHARS], "credits": _credits(payload), "cached": False}
    cache_put("tavily_extract", cache_key, {"url": out["url"], "text": out["text"], "credits": out["credits"]})
    return out


def _blank_place() -> dict:
    return {
        "village": "",
        "district": "",
        "regency": "",
        "province": "",
        "country": "",
        "display_name": "",
    }


def _first(address: dict, *keys: str) -> str:
    for key in keys:
        value = address.get(key)
        if value:
            return str(value)
    return ""


def reverse_geocode(lat: float, lon: float, timeout_s: float = 8.0) -> dict:
    """Nominatim reverse geocode. Returns blank strings on any error and never raises."""
    key = {"lat": lat, "lon": lon, "zoom": 10}
    cached = cache_get("geocode", key, None)
    if isinstance(cached, dict):
        place = _blank_place()
        place.update({k: cached.get(k, "") for k in place})
        return place
    place = _blank_place()
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lon,
                "zoom": 10,
                "accept-language": "en",
            },
            headers={"User-Agent": "Clearcut/0.1 (hackathon)"},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
        address = payload.get("address") if isinstance(payload, dict) else None
        if not isinstance(address, dict):
            address = {}
        place = {
            "village": _first(address, "village", "hamlet", "town", "city", "municipality"),
            "district": _first(address, "city_district", "district", "suburb", "subdistrict"),
            "regency": _first(address, "county", "state_district"),
            "province": _first(address, "state", "region"),
            "country": _first(address, "country"),
            "display_name": str(payload.get("display_name") or "") if isinstance(payload, dict) else "",
        }
    except (requests.RequestException, ValueError, TypeError):
        return _blank_place()
    cache_put("geocode", key, place)
    return place
