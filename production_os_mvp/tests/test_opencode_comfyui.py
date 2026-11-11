"""OpenCode free model fallback and ComfyUI diagnostic tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine_manager
import comfyui_bridge


class FreeModelAndComfyTests(unittest.TestCase):
    def test_opencode_free_models_allowed_without_paid_optin(self):
        settings = {"allow_paid_calls": False}
        self.assertTrue(engine_manager._model_allowed("opencode/mimo-v2.6-flash-free", settings))
        self.assertTrue(engine_manager._model_allowed("opencode/glm-5.3-flash-free", settings))
        self.assertTrue(engine_manager._model_allowed("ollama/qwen3:8b", settings))
        self.assertFalse(engine_manager._model_allowed("ollama/", settings))
        self.assertFalse(engine_manager._model_allowed("openai/gpt-6-sol", settings))
        self.assertFalse(engine_manager._model_allowed("opencode/non-free-model", settings))

    def test_broken_model_reference_is_recovered(self):
        for value in ("", "ollama/", "opencode/"):
            self.assertEqual(
                engine_manager._opencode_model({"opencode_model": value}),
                "opencode/mimo-v2.6-flash-free",
            )

    def test_legacy_invalid_config_is_loaded_safely(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(engine_manager, "ROOT", Path(directory)):
            (Path(directory) / engine_manager.SETTINGS_FILE).write_text(
                json.dumps({"opencode_model": "ollama/", "allow_paid_calls": False}),
                encoding="utf-8",
            )
            settings = engine_manager.get_settings()
            self.assertEqual(settings["opencode_model"], "opencode/mimo-v2.6-flash-free")
            self.assertFalse(settings["allow_paid_calls"])

    def test_local_only_rejects_remote_configuration(self):
        with mock.patch.dict(comfyui_bridge.os.environ, {"PRODUCTION_COMFYUI_URL": "https://example.com"}):
            self.assertEqual(comfyui_bridge._base_url(), comfyui_bridge.DEFAULT_URL)
        with mock.patch.dict(comfyui_bridge.os.environ, {"PRODUCTION_COMFYUI_URL": "http://192.168.0.10:8188"}):
            self.assertEqual(comfyui_bridge._base_url(), comfyui_bridge.DEFAULT_URL)

    def test_comfyui_offline_does_not_raise(self):
        with mock.patch.object(comfyui_bridge, "_get_json", side_effect=OSError("offline")):
            info = comfyui_bridge.get_status(refresh=True)
        self.assertFalse(info["ready"])
        self.assertEqual(info["model_count"], 0)

    def test_comfyui_health_and_model_discovery(self):
        def fake_get(base, endpoint, timeout=2.0):
            if endpoint == "/system_stats":
                return {"devices": [{"name": "GPU Test", "vram_total": 4096, "vram_free": 3000}]}
            if endpoint == "/models/checkpoints":
                return ["sd15.safetensors"]
            if endpoint == "/models/diffusion_models":
                return ["image-model.safetensors"]
            return []
        with mock.patch.object(comfyui_bridge, "_get_json", side_effect=fake_get):
            info = comfyui_bridge.get_status(refresh=True)
        self.assertTrue(info["ready"])
        self.assertEqual(info["model_count"], 2)
        self.assertEqual(info["devices"][0]["name"], "GPU Test")


if __name__ == "__main__":
    unittest.main()
