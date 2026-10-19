import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_collection_modifyitems(config, items):
    markexpr = getattr(config.option, "markexpr", "") or ""
    if "live" in markexpr and "not live" not in markexpr:
        return
    skip = pytest.mark.skip(reason="pass -m live to call Perceptron and Tavily")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARCUT_CACHE_DIR", str(tmp_path / "cache"))
