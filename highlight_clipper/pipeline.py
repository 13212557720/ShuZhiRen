import os
import re
import shutil
import tempfile
from pathlib import Path

import yt_dlp

from video_utils import clip_video, get_video_info
from gemini_analyzer import analyze_video


class Pipeline:
    def __init__(self, api_key, base_url, model, output_dir, log_callback=None, stop_check=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.output_dir = Path(output_dir)
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
        highlights = analyze_video(url, self.api_key, self.base_url, self.model, log_callback=self.log)

        if not highlights:
            self.log(f"未发现高光片段，跳过下载")
            return {"url": url, "clips": [], "error": None}

        self.log(f"发现 {len(highlights)} 个高光片段，开始下载视频...")

        self.log("步骤2/3: 下载视频 (720p)...")
        video_path, title = self._download(url)
        self.log(f"下载完成: {title}")

        info = get_video_info(video_path)
        video_duration = info["duration"]
        self.log(f"视频时长: {video_duration:.0f}s")

        self.log("步骤3/3: 剪辑高光片段...")
        clips = self._clip(video_path, title, highlights, video_duration)

        try:
            os.unlink(video_path)
        except Exception:
            pass

        return {"url": url, "title": title, "clips": clips, "error": None}

    def _clip(self, video_path, title, highlights, video_duration):
        safe_title = _safe_filename(title)
        video_dir = self.output_dir / safe_title
        video_dir.mkdir(parents=True, exist_ok=True)

        highlights.sort(key=lambda x: x.get("score", 0), reverse=True)

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
            score = h.get("score", 0)
            desc = h.get("desc", "高光")
            safe_desc = _safe_filename(desc)[:20]
            clip_name = f"{j+1:02d}_{safe_desc}_评分{score}_{duration}s.mp4"
            clip_path = video_dir / clip_name

            self.log(f"  剪辑 [{start:.0f}s - {end:.0f}s] (得分:{score}) {desc}")
            clip_video(str(video_path), start, end, str(clip_path))
            clips.append({
                "file": str(clip_path),
                "start": start,
                "end": end,
                "desc": desc,
                "score": score,
            })
            self.log(f"  → {safe_title}/{clip_name}")
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

        ydl_opts = {
            "outtmpl": output_template,
            "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/mp4",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "logger": LogCollector(self.log),
            "progress_hooks": [lambda d: self._progress(d)],
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
        }

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
        persistent_path = self.output_dir / f"_dl_{_safe_filename(title)}.mp4"
        shutil.move(str(video_path), str(persistent_path))
        shutil.rmtree(temp_dir, ignore_errors=True)
        return persistent_path, title

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
