"""Persistent orchestrator and task state with SQLite. No credentials needed."""
from __future__ import annotations
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid
from datetime import datetime, timezone
from pathlib import Path
from agents import AGENTS, HANDLERS, LABELS, ROOT, WORKFLOW_STEPS, safe_text

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
    clean = {"workflow": typ, "brief": brief, "source_file": safe_text(data.get("source_file"), 170),
             "rights_confirmed": bool(data.get("rights_confirmed")),
             "start": data.get("start", 0), "end": data.get("end", 10)}
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
    ctx["notes"] = []
    folder = ROOT / "outputs" / job_id
    folder.mkdir(exist_ok=True)
    set_status(job_id, "running")
    add_event(job_id, "orchestrator", "running", "Workflow démarré")
    try:
        for agent in WORKFLOW_STEPS[job["workflow"]]:
            add_event(job_id, agent, "running", "Exécution")
            artifact = HANDLERS[agent](ctx, folder)
            add_event(job_id, agent, "done", f"Livrable : {artifact}")
        (folder / "run_notes.json").write_text(json.dumps(ctx["notes"], ensure_ascii=False, indent=2), encoding="utf-8")
        set_status(job_id, "awaiting_approval")
        add_event(job_id, "orchestrator", "awaiting_approval", "Livrables prêts pour revue (aucune publication)")
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


def job_detail(job_id):
    job = get_job(job_id)
    if not job:
        return None
    with connect() as db:
        rows = db.execute("SELECT agent,state,message,created FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    job["events"] = [dict(r) for r in rows]
    folder = ROOT / "outputs" / job_id
    job["files"] = [{"name": p.name, "size": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()] if folder.is_dir() else []
    job.pop("input_json", None)
    return job


def stats():
    jobs = list_jobs(1000)
    return {"total": len(jobs), "queued": sum(x["status"] == "queued" for x in jobs),
            "running": sum(x["status"] == "running" for x in jobs),
            "awaiting_approval": sum(x["status"] == "awaiting_approval" for x in jobs),
            "failed": sum(x["status"] == "failed" for x in jobs),
            "by_workflow": {w: sum(x["workflow"] == w for x in jobs) for w in WORKFLOW_STEPS}}
