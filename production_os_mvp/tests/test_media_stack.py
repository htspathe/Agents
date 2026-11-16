from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_stack


class MediaStackTests(unittest.TestCase):
    def test_srt_rounding(self):
        self.assertEqual(media_stack.srt_time(59.9999), "00:01:00,000")
        self.assertEqual(media_stack.srt_time(3601.025), "01:00:01,025")

    def test_rights_confirmation_precedes_download(self):
        with patch.object(media_stack, "run") as run:
            with self.assertRaises(ValueError):
                media_stack.acquire("https://example.org/test", ".", rights_confirmed=False)
            run.assert_not_called()

    def test_non_https_rejected(self):
        with self.assertRaises(ValueError):
            media_stack.acquire("file:///private.mp4", ".", rights_confirmed=True)

    def test_failed_probe_cannot_pass(self):
        with patch.object(media_stack, "probe", side_effect=RuntimeError("broken")):
            self.assertFalse(media_stack.video_qa("bad.mp4")["technical_pass"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_real_vertical_media_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=90x160:r=10:d=1", "-c:v", "mpeg4", str(good)],
                           check=True, timeout=30)
            report = media_stack.video_qa(good, require_audio=False)
            self.assertTrue(report["technical_pass"], report)
            self.assertFalse(report["publication_ready"])
            self.assertIsNone(report["editorial_score"])
            self.assertFalse(media_stack.video_qa(good)["technical_pass"])
            bad = Path(tmp) / "bad.mp4"
            bad.write_bytes(b"invalid")
            self.assertFalse(media_stack.video_qa(bad)["technical_pass"])


if __name__ == "__main__":
    unittest.main()
