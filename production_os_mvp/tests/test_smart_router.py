"""Regression tests for Production OS V0.5 routing; stdlib only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smart_router
import engine_manager


class SmartRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_file = smart_router.METRICS_FILE
        smart_router.METRICS_FILE = Path(self.tmp.name) / "metrics.json"

    def tearDown(self):
        smart_router.METRICS_FILE = self.original_file
        self.tmp.cleanup()

    def test_default_per_zone_and_task(self):
        s = {"selection_mode": "auto", "auto_fallback": True}
        self.assertEqual(smart_router.provider_order(s, "kdp", "content")[0], "antigravity")
        self.assertEqual(smart_router.provider_order(s, "youtube", "video")[0], "codex")
        self.assertEqual(smart_router.provider_order(s, "clipping", "clipping")[0], "codex")
        self.assertEqual(smart_router.provider_order(s, "clogz", "shopify")[0], "codex")

    def test_legacy_manual_order_and_disabled_fallback(self):
        s = {"primary": "codex", "fallbacks": ["antigravity", "ollama"], "auto_fallback": True}
        self.assertEqual(engine_manager.provider_order(s), ["codex", "antigravity", "ollama"])
        s["auto_fallback"] = False
        self.assertEqual(engine_manager.provider_order(s), ["codex"])

    def test_zone_and_task_overrides(self):
        s = {"selection_mode": "auto", "auto_fallback": True,
             "zone_priorities": {"kdp": ["ollama", "opencode", "codex", "antigravity"]},
             "task_priorities": {"kdp:creative": ["opencode", "ollama", "antigravity", "codex"]}}
        self.assertEqual(smart_router.provider_order(s, "kdp", "content")[0], "ollama")
        self.assertEqual(smart_router.provider_order(s, "kdp", "creative")[0], "opencode")

    def test_reject_invalid_rules_and_duplicates(self):
        for zones, tasks in [
            ({"unknown": ["codex"]}, {}),
            ({"kdp": ["codex", "codex"]}, {}),
            ({}, {"kdp:noagent": ["codex"]}),
            ({}, {"kdp:quality": ["bad"]}),
        ]:
            with self.assertRaises(ValueError):
                smart_router.validate_priorities(zones, tasks)

    def test_measured_performance_reorders_only_after_sufficient_samples(self):
        s = {"selection_mode": "auto", "auto_fallback": True, "performance_learning": True}
        self.assertEqual(smart_router.provider_order(s, "kdp", "content")[0], "antigravity")
        for _ in range(4):
            smart_router.record_attempt("kdp", "content", "antigravity", False, 150)
            smart_router.record_attempt("kdp", "content", "codex", True, 1)
        self.assertEqual(smart_router.provider_order(s, "kdp", "content")[0], "antigravity")
        smart_router.record_attempt("kdp", "content", "antigravity", False, 150)
        smart_router.record_attempt("kdp", "content", "codex", True, 1)
        self.assertEqual(smart_router.provider_order(s, "kdp", "content")[0], "codex")
        data = smart_router.read_metrics()
        self.assertEqual(data["kdp:content"]["codex"]["successes"], 5)

    def test_paid_opencode_rejected_without_authorization(self):
        settings = {"allow_paid_calls": False}
        self.assertTrue(engine_manager._model_allowed("ollama/qwen3:8b", settings))
        self.assertFalse(engine_manager._model_allowed("anthropic/claude-sonnet", settings))
        self.assertFalse(engine_manager._model_allowed("openai/gpt", settings))

    def test_opencode_is_read_only_and_parses_json_text_event(self):
        settings = {"opencode_model": "ollama/qwen3:8b", "allow_paid_calls": False}
        event = json.dumps({"type": "text", "part": {"type": "text", "text": "Réponse test"}})
        fake_result = mock.Mock(returncode=0, stdout=event + "\n", stderr="")
        with mock.patch.object(engine_manager, "find_opencode", return_value="/opt/opencode"), \
             mock.patch.object(engine_manager, "get_settings", return_value=settings), \
             mock.patch.object(engine_manager.subprocess, "run", return_value=fake_result) as run:
            answer = engine_manager._generate_opencode("Un test")
        self.assertEqual(answer, "Réponse test")
        args, kwargs = run.call_args
        self.assertIn("--agent", args[0])
        self.assertIn("plan", args[0])
        permissions = json.loads(kwargs["env"]["OPENCODE_PERMISSION"])
        self.assertEqual(permissions["edit"], "deny")
        self.assertEqual(permissions["bash"], "deny")

    def test_saved_settings_validate_and_keep_zero_budget(self):
        with mock.patch.object(engine_manager, "ROOT", Path(self.tmp.name)):
            settings = engine_manager.save_settings({
                "primary": "codex", "fallbacks": ["antigravity", "opencode", "ollama"],
                "selection_mode": "auto", "zone_priorities": {"kdp": ["antigravity", "codex"]},
                "task_priorities": {"kdp:quality": ["codex", "opencode"]},
            })
            self.assertIs(settings["allow_paid_calls"], False)
            self.assertEqual(engine_manager.get_settings()["zone_priorities"]["kdp"][0], "antigravity")


if __name__ == "__main__":
    unittest.main()
