"""Persistent topic-to-video coordinator over the existing local and media engines.

One mission at a time. No automatic paid fallback, publication, or retry of
ambiguous external requests. Existing media jobs are referenced, not duplicated.
"""
from __future__ import annotations
import hashlib
import importlib.util
import math
import os
import re
import shutil
import threading
import time
import uuid
import wave
from pathlib import Path

import hybrid_compute as compute
import hybrid_video as hybrid
import media_factory as media
import piper_tts

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "directed_video"
_LOCK = threading.RLock()
_STOP = threading.Event()
_WAKE = threading.Event()
_WORKER = None
ACTIVE_STATES = {"queued", "planning", "waiting_scenes", "assembling"}

def _folder(job_id):
    if not re.fullmatch(r"[0-9a-f]{12}", str(job_id)):
        raise ValueError("Mission invalide.")
    return OUTPUT / job_id

def _save(job):
    hybrid._write(_folder(job["id"]) / "mission.json", job)

def get(job_id):
    import json
    path = _folder(job_id) / "mission.json"
    if not path.is_file():
        raise ValueError("Mission introuvable.")
    return json.loads(path.read_text(encoding="utf-8"))

def list_jobs():
    if not OUTPUT.is_dir():
        return []
    jobs = []
    for path in OUTPUT.glob("*/mission.json"):
        try:
            jobs.append(get(path.parent.name))
        except (OSError, ValueError):
            continue
    return sorted(jobs, key=lambda j: j["created_at"], reverse=True)

def create(data):
    if not isinstance(data, dict):
        raise ValueError("Objet JSON attendu.")
    topic = str(data.get("topic", "")).strip()
    narration = str(data.get("narration", "")).strip()
    provider = str(data.get("provider", "freellmapi"))
    model = str(data.get("model", "")).strip()
    ratio = data.get("ratio", "9:16")
    duration = data.get("scene_duration", 6)
    maximum = data.get("max_scenes", 5)
    if not 3 <= len(topic) <= 2000 or len(narration) > 2000 or (narration and len(narration)<12):
        raise ValueError("Sujet ou narration invalide.")
    if ratio not in {"9:16","16:9"} or type(duration) is not int or not 4<=duration<=12 or type(maximum) is not int or not 1<=maximum<=12:
        raise ValueError("Paramètres vidéo invalides.")
    media._validate_request({"provider":provider, "model":model, "prompt":"Validation locale uniquement", "duration":duration, "aspect_ratio":ratio})
    job = {"id":uuid.uuid4().hex[:12], "topic":topic, "narration":narration,
           "provider":provider, "model":model, "ratio":ratio, "scene_duration":duration,
           "max_scenes":maximum, "status":"queued", "created_at":time.time(),
           "error":None, "prompts":[], "scene_ids":[], "hybrid_id":None,
           "review_required":True, "usage":"rights_unverified"}
    with _LOCK:
        _folder(job["id"]).mkdir(parents=True)
        _save(job)
    _WAKE.set()
    return job

def resume(job_id):
    with _LOCK:
        job=get(job_id)
        if job["status"]!="blocked":
            raise ValueError("Seules les missions bloquées peuvent être reprises.")
        job.update(status="queued",error=None)
        _save(job)
    _WAKE.set()

def _preflight():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg et FFprobe requis.")
    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError("Installer faster-whisper dans le Python de Production OS.")
    hybrid.whisper_model_path()

