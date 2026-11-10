"""Quota threshold regression tests: never confuse context usage with cloud quota."""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine_manager
import quota_monitor


class QuotaTests(unittest.TestCase):
    def tearDown(self):
        quota_monitor.invalidate_cache()

    def test_codex_minimum_of_available_windows_switches_at_five(self):
        now = time.time()
        info = quota_monitor.parse_codex_rate_limits({
            "rateLimits": {
                "primary": {"usedPercent": 95, "resetsAt": now + 3600},
                "secondary": {"usedPercent": 22, "resetsAt": now + 7200},
            }
        }, checked_at=now)
        self.assertEqual(info["remaining_percent"], 5)
        self.assertEqual(len(info["windows"]), 2)
        with mock.patch.object(quota_monitor, "get_quota", return_value=info):
            switch, _ = quota_monitor.quota_exhausted_soon("codex", 5)
        self.assertTrue(switch)

    def test_codex_ignores_expired_and_invalid_window(self):
        now = time.time()
        info = quota_monitor.parse_codex_rate_limits({
            "rateLimits": {
                "primary": {"usedPercent": 99, "resetsAt": now - 60},
                "secondary": {"usedPercent": 30, "resetsAt": now + 2000},
            }
        }, checked_at=now)
        self.assertEqual(info["remaining_percent"], 70)
        self.assertEqual(len(info["windows"]), 1)

    def test_codex_handles_nested_app_server_schema(self):
        now = time.time()
        info = quota_monitor.parse_codex_rate_limits({
            "rateLimits": {"primary": {"usedPercent": 5}},
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 97, "resetsAt": now + 600}},
                "codex_other": {"primary": {"usedPercent": 50, "resetsAt": now + 600}},
            },
        }, checked_at=now)
        self.assertEqual(info["remaining_percent"], 3)

    def test_antigravity_uses_quota_fraction_not_context_percentage(self):
        now = time.time()
        info = quota_monitor.parse_antigravity_status({
            "captured_at": now - 10,
            "model_id": "Gemini 4 Argon",
            "context_window": {"remaining_percentage": 1},
            "quota": {
                "gemini-weekly": {"remaining_fraction": 0.83, "reset_time": "2099-01-01T00:00:00Z"},
                "gpt-weekly": {"remaining_fraction": 0.02, "reset_time": "2099-01-01T00:00:00Z"},
            },
        }, checked_at=now)
        self.assertEqual(info["remaining_percent"], 83)
        self.assertEqual(len(info["windows"]), 1)

    def test_antigravity_switches_at_4_percent_but_not_on_stale_snapshot(self):
        now = time.time()
        payload = {
            "captured_at": now,
            "model_id": "Gemini 4 Argon",
            "quota": {"gemini-weekly": {"remaining_fraction": 0.04, "reset_time": "2099-01-01T00:00:00Z"}},
        }
        fresh = quota_monitor.parse_antigravity_status(payload, checked_at=now)
        self.assertEqual(fresh["remaining_percent"], 4)
        with mock.patch.object(quota_monitor, "get_quota", return_value=fresh):
            self.assertTrue(quota_monitor.quota_exhausted_soon("antigravity", 5)[0])
        payload["captured_at"] = now - 700
        stale = quota_monitor.parse_antigravity_status(payload, checked_at=now)
        self.assertIsNone(stale["remaining_percent"])

    def test_unknown_is_not_guessed_and_does_not_trigger_threshold(self):
        with mock.patch.object(quota_monitor, "get_quota", return_value={
            "provider": "codex", "fresh": False, "remaining_percent": None
        }):
            self.assertFalse(quota_monitor.quota_exhausted_soon("codex", 5)[0])
        self.assertIsNone(quota_monitor.get_quota("ollama")["remaining_percent"])

    def test_rejects_threshold_outside_allowed_range(self):
        with mock.patch.object(quota_monitor, "get_quota", return_value={"fresh": True, "remaining_percent": 3}):
            for invalid in (0, 51):
                with self.assertRaises(ValueError):
                    quota_monitor.quota_exhausted_soon("codex", invalid)

    def test_engine_settings_preserve_five_percent_default(self):
        with mock.patch.object(engine_manager, "settings_path", return_value=Path("/missing-production-os-quota-settings")):
            settings = engine_manager.get_settings()
        self.assertEqual(settings["switch_at_remaining_percent"], 5)

    def test_antigravity_cannot_infer_percent_from_remaining_amount(self):
        now = time.time()
        info = quota_monitor.parse_antigravity_status({
            "captured_at": now, "model_id": "Gemini 4",
            "quota": {"gemini-weekly": {"remaining_amount": 3}},
        }, checked_at=now)
        self.assertIsNone(info["remaining_percent"])


if __name__ == "__main__":
    unittest.main()
