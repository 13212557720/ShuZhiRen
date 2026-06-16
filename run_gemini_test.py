import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from web_app import load_env
from highlight_clipper.gemini_analyzer import analyze_video

PROJECT_DIR = Path(__file__).parent
ENV_PATH = PROJECT_DIR / "env"
OUT_PATH = PROJECT_DIR / "gemini_output.txt"
YOUTUBE_SHORTS_URL = "https://www.youtube.com/shorts/MOgT6UtcCCQ"

env = load_env(ENV_PATH)
api_key = env.get("GRSAI_OPENAI_API_KEY") or ""
api_base = env.get("GRSAI_OPENAI_API_BASE") or ""
model = "gemini-3.1-pro"

lines = []
lines.append(f"model={model}")
lines.append(f"api_base={api_base}")
lines.append("")

logs = []
highlights = analyze_video(
    YOUTUBE_SHORTS_URL,
    api_key=api_key,
    base_url=api_base,
    model=model,
    log_callback=logs.append,
)

lines.append("=== logs ===")
lines.extend(logs)
lines.append("")
lines.append(f"=== highlights ({len(highlights)}) ===")
for i, h in enumerate(highlights):
    lines.append(f'{i+1}. [{h["start"]}s - {h["end"]}s] score={h["score"]} {h["desc"]}')

OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
print("done")
