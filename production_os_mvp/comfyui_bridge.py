"""Non-invasive ComfyUI local readiness checks for Production OS.

Never starts GPU inference; never sends data to cloud endpoints.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_LOCK = threading.Lock()
_CACHE: dict | None = None
_CACHE_AT = 0.0
TTL_SECONDS = 20
DEFAULT_URL = "http://127.0.0.1:8188"


def _base_url() -> str:
    value = os.environ.get("PRODUCTION_COMFYUI_URL", DEFAULT_URL).rstrip("/")
    parsed = urlsplit(value)
    # Intentionally localhost-only: do not permit LAN or cloud targets.
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return DEFAULT_URL
    return value


def _get_json(base: str, endpoint: str, timeout: float = 2.0):
    request = urllib.request.Request(base + endpoint, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("Réponse HTTP inattendue")
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Réponse JSON trop volumineuse")
    return json.loads(raw.decode("utf-8"))


def get_status(refresh: bool = False) -> dict:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not refresh and _CACHE and time.monotonic() - _CACHE_AT < TTL_SECONDS:
            return json.loads(json.dumps(_CACHE))
    base = _base_url()
    state = {"url": base, "ready": False, "model_count": 0, "models": [],
             "note": "ComfyUI local ne répond pas. Démarre ComfyUI, puis actualise."}
    try:
        stats = _get_json(base, "/system_stats")
        if not isinstance(stats, dict):
            raise ValueError("Statut JSON invalide")
        state["ready"] = True
        state["note"] = "API ComfyUI locale accessible. Aucun rendu GPU lancé par ce diagnostic."
        devices = stats.get("devices")
        if isinstance(devices, list):
            state["devices"] = [
                {"name": str(device.get("name", ""))[:100],
                 "vram_total": device.get("vram_total"),
                 "vram_free": device.get("vram_free")}
                for device in devices[:4] if isinstance(device, dict)
            ]
        # Optional model inventory. Failure here does not make the server unavailable.
        found = []
        for endpoint in ("/models/checkpoints", "/models/diffusion_models", "/models/unet"):
            try:
                names = _get_json(base, endpoint, timeout=1.5)
                if isinstance(names, list):
                    found.extend(str(n)[:160] for n in names if isinstance(n, str))
            except (OSError, ValueError, TypeError, urllib.error.URLError):
                pass
        state["models"] = list(dict.fromkeys(found))[:30]
        state["model_count"] = len(set(found))
        if not found:
            state["note"] += " Aucun checkpoint/diffusion détecté sur les endpoints consultés."
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        pass
    with _LOCK:
        _CACHE = state
        _CACHE_AT = time.monotonic()
    return json.loads(json.dumps(state))
