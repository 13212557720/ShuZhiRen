# -*- coding: utf-8 -*-
"""Local web UI for YouTube downloading and Gemini highlight clipping."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import yt_dlp
from yt_dlp.version import __version__ as YT_DLP_VERSION
from flask import Flask, jsonify, request

from highlight_clipper.pipeline import Pipeline


APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / "env"
DEFAULT_DOWNLOADS = APP_DIR / "downloads"
DEFAULT_OUTPUT = APP_DIR / "output"
RUNTIME_DIR = APP_DIR / ".runtime"
DEFAULT_COOKIES_FILE = APP_DIR / "youtube_cookies.txt"
CODEX_NODE_PATH = Path("/Applications/Codex.app/Contents/Resources/node")
JOBS: dict[str, "Job"] = {}
QUALITY_HEIGHTS = {
    "4k": 2160,
    "2k": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
    "240p": 240,
}
AUTO_QUALITY_ORDER = ("720p", "1080p", "480p", "360p", "240p")


def load_env(path: Path) -> dict[str, str]:
    config = dict(os.environ)
    if not path.exists():
        return config

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        config[key.strip()] = value
    return config


def env_value(env: dict[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def default_browser_cookies(env: dict[str, str]) -> str:
    browser = env_value(env, "YOUTUBE_BROWSER_COOKIES", "")
    if browser and browser != "none":
        return browser if browser in {"none", "chrome", "safari", "firefox", "edge", "brave", "chromium"} else "none"
    if Path("/Applications/Google Chrome.app").exists():
        return "chrome"
    if Path("/Applications/Safari.app").exists():
        return "safari"
    if shutil.which("firefox"):
        return "firefox"
    return "none"


def default_player_client(env: dict[str, str]) -> str:
    player_client = env_value(env, "YOUTUBE_PLAYER_CLIENT", "mweb")
    return player_client or "mweb"


def default_download_quality(env: dict[str, str]) -> str:
    quality = env_value(env, "YOUTUBE_DOWNLOAD_QUALITY", "auto")
    return quality if quality in {"auto", "best", *QUALITY_HEIGHTS.keys()} else "auto"


def default_sleep_interval(env: dict[str, str]) -> str:
    return env_value(env, "YOUTUBE_SLEEP_INTERVAL", "5")


def default_proxy(env: dict[str, str]) -> str:
    return env_value(env, "YOUTUBE_PROXY")


def default_cookies_file(env: dict[str, str]) -> str:
    configured = env_value(env, "YOUTUBE_COOKIES_FILE")
    if configured:
        return configured
    return str(DEFAULT_COOKIES_FILE) if DEFAULT_COOKIES_FILE.exists() else ""


def parse_urls(text: str) -> list[str]:
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def safe_filename(name: str, fallback: str = "video") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    return (cleaned or fallback)[:90]


def unique_folder(parent: Path, base_name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    base_name = safe_filename(base_name)
    candidate = parent / base_name
    index = 2
    while candidate.exists():
        candidate = parent / f"{base_name}_{index}"
        index += 1
    return candidate


def quality_to_format(quality: str) -> str:
    height = QUALITY_HEIGHTS.get(quality)
    if not height:
        return "bv*+ba/b"
    return f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"


def format_sort(quality: str) -> list[str]:
    height = QUALITY_HEIGHTS.get(quality)
    if height:
        return [f"res:{height}", "ext:mp4:m4a"]
    return ["res", "ext:mp4:m4a"]


def downloadable_heights(info: dict) -> set[int]:
    heights: set[int] = set()
    for item in info.get("formats") or []:
        height = item.get("height")
        vcodec = item.get("vcodec")
        if isinstance(height, int) and height > 0 and vcodec and vcodec != "none":
            heights.add(height)
    return heights


def choose_download_quality(info: dict, requested_quality: str | None = None) -> str:
    requested_quality = requested_quality or "auto"
    heights = downloadable_heights(info)
    requested_height = QUALITY_HEIGHTS.get(requested_quality)
    if requested_height and requested_height in heights:
        return requested_quality
    for quality in AUTO_QUALITY_ORDER:
        if QUALITY_HEIGHTS[quality] in heights:
            return quality
    return requested_quality if requested_quality != "auto" else "best"


def youtube_extractor_args(player_client: str | None) -> dict:
    if not player_client or player_client == "default":
        return {}
    return {"youtube": {"player_client": player_client.split(",")}}


def env_cookie_header(env: dict[str, str]) -> str:
    return (
        env.get("YOUTUBE_COOKIE_HEADER")
        or env.get("YOUTUBE_COOKIES")
        or env.get("YOUTUBE_COOKIES_DEFAULT")
        or ""
    ).strip()


def default_cookie_mode(env: dict[str, str]) -> str:
    configured = (env.get("YOUTUBE_COOKIE_MODE") or "").strip()
    if configured in {"none", "env", "browser", "file"}:
        return configured
    if env.get("YOUTUBE_COOKIES_FILE"):
        return "file"
    if env_cookie_header(env):
        return "env"
    return "none"


def default_js_runtime_path(env: dict[str, str] | None = None) -> str:
    env = env or {}
    configured = (env.get("YOUTUBE_JS_RUNTIME_PATH") or "").strip()
    if configured:
        return configured
    node_path = shutil.which("node")
    if node_path:
        return node_path
    if CODEX_NODE_PATH.exists():
        return str(CODEX_NODE_PATH)
    return ""


def apply_js_runtime_options(ydl_opts: dict, options: dict):
    runtime_path = (options.get("jsRuntimePath") or "").strip()
    if runtime_path:
        ydl_opts["js_runtimes"] = {"node": {"path": runtime_path}}


def cookie_header_to_netscape(cookie_header: str, domain: str = ".youtube.com") -> str:
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated from a local Cookie header.",
    ]
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if not name:
            continue
        secure = "TRUE" if name.startswith("__Secure-") else "FALSE"
        lines.append(f"{domain}\tTRUE\t/\t{secure}\t0\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def cookie_header_file(cookie_header: str) -> str:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    cookie_path = RUNTIME_DIR / "youtube_cookies.txt"
    cookie_path.write_text(cookie_header_to_netscape(cookie_header), encoding="utf-8")
    return str(cookie_path)


def apply_cookie_options(ydl_opts: dict, options: dict):
    mode = (options.get("cookieMode") or "file").strip()
    cookies = (options.get("cookies") or "").strip()
    browser = (options.get("browserCookies") or "none").strip()
    cookie_header = (options.get("cookieHeader") or "").strip()
    if mode == "env" and cookie_header:
        ydl_opts["cookiefile"] = cookie_header_file(cookie_header)
    elif mode == "file" and cookies:
        ydl_opts["cookiefile"] = cookies
    elif mode == "browser" and browser and browser != "none":
        ydl_opts["cookiesfrombrowser"] = (browser,)


def format_seconds(seconds):
    if not seconds:
        return "-"
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def missing_tools() -> list[str]:
    return [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]


class Job:
    def __init__(self, mode: str, urls: list[str], options: dict):
        self.id = uuid.uuid4().hex
        self.mode = mode
        self.urls = urls
        self.options = options
        self.status = "running"
        self.logs: queue.Queue[str] = queue.Queue()
        self.results: list[dict] = []
        self.stop_requested = False
        self.created_at = time.time()

    def log(self, message: str):
        self.logs.put(str(message))

    def snapshot(self):
        logs = []
        while True:
            try:
                logs.append(self.logs.get_nowait())
            except queue.Empty:
                break
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "logs": logs,
            "results": self.results,
        }


class QueueLogger:
    def __init__(self, log):
        self.log = log

    def debug(self, _msg):
        return None

    def warning(self, msg):
        self.log(f"yt-dlp 警告: {msg}")

    def error(self, msg):
        self.log(f"yt-dlp 错误: {msg}")


def create_app() -> Flask:
    app = Flask(__name__)
    env = load_env(ENV_PATH)
    cookie_header = env_cookie_header(env)
    configured_cookie_mode = default_cookie_mode(env)
    if configured_cookie_mode == "none" and default_browser_cookies(env) != "none":
        configured_cookie_mode = "browser"
    js_runtime_path = default_js_runtime_path(env)

    @app.get("/")
    def index():
        return INDEX_HTML

    @app.get("/api/config")
    def config():
        return jsonify({
            "downloadOutput": str(DEFAULT_DOWNLOADS),
            "clipOutput": str(DEFAULT_OUTPUT),
            "apiKey": env.get("GEMINI_API_KEY") or env.get("GRSAI_OPENAI_API_KEY") or "",
            "apiBase": env.get("GEMINI_API_BASE") or env.get("GRSAI_OPENAI_API_BASE") or "https://generativelanguage.googleapis.com",
            "model": env.get("GEMINI_MODEL") or env.get("NANO_BANANA2_MODEL") or "gemini-3.5-flash",
            "cookieMode": configured_cookie_mode,
            "browserCookies": default_browser_cookies(env),
            "playerClient": default_player_client(env),
            "downloadQuality": default_download_quality(env),
            "clipQuality": default_download_quality(env),
            "sleepInterval": default_sleep_interval(env),
            "proxy": default_proxy(env),
            "cookiesFile": default_cookies_file(env),
            "hasEnvCookies": bool(cookie_header),
            "hasJsRuntime": bool(js_runtime_path),
            "missingTools": missing_tools(),
            "ytDlpVersion": YT_DLP_VERSION,
        })

    @app.post("/api/jobs")
    def create_job():
        payload = request.get_json(force=True)
        mode = payload.get("mode", "")
        urls = parse_urls(payload.get("urls", ""))
        options = payload.get("options", {})

        if mode not in {"download", "clip"}:
            return jsonify({"error": "未知任务类型"}), 400
        if not urls:
            return jsonify({"error": "请输入至少一个 URL"}), 400
        if missing_tools():
            return jsonify({"error": "缺少 ffmpeg 或 ffprobe"}), 400
        if mode == "clip" and not options.get("apiKey"):
            return jsonify({"error": "请填写 Gemini API Key"}), 400

        options["cookieHeader"] = cookie_header
        options["jsRuntimePath"] = js_runtime_path
        cookie_mode = options.get("cookieMode") or "none"
        cookies = options.get("cookies")
        browser = options.get("browserCookies") or "none"
        if cookie_mode == "browser" and (not browser or browser == "none"):
            browser = default_browser_cookies(env)
            options["browserCookies"] = browser
        if cookie_mode == "file" and not cookies and DEFAULT_COOKIES_FILE.exists():
            options["cookies"] = str(DEFAULT_COOKIES_FILE)
            cookies = options["cookies"]
        if cookie_mode == "file" and cookies and not Path(cookies).expanduser().exists():
            return jsonify({"error": "Cookies 文件不存在"}), 400
        if cookie_mode == "env" and not cookie_header:
            return jsonify({"error": "本地 env 未配置 YOUTUBE_COOKIE_HEADER"}), 400

        job = Job(mode, urls, options)
        JOBS[job.id] = job
        thread = threading.Thread(target=run_job, args=(job,), daemon=True)
        thread.start()
        return jsonify({"id": job.id})

    @app.get("/api/jobs/<job_id>")
    def job_state(job_id):
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(job.snapshot())

    @app.post("/api/jobs/<job_id>/stop")
    def stop_job(job_id):
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        job.stop_requested = True
        job.log("收到停止请求，会在当前步骤完成后退出。")
        return jsonify({"ok": True})

    @app.post("/api/open-folder")
    def open_folder():
        folder = request.get_json(force=True).get("folder", "")
        if folder:
            path = Path(folder).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            import subprocess
            subprocess.run(["open", str(path)], check=False)
        return jsonify({"ok": True})

    return app


def run_job(job: Job):
    try:
        if job.mode == "download":
            run_download_job(job)
        else:
            run_clip_job(job)
        if job.status == "running":
            job.status = "done"
            job.log("任务完成")
    except Exception as exc:
        job.status = "error"
        job.log(f"严重错误: {exc}")


def run_download_job(job: Job):
    output_dir = Path(job.options.get("output") or DEFAULT_DOWNLOADS).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    job.log(f"开始下载 {len(job.urls)} 个视频")

    for index, url in enumerate(job.urls, start=1):
        if job.stop_requested:
            job.status = "stopped"
            job.log("已停止")
            return
        job.log(f"[{index}/{len(job.urls)}] {url}")
        try:
            result = download_one(url, output_dir, job.options, job.log)
            job.results.append({"status": "完成", **result})
        except Exception as exc:
            job.log(f"失败: {exc}")
            job.results.append({"status": "失败", "title": url, "folder": ""})


def download_one(url: str, output_dir: Path, options: dict, log):
    info_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if options.get("proxy"):
        info_opts["proxy"] = options["proxy"]
    info_args = youtube_extractor_args(options.get("playerClient"))
    if info_args:
        info_opts["extractor_args"] = info_args
    apply_cookie_options(info_opts, options)
    apply_js_runtime_options(info_opts, options)

    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title") or "video"
    selected_quality = choose_download_quality(info, options.get("quality", "auto"))
    log(f"选择画质: {selected_quality}")
    video_dir = unique_folder(output_dir, title)
    video_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "outtmpl": str(video_dir / "original.%(ext)s"),
        "format": quality_to_format(selected_quality),
        "format_sort": format_sort(selected_quality),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "logger": QueueLogger(log),
        "progress_hooks": [progress_hook(log)],
        "retries": 3,
        "extractor_retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
    }
    sleep_interval = float(options.get("sleepInterval") or 0)
    if sleep_interval > 0:
        ydl_opts["sleep_interval"] = sleep_interval
        ydl_opts["max_sleep_interval"] = max(sleep_interval, sleep_interval + 2)
    if options.get("subtitle"):
        ydl_opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["zh-Hans", "zh", "en"],
        })
    if options.get("proxy"):
        ydl_opts["proxy"] = options["proxy"]
    extractor_args = youtube_extractor_args(options.get("playerClient"))
    if extractor_args:
        ydl_opts["extractor_args"] = extractor_args
    apply_cookie_options(ydl_opts, options)
    apply_js_runtime_options(ydl_opts, options)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        downloaded_info = ydl.extract_info(url, download=True)

    original_files = sorted(video_dir.glob("original.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    metadata = {
        "url": url,
        "title": downloaded_info.get("title", title),
        "duration": downloaded_info.get("duration"),
        "uploader": downloaded_info.get("uploader"),
        "selected_quality": selected_quality,
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "original": str(original_files[0]) if original_files else "",
    }
    (video_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"下载完成: {title} ({format_seconds(metadata['duration'])})")
    return {"title": title, "folder": str(video_dir)}


def progress_hook(log):
    state = {"last": -1}

    def hook(data):
        if data.get("status") != "downloading":
            return
        pct_text = data.get("_percent_str", "?").strip().replace("%", "")
        try:
            pct = float(pct_text)
        except ValueError:
            return
        if pct - state["last"] >= 10:
            state["last"] = pct
            speed = data.get("_speed_str", "?").strip()
            eta = data.get("_eta_str", "?").strip()
            log(f"下载中 {pct:.0f}%  速度 {speed}  剩余 {eta}")

    return hook


def run_clip_job(job: Job):
    job.log(f"开始分析剪辑 {len(job.urls)} 个视频")
    cookie_mode = job.options.get("cookieMode") or "none"
    cookies_file = None
    browser_cookies = None
    if cookie_mode == "env" and job.options.get("cookieHeader"):
        cookies_file = cookie_header_file(job.options["cookieHeader"])
    elif cookie_mode == "file":
        cookies_file = job.options.get("cookies") or None
    elif cookie_mode == "browser":
        browser_cookies = job.options.get("browserCookies") or None
    pipeline = Pipeline(
        job.options["apiKey"],
        job.options["apiBase"],
        job.options["model"],
        job.options.get("output") or str(DEFAULT_OUTPUT),
        download_quality=job.options.get("quality", "720p"),
        proxy=job.options.get("proxy") or None,
        cookies_file=cookies_file,
        browser_cookies=browser_cookies,
        js_runtime_path=job.options.get("jsRuntimePath") or None,
        player_client=job.options.get("playerClient") or None,
        sleep_interval=float(job.options.get("sleepInterval") or 0),
        log_callback=job.log,
        stop_check=lambda: job.stop_requested,
    )
    results = pipeline.run(job.urls)
    for item in results:
        if item.get("error"):
            job.results.append({"status": "失败", "title": item.get("url", ""), "folder": ""})
        else:
            job.results.append({
                "status": "完成",
                "title": f"{item.get('title', '')} - {len(item.get('clips', []))} 个片段",
                "folder": item.get("folder", ""),
            })


INDEX_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>自动高光剪辑</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d8dee8;
      --text: #17202f;
      --muted: #627084;
      --blue: #2563eb;
      --green: #0f766e;
      --red: #b42318;
      --amber: #b45309;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .app { min-height: 100vh; display: grid; grid-template-rows: auto 1fr auto; }
    header {
      display: flex;
      align-items: baseline;
      gap: 18px;
      padding: 18px 22px 14px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
    header span { color: var(--muted); font-size: 14px; }
    main { padding: 18px 22px; display: grid; grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr); gap: 16px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    h2 { margin: 0; font-size: 18px; letter-spacing: 0; }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 13px; }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }
    textarea { min-height: 132px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    button {
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 9px 13px;
      font: inherit;
      cursor: pointer;
      color: #fff;
      background: var(--blue);
    }
    button.secondary { background: #fff; color: var(--text); border-color: var(--line); }
    button.stop { background: var(--red); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .check { display: flex; align-items: center; gap: 8px; color: var(--text); }
    .check input { width: auto; }
    .log {
      min-height: 180px;
      max-height: 260px;
      overflow: auto;
      background: #101827;
      color: #e5edf8;
      border-radius: 6px;
      padding: 10px;
      white-space: pre-wrap;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 8px 6px; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    td.path { color: var(--green); cursor: pointer; word-break: break-all; }
    footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 22px;
      color: var(--muted);
      border-top: 1px solid var(--line);
      background: #fbfcfe;
      font-size: 13px;
    }
    .status.running { color: var(--amber); }
    .status.done { color: var(--green); }
    .status.error, .status.stopped { color: var(--red); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      header { display: block; }
      header span { display: block; margin-top: 6px; }
    }
  </style>
</head>
<body>
<div class="app">
  <header>
    <h1>自动高光剪辑</h1>
    <span>YouTube 批量下载 / Gemini 高光分析 / 原片与片段归档</span>
  </header>
  <main>
    <section id="download">
      <h2>下载视频</h2>
      <label>URL<textarea id="downloadUrls"></textarea></label>
      <label>保存位置<input id="downloadOutput"></label>
      <div class="grid">
        <label>视频画质<select id="downloadQuality"></select></label>
        <label>YouTube Client<select id="downloadPlayerClient"></select></label>
      </div>
      <div class="grid">
        <label>Cookies 模式<select id="downloadCookieMode"></select></label>
        <label>浏览器 Cookies<select id="downloadBrowserCookies"></select></label>
      </div>
      <div class="grid">
        <label>请求间隔<input id="downloadSleep" type="number" min="0" step="1" value="5"></label>
        <label>Cookies 文件<input id="downloadCookies"></label>
      </div>
      <div class="grid">
        <label>代理<input id="downloadProxy" placeholder="http://127.0.0.1:7890"></label>
      </div>
      <label class="check"><input id="downloadSubtitle" type="checkbox">同时下载字幕</label>
      <div class="actions">
        <div class="row">
          <button id="downloadStart">开始下载</button>
          <button id="downloadStop" class="stop" disabled>停止</button>
        </div>
        <button class="secondary" data-open="downloadOutput">打开输出文件夹</button>
      </div>
      <div id="downloadLog" class="log"></div>
      <table>
        <thead><tr><th>状态</th><th>内容</th><th>文件夹</th></tr></thead>
        <tbody id="downloadResults"></tbody>
      </table>
    </section>

    <section id="clip">
      <h2>分析剪辑</h2>
      <label>URL<textarea id="clipUrls"></textarea></label>
      <div class="grid">
        <label>API Key<input id="apiKey" type="password"></label>
        <label>模型<input id="model"></label>
      </div>
      <label>API 地址<input id="apiBase"></label>
      <label>保存位置<input id="clipOutput"></label>
      <div class="grid">
        <label>原片画质<select id="clipQuality"></select></label>
        <label>YouTube Client<select id="clipPlayerClient"></select></label>
      </div>
      <div class="grid">
        <label>Cookies 模式<select id="clipCookieMode"></select></label>
        <label>浏览器 Cookies<select id="clipBrowserCookies"></select></label>
      </div>
      <div class="grid">
        <label>请求间隔<input id="clipSleep" type="number" min="0" step="1" value="5"></label>
        <label>Cookies 文件<input id="clipCookies"></label>
      </div>
      <div class="grid">
        <label>代理<input id="clipProxy" placeholder="http://127.0.0.1:7890"></label>
      </div>
      <div class="actions">
        <div class="row">
          <button id="clipStart">开始分析剪辑</button>
          <button id="clipStop" class="stop" disabled>停止</button>
        </div>
        <button class="secondary" data-open="clipOutput">打开输出文件夹</button>
      </div>
      <div id="clipLog" class="log"></div>
      <table>
        <thead><tr><th>状态</th><th>内容</th><th>文件夹</th></tr></thead>
        <tbody id="clipResults"></tbody>
      </table>
    </section>
  </main>
  <footer>
    <span id="status" class="status">就绪</span>
    <span id="tools"></span>
  </footer>
</div>
<script>
const qualities = ["auto", "best", "4k", "2k", "1080p", "720p", "480p", "360p", "240p"];
const playerClients = ["default", "mweb", "tv_downgraded,web_safari", "web_safari", "ios", "web"];
const cookieModes = ["none", "env", "browser", "file"];
const cookieBrowsers = ["none", "chrome", "safari", "firefox", "edge", "brave", "chromium"];
const jobs = {};

function $(id) { return document.getElementById(id); }

function fillQuality(id, value) {
  const select = $(id);
  select.innerHTML = "";
  qualities.forEach(q => {
    const option = document.createElement("option");
    option.value = q;
    option.textContent = q;
    if (q === value) option.selected = true;
    select.appendChild(option);
  });
}

function fillSelect(id, values, selected) {
  const select = $(id);
  select.innerHTML = "";
  values.forEach(value => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === selected) option.selected = true;
    select.appendChild(option);
  });
}

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  fillQuality("downloadQuality", cfg.downloadQuality || "auto");
  fillQuality("clipQuality", cfg.clipQuality || "auto");
  fillSelect("downloadPlayerClient", playerClients, cfg.playerClient || "default");
  fillSelect("clipPlayerClient", playerClients, cfg.playerClient || "default");
  fillSelect("downloadCookieMode", cookieModes, cfg.cookieMode || "none");
  fillSelect("clipCookieMode", cookieModes, cfg.cookieMode || "none");
  fillSelect("downloadBrowserCookies", cookieBrowsers, cfg.browserCookies || "none");
  fillSelect("clipBrowserCookies", cookieBrowsers, cfg.browserCookies || "none");
  $("downloadOutput").value = cfg.downloadOutput;
  $("clipOutput").value = cfg.clipOutput;
  $("apiKey").value = cfg.apiKey;
  $("apiBase").value = cfg.apiBase;
  $("model").value = cfg.model;
  $("downloadSleep").value = cfg.sleepInterval || "5";
  $("clipSleep").value = cfg.sleepInterval || "5";
  $("downloadProxy").value = cfg.proxy || "";
  $("clipProxy").value = cfg.proxy || "";
  $("downloadCookies").value = cfg.cookiesFile || "";
  $("clipCookies").value = cfg.cookiesFile || "";
  const cookieText = cfg.hasEnvCookies ? "Env Cookies 已配置" : "Env Cookies 未配置";
  const jsText = cfg.hasJsRuntime ? "JS Runtime 已就绪" : "JS Runtime 未找到";
  $("tools").textContent = cfg.missingTools.length ? `缺少: ${cfg.missingTools.join(", ")} · ${cookieText} · ${jsText}` : `FFmpeg 已就绪 · yt-dlp ${cfg.ytDlpVersion} · ${cookieText} · ${jsText}`;
}

function setBusy(mode, busy) {
  $(`${mode}Start`).disabled = busy;
  $(`${mode}Stop`).disabled = !busy;
  $("status").textContent = busy ? "运行中" : "就绪";
  $("status").className = busy ? "status running" : "status";
}

function appendLog(mode, lines) {
  const log = $(`${mode}Log`);
  for (const line of lines) log.textContent += `${line}\n`;
  log.scrollTop = log.scrollHeight;
}

function renderResults(mode, results) {
  const body = $(`${mode}Results`);
  body.innerHTML = "";
  for (const item of results) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${item.status || ""}</td><td>${item.title || ""}</td><td class="path">${item.folder || ""}</td>`;
    tr.querySelector(".path").addEventListener("dblclick", () => openFolder(item.folder));
    body.appendChild(tr);
  }
}

async function startJob(mode) {
  const isDownload = mode === "download";
  const options = isDownload ? {
    output: $("downloadOutput").value,
    quality: $("downloadQuality").value,
    playerClient: $("downloadPlayerClient").value,
    cookieMode: $("downloadCookieMode").value,
    browserCookies: $("downloadBrowserCookies").value,
    sleepInterval: $("downloadSleep").value,
    proxy: $("downloadProxy").value,
    cookies: $("downloadCookies").value,
    subtitle: $("downloadSubtitle").checked
  } : {
    apiKey: $("apiKey").value,
    apiBase: $("apiBase").value,
    model: $("model").value,
    output: $("clipOutput").value,
    quality: $("clipQuality").value,
    playerClient: $("clipPlayerClient").value,
    cookieMode: $("clipCookieMode").value,
    browserCookies: $("clipBrowserCookies").value,
    sleepInterval: $("clipSleep").value,
    proxy: $("clipProxy").value,
    cookies: $("clipCookies").value
  };
  $(`${mode}Log`).textContent = "";
  $(`${mode}Results`).innerHTML = "";
  setBusy(mode, true);
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({mode, urls: $(isDownload ? "downloadUrls" : "clipUrls").value, options})
  });
  const data = await res.json();
  if (!res.ok) {
    appendLog(mode, [data.error || "启动失败"]);
    setBusy(mode, false);
    return;
  }
  jobs[mode] = data.id;
  pollJob(mode, data.id);
}

async function pollJob(mode, id) {
  const res = await fetch(`/api/jobs/${id}`);
  const data = await res.json();
  appendLog(mode, data.logs || []);
  renderResults(mode, data.results || []);
  if (data.status === "running") {
    setTimeout(() => pollJob(mode, id), 700);
  } else {
    setBusy(mode, false);
    $("status").textContent = data.status === "done" ? "完成" : data.status;
    $("status").className = `status ${data.status}`;
  }
}

async function stopJob(mode) {
  const id = jobs[mode];
  if (!id) return;
  await fetch(`/api/jobs/${id}/stop`, {method: "POST"});
}

async function openFolder(folder) {
  if (!folder) return;
  await fetch("/api/open-folder", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({folder})
  });
}

$("downloadStart").addEventListener("click", () => startJob("download"));
$("clipStart").addEventListener("click", () => startJob("clip"));
$("downloadStop").addEventListener("click", () => stopJob("download"));
$("clipStop").addEventListener("click", () => stopJob("clip"));
document.querySelectorAll("[data-open]").forEach(btn => {
  btn.addEventListener("click", () => openFolder($(btn.dataset.open).value));
});
loadConfig();
</script>
</body>
</html>
"""


def main():
    app = create_app()
    url = "http://127.0.0.1:7860"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=7860, debug=False, threaded=True)


if __name__ == "__main__":
    main()
