"""Voice Studio: local male voices and opt-in ElevenLabs for Production OS.

No cloud credential is ever persisted or returned to the UI. Cloud generation
requires an explicit authorization flag on EACH invocation. Model downloads
happen only through the user's local install/generation action.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import piper_tts

ROOT = Path(__file__).resolve().parent
SETTINGS_NAME = ".production_os_voice_studio.json"
DEFAULTS = {
    "provider": "qwen3_local",
    "qwen_speaker": "Uncle_Fu",
    "qwen_style": "Low, warm, natural, calm male podcast narrator. Measured pacing and subtle emphasis.",
    "elevenlabs_voice_id": "",
}
PROVIDERS = {"piper", "qwen3_local", "elevenlabs"}
QWEN_SPEAKERS = {"Uncle_Fu", "Ryan", "Aiden"}
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_CHARS = 12000
LOCK = threading.Lock()
_QWEN_STATUS_CACHE = {"path": "", "checked": 0.0, "installed": False}


class VoiceError(RuntimeError):
    pass


def settings_path():
    return ROOT / SETTINGS_NAME


def get_settings():
    result = dict(DEFAULTS)
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            if data.get("provider") in PROVIDERS:
                result["provider"] = data["provider"]
            if data.get("qwen_speaker") in QWEN_SPEAKERS:
                result["qwen_speaker"] = data["qwen_speaker"]
            if isinstance(data.get("qwen_style"), str) and len(data["qwen_style"]) <= 300:
                result["qwen_style"] = data["qwen_style"].strip()
            v = data.get("elevenlabs_voice_id")
            if isinstance(v, str) and (not v or VOICE_ID_RE.fullmatch(v)):
                result["elevenlabs_voice_id"] = v
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Configuration Voice Studio invalide.")
    result = {**get_settings(), **data}
    if result.get("provider") not in PROVIDERS:
        raise ValueError("Fournisseur vocal non pris en charge.")
    if result.get("qwen_speaker") not in QWEN_SPEAKERS:
        raise ValueError("Profil Qwen3-TTS non reconnu.")
    style = result.get("qwen_style")
    if not isinstance(style, str) or len(style.strip()) > 300:
        raise ValueError("Description vocale Qwen3 trop longue.")
    voice_id = result.get("elevenlabs_voice_id")
    if not isinstance(voice_id, str) or (voice_id and not VOICE_ID_RE.fullmatch(voice_id)):
        raise ValueError("Identifiant ElevenLabs invalide.")
    result = {k: result[k] for k in DEFAULTS}
    path = settings_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return result


def qwen_python():
    supplied = os.environ.get("QWEN_TTS_PYTHON", "").strip()
    if supplied:
        return supplied
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        path = Path(local) / "ProductionOS" / "QwenTTS" / ".venv" / "Scripts" / "python.exe"
        if path.is_file():
            return str(path)
    unix = ROOT / "tools" / "qwen-tts" / ".venv" / "bin" / "python"
    return str(unix) if unix.is_file() else ""




def _qwen_package_installed(py):
    now = time.monotonic()
    if _QWEN_STATUS_CACHE["path"] == py and now - _QWEN_STATUS_CACHE["checked"] < 30:
        return _QWEN_STATUS_CACHE["installed"]
    installed = False
    if py and Path(py).is_file():
        try:
            cp = subprocess.run(
                [py, "-c", "import importlib.util; print(int(importlib.util.find_spec('qwen_tts') is not None))"],
                capture_output=True, text=True, timeout=8, check=False
            )
            installed = cp.returncode == 0 and cp.stdout.strip() == "1"
        except (OSError, subprocess.SubprocessError):
            installed = False
    _QWEN_STATUS_CACHE.update({"path": py, "checked": now, "installed": installed})
    return installed


def available_ram_gb():
    if os.name != "nt":
        return None
    try:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)
            ]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullAvailPhys / (1024 ** 3), 2)
    except (AttributeError, OSError):
        pass
    return None


def status():
    py = qwen_python()
    qwen_installed = _qwen_package_installed(py)
    ram = available_ram_gb()
    piper = piper_tts.get_status()
    settings = get_settings()
    return {
        "selected_provider": settings["provider"],
        "providers": {
            "piper": {"ready": bool(piper["ready"]), "voice": piper["voice"],
                      "note": piper["note"], "offline": True},
            "qwen3_local": {
                "installed": qwen_installed,
                "ready": qwen_installed and (ram is None or ram >= 3.5),
                "python_path": py if qwen_installed else "",
                "available_ram_gb": ram,
                "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
                "note": ("Installe Qwen3-TTS avec install_qwen_tts_windows.ps1."
                         if not qwen_installed else
                         "Libère au moins 3,5 Go de RAM avant de lancer le modèle."
                         if ram is not None and ram < 3.5 else
                         "Environnement détecté. Modèle téléchargé à la première génération si nécessaire."),
                "offline_after_download": True,
            },
            "elevenlabs": {
                "configured": bool(os.environ.get("ELEVENLABS_API_KEY") and settings["elevenlabs_voice_id"]),
                "has_api_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
                "voice_id_configured": bool(settings["elevenlabs_voice_id"]),
                "needs_explicit_paid_authorization": True,
                "note": "Clé ELEVENLABS_API_KEY en variable d'environnement ; chaque génération nécessite une confirmation."
            },
        },
    }


def _spoken_text(folder: Path, config: dict):
    if config.get("viral_template"):
        path = folder / "viral_voiceover.txt"
        if not path.is_file():
            raise VoiceError("La narration virale n'a pas encore été préparée.")
        text = path.read_text(encoding="utf-8").strip()
    else:
        content = (folder / "content.md")
        raw = content.read_text(encoding="utf-8") if content.is_file() else ""
        text, _source, error = piper_tts._narration_source(raw, config)
        if error:
            raise VoiceError(error)
    if not 12 <= len(text) <= MAX_CHARS:
        raise VoiceError("La narration est vide ou trop longue.")
    return text


def _word_alignments(text: str, alignment: dict):
    """Convert *real* vendor character timestamps into word-level timestamps."""
    if not isinstance(alignment, dict):
        raise VoiceError("ElevenLabs n'a pas retourné de minutage réel.")
    chars = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not isinstance(chars, list) or not isinstance(starts, list) or not isinstance(ends, list):
        raise VoiceError("Données d'alignement ElevenLabs incomplètes.")
    if not len(chars) == len(starts) == len(ends) == len(text) or "".join(chars) != text:
        raise VoiceError("La transcription n'est pas alignée exactement sur la réponse ElevenLabs.")
    words = []
    for m in re.finditer(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)*", text):
        start, end = starts[m.start()], ends[m.end() - 1]
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            raise VoiceError("Timestamp incorrect dans la réponse ElevenLabs.")
        words.append({"text": m.group(), "start_s": round(float(start), 3),
                      "end_s": round(float(end), 3),
                      "char_start": m.start(), "char_end": m.end()})
    return words


def _trigger_timestamps(timeline: dict, word_times: list):
    """Match each visual trigger to the exact real word in its scene, not an estimate."""
    events = []
    cursor = 0
    for scene in timeline["scenes"]:
        spoken = scene["voiceover_text"]
        for element in scene["visual_elements"]:
            needle = element["trigger_word"].casefold()
            matches = [m for m in re.finditer(re.escape(needle), spoken.casefold())]
            if not matches:
                raise VoiceError(f"Mot de déclenchement introuvable dans la scène {scene['scene_id']}.")
            start_char = cursor + matches[0].start()
            candidates = [w for w in word_times if w["char_start"] <= start_char < w["char_end"]]
            if not candidates:
                raise VoiceError("Le moteur n'a pas minuté un mot déclencheur.")
            word = candidates[0]
            events.append({
                "scene_id": scene["scene_id"], "trigger_word": element["trigger_word"],
                "event_time_s": round(max(0.0, word["start_s"] - 0.1), 3),
                "word_start_s": word["start_s"], "lead_in_ms": -100,
                "sfx": element["sfx_trigger"], "content": element["content"],
            })
        cursor += len(spoken) + 1
    return events


def _elevenlabs_audio(text: str, *, voice_id: str, allow_charge: bool, speed=1.08):
    if not allow_charge:
        raise VoiceError("Autorisation explicite requise avant cet appel ElevenLabs susceptible de consommer des crédits.")
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise VoiceError("ELEVENLABS_API_KEY n'est pas configurée dans l'environnement du serveur.")
    if not VOICE_ID_RE.fullmatch(voice_id):
        raise VoiceError("Choisis un identifiant de voix ElevenLabs valide.")
    payload = {"text": text, "model_id": "eleven_multilingual_v2",
               "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "speed": float(speed)}}
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        data=json.dumps(payload).encode("utf-8"),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read(16_000_001)
    except urllib.error.HTTPError as exc:
        raise VoiceError(f"ElevenLabs a refusé la génération (HTTP {exc.code}). Aucun retry automatique.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VoiceError("ElevenLabs est inaccessible. Aucun retry automatique pour éviter un double coût.") from exc
    if len(body) > 16_000_000:
        raise VoiceError("Réponse ElevenLabs anormalement volumineuse.")
    try:
        data = json.loads(body)
        audio = base64.b64decode(data["audio_base64"], validate=True)
        words = _word_alignments(text, data.get("alignment") or data.get("normalized_alignment"))
    except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
        raise VoiceError("Réponse audio ElevenLabs ou minutage invalide.") from exc
    if not audio or len(audio) > 12_000_000:
        raise VoiceError("Audio ElevenLabs absent ou excessif.")
    return audio, words


def _local_qwen(text: str, output: Path, speaker: str, style: str):
    python = qwen_python()
    ram = available_ram_gb()
    if ram is not None and ram < 3.5:
        raise VoiceError(f"RAM insuffisante pour Qwen3-TTS : {ram:.1f} Go libres ; libère au moins 3,5 Go.")
    if not python or not _qwen_package_installed(python):
        raise VoiceError("Qwen3-TTS n'est pas installé. Lance install_qwen_tts_windows.ps1.")
    if speaker not in QWEN_SPEAKERS:
        raise VoiceError("Profil Qwen3-TTS non reconnu.")
    input_path = output.with_suffix(".voice-input.json")
    input_path.write_text(json.dumps({"text": text, "speaker": speaker, "style": style,
                                      "output": str(output)}, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run([python, str(ROOT / "qwen_voice_worker.py"), str(input_path)],
                                capture_output=True, text=True, timeout=900,
                                encoding="utf-8", errors="replace", check=False)
        if result.returncode != 0 or not output.is_file():
            raise VoiceError("Échec Qwen3-TTS : " + (result.stderr or result.stdout)[-600:])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VoiceError("Qwen3-TTS n'a pas pu terminer sa génération locale.") from exc
    finally:
        input_path.unlink(missing_ok=True)


def _convert_mp3_to_wav(mp3: Path, wav: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VoiceError("FFmpeg doit être installé avant tout appel ElevenLabs.")
    cp = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                         "-i", str(mp3), "-ar", "44100", "-ac", "1", str(wav)],
                        capture_output=True, text=True, timeout=120, check=False)
    if cp.returncode != 0 or not wav.is_file():
        raise VoiceError("La conversion WAV ElevenLabs a échoué.")


def _write_json(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _complete(folder: Path, *, provider: str, voice: str, text: str, word_times=None):
    wav = folder / "narration.wav"
    duration, rate = piper_tts._wav_info(wav)
    cfg_path = folder / "stickman_job.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    timeline = None
    if cfg.get("viral_template"):
        timeline = json.loads((folder / "viral_timeline.json").read_text(encoding="utf-8"))
        if not word_times:
            raise VoiceError("Le storyboard viral exige des timestamps mot à mot réels.")
    existing = folder / "stickman_project" / "output" / "preview.mp4"
    if existing.is_file():
        raise VoiceError("Un aperçu existe déjà ; crée une nouvelle révision avant de changer sa voix.")
    meta = {
        "ready": True, "voice": voice, "provider": provider,
        "duration_seconds": round(duration, 2), "sample_rate": rate,
        "script_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "created_at": time.time(), "offline": provider != "elevenlabs",
        "has_real_word_timestamps": bool(word_times),
    }
    if word_times:
        _write_json(folder / "word_timestamps.json", {"source": "elevenlabs", "words": word_times})
    if timeline:
        triggers = _trigger_timestamps(timeline, word_times)
        _write_json(folder / "visual_trigger_timestamps.json", {"source": "elevenlabs", "events": triggers})
        last_s = word_times[-1]["end_s"]
        difference = round(abs(float(timeline["metadata"]["duration_seconds"]) - last_s), 2)
        timing_ok = difference <= 2.5
        meta["timing_review_required"] = not timing_ok
        meta["timeline_duration_drift_seconds"] = difference
        report_path = folder / "viral_timeline_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report.update({"voice_status": "generated", "word_alignment_status": "ready",
                       "timing_review_required": not timing_ok, "audio_duration_seconds": duration,
                       "render_status": "not_rendered"})
        _write_json(report_path, report)
    _write_json(folder / "narration_metadata.json", meta)
    (folder / "narration.md").write_text(text + "\n", encoding="utf-8")
    cfg.update({"narration_ready": True, "narration_provider": provider,
                "narration_voice": voice, "narration_audio": "narration.wav",
                "word_alignment_available": bool(word_times)})
    _write_json(cfg_path, cfg)
    asset_dir = folder / "stickman_project" / "assets" / "audio"
    if (folder / "stickman_project").is_dir():
        asset_dir.mkdir(parents=True, exist_ok=True)
        for file in ("narration.wav", "narration_metadata.json", "narration.md",
                     "word_timestamps.json", "visual_trigger_timestamps.json"):
            item = folder / file
            if item.is_file():
                shutil.copy2(item, asset_dir / file)
    (folder / "narration_blocker.md").unlink(missing_ok=True)
    request = folder / "stickman_request.md"
    if request.is_file():
        with request.open("a", encoding="utf-8") as document:
            document.write(
                "\n\n## État réel Voice Studio après génération\n"
                f"- Narration {provider} disponible et vérifiée : narration.wav.\n"
                f"- Voix fixée pour cette mission : {voice}.\n"
                "- Les anciennes mentions « non générée / en attente » sont désormais obsolètes.\n"
                + ("- Horodatages réels des mots : word_timestamps.json. Déclencheurs : visual_trigger_timestamps.json.\n"
                   if word_times else
                   "- Aucun timing mot à mot certifié ; ne pas inventer de sous-titres synchronisés.\n")
                + ("- Attention : dérive supérieure au seuil ; révision temporelle obligatoire avant rendu.\n"
                   if meta.get("timing_review_required") else "")
            )
    return meta


def generate_for_job(job_id: str, *, authorize_cloud_charge=False):
    import engine
    job = engine.get_job(job_id)
    if not job or job["workflow"] != "youtube":
        raise VoiceError("Mission YouTube introuvable.")
    if job["status"] != "awaiting_external":
        raise VoiceError("La narration ne peut être changée que pendant la préparation de la mission.")
    config = json.loads(job["input_json"])
    provider = config.get("voice_provider", "piper")
    if provider not in PROVIDERS:
        raise VoiceError("Fournisseur vocal de cette mission inconnu.")
    folder = (engine.ROOT / "outputs" / job_id).resolve()
    if folder.parent != (engine.ROOT / "outputs").resolve():
        raise VoiceError("Dossier de mission invalide.")
    text = _spoken_text(folder, config)
    settings = get_settings()
    if provider == "elevenlabs" and not authorize_cloud_charge:
        raise VoiceError("Confirme explicitement l'utilisation potentielle de crédits ElevenLabs.")
    if provider == "elevenlabs" and (not shutil.which("ffmpeg")):
        raise VoiceError("FFmpeg est requis avant un appel ElevenLabs payant.")
    if config.get("viral_template") and provider != "elevenlabs":
        raise VoiceError("Ce storyboard exige un alignement des mots. Utilise ElevenLabs avec timestamps ; Qwen3 ne retourne pas de minutage vérifié.")
    if (folder / "stickman_project" / "output" / "preview.mp4").is_file():
        raise VoiceError("L'aperçu existe déjà ; utilise une nouvelle révision pour modifier la voix.")
    with LOCK:
        # The expensive operation is guarded: no automatic retry, no background request.
        wav = folder / "narration.wav"
        with tempfile.TemporaryDirectory(prefix="voice-", dir=folder) as temp:
            temporary_wav = Path(temp) / "narration.wav"
            if provider == "elevenlabs":
                voice_id = settings["elevenlabs_voice_id"] or os.environ.get("ELEVENLABS_VOICE_ID", "")
                speed = 1.08 if config.get("viral_template") else 1.0
                audio, word_times = _elevenlabs_audio(text, voice_id=voice_id,
                                                      allow_charge=authorize_cloud_charge, speed=speed)
                mp3 = Path(temp) / "narration.mp3"
                mp3.write_bytes(audio)
                _convert_mp3_to_wav(mp3, temporary_wav)
                voice = voice_id
            elif provider == "qwen3_local":
                _local_qwen(text, temporary_wav, settings["qwen_speaker"], settings["qwen_style"])
                word_times = None
                voice = settings["qwen_speaker"]
            else:
                raise VoiceError("Pour Piper, utilise le générateur intégré de narration.")
            piper_tts._wav_info(temporary_wav)
            os.replace(temporary_wav, wav)
        meta = _complete(folder, provider=provider, voice=voice, text=text, word_times=word_times)
        engine.add_event(job_id, "voice_studio", "done",
                         f"Narration {provider} reçue ({meta['duration_seconds']} s). "
                         f"Alignement réel : {'oui' if word_times else 'non'}.")
        return meta


def generate_preview(text: str, provider: str, *, authorize_cloud_charge=False):
    if provider not in PROVIDERS:
        raise VoiceError("Fournisseur inconnu.")
    if not isinstance(text, str) or not 12 <= len(text) <= 500:
        raise VoiceError("Le test vocal doit contenir de 12 à 500 caractères.")
    output = ROOT / ".production_os_runtime" / "voice_studio_preview.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    with LOCK:
        if provider == "piper":
            piper_tts.synthesize_to_wav(text, output, piper_tts.get_settings())
        elif provider == "qwen3_local":
            _local_qwen(text, output, settings["qwen_speaker"], settings["qwen_style"])
        else:
            if not authorize_cloud_charge:
                raise VoiceError("Confirme le coût potentiel de chaque test ElevenLabs.")
            if not shutil.which("ffmpeg"):
                raise VoiceError("FFmpeg manquant.")
            vid = settings["elevenlabs_voice_id"] or os.environ.get("ELEVENLABS_VOICE_ID", "")
            audio, _ = _elevenlabs_audio(text, voice_id=vid, allow_charge=True, speed=1.0)
            with tempfile.TemporaryDirectory(prefix="voice-preview-") as folder:
                mp3 = Path(folder) / "preview.mp3"
                mp3.write_bytes(audio)
                _convert_mp3_to_wav(mp3, output)
    seconds, rate = piper_tts._wav_info(output)
    return {"provider": provider, "duration_seconds": round(seconds, 2),
            "sample_rate": rate, "path": str(output)}
