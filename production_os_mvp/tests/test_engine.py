import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import engine
import agents
import stickman_bridge


class ProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="production-os-test-")
        cls.old = (engine.ROOT, engine.DB, agents.ROOT, app.ROOT)
        root = Path(cls.tmp.name)
        (root / "knowledge").mkdir()
        (root / "samples").mkdir()
        (root / "inbox").mkdir()
        (root / "outputs").mkdir()
        source = Path(__file__).resolve().parents[1]
        for file in (source / "knowledge").glob("*.md"):
            shutil.copy2(file, root / "knowledge" / file.name)
        shutil.copy2(source / "samples" / "clogz_catalog_demo.csv", root / "samples" / "clogz_catalog_demo.csv")
        engine.ROOT, agents.ROOT, app.ROOT = root, root, root
        engine.DB = root / "test.sqlite3"
        engine.init()

    @classmethod
    def tearDownClass(cls):
        engine.ROOT, engine.DB, agents.ROOT, app.ROOT = cls.old
        cls.tmp.cleanup()

    def launch(self, workflow, brief="Exemple de production contrôlée", **kwargs):
        job_id = engine.new_job({"workflow": workflow, "brief": brief, **kwargs})
        engine.run_job(job_id)
        return engine.job_detail(job_id)

    def test_twelve_roles_registered(self):
        self.assertEqual(len(agents.AGENTS), 12)
        self.assertEqual(set(agents.HANDLERS), set(i for i, *_ in agents.AGENTS) - {"orchestrator"})

    def test_clogz_campaign(self):
        task = self.launch("clogz", "Promouvoir des chaussures confortables")
        self.assertEqual(task["status"], "awaiting_approval")
        files = {f["name"] for f in task["files"]}
        self.assertTrue({"visual.svg", "customer_reply_draft.md", "shopify_offline_audit.json", "publishing_manifest.json", "qa_report.json"}.issubset(files))
        folder = engine.ROOT / "outputs" / task["id"]
        audit = json.loads((folder / "shopify_offline_audit.json").read_text())
        self.assertFalse(audit["live_shopify_connected"])
        self.assertFalse(json.loads((folder / "publishing_manifest.json").read_text())["auto_publish"])
        self.assertFalse(json.loads((folder / "qa_report.json").read_text())["publication_ready"])

    def test_kdp_outline_only(self):
        task = self.launch("kdp", "Livre sur les finances personnelles")
        self.assertEqual(task["status"], "awaiting_approval")
        folder = engine.ROOT / "outputs" / task["id"]
        data = json.loads((folder / "kdp_metadata_draft.json").read_text())
        self.assertEqual(data["amazon_submission"], "NOT_READY")

    def test_youtube_storyboard(self):
        task = self.launch("youtube", "Comprendre le DCF")
        self.assertEqual(task["status"], "awaiting_external")
        folder = engine.ROOT / "outputs" / task["id"]
        data = json.loads((folder / "storyboard.json").read_text())
        self.assertFalse(data["requires_original_footage"])
        self.assertEqual(data["type"], "stickman_handoff")
        self.assertTrue((folder / "stickman_request.md").is_file())
        self.assertTrue((folder / "stickman_job.json").is_file())
        self.assertTrue((folder / "stickman_runtime_status.json").is_file())


    def test_library_indexes_artifacts_and_filters(self):
        task = self.launch("youtube", "Comprendre le DCF et sa valorisation")
        library = engine.list_library()
        entry = next(item for item in library["jobs"] if item["id"] == task["id"])
        names = {item["name"] for item in entry["files"]}
        self.assertTrue({"research.md", "content.md", "storyboard.json", "visual.svg"}.issubset(names))
        self.assertGreaterEqual(library["stats"]["files"], len(names))

        documents = engine.list_library(workflow="youtube", kind="document")
        filtered = next(item for item in documents["jobs"] if item["id"] == task["id"])
        self.assertIn("content.md", {item["name"] for item in filtered["files"]})
        self.assertNotIn("storyboard.json", {item["name"] for item in filtered["files"]})

        searched = engine.list_library(query="Comprendre le DCF")
        self.assertIn(task["id"], {item["id"] for item in searched["jobs"]})

    def test_library_indexes_nested_stickman_outputs(self):
        task = self.launch("youtube", "Pourquoi les riches utilisent-ils la dette ?")
        nested = engine.ROOT / "outputs" / task["id"] / "stickman_project" / "output"
        nested.mkdir(parents=True)
        (nested / "preview.mp4").write_bytes(b"fake-preview")
        library = engine.list_library(workflow="youtube", kind="video")
        entry = next(item for item in library["jobs"] if item["id"] == task["id"])
        self.assertIn("stickman_project/output/preview.mp4", {item["name"] for item in entry["files"]})
        detail = engine.job_detail(task["id"])
        self.assertIn("stickman_project/output/preview.mp4", {item["name"] for item in detail["files"]})

    def test_human_approval_and_targeted_revision(self):
        task = self.launch("youtube", "Pourquoi les riches utilisent-ils la dette ?")
        nested = engine.ROOT / "outputs" / task["id"] / "stickman_project" / "output"
        nested.mkdir(parents=True)
        (nested / "approved-scene.png").write_bytes(b"scene-reference")
        (nested / "preview.mp4").write_bytes(b"first-preview")
        preview = stickman_bridge.mark_preview_ready(task["id"], "stickman_project/output/preview.mp4")
        self.assertEqual(preview["status"], "awaiting_preview_approval")
        decision = engine.decide_job(
            task["id"], "request_changes",
            "Renforcer l'accroche et vérifier l'exemple financier.",
            "video"
        )
        self.assertEqual(decision["status"], "changes_requested")

        revision_id = engine.revise_job(task["id"])
        revision_input = json.loads(engine.get_job(revision_id)["input_json"])
        self.assertEqual(revision_input["revision_of"], task["id"])
        self.assertEqual(revision_input["start_agent"], "video")
        self.assertIn("Renforcer l'accroche", revision_input["correction_request"])
        self.assertTrue((engine.ROOT / "outputs" / revision_id / "stickman_project" / "output" / "approved-scene.png").is_file())

        engine.run_job(revision_id)
        revision = engine.job_detail(revision_id)
        self.assertEqual(revision["status"], "awaiting_external")
        self.assertTrue(any(item["name"] == "stickman_request.md" for item in revision["files"]))

        revision_output = engine.ROOT / "outputs" / revision_id / "stickman_project" / "output"
        revision_output.mkdir(parents=True, exist_ok=True)
        (revision_output / "preview.mp4").write_bytes(b"revised-preview")
        stickman_bridge.mark_preview_ready(revision_id, "stickman_project/output/preview.mp4")
        preview_approved = engine.decide_job(revision_id, "approve_preview", "Aperçu corrigé accepté")
        self.assertEqual(preview_approved["status"], "render_authorized")

        (revision_output / "final.mp4").write_bytes(b"final-render")
        (revision_output / "captions.srt").write_text("1\\n00:00:00,000 --> 00:00:01,000\\nTexte validé\\n", encoding="utf-8")
        final_ready = stickman_bridge.mark_final_ready(
            revision_id, "stickman_project/output/final.mp4", "stickman_project/output/captions.srt"
        )
        self.assertEqual(final_ready["status"], "awaiting_approval")
        approved = engine.decide_job(revision_id, "approve", "Rendu final accepté")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(engine.get_job(revision_id)["status"], "approved")
        self.assertFalse(json.loads((engine.ROOT / "outputs" / revision_id / "publishing_manifest.json").read_text())["auto_publish"])

    def test_reject_requires_reason_and_is_terminal(self):
        task = self.launch("kdp", "Guide de budget personnel")
        with self.assertRaises(ValueError):
            engine.decide_job(task["id"], "reject", "non")
        result = engine.decide_job(task["id"], "reject", "Le contenu ne répond pas au brief.")
        self.assertEqual(result["status"], "rejected")
        with self.assertRaises(ValueError):
            engine.decide_job(task["id"], "approve", "")

    def test_invalid_stickman_mode_and_style(self):
        with self.assertRaises(ValueError):
            engine.new_job({"workflow": "youtube", "brief": "Test configuration Stickman", "stickman_mode": "autopilot", "stickman_style": "A6"})

    def test_invalid_workflow_and_no_rights(self):
        with self.assertRaises(ValueError):
            engine.new_job({"workflow": "unknown", "brief": "Une mission test"})
        with self.assertRaises(ValueError):
            engine.new_job({"workflow": "clipping", "brief": "Clip à extraire", "source_file": "source_demo.mp4"})

    @unittest.skipUnless(agents.ffmpeg_ready(), "FFmpeg/ffprobe non installés")
    def test_clipping_real_export(self):
        path = app.make_demo_video()
        self.assertTrue(path.exists())
        task = self.launch("clipping", "Extraire un Short original", source_file="source_demo.mp4",
                           rights_confirmed=True, start=1, end=4)
        self.assertEqual(task["status"], "awaiting_approval", task.get("error"))
        folder = engine.ROOT / "outputs" / task["id"]
        data = agents.probe(folder / "short_vertical.mp4")
        videos = [x for x in data["streams"] if x.get("codec_type") == "video"]
        self.assertEqual((videos[0]["width"], videos[0]["height"]), (540, 960))

    def test_traversal_is_blocked(self):
        task = self.launch("clipping", "Essai path traversal", source_file="../secrets.mp4",
                           rights_confirmed=True, start=0, end=1)
        self.assertEqual(task["status"], "failed")


if __name__ == "__main__":
    unittest.main()
