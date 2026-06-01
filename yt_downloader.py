# -*- coding: utf-8 -*-
"""
YouTube / 多平台视频下载工具
基于 yt-dlp, 提炼自 yt-dlp 项目的核心下载能力。

安装依赖 (首次使用):
    pip install yt-dlp

使用方式:
    # 命令行
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx -q 1080p -o ./videos
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx --audio-only

    # 作为模块导入
    from yt_downloader import download_video, get_video_info
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

try:
    import yt_dlp
except ImportError:
    print("请先安装 yt-dlp: pip install yt-dlp")
    sys.exit(1)



def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def _build_ydl_opts(
    output_dir: str = ".",
    resolution: str = "best",
    audio_only: bool = False,
    subtitle: bool = False,
    playlist: bool = False,
    limit_rate: str | None = None,
    proxy: str | None = None,
    cookies_file: str | None = None,
) -> dict[str, Any]:
    """
    构建 yt-dlp 的下载选项字典。
    """
    output_dir = os.path.expanduser(output_dir)
    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

    opts: dict[str, Any] = {
        "outtmpl": output_template,
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [_progress_hook],
        "noplaylist": not playlist,
    }

    if proxy:
        opts["proxy"] = proxy

    if cookies_file:
        opts["cookiefile"] = cookies_file

    if limit_rate:
        opts["ratelimit"] = _parse_rate_limit(limit_rate)

    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        fmt = _resolution_to_format(resolution)
        opts["format"] = fmt

    if subtitle:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["zh-Hans", "zh", "en"]

    return opts


def _resolution_to_format(resolution: str) -> str:
    """
    分辨率字符串 -> yt-dlp format selector。
    """
    mapping: dict[str, str] = {
        "best": "bestvideo+bestaudio/best",
        "4k": "bestvideo[height<=2160]+bestaudio/best[height<=2160]",
        "2k": "bestvideo[height<=1440]+bestaudio/best[height<=1440]",
        "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
    }

    fmt = mapping.get(resolution.lower(), mapping["best"])
    return fmt + "/mp4"


def _parse_rate_limit(limit: str) -> int | None:
    """解析限速字符串 (如 '1M', '500K') 为字节数。"""
    limit = limit.strip().upper()
    multipliers: dict[str, int] = {"K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024}

    for suffix, multiplier in multipliers.items():
        if limit.endswith(suffix):
            try:
                return int(float(limit[:-1]) * multiplier)
            except ValueError:
                return None

    try:
        return int(limit)
    except ValueError:
        return None


def _progress_hook(d: dict[str, Any]) -> None:
    status = d.get("status")
    if status == "downloading":
        pct = d.get("_percent_str", "  ?%").strip()
        speed = d.get("_speed_str", "?")
        eta = d.get("_eta_str", "?")

        msg = f"  下载中 {pct}  速度 {speed}  剩余 {eta}"
        if len(msg) > 100:
            msg = msg[:97] + "..."
        sys.stdout.write(f"\r{msg}")
        sys.stdout.flush()
    elif status == "finished":
        sys.stdout.write("\r" + " " * 120 + "\r")
        sys.stdout.flush()


def get_video_info(url: str, proxy: str | None = None) -> dict[str, Any]:
    """
    获取视频基本信息，不下载。
    返回字典包含: title, duration, view_count, uploader, formats 等。
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
    }
    if proxy:
        opts["proxy"] = proxy

    info: dict[str, Any] = {}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return {
        "title": info.get("title", ""),
        "duration": info.get("duration", 0),
        "view_count": info.get("view_count", 0),
        "uploader": info.get("uploader", ""),
        "upload_date": info.get("upload_date", ""),
        "description": info.get("description", ""),
        "thumbnail": info.get("thumbnail", ""),
        "webpage_url": info.get("webpage_url", url),
        "formats_count": len(info.get("formats", [])),
    }


