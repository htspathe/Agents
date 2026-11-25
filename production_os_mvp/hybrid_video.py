"""Resumable local walking skeleton: narration -> Piper -> STT -> MP4.

No cloud calls are made here. Scenes come from local files or completed Video
Factory jobs. A manifest records content hashes; changed inputs invalidate
downstream stages. Run through CLI or the authenticated local dashboard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
import wave
from pathlib import Path

import media_factory
import piper_tts

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "hybrid_video"
_LOCK = threading.Lock()
_ACTIVE = set()
STAGES = ("voice", "subtitles", "assembly", "quality")


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _folder(job_id):
    if not re.fullmatch(r"[0-9a-f]{12}", str(job_id)):
        raise ValueError("Identifiant de mission invalide.")
    return OUTPUT / job_id


def _probe(path):
    binary = shutil.which("ffprobe")
    if not binary:
        raise RuntimeError("FFprobe requis.")
    result = subprocess.run([binary, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=30, check=True)
    data = json.loads(result.stdout)
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Durée média invalide.")
    return data, duration


def _srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    sec, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{sec:02},{ms:03}"


def transcribe(audio, output, language="fr"):
    """CPU INT8 only; local model path required, no implicit downloads."""
    from faster_whisper import WhisperModel
    model_path = Path(os.environ.get("PRODUCTION_WHISPER_MODEL_DIR", ""))
    if not os.environ.get("PRODUCTION_WHISPER_MODEL_DIR") or not model_path.is_dir():
        raise RuntimeError("Configurer PRODUCTION_WHISPER_MODEL_DIR vers un modèle faster-whisper local.")
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                         cpu_threads=4, local_files_only=True)
    segments, _ = model.transcribe(str(audio), language=language, vad_filter=False)
    cues = []
    for segment in segments:
        text = " ".join(segment.text.split())
        if text and segment.end > segment.start:
            cues.append(f"{len(cues)+1}\n{_srt_time(segment.start)} --> {_srt_time(segment.end)}\n{text}\n")
    if not cues:
        raise RuntimeError("Aucun sous-titre transcrit.")
    output.write_text("\n".join(cues), encoding="utf-8")


def create(narration, scene_paths, language="fr", ratio="9:16", source="local", *, job_id=None, voice_seed=None):
    narration = str(narration).strip()
    if not 12 <= len(narration) <= 12000:
        raise ValueError("Narration : 12 à 12 000 caractères.")
    if language != "fr" or ratio not in {"9:16", "16:9"}:
        raise ValueError("Ce pipeline utilise les voix françaises Piper existantes ; français et formats 9:16/16:9 uniquement.")
    if not 1 <= len(scene_paths) <= 12:
        raise ValueError("Entre 1 et 12 scènes MP4 requises.")
    if source not in {"local", "video_factory", "kaggle_experiment"}:
        raise ValueError("Source inconnue.")
    job_id = job_id or uuid.uuid4().hex[:12]
    destination = _folder(job_id)
    if destination.exists():
        prior = get(job_id)
        if (prior["narration"], prior["language"], prior["ratio"], prior["source"]) != (narration, language, ratio, source) or [s["sha256"] for s in prior["scenes"]] != [_hash(p) for p in scene_paths]:
            raise ValueError("Conflit de mission hybride existante.")
        return prior
    folder = destination.with_name(".staging-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    scenes = []
    try:
        for i, raw in enumerate(scene_paths):
            path = Path(raw).resolve()
            if not path.is_file() or path.suffix.lower() != ".mp4" or path.stat().st_size > 200*1024*1024:
                raise ValueError("Scène MP4 absente ou supérieure à 200 Mo.")
            data, _ = _probe(path)
            if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
                raise ValueError("La scène ne contient aucun flux vidéo.")
            target = folder / f"scene_{i:02}.mp4"
            shutil.copyfile(path, target)
            scenes.append({"file": target.name, "sha256": _hash(target)})
        manifest = {"id": job_id, "narration": narration, "language": language,
                    "ratio": ratio, "source": source, "scenes": scenes,
                    "status": "queued", "steps": {}, "error": None,
                    "usage": "noncommercial_experiment" if source == "kaggle_experiment" else "rights_unverified"}
        if voice_seed:
            audio, settings = voice_seed
            target = folder / "narration.wav"
            shutil.copyfile(audio, target)
            fingerprint = hashlib.sha256(json.dumps([narration, settings], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            manifest["steps"]["voice"] = {"input": fingerprint, "sha256": _hash(target), "file": target.name}
        _write(folder / "manifest.json", manifest)
        os.rename(folder, destination)
        return manifest
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def create_from_media(data):
    ids = data.get("scene_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 12:
        raise ValueError("Indiquer 1 à 12 identifiants Video Factory terminés.")
    paths = [media_factory.output_path(item) for item in ids]
    if any(path is None for path in paths):
        raise ValueError("Toutes les scènes doivent être terminées et disponibles.")
    return create(data.get("narration", ""), paths, data.get("language", "fr"),
                  data.get("ratio", "9:16"), source="video_factory")


def get(job_id):
    path = _folder(job_id) / "manifest.json"
    if not path.is_file():
        raise ValueError("Mission introuvable.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_jobs():
    if not OUTPUT.is_dir():
        return []
    jobs = []
    for path in sorted(OUTPUT.glob("*/manifest.json"), key=lambda p:p.stat().st_mtime, reverse=True)[:50]:
        try:
            jobs.append(get(path.parent.name))
        except (OSError, ValueError):
            continue
    return jobs


def _stage(manifest, folder, name, inputs, output, action):
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    record = manifest["steps"].get(name, {})
    if record.get("input") == fingerprint and output.is_file() and record.get("sha256") == _hash(output):
        return
    index = STAGES.index(name)
    for stage in STAGES[index:]:
        manifest["steps"].pop(stage, None)
    manifest["stage"] = name
    _write(folder / "manifest.json", manifest)
    temp = output.with_name(output.stem + ".part" + output.suffix)
    temp.unlink(missing_ok=True)
    try:
        action(temp)
        if not temp.is_file() or not temp.stat().st_size:
            raise RuntimeError("Livrable manquant : " + name)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    manifest["steps"][name] = {"input": fingerprint, "sha256": _hash(output), "file": output.name}
    _write(folder / "manifest.json", manifest)


def _assemble(folder, scenes, ratio, output):
    audio = folder / "narration.wav"
    with wave.open(str(audio), "rb") as stream:
        duration = stream.getnframes() / stream.getframerate()
    if not 0 < duration <= 600:
        raise RuntimeError("Narration hors limite (10 minutes).")
    # Normalize independent source clips; discard original audio.
    width, height = (720, 1280) if ratio == "9:16" else (1280, 720)
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("FFmpeg requis.")
    args = []
    filters = []
    total = 0.0
    for i, scene in enumerate(scenes):
        args += ["-i", str(folder / scene["file"])]
        _, seconds = _probe(folder / scene["file"])
        total += seconds
        filters.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p,setpts=PTS-STARTPTS[v{i}]")
    # Never present a frozen end frame as additional generated footage.
    if total + 0.1 < duration:
        raise RuntimeError(f"Scènes insuffisantes ({total:.1f}s pour {duration:.1f}s de narration). Ajouter des scènes.")
    labels = "".join(f"[v{i}]" for i in range(len(scenes)))
    filters.append(labels + f"concat=n={len(scenes)}:v=1:a=0[video]")
    # Pass a relative subtitle filename to avoid Windows drive-letter escaping.
    filters.append("[video]subtitles=subtitles.srt:force_style='FontSize=20,Outline=2,Alignment=2,MarginV=32'[out]")
    args += ["-i", str(audio), "-filter_complex", ";".join(filters),
             "-map", "[out]", "-map", f"{len(scenes)}:a:0", "-t", f"{duration:.3f}"]
    # CPU profile deliberately conservative for 16GB / hybrid GPU.
    command = [binary, "-y", "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "2",
               *args, "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-threads", "4",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output.resolve())]
    subprocess.run(command, cwd=folder, capture_output=True, timeout=1200, check=True)


def _run_locked(job_id):
    folder = _folder(job_id)
    manifest = get(job_id)
    manifest.update(status="running", error=None)
    _write(folder / "manifest.json", manifest)
    try:
        for scene in manifest["scenes"]:
            if _hash(folder / scene["file"]) != scene["sha256"]:
                raise RuntimeError("Une scène source a été modifiée ; créer une nouvelle mission.")
        audio, srt, final, qa = (folder / name for name in ("narration.wav","subtitles.srt","final.mp4","qa.json"))
        settings = piper_tts.get_settings()
        manifest["voice_attribution"] = piper_tts.VOICE_DATA.get(settings["voice"], {}).get("attribution", "")
        # Avoid storing credential-bearing settings in manifest; only hash inputs.
        _stage(manifest, folder, "voice", [manifest["narration"], settings],
               audio, lambda target: piper_tts.synthesize_to_wav(manifest["narration"], target))
        _stage(manifest, folder, "subtitles", [_hash(audio), manifest["language"],
               os.environ.get("PRODUCTION_WHISPER_MODEL_DIR", "")], srt,
               lambda target: transcribe(audio, target, language=manifest["language"]))
        _stage(manifest, folder, "assembly", [manifest["scenes"], _hash(audio), _hash(srt), manifest["ratio"]],
               final, lambda target: _assemble(folder, manifest["scenes"], manifest["ratio"], target))
        def quality(target):
            data, duration = _probe(final)
            video = next((s for s in data["streams"] if s.get("codec_type")=="video"), None)
            sound = next((s for s in data["streams"] if s.get("codec_type")=="audio"), None)
            expected = (720,1280) if manifest["ratio"]=="9:16" else (1280,720)
            with wave.open(str(audio),"rb") as stream:
                expected_duration = stream.getnframes()/stream.getframerate()
            if not video or not sound or (video.get("width"),video.get("height")) != expected or abs(duration-expected_duration)>0.5:
                raise RuntimeError("Contrôle technique MP4 échoué.")
            _write(target, {"duration": duration, "width": expected[0], "height": expected[1],
                            "technical_validation": True, "visual_review_required": True})
        _stage(manifest, folder, "quality", [_hash(final), _hash(audio)], qa, quality)
        manifest.update(status="awaiting_review", stage="quality", error=None)
    except Exception as exc:
        manifest.update(status="blocked", error=str(exc)[:600] or type(exc).__name__)
    _write(folder / "manifest.json", manifest)
    return manifest


def run(job_id):
    # SQLite OS-level write lock serializes this heavy pipeline across CLI/server
    # processes; a crash releases it automatically.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(OUTPUT / ".execution.sqlite3", timeout=0)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise ValueError("Une mission hybride est déjà en cours.") from exc
        return _run_locked(job_id)
    finally:
        connection.close()


def start(job_id):
    get(job_id)
    with _LOCK:
        if _ACTIVE:
            raise ValueError("Une mission hybride locale est déjà en cours.")
        _ACTIVE.add(job_id)
    def work():
        try:
            run(job_id)
        except ValueError:
            # A second server or CLI may already own the cross-process lock.
            pass
        finally:
            with _LOCK:
                _ACTIVE.discard(job_id)
    threading.Thread(target=work, daemon=True, name="HybridVideo-"+job_id).start()


def final_path(job_id):
    manifest = get(job_id)
    path = _folder(job_id) / "final.mp4"
    if manifest["status"] != "awaiting_review" or not path.is_file():
        return None
    if _hash(path) != manifest["steps"].get("assembly", {}).get("sha256"):
        return None
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("create")
    new.add_argument("--narration-file", required=True, type=Path)
    new.add_argument("--scene", action="append", required=True)
    new.add_argument("--language", choices=["fr"], default="fr")
    new.add_argument("--ratio", choices=["9:16","16:9"], default="9:16")
    new.add_argument("--source", choices=["local","kaggle_experiment"], default="local")
    resume = sub.add_parser("run")
    resume.add_argument("id")
    sub.add_parser("list")
    args = parser.parse_args()
    if args.command == "create":
        result = create(args.narration_file.read_text(encoding="utf-8"),args.scene,args.language,args.ratio,args.source)
    elif args.command == "run":
        result = run(args.id)
    else:
        result = list_jobs()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if isinstance(result,dict) and result.get("status")=="blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
