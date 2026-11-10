"""Locked, local Qwen3-TTS narrator for Brain to Billions.

Reference file is a synthetic voice supplied by its publisher and verified with SHA256.
No fallback to another speaker, no network fetch at inference (model weights may need
their initial download), and no external paid speech API.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
PROFILE_PATH = HERE / "brain_to_billions_voice.json"
MAX_REFERENCE_BYTES = 2_000_000


def load_profile(path: Path | None = None) -> dict:
    p = path or PROFILE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    required = (
        "brand", "narrator_key", "provider", "voice_name", "voice_id",
        "reference_audio", "reference_sha256", "reference_text",
        "reference_source_url", "model_id", "model_revision",
        "approval_status", "languages", "seed",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"Profil de voix incomplet: {', '.join(missing)}")
    if data["provider"] != "qwen3_tts_voice_clone":
        raise ValueError("Seul le narrateur Qwen3-TTS verrouillé est autorisé.")
    if data["approval_status"] != "approved":
        raise ValueError("Le narrateur n'a pas été validé.")
    if not all(isinstance(data[key], str) and data[key].strip() for key in (
        "narrator_key", "voice_name", "voice_id", "reference_text",
        "reference_source_url", "model_id", "model_revision",
    )):
        raise ValueError("Identité vocale ou référence incomplète.")
    if data["model_id"] != "Qwen/Qwen3-TTS-12Hz-1.7B-Base":
        raise ValueError("Le modèle Base 1.7B est verrouillé pour ce narrateur.")
    if not isinstance(data["seed"], int) or not 0 <= data["seed"] <= 4294967295:
        raise ValueError("Graine de génération invalide.")
    digest = data["reference_sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Empreinte de référence invalide.")
    try:
        bytes.fromhex(digest)
    except ValueError as exc:
        raise ValueError("Empreinte de référence non hexadécimale.") from exc
    rel = Path(data["reference_audio"])
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() != ".flac":
        raise ValueError("Chemin relatif FLAC attendu pour la référence.")
    if not isinstance(data["languages"], dict) or data["languages"].get("fr") != "French" or data["languages"].get("en") != "English":
        raise ValueError("Les langues approuvées sont fr/French et en/English.")
    return data


def reference_path(profile: dict) -> Path:
    path = (HERE / profile["reference_audio"]).resolve()
    root = (HERE / "voice_assets").resolve()
    if path.parent != root:
        raise ValueError("Le fichier voix doit rester dans voice_assets/")
    return path


def verify_reference(profile: dict) -> Path:
    path = reference_path(profile)
    if not path.is_file():
        raise FileNotFoundError(
            f"Référence Confident Male absente: {path}. "
            "Exécuter: python setup_btb_voice.py (réseau requis une seule fois)."
        )
    if path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("Fichier de référence anormalement volumineux.")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != profile["reference_sha256"]:
        raise ValueError("Référence vocale modifiée: SHA256 incorrect, génération bloquée.")
    return path


def install_reference(profile: dict | None = None) -> Path:
    """Explicit one-time reference download from pinned publisher URL, fail closed."""
    p = profile or load_profile()
    destination = reference_path(p)
    if destination.is_file():
        return verify_reference(p)
    source_url = p["reference_source_url"]
    allowed = "https://hangry-labs.github.io/faster-qwen3-tts-studio/examples/assets/references/confident_male.flac"
    if source_url != allowed:
        raise ValueError("URL d'installation non autorisée.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(source_url, headers={"User-Agent": "BrainToBillionsVoiceSetup/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(MAX_REFERENCE_BYTES + 1)
    if len(data) > MAX_REFERENCE_BYTES:
        raise ValueError("Référence vocale trop volumineuse.")
    if hashlib.sha256(data).hexdigest() != p["reference_sha256"]:
        raise ValueError("Référence téléchargée non conforme: SHA256 invalide.")
    staged = destination.with_suffix(".flac.tmp")
    try:
        staged.write_bytes(data)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    return verify_reference(p)


def qwen_python() -> str:
    """Dedicated Python environment; never silently fall back to an unrelated TTS."""
    selected = os.environ.get("BTB_QWEN_PYTHON", "").strip()
    if selected:
        candidate = Path(selected).expanduser()
    else:
        candidate = HERE / ".venv-qwen" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise RuntimeError(
            "Environnement Qwen absent. Créer .venv-qwen avec Python 3.12 puis "
            "installer 'qwen-tts' (voir BTB-STICKMAN.md). "
            "Ou définir BTB_QWEN_PYTHON vers son exécutable."
        )
    return str(candidate.resolve())


def synthesize(script: str, wav: Path, language: str, profile: dict | None = None) -> str:
    """Generate a WAV from the same locked FLAC and model for FR and EN."""
    p = profile or load_profile()
    if language not in p["languages"]:
        raise ValueError("Langue non autorisée : fr ou en uniquement.")
    if not script or len(script.strip()) < 10:
        raise ValueError("La narration est trop courte.")
    if os.environ.get("STICKMAN_VOICE_WAV", "").strip():
        raise ValueError("Voice Lock: l'injection d'une autre voix via STICKMAN_VOICE_WAV est interdite.")
    reference = verify_reference(p)
    target = wav.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "script": script,
        "target": str(target),
        "reference": str(reference),
        "reference_text": p["reference_text"],
        "language": p["languages"][language],
        "model": p["model_id"],
        "revision": p["model_revision"],
        "seed": p["seed"],
    }
    request = target.parent / "qwen_voice_request.json"
    request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    runner = HERE / "run_qwen_voice.py"
    proc = subprocess.run([qwen_python(), str(runner), str(request)],
                          capture_output=True, text=True, errors="replace", timeout=1800, check=False)
    if proc.returncode or not target.is_file() or target.stat().st_size < 5000:
        raise RuntimeError(
            "Qwen3-TTS a échoué (aucune voix de remplacement): "
            + (proc.stderr[-1000:] or proc.stdout[-1000:] or "audio absent")
        )
    return f"Qwen3-TTS | {p['voice_id']} | {p['languages'][language]}"


if __name__ == "__main__":
    profile = load_profile()
    installed = install_reference(profile)
    print(f"Référence Confident Male vérifiée: {installed}")
