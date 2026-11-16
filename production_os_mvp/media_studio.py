"""Media Studio: bounded local jobs, explicit operator actions, persistent results."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import media_stack
import comfyui_bridge

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "inbox"
STORE = ROOT / ".production_os_runtime" / "media_studio"
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-studio")
ACTIVE = set()
TASK_ID = re.compile(r"^[0-9a-f]{12}$")
SOURCE_ID = re.compile(r"^[0-9a-f]{10}\.(mp4|mov|mkv|webm)$")
ALLOWED_RESULTS = {".mp4", ".mov", ".mkv", ".webm", ".srt", ".json", ".wav", ".m4a", ".mp3"}
LIMIT = 100

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def folder_for(task_id):
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise ValueError("Identifiant de traitement invalide")
    root = STORE.resolve()
    folder = (root / task_id).resolve()
    if folder.parent != root:
        raise ValueError("Chemin de traitement invalide")
    return folder

def source_file(name):
    if not isinstance(name, str) or not SOURCE_ID.fullmatch(name):
        raise ValueError("Importe d'abord une vidéo avec le bouton dédié")
    root = INBOX.resolve()
    raw = root / name
    path = raw.resolve()
    if path.parent != root or raw.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Vidéo importée absente ou vide")
    return path

def configured_path(key):
    value = os.environ.get(key, "").strip()
    if value:
        return Path(value).expanduser().resolve()
    # Stable local paths: setup survives a new PowerShell window.
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    root = Path(local) / "ProductionOS"
    defaults = {
        "PRODUCTION_WHISPER_PYTHON": root / "Whisper" / ".venv" / "Scripts" / "python.exe",
        "PRODUCTION_WHISPER_MODEL": root / "Whisper" / "models" / "small",
        "PRODUCTION_HYPERFRAMES_PROJECT": root / "HyperFrames" / "btb-hyperframes",
    }
    default = defaults.get(key)
    return default.resolve() if default and default.exists() else None


def normalize_media_url(url):
    from urllib.parse import urlsplit, parse_qs
    if not isinstance(url, str) or len(url) > 1000:
        raise ValueError("URL invalide")
    url = url.strip()
    parts = urlsplit(url)
    allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com"}
    if parts.scheme != "https" or parts.hostname not in allowed or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("URL HTTPS YouTube/Vimeo prise en charge ; autre source via CLI")
    if parts.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        if parts.hostname == "youtu.be":
            video_id = parts.path.strip("/")
        elif parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0]
        elif parts.path.startswith(("/shorts/", "/live/")):
            video_id = parts.path.strip("/").split("/", 1)[1]
        else:
            raise ValueError("Utilise le lien direct d'une vidéo YouTube, pas une chaîne ou playlist")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("Identifiant vidéo YouTube invalide")
        return "https://www.youtube.com/watch?v=" + video_id
    return url

def whisper_python():
    configured = configured_path("PRODUCTION_WHISPER_PYTHON")
    if configured:
        if not configured.is_file():
            raise ValueError("Interpréteur Whisper configuré introuvable")
        return str(configured)
    return sys.executable

def status():
    model = configured_path("PRODUCTION_WHISPER_MODEL")
    project = configured_path("PRODUCTION_HYPERFRAMES_PROJECT")
    tool = media_stack.doctor()
    return {"tools": tool, "comfyui": comfyui_bridge.get_status(),
            "whisper_model_configured": bool(model and model.is_dir()),
            "whisper_separate_python": bool((configured_path("PRODUCTION_WHISPER_PYTHON")) and configured_path("PRODUCTION_WHISPER_PYTHON").is_file()),
            "hyperframes_project_configured": bool(project and (project / "index.html").is_file()
                                                    and (project / "node_modules/hyperframes/package.json").is_file()),
            "target": 18, "score": None, "publication_automatic": False,
            "tasks": list_tasks()}

def save(folder, task):
    media_stack.write_json(folder / "task.json", task)

def read_task(task_id):
    folder = folder_for(task_id)
    with LOCK:
        if not (folder / "task.json").is_file():
            raise ValueError("Traitement introuvable")
        task = json.loads((folder / "task.json").read_text(encoding="utf-8"))
        if task.get("status") in {"queued", "running"} and task_id not in ACTIVE:
            task.update(status="interrupted", error="Serveur redémarré : relancer volontairement.", ended=now())
            save(folder, task)
        return task

def list_tasks():
    if not STORE.is_dir():
        return []
    tasks = []
    paths = sorted((p for p in STORE.iterdir() if p.is_dir() and TASK_ID.fullmatch(p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:LIMIT]:
        try:
            tasks.append(read_task(path.name))
        except (ValueError, OSError):
            continue
    return tasks

def result_file(task_id, filename):
    folder = folder_for(task_id)
    task = read_task(task_id)
    if task.get("status") != "succeeded" or filename not in task.get("files", []):
        raise ValueError("Fichier non disponible")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Nom de fichier invalide")
    raw = folder / filename
    file = raw.resolve()
    if file.parent != folder or raw.is_symlink() or file.suffix.lower() not in ALLOWED_RESULTS or not file.is_file():
        raise ValueError("Fichier non disponible")
    return file

def submit(data):
    if not isinstance(data, dict):
        raise ValueError("Objet JSON attendu")
    action = data.get("action")
    if action not in ("transcribe", "qa", "acquire", "render"):
        raise ValueError("Action inconnue")
    payload = {"action": action}
    if action in {"transcribe", "qa"}:
        source = source_file(data.get("source_file"))
        payload["source_file"] = source.name
    if action == "transcribe":
        model = configured_path("PRODUCTION_WHISPER_MODEL")
        if not model or not model.is_dir():
            raise ValueError("Configure PRODUCTION_WHISPER_MODEL avant de transcrire")
        whisper_python()
        language = data.get("language", "auto")
        if language not in ("auto", "fr", "en"):
            raise ValueError("Langue non supportée")
        payload["language"] = language
    elif action == "qa":
        payload["silent_ok"] = data.get("silent_ok") is True
        payload["full_hd"] = data.get("full_hd") is True
        payload["any_ratio"] = data.get("any_ratio") is True
    elif action == "acquire":
        if data.get("rights_confirmed") is not True:
            raise ValueError("Confirmation explicite des droits requise")
        payload["url"] = normalize_media_url(data.get("url", ""))
        payload["rights_confirmed"] = True
    elif action == "render":
        project = configured_path("PRODUCTION_HYPERFRAMES_PROJECT")
        if data.get("trusted_project") is not True or not project or not (project / "index.html").is_file():
            raise ValueError("Projet HyperFrames local de confiance et confirmation requis")
    with LOCK:
        if ACTIVE:
            raise ValueError("Un traitement média est déjà en cours")
        task_id = uuid.uuid4().hex[:12]
        folder = folder_for(task_id)
        folder.mkdir(parents=True, exist_ok=False)
        task = {"id": task_id, "action": action, "created": now(), "status": "queued",
                "files": [], "publication_ready": False}
        save(folder, task)
        ACTIVE.add(task_id)
        try:
            POOL.submit(execute, task_id, payload)
        except Exception:
            ACTIVE.discard(task_id)
            task.update(status="failed", error="Impossible de démarrer le traitement", ended=now())
            save(folder, task)
            raise
        return task

def execute(task_id, data):
    folder = folder_for(task_id)
    task = read_task(task_id)
    task.update(status="running", started=now())
    try:
        save(folder, task)
        action = data["action"]
        if action == "transcribe":
            args = [whisper_python(), str(ROOT / "media_stack.py"), "transcribe",
                    str(source_file(data["source_file"])), str(folder), "--model-path",
                    str(configured_path("PRODUCTION_WHISPER_MODEL"))]
            if data["language"] != "auto":
                args += ["--language", data["language"]]
            result = json.loads(media_stack.run(args, timeout=1800))
        elif action == "qa":
            result = media_stack.video_qa(source_file(data["source_file"]),
                      require_audio=not data["silent_ok"], vertical=not data["any_ratio"],
                      expected_size=(1080, 1920) if data["full_hd"] else None)
            media_stack.write_json(folder / "qa_report.json", result)
        elif action == "acquire":
            names = media_stack.acquire(data["url"], folder, rights_confirmed=True)
            # Stage verified imported media under the existing uploader's naming convention.
            sources = []
            INBOX.mkdir(parents=True, exist_ok=True)
            for name in names:
                src = folder / name
                if src.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} and src.stat().st_size <= 300 * 1024 * 1024:
                    new_name = uuid.uuid4().hex[:10] + src.suffix.lower()
                    shutil.copyfile(src, INBOX / new_name)
                    sources.append(new_name)
            result = {"downloaded": names, "source_files": sources, "rights_confirmed": True}
        else:
            output = folder / "render.mp4"
            media_stack.render(configured_path("PRODUCTION_HYPERFRAMES_PROJECT"), output)
            result = media_stack.video_qa(output, require_audio=False, vertical=False)
            media_stack.write_json(folder / "qa_report.json", result)
            if not result.get("technical_pass"):
                raise RuntimeError("Le rendu a échoué au contrôle de décodage")
        files = sorted(p.name for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                       and p.name != "task.json" and p.suffix.lower() in ALLOWED_RESULTS)
        task.update(status="succeeded", result=result, files=files, ended=now())
    except Exception as exc:
        task.update(status="failed", error=str(exc)[-1500:], ended=now())
    finally:
        with LOCK:
            try:
                save(folder, task)
            finally:
                ACTIVE.discard(task_id)
