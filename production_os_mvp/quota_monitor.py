"""Best-effort quota telemetry with *no invented percentages*.

Codex: official app-server account/rateLimits/read JSON-RPC.
Antigravity: opt-in official statusLine JSON emitted via quota_statusline.py.
Ollama: local, no metered remote quota.
"""
from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import engine_manager

ROOT = Path(__file__).resolve().parent
ANTIGRAVITY_STATUS = ".antigravity_quota_status.json"
MAX_SNAPSHOT_AGE_SECONDS = 5 * 60
CACHE_SECONDS = 30
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()


def _unknown(provider: str, note: str) -> dict:
    return {
        "provider": provider,
        "remaining_percent": None,
        "source": None,
        "fresh": False,
        "note": note,
        "windows": [],
    }


def _valid_percent(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, 2) if math.isfinite(number) and 0 <= number <= 100 else None


def _unix_time(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def parse_codex_rate_limits(payload: dict, checked_at: float | None = None) -> dict:
    """Accepts app-server's rateLimits/read result, not context-window usage."""
    now = time.time() if checked_at is None else checked_at
    if not isinstance(payload, dict):
        return _unknown("codex", "Réponse de quota Codex illisible.")
    buckets = payload.get("rateLimitsByLimitId")
    limits = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(limits, dict):
        limits = payload.get("rateLimits")
    if not isinstance(limits, dict):
        return _unknown("codex", "Le serveur Codex ne fournit pas de quotas exploitables.")
    windows = []
    for kind in ("primary", "secondary"):
        window = limits.get(kind)
        if not isinstance(window, dict):
            continue
        used = _valid_percent(window.get("usedPercent", window.get("used_percent")))
        resets = _unix_time(window.get("resetsAt", window.get("resets_at")))
        if used is None or (resets is not None and resets <= now):
            continue
        windows.append({
            "name": kind,
            "remaining_percent": round(100 - used, 2),
            "resets_at": resets,
        })
    if not windows:
        return _unknown("codex", "Quotas Codex absents ou fenêtres déjà réinitialisées.")
    return {
        "provider": "codex",
        "remaining_percent": min(w["remaining_percent"] for w in windows),
        "source": "Codex app-server / account/rateLimits/read",
        "fresh": True,
        "note": "Minimum des fenêtres de quota Codex disponibles.",
        "windows": windows,
        "checked_at": now,
    }


def parse_antigravity_status(payload: dict, checked_at: float | None = None) -> dict:
    """Use quota.remaining_fraction; never confuse it with context_window.remaining_percentage."""
    now = time.time() if checked_at is None else checked_at
    if not isinstance(payload, dict):
        return _unknown("antigravity", "Statut Antigravity illisible.")
    captured_at = _unix_time(payload.get("captured_at"))
    if captured_at is None or captured_at > now + 60 or now - captured_at > MAX_SNAPSHOT_AGE_SECONDS:
        return _unknown("antigravity", "Statut Antigravity trop ancien ou non daté.")
    buckets = payload.get("quota")
    if not isinstance(buckets, dict):
        return _unknown("antigravity", "Aucune fenêtre de quota Antigravity disponible.")
    model = str(payload.get("model_id") or "").lower()
    # When multiple providers share Antigravity, do not read a quota for a different model group.
    model_group = (
        "gemini" if "gemini" in model else
        "claude" if "claude" in model else
        "gpt" if "gpt" in model else None
    )
    entries = [(str(key), val) for key, val in buckets.items() if isinstance(val, dict)]
    if model_group:
        matched = [(name, val) for name, val in entries if model_group in name.lower()]
        if matched:
            entries = matched
        elif len(entries) > 1:
            return _unknown("antigravity", "Impossible d'associer les quotas au modèle Antigravity actif.")
    elif len(entries) > 1:
        return _unknown("antigravity", "Plusieurs groupes de quotas : modèle actif non identifié.")
    windows = []
    for name, item in entries:
        if item.get("disabled") is True:
            continue
        fraction = item.get("remaining_fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            continue
        if not math.isfinite(float(fraction)) or not (0 <= fraction <= 1):
            continue
        reset = _unix_time(item.get("reset_time"))
        if reset is not None and reset <= now:
            continue
        windows.append({
            "name": name,
            "remaining_percent": round(float(fraction) * 100, 2),
            "resets_at": reset,
        })
    if not windows:
        return _unknown("antigravity", "Quotas fractionnels Antigravity indisponibles ou expirés.")
    return {
        "provider": "antigravity",
        "remaining_percent": min(w["remaining_percent"] for w in windows),
        "source": "Antigravity CLI / statusLine quota",
        "fresh": True,
        "note": "Dernier statut du modèle actif (pas la capacité de contexte).",
        "windows": windows,
        "checked_at": captured_at,
    }


def _read_codex(timeout: float = 6.0) -> dict:
    executable = engine_manager.find_codex()
    if not executable:
        return _unknown("codex", "Codex CLI introuvable.")
    command, shell = engine_manager._command(executable, ["app-server"])
    try:
        process = subprocess.Popen(
            command, shell=shell,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except (OSError, ValueError) as exc:
        return _unknown("codex", f"Impossible de démarrer le serveur de quotas ({type(exc).__name__}).")

    events: queue.Queue = queue.Queue()
    def reader():
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    events.put(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            pass

    threading.Thread(target=reader, daemon=True).start()
    start = time.monotonic()
    try:
        if process.stdin is None:
            return _unknown("codex", "Canal d'initialisation Codex indisponible.")

        def send(message: dict):
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def await_id(identifier: int):
            while True:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    return None
                try:
                    message = events.get(timeout=remaining)
                except queue.Empty:
                    return None
                if isinstance(message, dict) and message.get("id") == identifier:
                    return message

        send({
            "id": 1, "method": "initialize",
            "params": {"clientInfo": {
                "name": "production_os",
                "title": "Production OS quota monitor",
                "version": "0.4.0",
            }},
        })
        init = await_id(1)
        if not init or init.get("error"):
            return _unknown("codex", "Impossible d'initialiser Codex app-server.")
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read"})
        response = await_id(2)
        if not response or response.get("error"):
            return _unknown("codex", "Le compte ne fournit pas de limites consultables par app-server.")
        return parse_codex_rate_limits(response.get("result"))
    except (OSError, ValueError) as exc:
        return _unknown("codex", f"Lecture de quota indisponible ({type(exc).__name__}).")
    finally:
        try:
            process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


def _read_antigravity() -> dict:
    filename = os.environ.get("PRODUCTION_AGY_QUOTA_FILE", "").strip()
    path = Path(filename).expanduser() if filename else ROOT / ANTIGRAVITY_STATUS
    try:
        if not path.is_file() or path.stat().st_size > 128_000:
            return _unknown("antigravity", "Active la capture statusLine pour afficher le quota Antigravity.")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _unknown("antigravity", "Capture de quota Antigravity indisponible.")
    return parse_antigravity_status(snapshot)


def invalidate_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def get_quota(provider: str, refresh: bool = False) -> dict:
    if provider == "ollama":
        return _unknown("ollama", "Modèle local : pas de quota cloud en pourcentage.")
    if provider == "opencode":
        model = engine_manager._opencode_model(engine_manager.get_settings())
        note = "OpenCode avec Ollama local : sans quota cloud." if model.startswith("ollama/") else "Quota OpenCode inconnu : dépend du fournisseur du modèle."
        return _unknown("opencode", note)
    if provider not in {"codex", "antigravity"}:
        return _unknown(provider, "Moteur inconnu.")
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(provider)
        if cached and not refresh and now - cached[0] < CACHE_SECONDS:
            return dict(cached[1])
    result = _read_codex() if provider == "codex" else _read_antigravity()
    with _LOCK:
        _CACHE[provider] = (time.monotonic(), result)
    return dict(result)


def quota_exhausted_soon(provider: str, threshold_percent: int = 5, refresh: bool = False) -> tuple[bool, dict]:
    info = get_quota(provider, refresh=refresh)
    remaining = info.get("remaining_percent")
    threshold = int(threshold_percent)
    if not 1 <= threshold <= 50:
        raise ValueError("Le seuil de bascule doit être compris entre 1 et 50 %")
    return (
        bool(info.get("fresh") and remaining is not None and remaining <= threshold),
        info,
    )
