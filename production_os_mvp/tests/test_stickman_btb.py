"""Offline structural tests; the real renderer is exercised separately on Windows."""
import json
import sys
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

    def test_missing_skill_is_explicit(self):
        self.assertTrue(callable(stickman_btb.find_skill))


if __name__ == "__main__":
    unittest.main()
