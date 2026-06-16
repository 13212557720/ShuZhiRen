import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from highlight_clipper.pipeline import Pipeline
from web_app import (
    build_analysis_candidates,
    cookie_header_to_netscape,
    default_cookie_mode,
    format_seconds,
    non_negative_float,
    parse_urls,
    safe_filename,
    unique_folder,
)
from highlight_clipper.gemini_analyzer import _call_openai_compatible_with_frames, _parse_highlights


class CoreHelpersTest(unittest.TestCase):
    def test_parse_urls_ignores_empty_lines_and_comments(self):
        text = """
        # comment
        https://youtube.com/watch?v=one，

        https://youtu.be/two,
        """
        self.assertEqual(
            parse_urls(text),
            ["https://youtube.com/watch?v=one", "https://youtu.be/two"],
        )

    def test_safe_filename_removes_unsafe_chars(self):
        self.assertEqual(safe_filename('a/b:c*?"<>|'), "a_b_c")

    def test_unique_folder_avoids_existing_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "Video").mkdir()
            self.assertEqual(unique_folder(parent, "Video").name, "Video_2")

    def test_format_seconds(self):
        self.assertEqual(format_seconds(65), "1:05")
        self.assertEqual(format_seconds(3661), "1:01:01")
        self.assertEqual(format_seconds(None), "-")

    def test_parse_highlights_repairs_markdown_wrapped_json(self):
        raw = """
        ```json
        [
          {"start": 10, "end": 25, "score": 91, "desc": "爆分"},
          {"start": 19, "end": 30, "score": 80, "desc": "重叠"},
          {"start": 50, "end": 55, "score": 70, "desc": "短高光"}
        ]
        ```
        """
        highlights = _parse_highlights(raw)
        self.assertEqual(len(highlights), 2)
        self.assertEqual(highlights[0]["desc"], "爆分")

    def test_default_cookie_mode_uses_env_when_cookie_header_exists(self):
        self.assertEqual(default_cookie_mode({"YOUTUBE_COOKIE_HEADER": "SID=abc"}), "env")
        self.assertEqual(default_cookie_mode({}), "none")

    def test_cookie_header_to_netscape(self):
        cookie_file = cookie_header_to_netscape("SID=abc; __Secure-1PSID=def")
        self.assertIn(".youtube.com\tTRUE\t/\tFALSE\t0\tSID\tabc", cookie_file)
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-1PSID\tdef", cookie_file)

    def test_non_negative_float_handles_bad_input(self):
        self.assertEqual(non_negative_float("2.5"), 2.5)
        self.assertEqual(non_negative_float("-1"), 0)
        self.assertEqual(non_negative_float("bad", default=3), 3)

    def test_parse_highlights_skips_bad_items_and_clamps_values(self):
        raw = """
        [
          "bad",
          {"start": -2, "end": 10, "score": 200, "desc": ""},
          {"start": "bad", "end": 20, "score": 90, "desc": "无效"},
          {"start": 30, "end": 29, "score": 90, "desc": "倒置"}
        ]
        """
        highlights = _parse_highlights(raw)
        self.assertEqual(highlights, [{"start": 0, "end": 10.0, "desc": "高光", "score": 100}])

    def test_parse_highlights_accepts_timeline_object_with_clips(self):
        raw = """
        {
          "timeline": [{"start": 0, "end": 4, "content": "铺垫", "value": "medium"}],
          "clips": [{"start": 2, "end": 12, "score": 92, "desc": "大奖"}]
        }
        """
        highlights = _parse_highlights(raw)
        self.assertEqual(highlights, [{"start": 2.0, "end": 12.0, "desc": "大奖", "score": 92}])

    def test_openai_compatible_frame_flow_runs_timeline_then_clip_selection(self):
        class FakeResp:
            status_code = 200
            text = "ok"

            def __init__(self, content):
                self.content = content

            def json(self):
                return {"choices": [{"message": {"content": self.content}}]}

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "source.mp4"
            video_path.write_bytes(b"video")
            frame_path = Path(tmp) / "frame.jpg"
            frame_path.write_bytes(b"image")

            responses = [
                FakeResp('{"timeline":[{"start":0,"end":4,"content":"开始","event":"铺垫","value":"medium"}]}'),
                FakeResp('[{"start":0,"end":8,"score":90,"desc":"高光"}]'),
            ]

            with (
                patch("highlight_clipper.gemini_analyzer._download_temp_video", return_value=video_path),
                patch(
                    "highlight_clipper.gemini_analyzer._extract_frames",
                    return_value=[{"time": 0, "path": frame_path}],
                ),
                patch("highlight_clipper.gemini_analyzer._post_once", side_effect=responses),
            ):
                raw = _call_openai_compatible_with_frames(
                    "https://youtu.be/test",
                    "key",
                    "https://example.com",
                    "gemini-test",
                    None,
                )

        self.assertIn('"desc":"高光"', raw)

    def test_pipeline_process_one_writes_manifest_after_clipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            video_dir = Path(tmp) / "video"
            video_dir.mkdir()
            original = video_dir / "original.mp4"
            original.write_bytes(b"fake")
            pipeline = Pipeline("key", "base", "model", tmp)
            highlights = [{"start": 0.0, "end": 5.0, "score": 88, "desc": "高光"}]
            clips = [{"file": str(video_dir / "highlights" / "01.mp4"), "start": 0.0, "end": 5.0}]

            with (
                patch("highlight_clipper.pipeline.analyze_video_with_fallbacks", return_value=highlights),
                patch.object(Pipeline, "_download", return_value=(original, "标题", video_dir)),
                patch("highlight_clipper.pipeline.get_video_info", return_value={"duration": 10}),
                patch.object(Pipeline, "_clip", return_value=clips),
            ):
                result = pipeline._process_one("https://youtu.be/test")

            manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIsNone(result["error"])
            self.assertEqual(manifest["title"], "标题")
            self.assertEqual(manifest["clips"], clips)

    def test_analysis_candidates_include_zhenzhen_fallbacks(self):
        env = {
            "GRSAI_OPENAI_API_KEY": "grs-key",
            "GRSAI_OPENAI_API_BASE": "https://grsai.example",
            "ZHENZHEN_OPENAI_API_KEY": "zz-key",
            "ZHENZHEN_OPENAI_API_BASE": "https://zhenzhen.example",
        }
        candidates = build_analysis_candidates(
            env,
            {"apiKey": "grs-key", "apiBase": "https://grsai.example", "model": "gemini-3.5-flash"},
        )

        self.assertEqual(candidates[0]["provider"], "GRSAI")
        self.assertEqual(candidates[0]["model"], "gemini-3.5-flash")
        self.assertIn(("ZHENZHEN", "gemini-2.5-pro"), [(c["provider"], c["model"]) for c in candidates])
        self.assertIn(("ZHENZHEN", "gemini-2.5-flash"), [(c["provider"], c["model"]) for c in candidates])


if __name__ == "__main__":
    unittest.main()