def _prepare_voice(job):
    folder = _folder(job["id"])
    target=folder/"narration.wav"
    settings=piper_tts.get_settings()
    import json
    fingerprint=hashlib.sha256(json.dumps([job["narration"],settings],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    record=job.get("voice") or {}
    if record.get("input")!=fingerprint or not target.is_file() or record.get("sha256")!=hybrid._hash(target):
        if job["prompts"]:
            raise RuntimeError("La voix a changé après planification ; conserver la configuration initiale.")
        temp=folder/"narration.part.wav"
        try:
            piper_tts.synthesize_to_wav(job["narration"],temp)
            os.replace(temp,target)
        finally:
            temp.unlink(missing_ok=True)
        job["voice"]={"input":fingerprint,"sha256":hybrid._hash(target)}
        _save(job)
    with wave.open(str(target),"rb") as audio:
        duration=audio.getnframes()/audio.getframerate()
    if not 0<duration<=120:
        raise RuntimeError("Narration hors limite (120 secondes).")
    return target, settings, duration

def advance(job):
    """Advance at most one mission; wait without busy-polling external providers."""
    try:
        if job["status"] not in ACTIVE_STATES:
            return job
        _preflight()
        if not job["narration"]:
            job["status"]="planning"
            _save(job)
            job["narration"]=compute.local_narration(job["topic"],"fr")
            _save(job)
        audio,settings,seconds=_prepare_voice(job)
        if not job["prompts"]:
            # One second of allowance for clip duration variation.
            count=math.ceil((seconds+1)/job["scene_duration"])
            if count>job["max_scenes"]:
                raise RuntimeError(f"Voix de {seconds:.1f}s : {count} scènes nécessaires, maximum autorisé {job['max_scenes']}.")
            job["prompts"]=compute.local_storyboard(job["narration"],count,job["ratio"])
            _save(job)
        if not job["scene_ids"]:
            if _STOP.is_set():
                return job
            requests=[{"provider":job["provider"],"model":job["model"],"prompt":prompt,
                       "duration":job["scene_duration"],"aspect_ratio":job["ratio"]}
                      for prompt in job["prompts"]]
            job["scene_ids"]=media.enqueue_storyboard(job["id"],requests)
            job["status"]="waiting_scenes"
            _save(job)
        rows=[media.get_job(scene) for scene in job["scene_ids"]]
        if any(row is None or row["status"] in {"failed","interrupted","cancelled"} for row in rows):
            raise RuntimeError("Une scène a échoué ou a été interrompue. Aucun nouvel appel automatique ; utiliser la file Video Factory pour relancer puis remplacer la scène.")
        if any(row["status"]!="completed" for row in rows):
            job["status"]="waiting_scenes"
            _save(job)
            return job
        paths=[media.output_path(scene) for scene in job["scene_ids"]]
        if any(path is None for path in paths):
            raise RuntimeError("Une scène terminée est introuvable.")
        if _STOP.is_set():
            return job
        job["hybrid_id"]=job["id"]
        job["status"]="assembling"
        _save(job)
        hybrid.create(job["narration"],paths,"fr",job["ratio"],"video_factory",
                      job_id=job["hybrid_id"],voice_seed=(audio,settings))
        try:
            result=hybrid.run(job["hybrid_id"])
        except ValueError:
            # The other local assembly process holds the lock. Try later.
            return job
        if result["status"]!="awaiting_review":
            raise RuntimeError(result.get("error") or "Assemblage incomplet.")
        job["status"]="awaiting_review"
        job["error"]=None
    except Exception as exc:
        job["status"]="blocked"
        job["error"]=media._safe_error(exc)
    _save(job)
    return job

def replace_scene(job_id, old_id, new_id):
    """Attach an explicitly retried completed clip, without new provider calls."""
    with _LOCK:
        job=get(job_id)
        if job["status"]!="blocked" or job.get("hybrid_id") or old_id not in job["scene_ids"] or new_id in job["scene_ids"]:
            raise ValueError("Remplacement non autorisé pour cette mission.")
        old=media.get_job(old_id)
        new=media.get_job(new_id)
        if not old or old["status"] not in {"failed","interrupted","cancelled"} or not new or new["status"]!="completed":
            raise ValueError("Une scène en échec et une scène de remplacement terminée sont requises.")
        if any(old[k]!=new[k] for k in ("provider","model","prompt","duration","aspect_ratio")):
            raise ValueError("Le remplacement ne correspond pas au storyboard.")
        job["scene_ids"][job["scene_ids"].index(old_id)]=new_id
        _save(job)

def _loop():
    while not _STOP.is_set():
        # Lock is shared with dashboard modifications. No second worker is launched
        # in this process; the OS lock below prevents multiple server instances.
        try:
            jobs=list_jobs()
            job=next((item for item in reversed(jobs) if item["status"] in ACTIVE_STATES),None)
            if job:
                advance(job)
        except (OSError,ValueError):
            pass
        _WAKE.wait(3)
        _WAKE.clear()

def start_worker():
    global _WORKER
    import sqlite3
    with _LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        OUTPUT.mkdir(parents=True,exist_ok=True)
        # Create the connection inside the worker's thread to preserve SQLite's
        # thread ownership. The lock lasts for the entire worker lifetime.
        def work():
            db=sqlite3.connect(OUTPUT/".worker.sqlite3",timeout=0)
            try:
                try:
                    db.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    return
                _loop()
            finally:
                db.close()
        _STOP.clear()
        _WORKER=threading.Thread(target=work,daemon=True,name="ProductionOS-SceneDirector")
        _WORKER.start()

def stop_worker():
    _STOP.set()
    _WAKE.set()
