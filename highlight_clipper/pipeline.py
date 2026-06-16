import re
import shutil
import tempfile
import json
from pathlib import Path

import yt_dlp

from .video_utils import clip_video, get_video_info
from .gemini_analyzer import analyze_video_with_fallbacks


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


class Pipeline:
    def __init__(
        self,
        api_key,
        base_url,
        model,
        output_dir,
        download_quality="720p",
        proxy=None,
        cookies_file=None,
        browser_cookies=None,
        js_runtime_path=None,
        player_client=None,
        sleep_interval=0,
        analysis_candidates=None,
        log_callback=None,
        stop_check=None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.output_dir = Path(output_dir)
        self.download_quality = download_quality
        self.proxy = proxy
        self.cookies_file = cookies_file
        self.browser_cookies = browser_cookies
        self.js_runtime_path = js_runtime_path
        self.player_client = player_client
        self.sleep_interval = sleep_interval
        self.analysis_candidates = analysis_candidates or [
            {"provider": "PRIMARY", "api_key": api_key, "base_url": base_url, "model": model}
        ]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log = log_callback or (lambda msg: print(msg))
        self.stop_check = stop_check or (lambda: False)

    def run(self, urls):
        results = []
        for i, url in enumerate(urls):
            if self.stop_check():
                self.log("用户中止")
                break
            self.log(f"\n{'='*50}")
            self.log(f"[{i+1}/{len(urls)}] {url}")
            try:
                r = self._process_one(url)
                results.append(r)
            except Exception as e:
                self.log(f"失败: {e}")
                results.append({"url": url, "error": str(e), "clips": []})
        return results

    def _process_one(self, url):
        self.log("步骤1/3: Gemini 分析视频 (直连YouTube)...")
        highlights = analyze_video_with_fallbacks(url, self.analysis_candidates, log_callback=self.log)

        if highlights:
            self.log(f"发现 {len(highlights)} 个高光片段，开始下载视频...")
        else:
            self.log("未发现高光片段，仍会下载原视频并保存记录")

        if self.stop_check():
            self.log("用户中止")
            return {"url": url, "clips": [], "error": "用户中止"}

        self.log(f"步骤2/3: 下载视频 ({self.download_quality})...")
        video_path, title, video_dir = self._download(url)
        self.log(f"下载完成: {title}")

        info = get_video_info(video_path)
        video_duration = info["duration"]
        self.log(f"视频时长: {video_duration:.0f}s")

        clips = []
        if highlights:
            self.log("步骤3/3: 剪辑高光片段...")
            clips = self._clip(video_path, title, highlights, video_duration, video_dir)
        else:
            self.log("步骤3/3: 无高光片段，跳过剪辑")
        _write_manifest(video_dir, url, title, video_path, highlights, clips)

        return {
            "url": url,
            "title": title,
            "folder": str(video_dir),
            "original": str(video_path),
            "clips": clips,
            "error": None,
        }

    def _clip(self, video_path, title, highlights, video_duration, video_dir):
        video_dir.mkdir(parents=True, exist_ok=True)
        clips_dir = video_dir / "highlights"
        clips_dir.mkdir(parents=True, exist_ok=True)

        highlights.sort(key=lambda x: x.get("start", 0))

        clips = []
        for j, h in enumerate(highlights):
            start = h["start"]
            end = h["end"]

            if start >= video_duration:
                self.log(f"  跳过 [{start:.0f}s - {end:.0f}s] {h['desc']} (超出视频时长)")
                continue

            if end > video_duration:
                self.log(f"  [{start:.0f}s - {end:.0f}s] → 剪到视频末尾 ({video_duration:.0f}s)")
                end = video_duration

            duration = int(end - start)
            if duration <= 0:
                self.log(f"  跳过 [{start:.0f}s - {end:.0f}s] {h['desc']} (片段时长无效)")
                continue

            score = h.get("score", 0)
            desc = h.get("desc", "高光")
            safe_desc = _safe_filename(desc)[:20]
            clip_name = f"{j+1:02d}_{safe_desc}_评分{score}_{duration}s.mp4"
            clip_path = clips_dir / clip_name

            self.log(f"  剪辑 [{start:.0f}s - {end:.0f}s] (得分:{score}) {desc}")
            clip_video(str(video_path), start, end, str(clip_path))
            clips.append({
                "file": str(clip_path),
                "start": start,
                "end": end,
                "desc": desc,
                "score": score,
            })
            self.log(f"  → {video_dir.name}/highlights/{clip_name}")
        return clips

    def _download(self, url):
        temp_dir = Path(tempfile.mkdtemp())
        output_template = str(temp_dir / "%(title)s.%(ext)s")

        class LogCollector:
            def __init__(self, log_fn):
                self.log_fn = log_fn
            def debug(self, msg):
                pass
            def warning(self, msg):
                pass
            def error(self, msg):
                self.log_fn(f"  yt-dlp: {msg}")

        try:
            info_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }
            if self.proxy:
                info_opts["proxy"] = self.proxy
            if self.cookies_file:
                info_opts["cookiefile"] = self.cookies_file
            elif self.browser_cookies and self.browser_cookies != "none":
                info_opts["cookiesfrombrowser"] = (self.browser_cookies,)
            extractor_args = _youtube_extractor_args(self.player_client)
            if extractor_args:
                info_opts["extractor_args"] = extractor_args
            if self.js_runtime_path:
                info_opts["js_runtimes"] = {"node": {"path": self.js_runtime_path}}

            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            selected_quality = _choose_download_quality(info, self.download_quality)
            self.log(f"选择画质: {selected_quality}")

            ydl_opts = {
                "outtmpl": output_template,
                "format": _quality_to_format(selected_quality),
                "format_sort": _format_sort(selected_quality),
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True,
                "logger": LogCollector(self.log),
                "progress_hooks": [lambda d: self._progress(d)],
                "noplaylist": True,
                "retries": 3,
                "extractor_retries": 3,
                "fragment_retries": 3,
                "file_access_retries": 3,
                "concurrent_fragment_downloads": 4,
                "socket_timeout": 30,
            }
            if self.sleep_interval and self.sleep_interval > 0:
                ydl_opts["sleep_interval"] = self.sleep_interval
                ydl_opts["max_sleep_interval"] = max(self.sleep_interval, self.sleep_interval + 2)
            if self.proxy:
                ydl_opts["proxy"] = self.proxy
            if self.cookies_file:
                ydl_opts["cookiefile"] = self.cookies_file
            elif self.browser_cookies and self.browser_cookies != "none":
                ydl_opts["cookiesfrombrowser"] = (self.browser_cookies,)
            extractor_args = _youtube_extractor_args(self.player_client)
            if extractor_args:
                ydl_opts["extractor_args"] = extractor_args
            if self.js_runtime_path:
                ydl_opts["js_runtimes"] = {"node": {"path": self.js_runtime_path}}

            self._last_pct = -1
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "unknown")
                if "entries" in info:
                    info = info["entries"][0]
                    title = info.get("title", title)

            files = list(temp_dir.glob("*.mp4"))
            if not files:
                files = list(temp_dir.glob("*.*"))
            if not files:
                raise RuntimeError("下载后未找到视频文件")

            video_path = files[0]
            video_dir = self.output_dir / _unique_folder_name(self.output_dir, _safe_filename(title))
            video_dir.mkdir(parents=True, exist_ok=True)
            original_ext = video_path.suffix or ".mp4"
            persistent_path = video_dir / f"original{original_ext}"
            shutil.move(str(video_path), str(persistent_path))
            return persistent_path, title, video_dir
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _progress(self, d):
        if d.get("status") == "downloading":
            pct_str = d.get("_percent_str", "?").strip().replace("%", "")
            try:
                pct = float(pct_str)
                if pct - self._last_pct >= 20:
                    self._last_pct = pct
                    self.log(f"  下载中 {pct:.0f}%")
            except ValueError:
                pass


