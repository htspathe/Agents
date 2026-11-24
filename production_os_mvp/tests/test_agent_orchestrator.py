import unittest
from unittest.mock import patch

import agent_orchestrator


class AgentOrchestratorTests(unittest.TestCase):
    def test_specialist_is_suggested_but_never_dispatched(self):
        skill = {"name": "hub-blender-artist", "slug": "hub-blender-artist", "description": "Create 3D models", "sources": []}
        selected = agent_orchestrator.select_specialists("Créer un personnage 3D avec Blender", skills=[skill], mcp_names=[])
        self.assertEqual(selected[0]["id"], "blender")
        self.assertFalse(selected[0]["dispatch_ready"])

    def test_youtube_plan_assigns_video_skills_and_keeps_side_effects_disabled(self):
        skill = {
            "name": "directing-stickman-videos",
            "slug": "directing-stickman-videos",
            "description": "Animated video workflow",
            "sources": [{"runtime": "codex", "path": "C:/skills/directing-stickman-videos/SKILL.md", "sha256": "x"}],
        }
        fake_skills = {"skills": [skill], "skill_count": 1, "file_count": 1, "missing_core_skills": []}
        fake_runtimes = {"agents": {}, "tools": {}, "summary": {"agents_detected": 0}}
        fake_mcp = {"server_count": 0, "names": [], "note": "test"}
        with patch.object(agent_orchestrator.skill_registry, "get_status", return_value=fake_skills), \
             patch.object(agent_orchestrator.runtime_registry, "get_status", return_value=fake_runtimes), \
             patch.object(agent_orchestrator.mcp_registry, "get_status", return_value=fake_mcp), \
             patch.object(agent_orchestrator, "_execution_mode", return_value={"mode": "test"}):
            plan = agent_orchestrator.build_plan("youtube", "make a short animated video")
        video = next(step for step in plan["steps"] if step["agent_id"] == "video")
        self.assertEqual(video["skills"][0]["name"], "directing-stickman-videos")
        self.assertFalse(plan["safety"]["paid_calls_allowed_by_this_plan"])
        self.assertFalse(plan["safety"]["external_publication"])


if __name__ == "__main__":
    unittest.main()
