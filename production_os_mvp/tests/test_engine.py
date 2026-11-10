import json
import re
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import engine
import agents
import stickman_bridge
import stickman_worker
import engine_manager
import quota_monitor
import piper_tts
from unittest import mock


class ProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="production-os-test-")
        cls.old = (engine.ROOT, engine.DB, agents.ROOT, app.ROOT, engine_manager.ROOT, piper_tts.ROOT)
        root = Path(cls.tmp.name)
        (root / "knowledge").mkdir()
        (root / "samples").mkdir()
        (root / "inbox").mkdir()
        (root / "outputs").mkdir()
        source = Path(__file__).resolve().parents[1]
        for file in (source / "knowledge").glob("*.md"):
            shutil.copy2(file, root / "knowledge" / file.name)
        shutil.copy2(source / "samples" / "clogz_catalog_demo.csv", root / "samples" / "clogz_catalog_demo.csv")
        engine.ROOT, agents.ROOT, app.ROOT, engine_manager.ROOT, piper_tts.ROOT = root, root, root, root, root
        engine.DB = root / "test.sqlite3"
        engine_manager.invalidate_status_cache()
        piper_tts.invalidate_status_cache()
        engine.init()

    @classmethod
    def tearDownClass(cls):
        engine.ROOT, engine.DB, agents.ROOT, app.ROOT, engine_manager.ROOT, piper_tts.ROOT = cls.old
        engine_manager.invalidate_status_cache()
        piper_tts.invalidate_status_cache()
        cls.tmp.cleanup()

    def launch(self, workflow, brief="Exemple de production contrôlée", **kwargs):
        job_id = engine.new_job({"workflow": workflow, "brief": brief, **kwargs})
        if workflow == "youtube":
            with mock.patch.object(piper_tts, "get_status", return_value={
                "ready": False, "note": "Piper absent dans les tests unitaires"
            }):
                engine.run_job(job_id)
        else:
            engine.run_job(job_id)
        return engine.job_detail(job_id)


    def test_dashboard_javascript_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js non installé")
        source = Path(__file__).resolve().parents[1] / "index.html"
        html = source.read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
        self.assertTrue(scripts, "Aucun script JavaScript trouvé dans le dashboard")
        with tempfile.TemporaryDirectory(prefix="production-os-js-test-") as temp_dir:
            script = Path(temp_dir) / "dashboard.js"
            script.write_text("\n\n".join(scripts), encoding="utf-8")
            result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_piper_extracts_spoken_voiceover_section(self):
        source = (
            "# Script\n\n## Accroche\nUne question.\n\n"
            "## Voix off intégrale (à lire mot pour mot)\n"
            "Aujourd'hui, **nous expliquons** les intérêts composés.\n\n"
            "## Sources\nNe pas lire cette partie.\n"
        )
        self.assertEqual(
            piper_tts.extract_voiceover(source),
            "Aujourd'hui, nous expliquons les intérêts composés."
        )

    def test_piper_creates_clear_blocker_when_runtime_is_missing(self):
        folder = Path(engine.ROOT) / "outputs" / "piper-blocker"
        folder.mkdir(parents=True)
        (folder / "content.md").write_text(
            "## Voix off intégrale\nBienvenue sur Brain to Billions. Voici notre sujet.\n",
            encoding="utf-8"
        )
        with mock.patch.object(piper_tts, "get_settings", return_value={"voice": "fr_FR-siwis-medium"}), \
             mock.patch.object(piper_tts, "get_status", return_value={
                 "ready": False, "note": "Piper absent"
             }):
            result = piper_tts.prepare_job_narration(folder, {"stickman_duration": 60})
        self.assertFalse(result["ready"])
        self.assertTrue((folder / "narration.md").is_file())
        self.assertTrue((folder / "narration_blocker.md").is_file())
        self.assertFalse((folder / "narration.wav").exists())

    def test_piper_generates_and_stages_same_voice_wav(self):
        folder = Path(engine.ROOT) / "outputs" / "piper-ready"
        project = folder / "stickman_project"
        project.mkdir(parents=True)
        (folder / "content.md").write_text(
            "## Voix off intégrale\nBienvenue sur Brain to Billions. Nous parlons de création de richesse.\n",
            encoding="utf-8"
        )
        settings = {"voice": "fr_FR-siwis-medium", "sentence_silence": 0.18}
        status = {"ready": True, "python_path": "python.exe", "voice_dir": "C:/voices"}
        def fake_synthesis(text, output_path, selected):
            with wave.open(str(output_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(22050)
                audio.writeframes(b"\\x00\\x00" * 22050)
            return {"file": "narration.wav", "duration_seconds": 1.0, "sample_rate": 22050,
                    "size_bytes": output_path.stat().st_size, "voice": selected["voice"]}
        with mock.patch.object(piper_tts, "get_settings", return_value=settings), \
             mock.patch.object(piper_tts, "get_status", return_value=status), \
             mock.patch.object(piper_tts, "synthesize_to_wav", side_effect=fake_synthesis):
            result = piper_tts.prepare_job_narration(folder, {"stickman_duration": 60})
        staged = project / "assets" / "audio" / "narration.wav"
        self.assertTrue(result["ready"])
        self.assertEqual(result["voice"], "fr_FR-siwis-medium")
        self.assertTrue((folder / "narration.wav").is_file())
        self.assertTrue(staged.is_file())
        self.assertTrue((folder / "voice_attribution.md").is_file())
        metadata = json.loads((folder / "narration_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["voice_license"], "CC BY 4.0")

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
        self.assertTrue((folder / "narration.md").is_file())
        self.assertTrue((folder / "narration_blocker.md").is_file())
        self.assertFalse((folder / "narration.wav").exists())
        self.assertIn("Piper", (folder / "stickman_request.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            engine.decide_job(task["id"], "approve", "Approbation prématurée")


    def test_stickman_worker_can_be_started_from_dashboard(self):
        task = self.launch("youtube", "Comprendre la valorisation d'une entreprise")
        with mock.patch.object(piper_tts, "ensure_job_narration", return_value={"ready": True}), \
             mock.patch.object(engine_manager, "get_settings", return_value={
            "primary": "codex", "fallbacks": ["antigravity", "ollama"],
            "auto_fallback": True, "ollama_model": ""
        }), mock.patch.object(engine_manager, "get_engine_status", return_value={
            "codex": {"ready": True}, "antigravity": {"ready": False},
            "ollama": {"ready": False, "agent_ready": False}
        }), mock.patch.object(agents, "stickman_status", return_value={"runtime_ready": True}), \
             mock.patch.object(stickman_worker.subprocess, "Popen") as popen:
            popen.return_value.pid = __import__("os").getpid()
            result = stickman_worker.start_worker(task["id"], "start")
        self.assertEqual(result["state"], "starting")
        self.assertEqual(result["phase"], "start")
        command = popen.call_args.args[0]
        self.assertIn("run", command)
        self.assertIn(task["id"], command)
        state = stickman_worker.get_status(task["id"])
        self.assertEqual(state["state"], "starting")

    def test_stickman_worker_can_start_with_antigravity_when_codex_missing(self):
        task = self.launch("youtube", "Tester le moteur Gemini de secours")
        with mock.patch.object(piper_tts, "ensure_job_narration", return_value={"ready": True}), \
             mock.patch.object(engine_manager, "get_settings", return_value={
            "primary": "codex", "fallbacks": ["antigravity", "ollama"],
            "auto_fallback": True, "ollama_model": ""
        }), mock.patch.object(engine_manager, "get_engine_status", return_value={
            "codex": {"ready": False}, "antigravity": {"ready": True},
            "ollama": {"ready": False, "agent_ready": False}
        }), mock.patch.object(agents, "stickman_status", return_value={"runtime_ready": True}), \
             mock.patch.object(stickman_worker.subprocess, "Popen") as popen:
            popen.return_value.pid = __import__("os").getpid()
            result = stickman_worker.start_worker(task["id"], "start")
        self.assertEqual(result["state"], "starting")
        self.assertEqual(result["phase"], "start")

    def test_stickman_worker_codex_command_keeps_sandbox(self):
        args = stickman_worker.build_codex_args("start", Path(engine.ROOT))
        self.assertIn("--sandbox", args)
        self.assertIn("workspace-write", args)
        self.assertIn("--ask-for-approval", args)
        self.assertIn("never", args)
        self.assertNotIn("--yolo", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertEqual(args[-4:], ["exec", "--skip-git-repo-check", "--json", "-"])

    def test_engine_manager_default_chain_and_manual_mode(self):
        defaults = {
            "primary": "codex", "fallbacks": ["antigravity", "ollama"],
            "auto_fallback": True, "ollama_model": ""
        }
        self.assertEqual(engine_manager.provider_order(defaults), ["codex", "antigravity", "ollama"])
        manual = {**defaults, "primary": "antigravity", "auto_fallback": False}
        self.assertEqual(engine_manager.provider_order(manual), ["antigravity"])

    def test_engine_manager_saves_non_secret_settings_and_deduplicates(self):
        settings = engine_manager.save_settings({
            "primary": "antigravity",
            "fallbacks": ["codex", "codex", "antigravity", "ollama"],
            "auto_fallback": True,
            "ollama_model": "qwen3:8b"
        })
        self.assertEqual(settings["fallbacks"], ["codex", "ollama"])
        self.assertEqual(settings["ollama_model"], "qwen3:8b")
        self.assertTrue(engine_manager.settings_path().is_file())
        with self.assertRaises(ValueError):
            engine_manager.save_settings({"primary": "unknown", "fallbacks": []})

    def test_engine_manager_detects_quota_and_service_errors(self):
        self.assertTrue(engine_manager.should_fallback("HTTP 429: resource exhausted"))
        self.assertTrue(engine_manager.should_fallback("authentication required"))
        self.assertFalse(engine_manager.should_fallback("The output file was not created"))

    def test_engine_manager_text_generation_falls_back_to_antigravity(self):
        settings = {
            "primary": "codex", "fallbacks": ["antigravity", "ollama"],
            "auto_fallback": True, "ollama_model": ""
        }
        status = {
            "codex": {"ready": True}, "antigravity": {"ready": True},
            "ollama": {"ready": False}
        }
        ctx = {"notes": []}
        with mock.patch.object(engine_manager, "get_settings", return_value=settings), \
             mock.patch.object(engine_manager, "get_engine_status", return_value=status), \
             mock.patch.object(engine_manager, "_generate_codex", side_effect=engine_manager.ProviderError("codex", "429 quota exhausted")), \
             mock.patch.object(engine_manager, "_generate_antigravity", return_value="Texte généré par Gemini"):
            answer = engine_manager.generate_text("Rédige un paragraphe", "gabarit", ctx)
        self.assertEqual(answer, "Texte généré par Gemini")
        self.assertTrue(any("antigravity" in note for note in ctx["notes"]))

    def test_worker_ollama_uses_local_codex_harness(self):
        args = stickman_worker.build_codex_args(
            "start", Path(engine.ROOT), provider="ollama", model="qwen3:8b"
        )
        self.assertIn("--oss", args)
        self.assertIn("--local-provider", args)
        self.assertIn("ollama", args)
        self.assertIn("qwen3:8b", args)

    def test_worker_codex_exec_flags_follow_exec_subcommand(self):
        for phase, session_id in [
            ("start", None),
            ("resume", "123e4567-e89b-12d3-a456-426614174000"),
        ]:
            args = stickman_worker.build_codex_args(
                phase, Path(engine.ROOT), session_id=session_id
            )
            self.assertLess(args.index("exec"), args.index("--skip-git-repo-check"))
            self.assertNotIn("--skip-git-repo-check", args[:args.index("exec")])
            self.assertIn("--json", args)

    def test_engine_manager_codex_exec_flags_follow_exec_subcommand(self):
        args = engine_manager.build_codex_text_args(Path(engine.ROOT) / "answer.md")
        self.assertLess(args.index("exec"), args.index("--skip-git-repo-check"))
        self.assertLess(args.index("exec"), args.index("--output-last-message"))
        self.assertNotIn("--skip-git-repo-check", args[:args.index("exec")])
        self.assertIn("--json", args)

    def test_stickman_worker_falls_back_from_codex_quota_to_antigravity(self):
        task = self.launch("youtube", "Test bascule Codex vers Gemini")
        settings = {
            "primary": "codex", "fallbacks": ["antigravity", "ollama"],
            "auto_fallback": True, "ollama_model": "", "allow_gemini_api_key": False
        }
        status = {
            "codex": {"ready": True},
            "antigravity": {"ready": True},
            "ollama": {"ready": False, "agent_ready": False}
        }
        calls = []

        def fake_provider(provider, job_id, folder, phase, session_id, fallback_from=None):
            calls.append((provider, phase, fallback_from))
            if provider == "codex":
                return 1, None, "HTTP 429: resource exhausted; quota exceeded"
            preview = folder / "stickman_preview.mp4"
            preview.write_bytes(b"preview")
            stickman_bridge.mark_preview_ready(job_id, "stickman_preview.mp4")
            return 0, "123e4567-e89b-12d3-a456-426614174000", '{"event":"init"}'

        with mock.patch.object(piper_tts, "ensure_job_narration", return_value={"ready": True}), \
             mock.patch.object(engine_manager, "get_settings", return_value=settings), \
             mock.patch.object(engine_manager, "get_engine_status", return_value=status), \
             mock.patch.object(stickman_worker, "_run_provider", side_effect=fake_provider):
            result = stickman_worker.run_worker(task["id"], "start")

        self.assertEqual(result, 0, stickman_worker.get_status(task["id"]))
        self.assertEqual([call[0] for call in calls], ["codex", "antigravity"])
        self.assertEqual(calls[1][2], "codex")
        worker = stickman_worker.get_status(task["id"])
        self.assertEqual(worker["state"], "waiting_preview_approval")
        self.assertEqual(worker["provider"], "antigravity")
        self.assertTrue(any(item["provider"] == "codex" for item in worker["fallback_history"]))

    def test_stickman_worker_skips_codex_before_call_at_five_percent(self):
        task = self.launch("youtube", "Test quota cinq pour cent")
        settings = {
            "primary": "codex", "fallbacks": ["antigravity", "ollama"],
            "auto_fallback": True, "ollama_model": "",
            "switch_at_remaining_percent": 5, "allow_gemini_api_key": False
        }
        status = {
            "codex": {"ready": True}, "antigravity": {"ready": True},
            "ollama": {"ready": False, "agent_ready": False}
        }
        called = []
        quota_calls = []

        def fake_quota(provider, threshold):
            quota_calls.append((provider, threshold))
            if provider == "codex":
                return True, {"provider": provider, "fresh": True, "remaining_percent": 5}
            return False, {"provider": provider, "fresh": False, "remaining_percent": None}

        def fake_run(provider, job_id, folder, phase, session_id, fallback_from=None):
            called.append((provider, fallback_from))
            (folder / "stickman_preview.mp4").write_bytes(b"real-preview-mock")
            stickman_bridge.mark_preview_ready(job_id, "stickman_preview.mp4")
            return 0, "123e4567-e89b-12d3-a456-426614174000", "{}"

        with mock.patch.object(piper_tts, "ensure_job_narration", return_value={"ready": True}), \
             mock.patch.object(engine_manager, "get_settings", return_value=settings), \
             mock.patch.object(engine_manager, "get_engine_status", return_value=status), \
             mock.patch.object(quota_monitor, "quota_exhausted_soon", side_effect=fake_quota), \
             mock.patch.object(stickman_worker, "_run_provider", side_effect=fake_run):
            result = stickman_worker.run_worker(task["id"], "start")

        self.assertEqual(result, 0, stickman_worker.get_status(task["id"]))
        self.assertEqual(called, [("antigravity", "codex")])
        self.assertIn(("codex", 5), quota_calls)
        worker = stickman_worker.get_status(task["id"])
        self.assertEqual(worker["state"], "waiting_preview_approval")
        self.assertTrue(any(x["state"] == "low_quota" for x in worker["fallback_history"]))

    def test_text_generation_skips_codex_at_five_percent(self):
        settings = {
            "primary": "codex", "fallbacks": ["antigravity"],
            "auto_fallback": True, "ollama_model": "", "switch_at_remaining_percent": 5
        }
        status = {"codex": {"ready": True}, "antigravity": {"ready": True}}
        with mock.patch.object(engine_manager, "get_settings", return_value=settings), \
             mock.patch.object(engine_manager, "get_engine_status", return_value=status), \
             mock.patch.object(quota_monitor, "quota_exhausted_soon", side_effect=[
                 (True, {"remaining_percent": 4}),
                 (False, {"remaining_percent": 90}),
             ]), \
             mock.patch.object(engine_manager, "_generate_codex") as codex, \
             mock.patch.object(engine_manager, "_generate_antigravity", return_value="Texte Gemini"):
            result = engine_manager.generate_text("Test", "Fallback", {"notes": []})
        self.assertEqual(result, "Texte Gemini")
        codex.assert_not_called()

    def test_worker_antigravity_uses_headless_sandbox_without_permission_bypass(self):
        args = stickman_worker.build_antigravity_args("start", "Créer un aperçu")
        self.assertIn("--output-format", args)
        self.assertIn("stream-json", args)
        self.assertIn("--mode=auto", args)
        self.assertIn("--sandbox", args)
        self.assertNotIn("--dangerously-skip-permissions", args)

    def test_stickman_worker_rejects_director_route(self):
        task = self.launch("youtube", "Test route Director", stickman_mode="director", stickman_style="A6")
        with mock.patch.object(stickman_worker, "codex_status", return_value={
            "installed": True, "path": "codex.exe", "version": "test"
        }), mock.patch.object(agents, "stickman_status", return_value={"runtime_ready": True}):
            with self.assertRaisesRegex(ValueError, "Autopilot uniquement"):
                stickman_worker.start_worker(task["id"], "start")

    def test_stickman_worker_resume_requires_preview_approval(self):
        task = self.launch("youtube", "Test verrou d'approbation")
        with mock.patch.object(stickman_worker, "codex_status", return_value={
            "installed": True, "path": "codex.exe", "version": "test"
        }), mock.patch.object(agents, "stickman_status", return_value={"runtime_ready": True}):
            with self.assertRaisesRegex(ValueError, "statut actuel awaiting_external"):
                stickman_worker.start_worker(task["id"], "resume")

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
        self.assertEqual(json.loads((engine.ROOT / "outputs" / task["id"] / "publishing_manifest.json").read_text())["status"], "AWAITING_PREVIEW_APPROVAL")
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
        self.assertFalse((engine.ROOT / "outputs" / revision_id / "stickman_project" / "output" / "preview.mp4").exists())
        self.assertFalse((engine.ROOT / "outputs" / revision_id / "stickman_preview.mp4").exists())

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
        self.assertEqual(json.loads((engine.ROOT / "outputs" / revision_id / "publishing_manifest.json").read_text())["status"], "RENDER_AUTHORIZED")

        (revision_output / "final.mp4").write_bytes(b"final-render")
        (revision_output / "captions.srt").write_text("1\\n00:00:00,000 --> 00:00:01,000\\nTexte validé\\n", encoding="utf-8")
        final_ready = stickman_bridge.mark_final_ready(
            revision_id, "stickman_project/output/final.mp4", "stickman_project/output/captions.srt"
        )
        self.assertEqual(final_ready["status"], "awaiting_approval")
        final_qa = json.loads((engine.ROOT / "outputs" / revision_id / "qa_report.json").read_text())
        self.assertTrue(final_qa["stickman"]["final_video_received"])
        approved = engine.decide_job(revision_id, "approve", "Rendu final accepté")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(engine.get_job(revision_id)["status"], "approved")
        final_manifest = json.loads((engine.ROOT / "outputs" / revision_id / "publishing_manifest.json").read_text())
        self.assertEqual(final_manifest["status"], "APPROVED_MANUAL_REVIEW")
        self.assertFalse(final_manifest["auto_publish"])


    def test_downstream_revision_keeps_final_video_for_final_review(self):
        task = self.launch("youtube", "Expliquer le coût réel d'un crédit")
        output = engine.ROOT / "outputs" / task["id"] / "stickman_project" / "output"
        output.mkdir(parents=True)
        (output / "preview.mp4").write_bytes(b"preview")
        stickman_bridge.mark_preview_ready(task["id"], "stickman_project/output/preview.mp4")
        engine.decide_job(task["id"], "approve_preview", "Aperçu accepté")

        (output / "final.mp4").write_bytes(b"final")
        (output / "captions.srt").write_text("captions", encoding="utf-8")
        stickman_bridge.mark_final_ready(task["id"], "stickman_project/output/final.mp4", "stickman_project/output/captions.srt")

        engine.decide_job(task["id"], "request_changes", "Corriger le rapport QA, pas la vidéo.", "quality")
        revision_id = engine.revise_job(task["id"])
        engine.run_job(revision_id)

        revision = engine.job_detail(revision_id)
        self.assertEqual(revision["status"], "awaiting_approval")
        self.assertTrue((engine.ROOT / "outputs" / revision_id / "stickman_final.mp4").is_file())
        manifest = json.loads((engine.ROOT / "outputs" / revision_id / "publishing_manifest.json").read_text())
        self.assertEqual(manifest["status"], "AWAITING_FINAL_APPROVAL")

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
