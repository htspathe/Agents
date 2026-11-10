"""Local Piper TTS integration for Production OS."""
from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ".production_os_piper.json"
DEFAULT_VOICE = "fr_FR-siwis-medium"
VOICE_PATTERN = re.compile(r"^[a-z]{2}_[A-Z]{2}-[a-z0-9_-]+-(?:x_low|low|medium|high)$")
VOICE_LICENSE = "CC BY 4.0"
ENGINE_LICENSE = "GPL-3.0-or-later"
VOICE_CREDIT = (
    "fr_FR-siwis-medium — SIWIS French Speech Synthesis Database (2017), "
    "Junichi Yamagishi, Pierre-Edouard Honnet, Philip Garner et Alexandros Lazaridis. "
    "Licence CC BY 4.0. Modèle : "
    "https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/siwis/medium"
)
DEFAULT_SETTINGS = {"enabled": True, "voice": DEFAULT_VOICE, "python_path": "", "voice_dir": "", "sentence_silence": 0.18}
_STATUS_CACHE = None
_STATUS_CACHE_AT = 0.0
_PACKAGE_CACHE = {}
CACHE_TTL = 12.0


class PiperError(RuntimeError):
    pass


def settings_path():
    return ROOT / SETTINGS_FILE


def get_settings():
    result = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            if isinstance(data.get("enabled"), bool):
                result["enabled"] = data["enabled"]
            voice = data.get("voice")
            if isinstance(voice, str) and VOICE_PATTERN.fullmatch(voice.strip()):
                result["voice"] = voice.strip()
            for key in ("python_path", "voice_dir"):
                if isinstance(data.get(key), str):
                    result[key] = data[key].strip()[:1000]
            silence = data.get("sentence_silence")
            if isinstance(silence, (int, float)) and not isinstance(silence, bool) and 0 <= float(silence) <= 1:
                result["sentence_silence"] = float(silence)
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Configuration Piper invalide.")
    old = get_settings()
    enabled = data.get("enabled", old["enabled"])
    voice = data.get("voice", old["voice"])
    python_path = data.get("python_path", old["python_path"])
    voice_dir = data.get("voice_dir", old["voice_dir"])
    silence = data.get("sentence_silence", old["sentence_silence"])
    if not isinstance(enabled, bool):
        raise ValueError("Le paramètre d'activation Piper doit être booléen.")
    if not isinstance(voice, str) or not VOICE_PATTERN.fullmatch(voice.strip()):
        raise ValueError("Identifiant de voix Piper invalide.")
    if not isinstance(python_path, str) or len(python_path.strip()) > 1000:
        raise ValueError("Chemin Python Piper invalide.")
    if not isinstance(voice_dir, str) or len(voice_dir.strip()) > 1000:
        raise ValueError("Dossier des voix Piper invalide.")
    if isinstance(silence, bool) or not isinstance(silence, (int, float)) or not 0 <= float(silence) <= 1:
        raise ValueError("Le silence entre phrases doit être compris entre 0 et 1 seconde.")
    result = {"enabled": enabled, "voice": voice.strip(), "python_path": python_path.strip(),
              "voice_dir": voice_dir.strip(), "sentence_silence": float(silence)}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    invalidate_status_cache()
    return result


def invalidate_status_cache():
    global _STATUS_CACHE, _STATUS_CACHE_AT
    _STATUS_CACHE, _STATUS_CACHE_AT = None, 0.0
    _PACKAGE_CACHE.clear()


def _python_candidates(settings):
    explicit = settings.get("python_path") or os.environ.get("PIPER_PYTHON", "").strip()
    if explicit:
        return [shutil.which(explicit) or explicit]
    result = []
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        result.append(str(Path(local) / "ProductionOS" / "Piper" / ".venv" / "Scripts" / "python.exe"))
    result.extend(["C:/Tools/Piper/.venv/Scripts/python.exe",
                   str(ROOT / "tools" / "piper" / ".venv" / "Scripts" / "python.exe"), sys.executable])
    found = shutil.which("python")
    if found:
        result.append(found)
    unique = []
    for item in result:
        item = shutil.which(item) or item
        if item not in unique:
            unique.append(item)
    return unique


def _has_piper(python_path):
    now = time.monotonic()
    cached = _PACKAGE_CACHE.get(python_path)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        cp = subprocess.run([python_path, "-c", "import piper; print('PIPER_OK')"],
                            capture_output=True, text=True, timeout=6, check=False)
        ready = cp.returncode == 0 and "PIPER_OK" in cp.stdout
    except (OSError, subprocess.SubprocessError):
        ready = False
    _PACKAGE_CACHE[python_path] = (now, ready)
    return ready


