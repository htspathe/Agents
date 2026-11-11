"""Strict timeline contract for Brain to Billions vertical stickman production.

This file validates a *creative specification*, not a rendered video. The
provider-specific adapters must obtain real word timestamps before scheduling
visual triggers; no timing is fabricated from punctuation or word counts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATES = {
    "brain_to_billions_lifestyle_inflation": ROOT / "storyboards" / "brain_to_billions_lifestyle_inflation.json"
}
PHASES = ("HOOK", "MECANISME", "CHUTE", "ACTION_LOOP")
PHASE_BOUNDS = {"HOOK": (0, 3), "MECANISME": (3, 18), "CHUTE": (18, 26), "ACTION_LOOP": (26, 35)}
EMOTIONS = {"neutre", "surpris", "euphorique", "fier", "inquiet", "triste", "ecrase", "determine"}
IDLES = {"idle_breathe", "walk_cycle", "bounce", "tremble"}
CAMERAS = {"slow_zoom_in", "slow_zoom_out", "pan_right", "shake_light"}
SFX = {"pop_soft", "whoosh_fast", "cash_register", "tick_up", "thud_heavy", "ding_idea"}
BGM = {"lofi_chill", "dark_synth_pulse", "minimal_piano_tension", "upbeat_modern"}


def _spoken_words(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", text.casefold())


def validate_timeline(data: dict) -> dict:
    """Reject malformed or misleading timelines before any renderer consumes them."""
    if not isinstance(data, dict) or set(data) != {"metadata", "scenes"}:
        raise ValueError("Le storyboard doit contenir uniquement metadata et scenes.")
    meta, scenes = data["metadata"], data["scenes"]
    if not isinstance(meta, dict) or not isinstance(scenes, list) or not scenes:
        raise ValueError("Métadonnées ou scènes absentes.")
    duration = meta.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 30 <= duration <= 40:
        raise ValueError("La durée doit être comprise entre 30 et 40 secondes.")
    if meta.get("resolution") != "1080x1920" or meta.get("fps") != 30:
        raise ValueError("La vidéo doit être verticale 1080x1920 à 30 FPS.")
    if meta.get("bgm_mood") not in BGM or meta.get("visual_lead_in_ms") != -100:
        raise ValueError("Ambiance sonore ou anticipation visuelle invalide.")
    if meta.get("safe_zone_subtitles_y") != 1340:
        raise ValueError("La zone sûre des sous-titres n'est pas conforme.")
    if meta.get("subtitle_highlight_color") != "#D4A359" or meta.get("subtitle_highlight_scale") != 1.15:
        raise ValueError("La mise en évidence des sous-titres n'est pas conforme.")
    if meta.get("requires_word_timestamps") is not True:
        raise ValueError("Il faut de vrais timestamps mot à mot pour déclencher les visuels.")
    if not meta.get("topic") or not meta.get("voice_provider"):
        raise ValueError("Sujet ou fournisseur vocal absent.")
    expected_start = 0.0
    phases = []
    spoken = []
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict) or scene.get("scene_id") != index:
            raise ValueError(f"ID invalide pour la scène {index}.")
        start, end = scene.get("start_time_s"), scene.get("end_time_s")
        if any(not isinstance(t, (int, float)) or isinstance(t, bool) for t in (start, end)):
            raise ValueError(f"Temps non numérique pour la scène {index}.")
        if abs(start - expected_start) > 0.001 or not start < end or end - start > 3.001:
            raise ValueError(f"Trou, chevauchement ou scène trop statique : {index}.")
        phase = scene.get("narrative_phase")
        if phase not in PHASES:
            raise ValueError(f"Phase narrative inconnue : {index}.")
        lower, upper = PHASE_BOUNDS[phase]
        if start < lower - 0.001 or end > upper + 0.001:
            raise ValueError(f"Scène {index} hors de sa phase {phase}.")
        phases.append(phase)
        if scene.get("camera_movement") not in CAMERAS:
            raise ValueError(f"Caméra absente ou inconnue à la scène {index}.")
        character = scene.get("character")
        if not isinstance(character, dict) or character.get("emotion") not in EMOTIONS or character.get("idle_animation") not in IDLES:
            raise ValueError(f"Personnage incohérent à la scène {index}.")
        speech = scene.get("voiceover_text")
        if not isinstance(speech, str) or not speech.strip():
            raise ValueError(f"Voix off manquante à la scène {index}.")
        spoken.append(speech.strip())
        speech_lower = speech.casefold()
        visuals = scene.get("visual_elements")
        if not isinstance(visuals, list) or not visuals:
            raise ValueError(f"Éléments visuels absents à la scène {index}.")
        for element in visuals:
            if not isinstance(element, dict):
                raise ValueError(f"Élément visuel invalide à la scène {index}.")
            trigger = element.get("trigger_word")
            if not isinstance(trigger, str) or trigger.casefold() not in speech_lower:
                raise ValueError(f"Mot de déclenchement absent du texte à la scène {index}.")
            if element.get("lead_in_ms") != -100 or element.get("sfx_trigger") not in SFX:
                raise ValueError(f"Synchronisation ou SFX invalide à la scène {index}.")
            if not isinstance(element.get("sfx_volume_db"), (int, float)):
                raise ValueError(f"Volume du SFX absent à la scène {index}.")
        chunks = scene.get("subtitles_chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError(f"Sous-titres absents à la scène {index}.")
        chunks_words = []
        for chunk in chunks:
            text = chunk.get("text") if isinstance(chunk, dict) else None
            highlight = chunk.get("highlight_word") if isinstance(chunk, dict) else None
            if not isinstance(text, str) or not 1 <= len(_spoken_words(text)) <= 5:
                raise ValueError(f"Sous-titre de plus de cinq mots à la scène {index}.")
            if not isinstance(highlight, str) or highlight.casefold() not in text.casefold():
                raise ValueError(f"Mot à mettre en évidence introuvable à la scène {index}.")
            chunks_words.extend(_spoken_words(text))
        if chunks_words != _spoken_words(speech):
            raise ValueError(f"Les sous-titres ne reproduisent pas mot pour mot la narration à la scène {index}.")
        expected_start = float(end)
    if abs(expected_start - duration) > 0.001:
        raise ValueError("La dernière scène ne termine pas à la durée annoncée.")
    if list(dict.fromkeys(phases)) != list(PHASES):
        raise ValueError("Les quatre phases narratives doivent être présentes dans l'ordre.")
    if scenes[-1].get("seamless_loop_to_scene_id") != 1 or meta.get("loop_enabled") is not True:
        raise ValueError("La dernière scène doit boucler sur le hook.")
    return {"valid": True, "scenes": len(scenes), "duration_seconds": duration,
            "requires_word_timestamps": True, "audio_rendered": False, "video_rendered": False}


def load_template(template_id: str) -> dict:
    path = TEMPLATES.get(template_id)
    if path is None:
        raise ValueError("Storyboard inconnu ou non autorisé.")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_timeline(data)
    return data


def stage_template(folder: Path, template_id: str) -> dict:
    """Persist a validated spec in a job, never claim it was rendered."""
    data = load_template(template_id)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "viral_timeline.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    speech = " ".join(scene["voiceover_text"].strip() for scene in data["scenes"])
    (folder / "viral_voiceover.txt").write_text(speech + "\n", encoding="utf-8")
    report = validate_timeline(data)
    report.update({
        "template": template_id,
        "voice_provider_requested": data["metadata"]["voice_provider"],
        "voice_status": "not_generated",
        "word_alignment_status": "pending",
        "render_status": "not_rendered",
        "warning": "JSON de réalisation uniquement ; la voix ElevenLabs, le time-alignment et le MP4 restent à produire. Aucune API payante n'est appelée."
    })
    (folder / "viral_timeline_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
