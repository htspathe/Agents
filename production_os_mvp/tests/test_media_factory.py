import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import media_factory


class MediaFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_db = media_factory.DB_PATH
        self.old_runtime = media_factory.RUNTIME
        self.old_output = media_factory.OUTPUT_DIR
        self.old_refs = media_factory.REFERENCE_DIR
        media_factory.RUNTIME = self.root / "runtime"
        media_factory.DB_PATH = media_factory.RUNTIME / "media.sqlite3"
        media_factory.OUTPUT_DIR = self.root / "outputs"
        media_factory.REFERENCE_DIR = self.root / "refs"
        media_factory._STATUS_CACHE = None
        media_factory._STATUS_CACHE_AT = 0

    def tearDown(self):
        media_factory.DB_PATH = self.old_db
        media_factory.RUNTIME = self.old_runtime
        media_factory.OUTPUT_DIR = self.old_output
        media_factory.REFERENCE_DIR = self.old_refs
        media_factory._STATUS_CACHE = None
        self.temp.cleanup()

    def test_paid_or_unlisted_model_is_blocked(self):
        with patch.dict(os.environ, {
            "FREELLMAPI_FREE_VIDEO_MODELS": "",
            "FREELLMAPI_API_KEY": "not-a-real-key",
        }, clear=False):
            with self.assertRaisesRegex(ValueError, "allowlist"):
                media_factory._validate_request({
                    "provider": "freellmapi", "model": "auto",
                    "prompt": "A cinematic city at sunrise", "duration": 6,
                    "aspect_ratio": "9:16",
                })

    def test_auto_requires_explicit_free_confirmation(self):
        with patch.dict(os.environ, {
            "FREELLMAPI_FREE_VIDEO_MODELS": "auto",
            "FREELLMAPI_VIDEO_AUTO_FREE_CONFIRMED": "0",
        }, clear=False):
            models, confirmed = media_factory._free_model_policy("freellmapi")
            self.assertEqual(models, [])
            self.assertFalse(confirmed)
        with patch.dict(os.environ, {
            "FREELLMAPI_FREE_VIDEO_MODELS": "auto",
            "FREELLMAPI_VIDEO_AUTO_FREE_CONFIRMED": "1",
        }, clear=False):
            models, confirmed = media_factory._free_model_policy("freellmapi")
            self.assertEqual(models, ["auto"])
            self.assertTrue(confirmed)

    def test_create_job_persists_queue_without_starting_provider(self):
        with patch.dict(os.environ, {
            "FREELLMAPI_FREE_VIDEO_MODELS": "video-model-free",
            "FREELLMAPI_API_KEY": "secret-test-key",
        }, clear=False), patch.object(media_factory, "provider_status", return_value={
            "providers": {"freellmapi": {"ready": True, "configured_models": ["video-model-free"]}}
        }):
            jobs = media_factory.create_jobs({
                "provider": "freellmapi", "model": "video-model-free",
                "prompt": "A cinematic city at sunrise", "duration": 6,
                "aspect_ratio": "9:16",
            }, 2)
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(item["status"] == "queued" for item in jobs))
        stored = media_factory.list_jobs(10)
        self.assertEqual(len(stored), 2)
        self.assertEqual({item["status"] for item in stored}, {"queued"})

    def test_reference_image_checks_real_file_signature(self):
        import io
        media_factory.REFERENCE_DIR.mkdir(parents=True)
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 40
        saved = media_factory.save_reference_image("ref.png", io.BytesIO(png), len(png))
        self.assertTrue((media_factory.REFERENCE_DIR / (saved["image_id"] + ".png")).is_file())
        with self.assertRaisesRegex(ValueError, "extension"):
            media_factory.save_reference_image("bad.png", io.BytesIO(b"not png data at all"), 19)

    def test_result_url_rejects_http_and_private_ip(self):
        with self.assertRaises(RuntimeError):
            media_factory._safe_result_url("http://example.com/video.mp4")
        with self.assertRaises(RuntimeError):
            media_factory._safe_result_url("https://127.0.0.1/video.mp4")
        self.assertEqual(
            media_factory._safe_result_url("https://cdn.example.com/video.mp4"),
            "https://cdn.example.com/video.mp4",
        )

    def test_mp4_binary_response_is_saved_with_provider_headers(self):
        class FakeResponse:
            def __init__(self):
                self.headers = {
                    "Content-Type": "video/mp4",
                    "X-Provider": "pollinations",
                    "X-Model": "video-test-model",
                }
                self.data = b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 1400)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size=-1):
                if size < 0:
                    size = len(self.data)
                chunk, self.data = self.data[:size], self.data[size:]
                return chunk
        target = self.root / "sample.mp4"
        fake = FakeResponse()
        with patch.object(media_factory, "urlopen", return_value=fake):
            result = media_factory._write_mp4_response(object(), target, 2)
        self.assertTrue(target.is_file())
        self.assertEqual(result["provider"], "pollinations")
        self.assertEqual(result["model"], "video-test-model")
        self.assertGreater(result["bytes"], 1024)

    def test_provider_status_never_returns_api_key(self):
        with patch.dict(os.environ, {
            "FREELLMAPI_API_KEY": "super-secret-key",
            "FREELLMAPI_FREE_VIDEO_MODELS": "auto",
            "FREELLMAPI_VIDEO_AUTO_FREE_CONFIRMED": "1",
            "AGNES_API_KEY": "",
            "AGNES_FREE_VIDEO_MODELS": "",
        }, clear=False), patch.object(media_factory, "_json_request", return_value=(
            {"data": [{"id": "pollinations-video-model"}]}, {}
        )):
            status = media_factory.provider_status(refresh=True)
        rendered = json.dumps(status)
        self.assertTrue(status["providers"]["freellmapi"]["ready"])
        self.assertNotIn("super-secret-key", rendered)
        self.assertFalse(status["paid_fallback"])


if __name__ == "__main__":
    unittest.main()
