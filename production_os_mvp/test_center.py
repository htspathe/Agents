"""Production OS acceptance-test center: fixed suites, isolated child process, no publishing."""
from __future__ import annotations

import io
import json
from pathlib import Path
import os
import subprocess
import sys
import threading
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
STORE = ROOT / ".production_os_runtime" / "test_center"
LOCK = threading.RLock()
ACTIVE = None
ENGINE = "test_engine.ProductionTests."
MEDIA = "test_media_stack.MediaStackTests."
STUDIO = "test_media_studio.MediaStudioTests."
DOMAINS = {
    "clipping": {"label": "Clipping", "scope": "Export réel d'une mire vidéo locale (si FFmpeg présent), décodage MP4, sécurité des chemins et droits.", "tests": [ENGINE+"test_clipping_real_export", MEDIA+"test_real_vertical_media_and_corrupt_file", ENGINE+"test_traversal_is_blocked", ENGINE+"test_invalid_workflow_and_no_rights"]},
    "youtube": {"label": "YouTube Automation", "scope": "Storyboard, transfert Stickman, reprise d'aperçu simulée, autorisation du rendu et blocage des appels payants. Pas de narration ou de vidéo finale générée.", "tests": [ENGINE+"test_youtube_storyboard", ENGINE+"test_dashboard_start_action_recovers_existing_preview_without_provider", ENGINE+"test_stickman_worker_resume_requires_preview_approval", ENGINE+"test_voice_studio_cannot_call_elevenlabs_without_explicit_consent"]},
    "kdp": {"label": "KDP Publishing", "scope": "Création du plan et métadonnées de brouillon ; ne valide pas un livre complet ou un PDF imprimable.", "tests": [ENGINE+"test_kdp_outline_only"]},
    "clogz": {"label": "CLOGZ Commerce", "scope": "Campagne, maquette et support client sur catalogue fictif local ; aucune modification Shopify.", "tests": [ENGINE+"test_clogz_campaign"]},
    "media": {"label": "Media Studio", "scope": "Création de rapports, droits, chemins, états des traitements et décodage vidéo avec FFmpeg si disponible.", "tests": [MEDIA+"test_real_vertical_media_and_corrupt_file", MEDIA+"test_failed_probe_cannot_pass", MEDIA+"test_rights_confirmation_precedes_download", STUDIO+"test_single_task_and_persistent_qa", STUDIO+"test_render_needs_explicit_trust", STUDIO+"test_invalid_and_empty_sources", STUDIO+"test_shared_youtube_links_normalize"]},
}
TIMEOUT_SECONDS = 240

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _save(record):
    STORE.mkdir(parents=True, exist_ok=True)
    path = STORE / (record["id"] + ".json")
    temp = path.with_suffix(".tmp")
    with LOCK:
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

def records():
    with LOCK:
        if not STORE.is_dir():
            return []
        paths = sorted(STORE.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for path in paths[:30]:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status") in {"queued", "running"} and record.get("id") != ACTIVE:
                    record.update(status="interrupted", ended=now(), error="Serveur redémarré pendant le test.")
                    _save(record)
                result.append(record)
            except (OSError, ValueError, KeyError):
                continue
        return result

def status():
    return {"domains": [{"id": key, "label": v["label"], "scope": v["scope"], "test_count": len(v["tests"])}
                        for key, v in DOMAINS.items()],
            "active": ACTIVE, "runs": records(), "production_validated": False}

def submit(domain):
    global ACTIVE
    if not isinstance(domain, str) or domain not in {*DOMAINS, "all"}:
        raise ValueError("Domaine de test inconnu")
    with LOCK:
        if ACTIVE:
            raise ValueError("Un test est déjà en cours")
        # Do not start a test while local media or core jobs are running.
        import engine
        import media_studio
        stats = engine.stats()
        if stats.get("running", 0) or stats.get("queued", 0) or media_studio.ACTIVE:
            raise ValueError("Attends la fin des productions en cours avant les tests.")
        record = {"id": uuid.uuid4().hex[:12], "domain": domain, "status": "queued",
                  "created": now(), "results": [], "production_validated": False}
        ACTIVE = record["id"]
        try:
            _save(record)
            threading.Thread(target=run, args=(record,), daemon=True, name="production-test-center").start()
        except BaseException:
            ACTIVE = None
            raise
        return dict(record)

def run(record):
    global ACTIVE
    try:
        record.update(status="running", started=now())
        _save(record)
        for key in (list(DOMAINS) if record["domain"] == "all" else [record["domain"]]):
            try:
                # Fixed executable and allowlisted domain, never arbitrary shell commands.
                proc = subprocess.run([sys.executable, str(ROOT / "test_center.py"), "--worker", key],
                                      cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=TIMEOUT_SECONDS, check=False,
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                data = json.loads(proc.stdout)
                if proc.returncode != 0:
                    data["status"] = "failed"
                data["stderr"] = proc.stderr[-2000:]
            except subprocess.TimeoutExpired:
                data = {"status": "timeout", "error": "Test interrompu après 240 secondes.", "passed": 0, "skipped": 0, "failed": 0}
            except (OSError, ValueError, TypeError) as exc:
                data = {"status": "failed", "error": str(exc), "passed": 0, "skipped": 0, "failed": 0}
            record["results"].append({"domain": key, "label": DOMAINS[key]["label"], "scope": DOMAINS[key]["scope"], **data})
            _save(record)
        statuses = {r["status"] for r in record["results"]}
        final = "failed" if statuses & {"failed", "timeout"} else ("partial" if "partial" in statuses else "passed")
        record.update(status=final, ended=now())
    except Exception as exc:
        record.update(status="failed", error=str(exc)[:400], ended=now())
    finally:
        with LOCK:
            try:
                _save(record)
            finally:
                ACTIVE = None

def worker(domain):
    """Run existing controlled fixtures; disable provider text inference in this process."""
    import agents
    import engine_manager
    sys.path.insert(0, str(ROOT / "tests"))
    tests = unittest.defaultTestLoader.loadTestsFromNames(DOMAINS[domain]["tests"])
    stream = io.StringIO()
    def local_text(prompt, fallback, ctx):
        ctx.setdefault("notes", []).append("Test isolé : gabarit local, aucun fournisseur appelé.")
        return fallback
    with patch.object(engine_manager, "generate_text", side_effect=local_text):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(tests)
    failures = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
    skipped = len(result.skipped) + len(result.expectedFailures)
    passed = max(0, result.testsRun - failures - skipped)
    status = "failed" if not result.wasSuccessful() or result.testsRun == 0 else ("partial" if skipped else "passed")
    print(json.dumps({"status": status, "total": result.testsRun, "passed": passed,
                      "failed": failures, "skipped": skipped, "log": stream.getvalue()[-12000:]}, ensure_ascii=False))
    return 0 if result.wasSuccessful() and result.testsRun else 1

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker" and sys.argv[2] in DOMAINS:
        raise SystemExit(worker(sys.argv[2]))
    raise SystemExit("Usage: test_center.py --worker <known-domain>")
