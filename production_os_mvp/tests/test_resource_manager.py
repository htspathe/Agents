import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import resource_manager


class ResourceManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="production-os-resources-")
        self.original_root = resource_manager.ROOT
        resource_manager.ROOT = Path(self.tmp.name)
        resource_manager.invalidate_status_cache()
        resource_manager._CPU_SAMPLE = None

    def tearDown(self):
        resource_manager.ROOT = self.original_root
        resource_manager.invalidate_status_cache()
        self.tmp.cleanup()

    def test_defaults_are_safe_for_four_gb_vram_laptop(self):
        settings = resource_manager.get_settings()
        self.assertTrue(settings["gpu_enabled"])
        self.assertTrue(settings["cpu_fallback"])
        self.assertEqual(settings["max_vram_percent"], 70)
        self.assertEqual(settings["min_free_vram_mb"], 768)
        self.assertEqual(settings["cpu_threads"], 6)

    def test_save_settings_persists_valid_values(self):
        settings = resource_manager.save_settings({
            "gpu_enabled": True,
            "cpu_fallback": True,
            "max_vram_percent": 75,
            "min_free_vram_mb": 1024,
            "cpu_threads": 4,
        })
        self.assertEqual(settings, resource_manager.get_settings())
        self.assertTrue(resource_manager.settings_path().is_file())

    def test_save_settings_rejects_unsafe_values(self):
        with self.assertRaises(ValueError):
            resource_manager.save_settings({
                "gpu_enabled": True,
                "cpu_fallback": True,
                "max_vram_percent": 99,
                "min_free_vram_mb": 768,
                "cpu_threads": 6,
            })
        with self.assertRaises(ValueError):
            resource_manager.save_settings({
                "gpu_enabled": True,
                "cpu_fallback": True,
                "max_vram_percent": 70,
                "min_free_vram_mb": 768,
                "cpu_threads": -1,
            })

    def test_nvenc_arguments_use_supported_preset_and_bitrate(self):
        args = resource_manager.video_encoder_args("clip", "h264_nvenc")
        self.assertEqual(args, ["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "4M"])

    def test_cpu_fallback_uses_configured_thread_count(self):
        resource_manager.save_settings({
            "gpu_enabled": False,
            "cpu_fallback": True,
            "max_vram_percent": 70,
            "min_free_vram_mb": 768,
            "cpu_threads": 4,
        })
        args = resource_manager.video_encoder_args("clip", "libx264")
        self.assertIn("libx264", args)
        self.assertIn("veryfast", args)
        self.assertEqual(args[args.index("-threads") + 1], "4")

    def test_nvenc_failure_retries_once_with_cpu(self):
        output = Path(self.tmp.name) / "render.mp4"
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if "h264_nvenc" in command:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="NVENC test failure")
            Path(command[-1]).write_bytes(b"test-mp4")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with mock.patch.object(resource_manager.shutil, "which", return_value="ffmpeg"):
            with mock.patch.object(resource_manager, "choose_video_encoder", return_value="h264_nvenc"):
                with mock.patch.object(resource_manager, "gpu_task_lock", return_value=nullcontext()):
                    with mock.patch.object(resource_manager.subprocess, "run", side_effect=fake_run):
                        result = resource_manager.run_video_encode(
                            ["-f", "lavfi", "-i", "color=c=black:s=64x64:r=1"],
                            output,
                            profile="clip",
                            timeout=10,
                        )
        self.assertEqual(result["encoder"], "libx264")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
