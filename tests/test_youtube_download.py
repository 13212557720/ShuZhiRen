import os
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from yt_dlp.utils import DownloadError

from web_app import choose_download_quality, default_js_runtime_path, download_one


YOUTUBE_SHORTS_URL = "https://www.youtube.com/shorts/4FbJv71E2KE"



def test_choose_download_quality_uses_preferred_available_order() -> None:
    info = {
        "formats": [
            {"height": 360, "vcodec": "avc1.4d401e"},
            {"height": 480, "vcodec": "avc1.4d401f"},
            {"height": 1080, "vcodec": "avc1.640028"},
            {"height": None, "vcodec": "none"},
        ]
    }

    assert choose_download_quality(info, "auto") == "1080p"
    assert choose_download_quality(info, "480p") == "480p"
    assert choose_download_quality(info, "720p") == "1080p"

    info["formats"].append({"height": 720, "vcodec": "avc1.4d401f"})
    assert choose_download_quality(info, "auto") == "720p"


def test_download_one_writes_video_and_metadata_with_youtube_options(tmp_path) -> None:
    captured_opts: list[dict] = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            captured_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert url == YOUTUBE_SHORTS_URL
            if download:
                output = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"fake video")
            return {
                "title": "Mock YouTube Short",
                "duration": 12,
                "uploader": "Mock Channel",
                "formats": [
                    {"height": 240, "vcodec": "avc1.4d400d"},
                    {"height": 360, "vcodec": "avc1.4d401e"},
                    {"height": 480, "vcodec": "avc1.4d401f"},
                ],
            }

    options = {
        "quality": "auto",
        "playerClient": "mweb",
        "cookieMode": "browser",
        "browserCookies": "chrome",
        "jsRuntimePath": "/Applications/Codex.app/Contents/Resources/node",
        "sleepInterval": "0",
    }
    logs: list[str] = []

    with patch("web_app.yt_dlp.YoutubeDL", FakeYoutubeDL):
        result = download_one(YOUTUBE_SHORTS_URL, tmp_path, options, logs.append)

    video_dir = Path(result["folder"])
    metadata = json.loads((video_dir / "metadata.json").read_text(encoding="utf-8"))

    assert result["title"] == "Mock YouTube Short"
    assert (video_dir / "original.mp4").read_bytes() == b"fake video"
    assert metadata["url"] == YOUTUBE_SHORTS_URL
    assert metadata["duration"] == 12
    assert metadata["uploader"] == "Mock Channel"
    assert metadata["selected_quality"] == "480p"
    assert "选择画质: 480p" in logs
    assert "下载完成: Mock YouTube Short (0:12)" in logs

    info_opts, download_opts = captured_opts
    assert info_opts["cookiesfrombrowser"] == ("chrome",)
    assert download_opts["cookiesfrombrowser"] == ("chrome",)
    assert info_opts["js_runtimes"] == {"node": {"path": options["jsRuntimePath"]}}
    assert download_opts["js_runtimes"] == {"node": {"path": options["jsRuntimePath"]}}
    assert download_opts["format"] == "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b"
    assert download_opts["extractor_args"] == {"youtube": {"player_client": ["mweb"]}}


@pytest.mark.integration
def test_can_download_youtube_shorts_url() -> None:
    if os.environ.get("RUN_YOUTUBE_DOWNLOAD_TEST") != "1":
        pytest.skip("set RUN_YOUTUBE_DOWNLOAD_TEST=1 to download from YouTube")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to merge downloaded media")

    logs: list[str] = []
    cookie_header = os.environ.get("YOUTUBE_COOKIE_HEADER", "")
    options = {
        "quality": os.environ.get("YOUTUBE_DOWNLOAD_QUALITY", "auto"),
        "playerClient": os.environ.get("YOUTUBE_PLAYER_CLIENT", "default"),
        "cookieMode": os.environ.get("YOUTUBE_COOKIE_MODE", "env" if cookie_header else "none"),
        "cookieHeader": cookie_header,
        "browserCookies": os.environ.get("YOUTUBE_BROWSER_COOKIES", "none"),
        "cookies": os.environ.get("YOUTUBE_COOKIES_FILE", ""),
        "jsRuntimePath": os.environ.get("YOUTUBE_JS_RUNTIME_PATH", default_js_runtime_path()),
        "proxy": os.environ.get("YOUTUBE_PROXY", ""),
        "sleepInterval": os.environ.get("YOUTUBE_SLEEP_INTERVAL", "0"),
    }

    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = download_one(
                YOUTUBE_SHORTS_URL,
                Path(tmp),
                options,
                logs.append,
            )
        except DownloadError as exc:
            pytest.fail(
                "YouTube download failed. If YouTube asks for bot verification, "
                "rerun with YOUTUBE_COOKIES_FILE=/path/to/cookies.txt or "
                "YOUTUBE_BROWSER_COOKIES=chrome/safari/firefox. "
                f"Original error: {exc}",
                pytrace=False,
            )

        video_dir = Path(result["folder"])
        metadata_path = video_dir / "metadata.json"
        downloaded_files = list(video_dir.glob("original.*"))

        assert result["title"]
        assert video_dir.exists()
        assert metadata_path.exists()
        assert downloaded_files
        assert downloaded_files[0].stat().st_size > 0
