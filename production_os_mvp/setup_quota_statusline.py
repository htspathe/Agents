"""Opt-in installer for Antigravity quota statusLine capture.

Preserves existing settings; refuses to overwrite another custom status line.
Run locally once: py -3 setup_quota_statusline.py
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def install() -> int:
    settings_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    script = Path(__file__).resolve().with_name("quota_statusline.py")
    if not script.is_file():
        print("Script quota_statusline.py absent.")
        return 1
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except (ValueError, OSError):
        print("Configuration Antigravity illisible ; aucune modification.")
        return 1
    if not isinstance(settings, dict):
        print("Configuration Antigravity invalide ; aucune modification.")
        return 1
    command = f'"{sys.executable}" "{script}"'
    previous = settings.get("statusLine")
    if isinstance(previous, dict):
        configured_command = str(previous.get("command") or "")
        if configured_command and "quota_statusline.py" not in configured_command:
            print("Une autre barre de statut Antigravity est déjà configurée.")
            print("Pour préserver cette personnalisation, ajoute toi-même la capture dans ton script.")
            print("Fichier :", settings_path)
            return 2
    if settings_path.is_file():
        backup_name = settings_path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(settings_path, settings_path.with_name(backup_name))
    settings["statusLine"] = {
        **(previous if isinstance(previous, dict) else {}),
        "type": "command",
        "command": command,
        "enabled": True,
        "stack_with_default": True,
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temp = settings_path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(settings_path)
    print("Capture Antigravity configurée. Redémarre agy pour actualiser le statut.")
    print("Seuls les pourcentages et identifiants des groupes de quota seront enregistrés localement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(install())
