import os
from pathlib import Path

import pytest

from web_app import load_env
from highlight_clipper.gemini_analyzer import analyze_video

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / "env"
TEST_URL = "https://www.youtube.com/shorts/MOgT6UtcCCQ"


def _supplier_configs():
    env = load_env(ENV_PATH)
    return [
        {
            "name": "GRSAI / gemini-3.1-pro",
            "api_key": env.get("GRSAI_OPENAI_API_KEY", ""),
            "api_base": env.get("GRSAI_OPENAI_API_BASE", ""),
            "model": "gemini-3.1-pro",
        },
        {
            "name": "GRSAI / gemini-3.5-flash",
            "api_key": env.get("GRSAI_OPENAI_API_KEY", ""),
            "api_base": env.get("GRSAI_OPENAI_API_BASE", ""),
            "model": "gemini-3.5-flash",
        },
        {
            "name": "ZHENZHEN / gemini-2.5-pro",
            "api_key": env.get("ZHENZHEN_OPENAI_API_KEY", ""),
            "api_base": env.get("ZHENZHEN_OPENAI_API_BASE", ""),
            "model": "gemini-2.5-pro",
        },
        {
            "name": "ZHENZHEN / gemini-2.5-flash",
            "api_key": env.get("ZHENZHEN_OPENAI_API_KEY", ""),
            "api_base": env.get("ZHENZHEN_OPENAI_API_BASE", ""),
            "model": "gemini-2.5-flash",
        },
    ]


@pytest.mark.integration
@pytest.mark.parametrize("config", _supplier_configs(), ids=lambda c: c["name"])
def test_gemini_analyze_with_supplier(config) -> None:
    if os.environ.get("RUN_GEMINI_ANALYZE_TEST") != "1":
        pytest.skip("set RUN_GEMINI_ANALYZE_TEST=1")

    if not config["api_key"]:
        pytest.skip(f"no API key for {config['name']}")

    logs: list[str] = []

    try:
        highlights = analyze_video(
            TEST_URL,
            api_key=config["api_key"],
            base_url=config["api_base"],
            model=config["model"],
            log_callback=logs.append,
        )
    except Exception as e:
        msg = str(e)
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            pytest.skip(f"{config['name']}: API timeout (skip)")
        raise

    assert isinstance(highlights, list), f"Expected list, got {type(highlights)}"
    assert len(highlights) > 0, f"{config['name']}: no highlights returned"

    first = highlights[0]
    assert isinstance(first, dict)
    for key in ("start", "end", "score", "desc"):
        assert key in first, f"missing key '{key}'"
    assert first["start"] >= 0
    assert first["end"] > first["start"]
    assert 0 <= first["score"] <= 100
    assert len(first["desc"]) > 0

    print(f"\n{config['name']} -> {len(highlights)} highlights")
    for i, h in enumerate(highlights):
        print(f"  {i+1}. [{h['start']}s-{h['end']}s] score={h['score']} {h['desc']}")
