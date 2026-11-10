"""CLI bridge between Stickman Autopilot and the Production OS review queue."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import engine


def _job_root(job_id: str) -> tuple[dict, Path]:
    if len(job_id) != 12 or any(ch not in "0123456789abcdef" for ch in job_id):
        raise ValueError("Identifiant de mission invalide")
    job = engine.get_job(job_id)
    if not job:
        raise ValueError("Mission introuvable")
    config = json.loads(job["input_json"])
    if job["workflow"] != "youtube" or not config.get("stickman_enabled", True):
        raise ValueError("Cette mission n'utilise pas Stickman")
    root = (engine.ROOT / "outputs" / job_id).resolve()
    output_root = (engine.ROOT / "outputs").resolve()
    if root.parent != output_root or not root.is_dir():
        raise ValueError("Dossier de mission indisponible")
    return job, root


def _source_file(root: Path, relative_path: str, extensions: set[str]) -> Path:
    relative = Path(relative_path)
    if not relative_path or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Chemin de livrable invalide")
    unresolved = root / relative
    if unresolved.is_symlink():
        raise ValueError("Les liens symboliques ne sont pas acceptés")
    source = unresolved.resolve()
    if root not in source.parents or not source.is_file():
        raise ValueError("Le fichier doit exister dans le dossier de cette mission")
    if source.suffix.lower() not in extensions:
        raise ValueError("Extension de fichier non autorisée")
    if source.stat().st_size < 1:
        raise ValueError("Le fichier est vide")
    return source



def _update_generated_reports(root: Path, manifest_status: str, extra: dict) -> None:
    manifest_path = root / "publishing_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = manifest_status
            manifest["auto_publish"] = False
            manifest["updated"] = engine.now()
            manifest["stickman"] = extra
            manifest["files_at_manifest_time"] = sorted(
                item.relative_to(root).as_posix()
                for item in root.rglob("*")
                if item.is_file() and not item.is_symlink()
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass

def mark_preview_ready(job_id: str, file: str) -> dict:
    job, root = _job_root(job_id)
    if job["status"] != "awaiting_external":
        raise ValueError("La mission n'attend pas un aperçu Stickman")
    source = _source_file(root, file, {".mp4"})
    destination = root / "stickman_preview.mp4"
    if source != destination:
        shutil.copy2(source, destination)
    engine.set_status(job_id, "awaiting_preview_approval")
    _update_generated_reports(root, "AWAITING_PREVIEW_APPROVAL", {
        "preview_received": True, "preview_file": destination.name, "final_render_received": False
    })
    engine.add_event(job_id, "stickman", "awaiting_preview_approval",
                     "Aperçu audio/vidéo importé ; validation humaine requise avant le rendu complet.")
    return {"id": job_id, "status": "awaiting_preview_approval", "preview": destination.name}


def mark_final_ready(job_id: str, file: str, captions: str) -> dict:
    job, root = _job_root(job_id)
    if job["status"] != "render_authorized":
        raise ValueError("Le rendu complet n'a pas encore été autorisé dans Production OS")
    source = _source_file(root, file, {".mp4"})
    caption_source = _source_file(root, captions, {".srt", ".vtt"})
    final_path = root / "stickman_final.mp4"
    caption_path = root / ("stickman_subtitles" + caption_source.suffix.lower())
    if source != final_path:
        shutil.copy2(source, final_path)
    if caption_source != caption_path:
        shutil.copy2(caption_source, caption_path)
    engine.set_status(job_id, "awaiting_approval")
    _update_generated_reports(root, "AWAITING_FINAL_APPROVAL", {
        "preview_received": True, "final_render_received": True,
        "final_video": final_path.name, "captions": caption_path.name
    })
    qa_path = root / "qa_report.json"
    if qa_path.is_file():
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            pending = qa.get("pending_human_checks", [])
            qa["pending_human_checks"] = [
                item for item in pending
                if "aperçu audio/vidéo et rendu final encore à produire" not in item
            ]
            qa["pending_human_checks"].append("Rendu final et sous-titres reçus : revue humaine finale requise")
            qa["stickman"] = {"final_video_received": True, "captions_received": True}
            qa["publication_ready"] = False
            qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass
    analytics_path = root / "analytics.json"
    if analytics_path.is_file():
        try:
            analytics = json.loads(analytics_path.read_text(encoding="utf-8"))
            analytics["video_exports"] = len([item for item in root.rglob("*.mp4") if item.is_file() and not item.is_symlink()])
            analytics["stickman_final_received"] = True
            analytics_path.write_text(json.dumps(analytics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass
    engine.add_event(job_id, "stickman", "awaiting_approval",
                     "Rendu final et sous-titres importés ; revue finale requise avant acceptation.")
    return {"id": job_id, "status": "awaiting_approval",
            "files": [final_path.name, caption_path.name]}


def mission_status(job_id: str) -> dict:
    detail = engine.job_detail(job_id)
    if not detail:
        raise ValueError("Mission introuvable")
    return {
        "id": job_id,
        "status": detail["status"],
        "latest_decisions": detail.get("decisions", [])[-3:],
        "next_step": {
            "awaiting_external": "Produire un aperçu Stickman puis appeler preview-ready.",
            "awaiting_preview_approval": "Attendre l'approbation de l'aperçu dans le dashboard.",
            "render_authorized": "Lancer le rendu complet puis appeler final-ready avec le MP4 et les sous-titres.",
            "awaiting_approval": "Attendre la revue finale dans le dashboard.",
            "approved": "Livrable accepté ; aucune publication automatique.",
            "changes_requested": "Une mission de correction a été créée ; arrêter l'ancienne mission.",
            "rejected": "Livrable rejeté ; aucune publication."
        }.get(detail["status"], "Consulter le dashboard Production OS.")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronise les étapes Stickman avec la revue Production OS.")
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview-ready", help="Importer l'aperçu et demander sa validation humaine")
    preview.add_argument("job_id")
    preview.add_argument("--file", required=True, help="Chemin relatif au dossier de mission")
    final = sub.add_parser("final-ready", help="Importer le MP4 final et ses sous-titres après approbation de l'aperçu")
    final.add_argument("job_id")
    final.add_argument("--file", required=True, help="Chemin relatif au dossier de mission")
    final.add_argument("--captions", required=True, help="Fichier .srt ou .vtt relatif au dossier de mission")
    status = sub.add_parser("status", help="Lire le statut et la prochaine étape d'une mission")
    status.add_argument("job_id")
    args = parser.parse_args()

    engine.init()
    try:
        if args.command == "preview-ready":
            result = mark_preview_ready(args.job_id, args.file)
        elif args.command == "final-ready":
            result = mark_final_ready(args.job_id, args.file, args.captions)
        else:
            result = mission_status(args.job_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Erreur : {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
