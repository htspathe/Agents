import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stickman_worker as worker


class StickmanStateTests(unittest.TestCase):
    def test_retry_windows_permission_error_without_leaking_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "stickman_worker.json").write_text('{"state":"running"}', encoding="utf-8")
            real_replace = worker.os.replace
            calls = []
            def flaky_replace(source, target):
                calls.append(Path(source))
                if len(calls) == 1:
                    raise PermissionError("simulated Windows file lock")
                return real_replace(source, target)
            with patch.object(worker.os, "replace", side_effect=flaky_replace), patch.object(worker.time, "sleep"):
                worker._write_state(folder, {"state": "waiting_preview_approval"})
            self.assertEqual(worker._read_state(folder)["state"], "waiting_preview_approval")
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0].name, "stickman_worker.tmp")
            self.assertEqual(list(folder.glob("*.tmp")), [])

    def test_review_status_overrides_stale_metadata_error_only_with_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            worker._write_state(folder, {"state": "failed", "message": "WinError 5"})
            with patch.object(worker, "_folder", return_value=folder), patch.object(worker.engine, "get_job", return_value={"status": "awaiting_preview_approval"}):
                self.assertEqual(worker.get_status("0123456789ab")["state"], "failed")
                (folder / "stickman_preview.mp4").write_bytes(b"fixture")
                result = worker.get_status("0123456789ab")
                self.assertEqual(result["state"], "waiting_preview_approval")
                self.assertEqual(result["state_warning"], "WinError 5")

if __name__ == "__main__":
    unittest.main()
