"""Offline structural tests; the real renderer is exercised separately on Windows."""
import json
import os
import sys
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agents
import stickman_btb


class StickmanBridgeTests(unittest.TestCase):
    def test_workflow_registered_without_auto_publish(self):
        self.assertIn("brain_to_billions", agents.WORKFLOW_STEPS)
        self.assertIn("video", agents.WORKFLOW_STEPS["brain_to_billions"])
        self.assertEqual(len(agents.AGENTS), 12)

    def test_caption_timing_and_short_phrases(self):
        cues = stickman_btb.captions_for(
            "Comprendre une entreprise demande du temps et de la prudence. "
            "Les chiffres doivent être vérifiés avant chaque conclusion.", 15.5
        )
        self.assertTrue(cues)
        self.assertGreaterEqual(cues[0]["start"], 0)
        self.assertAlmostEqual(cues[-1]["end"], 15.42, places=2)
        for cue in cues:
            self.assertLessEqual(len(cue["text"].split()), 4)
            self.assertGreater(cue["end"], cue["start"])
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(cues, cues[1:])))

    def test_storyboard_both_native_ratios(self):
        for portrait in (True, False):
            shots = stickman_btb._storyboard(24.0, portrait)
            self.assertGreaterEqual(len(shots), 8)
            self.assertEqual(shots[0]["start"], 0)
            self.assertEqual(shots[-1]["end"], 24.0)
            self.assertTrue(all(a["end"] == b["start"] for a, b in zip(shots, shots[1:])))
            self.assertTrue(all(any(layer.get("type") == "pose" for layer in s["layers"]) for s in shots))

    def test_voice_profile_fixed_identity(self):
        profile = stickman_btb.load_voice_profile()
        self.assertEqual(profile["brand"], "Brain to Billions")
        self.assertEqual(profile["narrator_key"], "brain-to-billions-main")
        self.assertEqual(profile["voice_name"], "Confident Male — Sample 04")
        self.assertEqual(profile["provider"], "qwen3_tts_voice_clone")
        self.assertEqual(profile["voice_id"], "hangry-labs/confident_male/sample04")
        self.assertEqual(profile["languages"], {"fr": "French", "en": "English"})
        self.assertEqual(profile["approval_status"], "approved")

    def test_voice_profile_rejects_empty_identity(self):
        profile = stickman_btb.load_voice_profile()
        profile["voice_name"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "voice.json"
            config.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaises(ValueError):
                stickman_btb.load_voice_profile(config)

    def test_no_silent_override_with_random_wav(self):
        profile = stickman_btb.load_voice_profile()
        with patch.dict(os.environ, {"STICKMAN_VOICE_WAV": "other-speaker.wav"}):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "Voice Lock"):
                    stickman_btb._produce_narration(
                        "Un test de voix", Path(tmp), Path(tmp) / "narration.wav", profile
                    )

    def test_reference_hash_pinned_and_model_locked(self):
        profile = stickman_btb.load_voice_profile()
        self.assertEqual(profile["reference_sha256"], "107f67576b04f5c0b050327863c198eceb9bab297daf061e4138912595183368")
        self.assertEqual(profile["model_id"], "Qwen/Qwen3-TTS-12Hz-1.7B-Base")

    def test_unsupported_language_rejected_before_inference(self):
        profile = stickman_btb.load_voice_profile()
        from qwen_btb_voice import synthesize
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Langue non autorisée"):
                synthesize("Hello world. This is a test.", Path(tmp) / "test.wav", "es", profile)

    def test_missing_skill_is_explicit(self):
        self.assertTrue(callable(stickman_btb.find_skill))


if __name__ == "__main__":
    unittest.main()
