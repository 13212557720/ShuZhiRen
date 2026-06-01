import tempfile
import unittest
from pathlib import Path

from web_app import (
    cookie_header_to_netscape,
    default_cookie_mode,
    format_seconds,
    parse_urls,
    safe_filename,
    unique_folder,
)
from highlight_clipper.gemini_analyzer import _parse_highlights


class CoreHelpersTest(unittest.TestCase):
    def test_parse_urls_ignores_empty_lines_and_comments(self):
        text = """
        # comment
        https://youtube.com/watch?v=one

        https://youtu.be/two
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


if __name__ == "__main__":
    unittest.main()