def _safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:80]


def _downloadable_heights(info):
    heights = set()
    for item in info.get("formats") or []:
        height = item.get("height")
        vcodec = item.get("vcodec")
        if isinstance(height, int) and height > 0 and vcodec and vcodec != "none":
            heights.add(height)
    return heights


def _choose_download_quality(info, requested_quality=None):
    requested_quality = requested_quality or "auto"
    heights = _downloadable_heights(info)
    requested_height = QUALITY_HEIGHTS.get(requested_quality)
    if requested_height and requested_height in heights:
        return requested_quality
    for quality in AUTO_QUALITY_ORDER:
        if QUALITY_HEIGHTS[quality] in heights:
            return quality
    return requested_quality if requested_quality != "auto" else "best"


def _unique_folder_name(parent, base_name):
    base_name = base_name or "video"
    candidate = base_name
    index = 2
    while (Path(parent) / candidate).exists():
        candidate = f"{base_name}_{index}"
        index += 1
    return candidate


def _quality_to_format(quality):
    height = QUALITY_HEIGHTS.get(quality)
    if not height:
        return "bv*+ba/b"
    return f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"


def _format_sort(quality):
    height = QUALITY_HEIGHTS.get(quality)
    if height:
        return [f"res:{height}", "ext:mp4:m4a"]
    return ["res", "ext:mp4:m4a"]


def _youtube_extractor_args(player_client):
    if not player_client or player_client == "default":
        return {}
    return {"youtube": {"player_client": player_client.split(",")}}


def _write_manifest(video_dir, url, title, original_path, highlights, clips):
    manifest = {
        "url": url,
        "title": title,
        "original": str(original_path),
        "highlights": highlights,
        "clips": clips,
    }
    manifest_path = Path(video_dir) / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
