import os
from pathlib import Path

import pytest

from web_app import load_env
from highlight_clipper.gemini_analyzer import analyze_video


PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / "env"
YOUTUBE_SHORTS_URL = "https://www.youtube.com/shorts/4FbJv71E2KE"


def _gemini_config():
    env = load_env(ENV_PATH)
    api_key = env.get("GEMINI_API_KEY") or env.get("GRSAI_OPENAI_API_KEY") or ""
    api_base = env.get("GEMINI_API_BASE") or env.get("GRSAI_OPENAI_API_BASE") or ""
    model = env.get("GEMINI_MODEL") or env.get("NANO_BANANA2_MODEL") or "gemini-3.5-flash"
    return api_key, api_base, model


@pytest.mark.integration
def test_gemini_analyze_youtube_url_with_grsai_credentials() -> None:
    if os.environ.get("RUN_GEMINI_ANALYZE_TEST") != "1":
        pytest.skip("set RUN_GEMINI_ANALYZE_TEST=1 to test Gemini API")

    api_key, api_base, model = _gemini_config()
    if not api_key:
        pytest.skip("no GEMINI_API_KEY or GRSAI_OPENAI_API_KEY configured")

    logs: list[str] = []
    highlights = analyze_video(
        YOUTUBE_SHORTS_URL,
        api_key=api_key,
        base_url=api_base,
        model=model,
        log_callback=logs.append,
    )

    assert isinstance(highlights, list)
    assert len(highlights) > 0
    first = highlights[0]
    assert isinstance(first, dict)
    assert "start" in first
    assert "end" in first
    assert "score" in first
    assert "desc" in first
    assert first["start"] >= 0
    assert first["end"] > first["start"]
    assert 0 <= first["score"] <= 100
    assert len(first["desc"]) > 0

    print("\n".join(logs))
