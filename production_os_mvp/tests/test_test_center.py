import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_center as center

class TestCenterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = patch.object(center, "STORE", Path(tmp.name))
        active = patch.object(center, "ACTIVE", None)
        store.start()
        active.start()
        self.addCleanup(store.stop)
        self.addCleanup(active.stop)

    def test_invalid_domain_does_not_launch(self):
        with patch.object(center.threading, "Thread") as thread:
            with self.assertRaises(ValueError):
                center.submit("arbitrary-command")
            thread.assert_not_called()

    def test_run_records_actual_results_and_partial_status(self):
        record = {"id": "aabbccddeeff", "domain": "clipping", "status": "queued", "results": []}
        center.ACTIVE = record["id"]
        completed = Mock(returncode=0, stderr="", stdout=json.dumps({"status":"partial","total":4,"passed":2,"skipped":2,"failed":0,"log":"fixture"}))
        with patch.object(center.subprocess, "run", return_value=completed) as run:
            center.run(record)
        self.assertEqual(center.records()[0]["status"], "partial")
        self.assertEqual(center.records()[0]["results"][0]["passed"], 2)
        self.assertIsNone(center.ACTIVE)
        self.assertEqual(run.call_args.args[0][-2:], ["--worker", "clipping"])

    def test_timeout_is_not_reported_as_success(self):
        record = {"id": "aabbccddeeff", "domain": "kdp", "status": "queued", "results": []}
        with patch.object(center.subprocess, "run", side_effect=center.subprocess.TimeoutExpired("fixture", 1)):
            center.run(record)
        self.assertEqual(center.records()[0]["status"], "failed")
        self.assertEqual(center.records()[0]["results"][0]["status"], "timeout")

    def test_restart_marks_old_run_interrupted(self):
        center._save({"id":"aabbccddeeff","domain":"youtube","status":"running","results":[]})
        self.assertEqual(center.records()[0]["status"], "interrupted")

if __name__ == "__main__":
    unittest.main()