def _voice_dirs(settings):
    explicit = settings.get("voice_dir") or os.environ.get("PIPER_VOICE_DIR", "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    result = []
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        result.append(Path(local) / "ProductionOS" / "Piper" / "voices")
    result.extend([Path("C:/Tools/Piper"), ROOT / "tools" / "piper" / "voices",
                   Path.home() / ".local" / "share" / "piper" / "voices", Path.cwd()])
    return list(dict.fromkeys(path.expanduser() for path in result))


def _voice_files(directory, voice):
    model, config = directory / f"{voice}.onnx", directory / f"{voice}.onnx.json"
    if model.is_file() and config.is_file() and model.stat().st_size > 1024:
        return model, config
    return None


def get_status(refresh=False):
    global _STATUS_CACHE, _STATUS_CACHE_AT
    now = time.monotonic()
    if not refresh and _STATUS_CACHE is not None and now - _STATUS_CACHE_AT < CACHE_TTL:
        return json.loads(json.dumps(_STATUS_CACHE))
    settings = get_settings()
    python_path = None
    for candidate in _python_candidates(settings):
        if (Path(candidate).is_file() or shutil.which(candidate)) and _has_piper(candidate):
            python_path = candidate
            break
    voice_dir = model_path = None
    for candidate in _voice_dirs(settings):
        files = _voice_files(candidate, settings["voice"])
        if files:
            voice_dir, model_path = str(candidate.resolve()), str(files[0].resolve())
            break
    enabled = settings["enabled"]
    ready = bool(enabled and python_path and voice_dir and model_path)
    if not enabled:
        note = "Piper est désactivé dans les paramètres."
    elif not python_path:
        note = "Piper non détecté. Lance install_piper_windows.ps1 ou renseigne le Python de son environnement."
    elif not voice_dir:
        note = f"Modèle {settings['voice']} absent. Télécharge la voix avec le script d'installation Piper."
    else:
        note = f"Piper prêt avec {settings['voice']}."
    status = {"enabled": enabled, "installed": bool(python_path), "voice_available": bool(voice_dir),
              "ready": ready, "voice": settings["voice"], "python_path": python_path or settings["python_path"],
              "voice_dir": voice_dir or settings["voice_dir"], "model_path": model_path, "note": note,
              "voice_license": VOICE_LICENSE, "voice_attribution": VOICE_CREDIT,
              "engine_license": ENGINE_LICENSE, "offline": True}
    _STATUS_CACHE, _STATUS_CACHE_AT = status, now
    return json.loads(json.dumps(status))


def extract_voiceover(markdown):
    if not isinstance(markdown, str):
        return ""
    pattern = re.compile(r"(?ims)^#{1,6}\s*(?:voix\s*off|narration|script\s+(?:a|à)\s+lire|texte\s+(?:a|à)\s+lire|voice[\s-]?over)[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)")
    match = pattern.search(markdown)
    if not match:
        return ""
    lines = []
    for line in match.group(1).splitlines():
        value = line.strip()
        if not value:
            continue
        value = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", value)
        value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
        value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
        value = value.replace(chr(96), "")
        value = re.sub(r"[*_#>]", "", value)
        lines.append(value)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _wav_info(path):
    try:
        with wave.open(str(path), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            if frames <= 0 or rate <= 0:
                raise PiperError("Le WAV Piper ne contient aucun échantillon audio.")
            return frames / float(rate), rate
    except (wave.Error, OSError) as exc:
        raise PiperError("Piper a produit un WAV illisible.") from exc


def synthesize_to_wav(text, output_path, settings=None):
    settings = settings or get_settings()
    status = get_status(refresh=True)
    if not status["ready"]:
        raise PiperError(status["note"])
    spoken = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(spoken) < 12:
        raise PiperError("Le texte de narration est trop court.")
    if len(spoken) > 30000:
        raise PiperError("Le texte de narration dépasse 30 000 caractères.")
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="production-os-piper-", dir=str(output_path.parent)) as temp:
        text_file = Path(temp) / "narration.txt"
        text_file.write_text(spoken, encoding="utf-8")
        command = [status["python_path"], "-m", "piper", "-m", settings["voice"],
                   "--data-dir", status["voice_dir"], "--input-file", str(text_file),
                   "-f", str(output_path), "--sentence-silence", str(settings["sentence_silence"])]
        try:
            cp = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PiperError(f"Impossible de lancer Piper ({type(exc).__name__}).") from exc
        if cp.returncode != 0 or not output_path.is_file():
            detail = (cp.stderr or cp.stdout or "aucun détail").strip()[-1200:]
            raise PiperError("Échec de synthèse Piper : " + detail)
    duration, rate = _wav_info(output_path)
    return {"file": output_path.name, "duration_seconds": round(duration, 2), "sample_rate": rate,
            "size_bytes": output_path.stat().st_size, "voice": settings["voice"], "provider": "Piper local"}


def _write_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _stage_for_stickman(folder):
    if not (folder / "narration.wav").is_file():
        return
    project = folder / "stickman_project"
    if not project.is_dir():
        return
    assets = project / "assets" / "audio"
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("narration.wav", "narration.md", "narration_metadata.json", "voice_attribution.md"):
        source = folder / name
        if source.is_file():
            shutil.copy2(source, assets / name)


def prepare_job_narration(folder, ctx, force=False):
    folder = Path(folder).resolve()
    content_file = folder / "content.md"
    content = content_file.read_text(encoding="utf-8") if content_file.is_file() else ""
    narration = extract_voiceover(content)
    if not narration:
        note = ("Aucune section de narration dédiée n'a été trouvée dans content.md.\n"
                "Le Content Strategist doit produire une section « Voix off intégrale » avant Piper.\n")
        (folder / "narration_blocker.md").write_text(note, encoding="utf-8")
        return {"ready": False, "note": note.strip(), "voice": get_settings()["voice"]}
    settings, status = get_settings(), get_status(refresh=True)
    (folder / "narration.md").write_text(narration + "\n", encoding="utf-8")
    if not status["ready"]:
        note = ("Narration préparée, mais Piper n'est pas prêt.\n\n"
                f"- Voix demandée : {settings['voice']}\n- Diagnostic : {status['note']}\n"
                "- Installer Piper et télécharger le modèle vocal, puis relancer Stickman.\n")
        (folder / "narration_blocker.md").write_text(note, encoding="utf-8")
        return {"ready": False, "note": status["note"], "voice": settings["voice"]}
    script_hash = hashlib.sha256(narration.encode("utf-8")).hexdigest()
    audio_path, metadata_path = folder / "narration.wav", folder / "narration_metadata.json"
    if not force and audio_path.is_file() and metadata_path.is_file():
        try:
            old = json.loads(metadata_path.read_text(encoding="utf-8"))
            if old.get("script_sha256") == script_hash and old.get("voice") == settings["voice"]:
                _stage_for_stickman(folder)
                (folder / "narration_blocker.md").unlink(missing_ok=True)
                return {"ready": True, **old, "reused": True}
        except (OSError, ValueError, TypeError):
            pass
    try:
        result = synthesize_to_wav(narration, audio_path, settings)
    except PiperError as exc:
        audio_path.unlink(missing_ok=True)
        note = f"La synthèse Piper a échoué : {exc}"
        (folder / "narration_blocker.md").write_text(note + "\\n", encoding="utf-8")
        return {"ready": False, "note": note, "voice": settings["voice"]}
    metadata = {**result, "voice": settings["voice"], "voice_license": VOICE_LICENSE,
                "voice_attribution": VOICE_CREDIT, "engine_license": ENGINE_LICENSE,
                "script_sha256": script_hash, "script_characters": len(narration),
                "target_duration_seconds": int(ctx.get("stickman_duration", 60) or 60),
                "created_at": time.time(), "offline": True}
    _write_json(metadata_path, metadata)
    attribution = ("# Attribution de la voix\n\n" + VOICE_CREDIT + "\n\n"
                   "Le moteur Piper local est distribué sous licence GPL-3.0-or-later. "
                   "La licence du moteur et celle du modèle vocal sont distinctes.\n")
    (folder / "voice_attribution.md").write_text(attribution, encoding="utf-8")
    (folder / "narration_blocker.md").unlink(missing_ok=True)
    _stage_for_stickman(folder)
    return {"ready": True, **metadata, "reused": False}


def ensure_job_narration(folder, ctx, phase="start"):
    folder = Path(folder).resolve()
    audio, metadata = folder / "narration.wav", folder / "narration_metadata.json"
    if phase == "resume":
        if not audio.is_file() or not metadata.is_file():
            raise PiperError("La narration Piper approuvée est introuvable. Restaure narration.wav et narration_metadata.json ; aucune autre voix ne sera substituée.")
        _stage_for_stickman(folder)
        return json.loads(metadata.read_text(encoding="utf-8"))
    return prepare_job_narration(folder, ctx, force=False)


def test_audio_path():
    return ROOT / ".production_os_runtime" / "piper_test.wav"


def generate_test_audio(text=None):
    sample = text or "Bienvenue sur Brain to Billions. Aujourd'hui, nous allons comprendre comment fonctionne l'argent."
    path = test_audio_path()
    result = synthesize_to_wav(sample, path, get_settings())
    result["path"] = str(path)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Production OS Piper TTS helper")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    test = sub.add_parser("test")
    test.add_argument("--text", default="")
    args = parser.parse_args()
    if args.command == "status":
        status = get_status(refresh=True)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ready"] else 1
    print(json.dumps(generate_test_audio(args.text or None), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
