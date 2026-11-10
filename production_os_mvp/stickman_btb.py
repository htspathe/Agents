"""Budget-zero, local Stickman Autopilot bridge for Brain to Billions.

Produces a fully encoded *review* MP4. It never uploads or claims editorial approval.
Requires Node >=20, FFmpeg/FFprobe and installed directing-stickman-videos Skill.
Narration uses the pinned Qwen3-TTS synthetic reference; never Windows SAPI.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PREVIEW_LIMIT = 59.0


def _run(args, *, timeout=300):
    proc = subprocess.run([str(v) for v in args], capture_output=True, text=True,
                          errors="replace", timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} failed: {proc.stderr[-850:] or proc.stdout[-850:]}")
    return proc.stdout


def find_skill() -> Path:
    home = Path.home()
    candidates = [
        os.environ.get("STICKMAN_SKILL_DIR", ""),
        home / ".codex" / "skills" / "directing-stickman-videos",
        home / "Production-OS" / "Outils" / "Stickman-Video-Director" / "skills" / "directing-stickman-videos",
        home / "Documents" / "ChatGPT" / "Motion" / "stickman-video-director" / "skills" / "directing-stickman-videos",
    ]
    for item in candidates:
        if item and (Path(item) / "scripts" / "autopilot.mjs").is_file() and (Path(item) / "SKILL.md").is_file():
            return Path(item).resolve()
    raise FileNotFoundError(
        "Stickman introuvable. Définir STICKMAN_SKILL_DIR avec le dossier contenant SKILL.md."
    )


def _duration(media: Path) -> float:
    data = json.loads(_run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,width,height",
                            "-of", "json", str(media)], timeout=35))
    duration = float(data.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise RuntimeError(f"Durée du média invalide : {media.name}")
    return duration


from qwen_btb_voice import (
    PROFILE_PATH as VOICE_PROFILE_PATH,
    load_profile as load_voice_profile,
    synthesize as synthesize_voice,
)


def _produce_narration(script: str, folder: Path, wav: Path, profile: dict, language: str = "fr") -> str:
    """Qwen3-TTS only: fixed synthetic reference for all Brain to Billions episodes."""
    return synthesize_voice(script, wav, language, profile)


def captions_for(script: str, duration: float) -> list[dict]:
    """Approximation de timings : non assimilable à un alignement phonétique."""
    words = re.findall(r"\S+", script)
    if not words:
        raise ValueError("Le script ne contient aucun mot.")
    chunks = [" ".join(words[i:i + 4]) for i in range(0, len(words), 4)]
    weights = [max(1, len(re.findall(r"\w+", cue))) for cue in chunks]
    total = sum(weights)
    cursor = 0.08
    available = max(1, duration - 0.22)
    result = []
    for i, (cue, weight) in enumerate(zip(chunks, weights)):
        span = available * weight / total
        end = duration - 0.08 if i == len(chunks) - 1 else cursor + span
        result.append({"start": round(cursor, 3), "end": round(end, 3), "text": cue})
        cursor = end
    return result


def _storyboard(duration: float, portrait: bool) -> list[dict]:
    count = max(4, math.ceil(duration / 2.5))
    titles = ["L'IDÉE", "LE MÉCANISME", "L'EXEMPLE", "LES RISQUES", "À RETENIR"]
    shots = []
    for i in range(count):
        start, end = duration * i / count, duration * (i + 1) / count
        title = titles[min(len(titles) - 1, i * len(titles) // count)]
        if portrait:
            heading = {"type": "text", "text": title, "x": 130, "y": 270,
                       "size": 72, "bold": True, "color": "#212121"}
            x, y, w = 215, 720, 790
            arrow_y = 1570
        else:
            heading = {"type": "text", "text": title, "x": 90, "y": 105,
                       "size": 66, "bold": True, "color": "#212121"}
            x, y, w = 650, 130, 415
            arrow_y = 500
        shots.append({
            "start": round(start, 5), "end": round(end, 5),
            "layers": [
                heading,
                {"type": "pose", "pose": i % 3, "x": x, "y": y, "w": w,
                 "keys": [{"t": 0, "alpha": 0, "y": y + 36},
                          {"t": 0.4, "alpha": 1, "y": y},
                          {"t": round((end - start) * .8, 3), "x": x + 15}]},
                {"type": "line", "points": [[120, arrow_y], [440 if portrait else 540, arrow_y]],
                 "color": "#b68e30", "lineWidth": 8,
                 "keys": [{"t": 0, "progress": 0}, {"t": 0.8, "progress": 1}]},
            ],
        })
    return shots


def _make_project(folder: Path, wav: Path, script: str, duration: float, portrait: bool) -> Path:
    project_dir = folder / ("stickman_9x16" if portrait else "stickman_16x9")
    project_dir.mkdir(exist_ok=True)
    shutil.copyfile(wav, project_dir / "narration.wav")
    data = {
        "version": 1, "style": "B1",
        "width": 720 if portrait else 1280,
        "height": 1280 if portrait else 720, "fps": 30,
        "voice": {"path": "narration.wav", "lead": 0, "tail": 0},
        "captions": captions_for(script, duration),
        "shots": _storyboard(duration, portrait),
        "sfx": [],
    }
    project_file = project_dir / "project.json"
    project_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return project_file


def render(ctx: dict, folder: Path) -> str:
    """Create an actual voiced MP4 review asset, never a silent title-card placeholder."""
    from agents import llm, write_json, write_text
    skill = find_skill()
    for binary in ("node", "ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            raise RuntimeError(f"{binary} manque dans PATH.")
    cli = skill / "scripts" / "autopilot.mjs"
    _run(["node", cli, "doctor"], timeout=75)
    idea = str(ctx.get("brief", "")).strip()
    explicitly_provided = bool(str(ctx.get("narration", "")).strip())
    language = ctx.get("voice_language", "fr")
    if language not in ("fr", "en"):
        raise ValueError("Choisir le français ou l'anglais pour la voix.")
    fallback = (
        f"Pourquoi s'intéresser à cette question : {idea} ? "
        "Pour comprendre un mécanisme financier, il faut d'abord identifier ce qui crée de la valeur, "
        "puis examiner les coûts, les risques et les hypothèses. "
        "Un scénario favorable n'est jamais une garantie. "
        "La prochaine étape consiste à comparer des exemples documentés "
        "et à vérifier les chiffres avant de prendre une décision."
    )
    if language == "en":
        fallback = (
            f"Why does this question matter: {idea}? "
            "A good financial explanation begins with the source of value, the costs, "
            "and the risks. A positive scenario is never a guarantee of future returns. "
            "Before drawing conclusions, compare documented examples and verify the "
            "numbers independently. Understanding uncertainty matters more than "
            "claiming that an investment is certain to succeed."
        )
    if explicitly_provided:
        script = str(ctx["narration"]).strip()
    else:
        if language == "en":
            prompt = (
                "Write only an original English narration of 65 to 95 words for a Brain to Billions video. "
                "No headings, Markdown, invented figures, invented citations or guarantees of profit. "
                "Explain the risks and nuances. Topic: " + idea
            )
        else:
            prompt = (
                "Rédige uniquement un script de narration original en français de 65 à 95 mots "
                "pour une vidéo courte du média Brain to Billions. Pas de titres Markdown, de balises "
                "ni de chiffres, sources ou citations inventés. Présente les risques et nuances "
                f"du sujet suivant : {idea}"
            )
        script = llm(prompt, fallback, ctx).strip()
    if len(script) < 45:
        raise ValueError("Script trop court pour produire une vidéo intelligible.")
    if len(script) > 2400:
        raise ValueError("Le MVP accepte au maximum 2 400 caractères de narration.")
    write_text(folder, "narration.txt", script)
    wav = folder / "narration.wav"
    profile = load_voice_profile()
    voice_name = _produce_narration(script, folder, wav, profile, language)
    duration = _duration(wav)
    if not 8 <= duration <= PREVIEW_LIMIT:
        raise RuntimeError(
            f"Voix de {duration:.1f}s : MVP limité à 8–59s, pour l'aperçu Autopilot. "
            "Découper la narration ou utiliser le rendu complet avec approbation."
        )
    outputs = {}
    formats = [False, True] if os.environ.get("BTB_BOTH_FORMATS", "1") == "1" else [True]
    for portrait in formats:
        project = _make_project(folder, wav, script, duration, portrait)
        seconds = f"{duration:.4f}"
        _run(["node", cli, "validate", project, "--seconds", seconds], timeout=120)
        _run(["node", cli, "preview", project, "--seconds", seconds], timeout=600)
        source = project.parent / "output" / "preview.mp4"
        if not source.is_file() or source.stat().st_size < 5000:
            raise RuntimeError("Autopilot n'a pas créé le fichier MP4 attendu.")
        target = folder / ("brain_to_billions_9x16.mp4" if portrait else "brain_to_billions_16x9.mp4")
        shutil.copyfile(source, target)
        _duration(target)
        for ext in ("srt", "vtt"):
            sidecar = source.with_suffix("." + ext)
            if sidecar.is_file():
                shutil.copyfile(sidecar, target.with_suffix("." + ext))
        outputs["9:16" if portrait else "16:9"] = target.name
    write_json(folder, "stickman_report.json", {
        "engine": "Stickman Video Director / Autopilot B1",
        "skill_dir": str(skill), "voice": voice_name,
        "narrator_key": profile["narrator_key"],
        "voice_id": profile["voice_id"],
        "voice_language": language,
        "voice_reference_sha256": profile["reference_sha256"],
        "voice_lock_applied": True,
        "voice_approval_status": profile["approval_status"],
        "voice_profile_sha256": hashlib.sha256(VOICE_PROFILE_PATH.read_bytes()).hexdigest(),
        "duration_seconds": round(duration, 3),
        "outputs": outputs, "subtitles_alignment": "ESTIMATED_NOT_FORCED_ALIGNED",
        "narration_source": "user" if explicitly_provided else "local_llm_or_fallback",
        "external_api_cost": 0, "youtube_upload": "DISABLED",
        "publication_ready": False,
        "pending": (["approbation de la voix permanente"] if profile["approval_status"] != "approved" else []) + ["écoute humaine", "relecture éditoriale et sources",
                    "vérification du timing des sous-titres",
                    "approbation de l'habillage Brain to Billions"],
    })
    ctx["notes"].append(
        "Stickman : MP4 avec narration produit localement; sous-titres estimés. "
        "Validation humaine obligatoire avant publication manuelle."
    )
    return outputs.get("9:16", next(iter(outputs.values())))
