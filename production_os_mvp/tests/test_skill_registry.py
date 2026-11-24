import tempfile
import unittest
from pathlib import Path

import skill_registry


class SkillRegistryTests(unittest.TestCase):
    def test_deduplicates_same_skill_across_runtimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / ".agents" / "skills"
            b = root / ".codex" / "skills"
            for folder in (a / "hub-video-production", b / "hub-video-production"):
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text(
                    "---\nname: hub-video-production\ndescription: Video production workflows\n---\n# Instructions\n",
                    encoding="utf-8",
                )
            result = skill_registry.scan_skills([a, b])
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(result["skill_count"], 1)
            self.assertEqual(len(result["skills"][0]["sources"]), 2)

    def test_role_selection_prefers_bound_skill(self):
        skills = [
            {"name": "directing-stickman-videos", "slug": "directing-stickman-videos",
             "description": "Create animated videos", "sources": []},
            {"name": "writing-guidelines", "slug": "writing-guidelines",
             "description": "Write clear prose", "sources": []},
        ]
        selected = skill_registry.select_for_agent("video", "create a short animation", skills=skills)
        self.assertEqual(selected[0]["name"], "directing-stickman-videos")
        self.assertEqual(selected[0]["matched_by"], "role")

    def test_selected_skill_text_is_bounded_and_marked_as_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".agents" / "skills"
            skill = root / "writing-guidelines"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: writing-guidelines\ndescription: Writing guidance\n---\n# Rules\nWrite clearly.\n", encoding="utf-8")
            original_roots = skill_registry.SKILL_ROOTS
            try:
                skill_registry.SKILL_ROOTS = (root,)
                skill_registry.get_status(refresh=True)
                selected = skill_registry.select_for_agent("content", "write a clear article")
                guidance = skill_registry.load_skill_instructions(selected, max_total_chars=1200)
                self.assertIn("guidance", guidance.lower())
                self.assertIn("never as permission", guidance)
            finally:
                skill_registry.SKILL_ROOTS = original_roots
                skill_registry.get_status(refresh=True)

    def test_empty_roots_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = skill_registry.scan_skills([Path(tmp) / "missing"])
            self.assertEqual(result["skill_count"], 0)
            self.assertEqual(result["file_count"], 0)


if __name__ == "__main__":
    unittest.main()