def download_video(
    url: str,
    output_dir: str = ".",
    resolution: str = "best",
    audio_only: bool = False,
    subtitle: bool = False,
    playlist: bool = False,
    limit_rate: str | None = None,
    proxy: str | None = None,
    cookies_file: str | None = None,
) -> dict[str, Any]:
    """
    下载单个视频。

    参数:
        url:            视频链接 (YouTube / Bilibili / 其他 yt-dlp 支持的站点)
        output_dir:     输出目录, 默认当前目录
        resolution:     分辨率: best / 4k / 2k / 1080p / 720p / 480p / 360p
        audio_only:     仅下载音频并转为 mp3
        subtitle:       下载字幕 (中文/英文)
        playlist:       是否下载整个播放列表
        limit_rate:     限速 (如 '1M', '500K')
        proxy:          代理地址 (如 'http://127.0.0.1:7890')
        cookies_file:   cookies 文件路径 (用于需要登录的视频)

    返回:
        dict: {"title": ..., "filepath": ..., "duration": ...}
    """
    opts = _build_ydl_opts(
        output_dir=output_dir,
        resolution=resolution,
        audio_only=audio_only,
        subtitle=subtitle,
        playlist=playlist,
        limit_rate=limit_rate,
        proxy=proxy,
        cookies_file=cookies_file,
    )

    result: dict[str, Any] = {}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

        if playlist and "entries" in info:
            entries = info["entries"]
            video_list = []
            for entry in entries:
                if entry is None:
                    continue
                video_list.append(
                    {
                        "title": entry.get("title", ""),
                        "filepath": ydl.prepare_filename(entry),
                        "duration": entry.get("duration", 0),
                    }
                )
            result["entries"] = video_list
            result["playlist_title"] = info.get("title", "")
            result["count"] = len(video_list)
        else:
            result = {
                "title": info.get("title", ""),
                "filepath": ydl.prepare_filename(info),
                "duration": info.get("duration", 0),
            }

    return result


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube / 多平台视频下载工具 (基于 yt-dlp)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx -q 1080p -o ./videos
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx --audio-only
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx --info
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx --proxy http://127.0.0.1:7890
    python yt_downloader.py https://www.youtube.com/watch?v=xxxxx --cookies cookies.txt
        """,
    )

    parser.add_argument("url", help="视频链接")
    parser.add_argument("-o", "--output", default=".", help="输出目录 (默认: 当前目录)")
    parser.add_argument(
        "-q",
        "--quality",
        default="best",
        choices=["best", "4k", "2k", "1080p", "720p", "480p", "360p"],
        help="视频质量 (默认: best)",
    )
    parser.add_argument("--audio-only", action="store_true", help="仅下载音频 (转 mp3)")
    parser.add_argument("--subtitle", action="store_true", help="同时下载字幕")
    parser.add_argument("--playlist", action="store_true", help="下载整个播放列表")
    parser.add_argument("--limit-rate", default=None, help="下载限速 (如: 1M, 500K)")
    parser.add_argument("--proxy", default=None, help="代理地址 (如: http://127.0.0.1:7890)")
    parser.add_argument("--cookies", default=None, help="Netscape 格式 cookies 文件路径")
    parser.add_argument("--info", action="store_true", help="仅查看视频信息, 不下载")

    args = parser.parse_args()

    if args.info:
        log(f"正在获取视频信息: {args.url}")
        info = get_video_info(args.url, proxy=args.proxy)
        print(f"\n  标题:     {info['title']}")
        print(f"  上传者:   {info['uploader']}")
        print(f"  时长:     {info['duration']} 秒")
        print(f"  播放量:   {info['view_count']}")
        print(f"  上传日期: {info['upload_date']}")
        print(f"  可用格式: {info['formats_count']} 种")
        print(f"  描述:     {info['description'][:200]}...")
        return

    log(f"开始下载: {args.url}")
    log(f"画质: {args.quality}  |  输出: {os.path.abspath(args.output)}")

    start = time.time()
    try:
        result = download_video(
            url=args.url,
            output_dir=args.output,
            resolution=args.quality,
            audio_only=args.audio_only,
            subtitle=args.subtitle,
            playlist=args.playlist,
            limit_rate=args.limit_rate,
            proxy=args.proxy,
            cookies_file=args.cookies,
        )
    except yt_dlp.utils.DownloadError as exc:
        log(f"下载失败: {exc}")
        sys.exit(1)

    elapsed = time.time() - start

    if args.playlist and "entries" in result:
        entries = result["entries"]
        log(f"播放列表 [{result['playlist_title']}] 下载完成, 共 {result['count']} 个视频, 耗时 {elapsed:.0f} 秒")
        for i, entry in enumerate(entries):
            print(f"  {i + 1}. {entry['title']}")
            print(f"     -> {entry['filepath']}")
    else:
        log(f"下载完成, 耗时 {elapsed:.0f} 秒")
        print(f"\n  标题: {result['title']}")
        print(f"  文件: {result['filepath']}")
        print(f"  时长: {result['duration']} 秒")


if __name__ == "__main__":
    _main()
