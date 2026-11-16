import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_studio as studio


class MediaStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.inbox = root / "inbox"
        self.inbox.mkdir()
        self.store = root / "store"
        self.store.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(studio, "STORE", self.store).start()
        patch.object(studio, "INBOX", self.inbox).start()
        patch.object(studio, "ACTIVE", set()).start()

    def test_invalid_and_empty_sources(self):
        for name in ["../test.mp4", "bad.mp4", "0123456789.mp4", None]:
            with self.assertRaises(ValueError):
                studio.source_file(name)
        file = self.inbox / "0123456789.mp4"
        file.touch()
        with self.assertRaises(ValueError):
            studio.source_file(file.name)
        file.write_bytes(b"source")
        self.assertEqual(studio.source_file(file.name), file.resolve())

    def test_acquisition_requires_rights_and_supported_host(self):
        for payload in [
            {"action": "acquire", "url": "https://www.youtube.com/watch?v=approved"},
            {"action": "acquire", "url": "https://127.0.0.1", "rights_confirmed": True},
            {"action": "acquire", "url": "https://youtube.com.evil.org", "rights_confirmed": True},
        ]:
            with self.assertRaises(ValueError):
                studio.submit(payload)

    def test_single_task_and_persistent_qa(self):
        source = self.inbox / "0123456789.mp4"
        source.write_bytes(b"source")
        payload = {"action": "qa", "source_file": source.name}
        with patch.object(studio.POOL, "submit") as submit:
            task = studio.submit(payload)
            self.assertEqual(studio.read_task(task["id"])["status"], "queued")
            with self.assertRaises(ValueError):
                studio.submit(payload)
            args = submit.call_args.args
        with patch.object(studio.media_stack, "video_qa", return_value={"technical_pass": False}):
            args[0](*args[1:])
        result = studio.read_task(task["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result["result"]["technical_pass"])
        self.assertFalse(result["publication_ready"])
        self.assertTrue(studio.result_file(task["id"], "qa_report.json").is_file())
        with self.assertRaises(ValueError):
            studio.result_file(task["id"], "task.json")
        self.assertFalse(studio.ACTIVE)

    def test_interrupted_task_is_not_resumed_automatically(self):
        task_id = "abcdefabcdef"
        folder = studio.folder_for(task_id)
        folder.mkdir()
        studio.save(folder, {"id": task_id, "status": "running", "files": []})
        self.assertEqual(studio.read_task(task_id)["status"], "interrupted")

    def test_render_needs_explicit_trust(self):
        with self.assertRaises(ValueError):
            studio.submit({"action": "render"})

if __name__ == "__main__":
    unittest.main()
