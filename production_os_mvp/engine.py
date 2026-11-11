"""Persistent orchestrator and task state with SQLite. No credentials needed."""
from __future__ import annotations
import json
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid
from datetime import datetime, timezone
from pathlib import Path
from agents import AGENTS, HANDLERS, LABELS, ROOT, WORKFLOW_STEPS, safe_text
import viral_storyboard

DB = ROOT / "production_os.sqlite3"
LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="production")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    cn = sqlite3.connect(DB, timeout=15)
    cn.row_factory = sqlite3.Row
    cn.execute("PRAGMA journal_mode=WAL")
    return cn


def init():
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "inbox").mkdir(exist_ok=True)
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, workflow TEXT NOT NULL,
          brief TEXT NOT NULL, input_json TEXT NOT NULL, status TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL, agent TEXT NOT NULL, state TEXT NOT NULL,
          message TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL,
          target_agent TEXT NOT NULL, created TEXT NOT NULL);
        """)
        db.commit()


def add_event(job_id: str, agent: str, state: str, message: str):
    with LOCK, connect() as db:
        db.execute("INSERT INTO events(job_id,agent,state,message,created) VALUES(?,?,?,?,?)",
                   (job_id, agent, state, message[:1100], now()))
        db.commit()


def set_status(job_id: str, status: str, error: str | None = None):
    with LOCK, connect() as db:
        db.execute("UPDATE jobs SET status=?,updated=?,error=? WHERE id=?", (status, now(), error, job_id))
        db.commit()


def new_job(data: dict) -> str:
    typ = str(data.get("workflow", ""))
    if typ not in WORKFLOW_STEPS:
        raise ValueError("Workflow inconnu")
    brief = safe_text(data.get("brief"), 800)
    if len(brief) < 4:
        raise ValueError("Brief requis (minimum 4 caractères)")
    if typ == "clipping" and not data.get("rights_confirmed"):
        raise ValueError("Confirmer les droits du fichier avant le clipping")
    status_count = stats()
    if status_count["running"] + status_count["queued"] >= 10:
        raise ValueError("File de missions pleine : maximum 10 en attente/en exécution")
    job_id = uuid.uuid4().hex[:12]
    start_agent = safe_text(data.get("start_agent"), 40) or "orchestrator"
    if start_agent != "orchestrator" and start_agent not in WORKFLOW_STEPS[typ]:
        raise ValueError("Agent de reprise incompatible avec ce workflow")
    revision_of = safe_text(data.get("revision_of"), 12)
    if revision_of:
        parent = get_job(revision_of)
        if not parent or parent["workflow"] != typ:
            raise ValueError("Mission source de la révision introuvable")
    stickman_enabled = typ == "youtube" and bool(data.get("stickman_enabled", True))
    stickman_mode = str(data.get("stickman_mode", "autopilot")).lower()
    stickman_style = str(data.get("stickman_style", "B1")).upper()
    aspect_ratio = str(data.get("aspect_ratio", "9:16"))
    viral_template = safe_text(data.get("viral_template"), 100)
    if viral_template:
        if typ != "youtube" or not stickman_enabled:
            raise ValueError("Les storyboards viraux nécessitent le workflow YouTube et Stickman.")
        selected = viral_storyboard.load_template(viral_template)
        aspect_ratio = "9:16"
    if typ == "youtube" and stickman_enabled:
        if stickman_mode not in {"autopilot", "director"}:
            raise ValueError("Mode Stickman invalide")
        if stickman_style not in {"B1", "B2", "B3", "B4", "B5", "A6"}:
            raise ValueError("Style Stickman invalide")
        if stickman_mode == "autopilot" and stickman_style == "A6":
            raise ValueError("A6 utilise le mode Director, pas Autopilot")
        if stickman_mode == "director" and not stickman_style.startswith("A"):
            raise ValueError("Director nécessite un style A")
        if aspect_ratio not in {"9:16", "16:9"}:
            raise ValueError("Format vidéo invalide")
    try:
        stickman_duration = int(data.get("stickman_duration", 60))
    except (TypeError, ValueError):
        raise ValueError("Durée Stickman invalide")
    if typ == "youtube" and stickman_enabled and not 15 <= stickman_duration <= 3600:
        raise ValueError("La durée Stickman doit être comprise entre 15 et 3600 secondes")
    if viral_template:
        stickman_duration = int(selected["metadata"]["duration_seconds"])
    clean = {"workflow": typ, "brief": brief, "source_file": safe_text(data.get("source_file"), 170),
             "rights_confirmed": bool(data.get("rights_confirmed")),
             "start": data.get("start", 0), "end": data.get("end", 10),
             "revision_of": revision_of, "correction_request": safe_text(data.get("correction_request"), 1000),
             "start_agent": start_agent, "stickman_enabled": stickman_enabled,
             "stickman_mode": stickman_mode, "stickman_style": stickman_style,
             "aspect_ratio": aspect_ratio, "stickman_duration": stickman_duration,
             "viral_template": viral_template,
             "language": "fr" if viral_template else (safe_text(data.get("language", "fr"), 12) or "fr"),
             "channel": safe_text(data.get("channel", "Brain to Billions"), 80) or "Brain to Billions"}
    with LOCK, connect() as db:
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
                   (job_id, typ, brief, json.dumps(clean), "queued", now(), now(), None))
        db.commit()
    add_event(job_id, "orchestrator", "queued", f"Mission créée : {LABELS[typ]}")
    return job_id


def run_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job or job["status"] != "queued":
        return
    ctx = json.loads(job["input_json"])
    ctx["job_id"] = job_id
    ctx["notes"] = []
    folder = ROOT / "outputs" / job_id
    folder.mkdir(exist_ok=True)
    if ctx.get("correction_request"):
        (folder / "correction_request.md").write_text(
            "Agent cible : " + str(ctx.get("start_agent", "orchestrator")) + "\n\n" +
            str(ctx["correction_request"]) + "\n",
            encoding="utf-8"
        )
        ctx["notes"].append("Consignes de correction transmises à l'agent cible.")
    set_status(job_id, "running")
    add_event(job_id, "orchestrator", "running", "Workflow démarré")
    try:
        steps = WORKFLOW_STEPS[job["workflow"]]
        start_agent = ctx.get("start_agent", "orchestrator")
        if start_agent != "orchestrator":
            steps = steps[steps.index(start_agent):]
        for agent in steps:
            add_event(job_id, agent, "running", "Exécution")
            artifact = HANDLERS[agent](ctx, folder)
            add_event(job_id, agent, "done", f"Livrable : {artifact}")
        (folder / "run_notes.json").write_text(json.dumps(ctx["notes"], ensure_ascii=False, indent=2), encoding="utf-8")
        stickman_active = job["workflow"] == "youtube" and ctx.get("stickman_enabled", True)
        video_reran = "video" in steps
        final_status = "awaiting_external" if stickman_active and video_reran else "awaiting_approval"
        set_status(job_id, final_status)
        final_assets_present = (
            (folder / "stickman_final.mp4").is_file()
            and any((folder / name).is_file() for name in ("stickman_subtitles.srt", "stickman_subtitles.vtt"))
        )
        if stickman_active and video_reran:
            manifest_status = "AWAITING_STICKMAN_RENDER"
        elif stickman_active and final_assets_present:
            manifest_status = "AWAITING_FINAL_APPROVAL"
        else:
            manifest_status = "AWAITING_MANUAL_APPROVAL"
        update_manifest(job_id, manifest_status, extra={"revision_of": ctx.get("revision_of")} if ctx.get("revision_of") else None)
        final_message = ("Passerelle Stickman prête ; le rendu externe doit être terminé avant revue finale."
                         if final_status == "awaiting_external"
                         else "Livrables prêts pour revue (aucune publication)")
        add_event(job_id, "orchestrator", final_status, final_message)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"[:500]
        set_status(job_id, "failed", msg)
        add_event(job_id, "orchestrator", "failed", msg)


def run_async(job_id: str):
    EXECUTOR.submit(run_job, job_id)


def get_job(job_id):
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit=100):
    with connect() as db:
        rows = db.execute("SELECT id,workflow,brief,status,created,updated,error FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]



ARTIFACT_EXTENSIONS = {
    ".json", ".md", ".txt", ".svg", ".mp4", ".mov", ".mkv", ".webm",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".csv",
    ".srt", ".vtt", ".mp3", ".wav", ".m4a", ".aac", ".docx",
    ".epub", ".xlsx", ".tsv", ".log", ".pptx"
}

ARTIFACT_KIND_BY_SUFFIX = {
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".gif": "image", ".svg": "image",
    ".md": "document", ".txt": "document", ".pdf": "document",
    ".docx": "document", ".epub": "document", ".srt": "document",
    ".vtt": "document", ".pptx": "document",
    ".json": "data", ".csv": "data", ".xlsx": "data", ".tsv": "data",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio",
    ".log": "other"
}


def list_library(query="", workflow="", kind="", status=""):
    """Return every mission and its generated artifacts for the central library."""
    needle = str(query or "").strip().casefold()
    workflow_filter = str(workflow or "").strip().casefold()
    kind_filter = str(kind or "").strip().casefold()
    status_filter = str(status or "").strip().casefold()
    items = []

    for job in list_jobs(1000):
        if workflow_filter and workflow_filter != "all" and job["workflow"].casefold() != workflow_filter:
            continue
        if status_filter and status_filter != "all" and job["status"].casefold() != status_filter:
            continue

        folder = (ROOT / "outputs" / job["id"]).resolve()
        output_root = (ROOT / "outputs").resolve()
        files = []
        if folder.parent == output_root and folder.is_dir():
            for path in sorted(folder.rglob("*"), key=lambda item: item.relative_to(folder).as_posix().casefold()):
                if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                    continue
                resolved = path.resolve()
                if folder not in resolved.parents:
                    continue
                suffix = path.suffix.casefold()
                if suffix not in ARTIFACT_EXTENSIONS:
                    continue
                try:
                    stat = resolved.stat()
                except OSError:
                    continue
                files.append({
                    "name": path.relative_to(folder).as_posix(),
                    "size": stat.st_size,
                    "extension": suffix,
                    "kind": ARTIFACT_KIND_BY_SUFFIX.get(suffix, "other")
                })

        job_text = " ".join((job["id"], job["workflow"], job["brief"])).casefold()
        job_matches_query = not needle or needle in job_text
        if needle and not job_matches_query:
            files = [item for item in files if needle in item["name"].casefold()]
            if not files:
                continue

        if kind_filter and kind_filter != "all":
            files = [item for item in files if item["kind"] == kind_filter]
            if not files:
                continue

        items.append({
            "id": job["id"],
            "workflow": job["workflow"],
            "workflow_label": LABELS.get(job["workflow"], job["workflow"].upper()),
            "brief": job["brief"],
            "status": job["status"],
            "created": job["created"],
            "updated": job["updated"],
            "error": job.get("error"),
            "files": files
        })

    file_count = sum(len(item["files"]) for item in items)
    total_bytes = sum(file["size"] for item in items for file in item["files"])
    return {
        "jobs": items,
        "stats": {
            "jobs": len(items),
            "files": file_count,
            "bytes": total_bytes,
            "active": sum(item["status"] in ("queued", "running") for item in items),
            "ready": sum(item["status"] == "awaiting_approval" for item in items)
        }
    }


def update_manifest(job_id, status, decision=None, extra=None):
    folder = (ROOT / "outputs" / job_id).resolve()
    output_root = (ROOT / "outputs").resolve()
    manifest_path = folder / "publishing_manifest.json"
    if folder.parent != output_root or not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = status
        manifest["auto_publish"] = False
        manifest["updated"] = now()
        if decision:
            manifest["human_decision"] = decision
        if extra:
            manifest.update(extra)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError):
        # SQLite remains the source of truth if a generated manifest cannot be updated.
        return

def decide_job(job_id, action, note="", target_agent="orchestrator"):
    actions = {"approve": "approved", "approve_preview": "render_authorized",
               "request_changes": "changes_requested", "reject": "rejected"}
    if action not in actions:
        raise ValueError("Décision inconnue")
    note = safe_text(note, 1000)
    if action in {"request_changes", "reject"} and len(note) < 4:
        raise ValueError("Ajoute un motif ou des consignes (4 caractères minimum)")
    job = get_job(job_id)
    if not job:
        raise ValueError("Mission introuvable")
    allowed_statuses = {
        "approve": {"awaiting_approval"},
        "approve_preview": {"awaiting_preview_approval"},
        "request_changes": {"awaiting_approval", "awaiting_preview_approval", "render_authorized", "changes_requested"},
        "reject": {"awaiting_approval", "awaiting_preview_approval", "render_authorized"}
    }
    if job["status"] not in allowed_statuses[action]:
        raise ValueError("Cette décision n'est pas autorisée pour le statut actuel de la mission")
    target_agent = safe_text(target_agent, 40) or "orchestrator"
    if action == "request_changes" and target_agent != "orchestrator" and target_agent not in WORKFLOW_STEPS[job["workflow"]]:
        raise ValueError("L'agent choisi ne participe pas à ce workflow")
    status = actions[action]
    with LOCK, connect() as db:
        current = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not current or current["status"] not in allowed_statuses[action]:
            raise ValueError("Le statut de la mission a changé ; actualise la page")
        db.execute("UPDATE jobs SET status=?,updated=?,error=NULL WHERE id=?", (status, now(), job_id))
        db.execute("INSERT INTO decisions(job_id,action,note,target_agent,created) VALUES(?,?,?,?,?)",
                   (job_id, action, note, target_agent, now()))
        message = {"approve": "Rendu final approuvé par revue humaine (aucune publication déclenchée).",
                   "approve_preview": "Aperçu Stickman approuvé ; le rendu complet est autorisé, pas la publication.",
                   "request_changes": f"Correction demandée à {target_agent}: {note}",
                   "reject": f"Livrable rejeté: {note}"}[action]
        db.execute("INSERT INTO events(job_id,agent,state,message,created) VALUES(?,?,?,?,?)",
                   (job_id, "human_review", status, message[:1100], now()))
        db.commit()
    manifest_states = {"approve": "APPROVED_MANUAL_REVIEW",
                       "approve_preview": "RENDER_AUTHORIZED",
                       "request_changes": "CHANGES_REQUESTED",
                       "reject": "REJECTED"}
    update_manifest(job_id, manifest_states[action], {
        "action": action, "note": note, "target_agent": target_agent, "created": now()
    })
    return {"id": job_id, "status": status, "action": action, "target_agent": target_agent, "note": note}


def revise_job(job_id):
    parent = get_job(job_id)
    if not parent or parent["status"] != "changes_requested":
        raise ValueError("Aucune correction en attente pour cette mission")
    with connect() as db:
        decision = db.execute(
            "SELECT note,target_agent FROM decisions WHERE job_id=? AND action='request_changes' ORDER BY id DESC LIMIT 1",
            (job_id,)
        ).fetchone()
    if not decision:
        raise ValueError("Consignes de correction introuvables")
    original = json.loads(parent["input_json"])
    data = {key: value for key, value in original.items()
            if key not in {"revision_of", "correction_request", "start_agent"}}
    data.update({"revision_of": job_id, "correction_request": decision["note"],
                 "start_agent": decision["target_agent"] or "orchestrator"})
    new_id = new_job(data)
    source = (ROOT / "outputs" / job_id).resolve()
    target = (ROOT / "outputs" / new_id).resolve()
    output_root = (ROOT / "outputs").resolve()
    if source.parent != output_root or target.parent != output_root:
        raise ValueError("Dossier de révision invalide")
    target.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        for item in source.rglob("*"):
            if item.is_file() and not item.is_symlink():
                resolved = item.resolve()
                if source not in resolved.parents:
                    continue
                destination = target / item.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, destination)
    workflow_steps = WORKFLOW_STEPS[parent["workflow"]]
    start_agent = data["start_agent"]
    remaining_steps = workflow_steps if start_agent == "orchestrator" else workflow_steps[workflow_steps.index(start_agent):]
    if "video" in remaining_steps:
        superseded_names = {"stickman_preview.mp4", "stickman_final.mp4", "stickman_subtitles.srt",
                            "stickman_subtitles.vtt", "preview.mp4", "final.mp4", "captions.srt", "captions.vtt"}
        for item in target.rglob("*"):
            if item.is_file() and not item.is_symlink() and item.name in superseded_names:
                item.unlink()
    manifest_path = target / "publishing_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("human_decision", None)
            manifest["status"] = "REVISION_QUEUED"
            manifest["auto_publish"] = False
            manifest["revision_of"] = job_id
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass
    add_event(new_id, "orchestrator", "revision", f"Révision de {job_id}, reprise à {data['start_agent']}.")
    return new_id


def job_detail(job_id):
    job = get_job(job_id)
    if not job:
        return None
    input_data = json.loads(job["input_json"])
    with connect() as db:
        rows = db.execute("SELECT agent,state,message,created FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        decisions = db.execute("SELECT action,note,target_agent,created FROM decisions WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    job["events"] = [dict(r) for r in rows]
    job["decisions"] = [dict(r) for r in decisions]
    job["workflow_agents"] = WORKFLOW_STEPS[job["workflow"]]
    job["revision_of"] = input_data.get("revision_of") or None
    job["correction_request"] = input_data.get("correction_request") or None
    job["start_agent"] = input_data.get("start_agent", "orchestrator")
    job["stickman_enabled"] = bool(input_data.get("stickman_enabled", job["workflow"] == "youtube"))
    job["stickman_mode"] = input_data.get("stickman_mode", "autopilot")
    job["stickman_style"] = input_data.get("stickman_style", "B1")
    job["aspect_ratio"] = input_data.get("aspect_ratio", "9:16")
    job["stickman_duration"] = input_data.get("stickman_duration", 60)
    job["viral_template"] = input_data.get("viral_template") or None
    folder = (ROOT / "outputs" / job_id).resolve()
    job["files"] = [
        {"name": p.relative_to(folder).as_posix(), "size": p.stat().st_size}
        for p in sorted(folder.rglob("*"), key=lambda item: item.relative_to(folder).as_posix().casefold())
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in ARTIFACT_EXTENSIONS and folder in p.resolve().parents
    ] if folder.is_dir() else []
    job.pop("input_json", None)
    return job


def stats():
    jobs = list_jobs(1000)
    return {"total": len(jobs), "queued": sum(x["status"] == "queued" for x in jobs),
            "running": sum(x["status"] == "running" for x in jobs),
            "awaiting_external": sum(x["status"] == "awaiting_external" for x in jobs),
            "awaiting_preview_approval": sum(x["status"] == "awaiting_preview_approval" for x in jobs),
            "render_authorized": sum(x["status"] == "render_authorized" for x in jobs),
            "awaiting_approval": sum(x["status"] == "awaiting_approval" for x in jobs),
            "approved": sum(x["status"] == "approved" for x in jobs),
            "changes_requested": sum(x["status"] == "changes_requested" for x in jobs),
            "rejected": sum(x["status"] == "rejected" for x in jobs),
            "failed": sum(x["status"] == "failed" for x in jobs),
            "by_workflow": {w: sum(x["workflow"] == w for x in jobs) for w in WORKFLOW_STEPS}}
