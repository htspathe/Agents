"""Task-aware, observable routing for Production OS.

Orders are initial preferences, NOT claims of benchmark superiority.
Empirical success/latency can modestly reorder engines after 5 attempts.
No API credentials or prompts are persisted here.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METRICS_FILE = ROOT / ".production_os_engine_metrics.json"
PROVIDERS = ("codex", "antigravity", "opencode", "ollama")
ZONES = ("clogz", "kdp", "youtube", "clipping")
AGENTS = (
    "orchestrator", "research", "content", "creative", "video",
    "clipping", "publishing", "shopify", "support", "kdp",
    "analytics", "quality",
)
ZONE_PRIORITIES = {
    "clogz": ["codex", "opencode", "antigravity", "ollama"],
    "kdp": ["antigravity", "codex", "opencode", "ollama"],
    "youtube": ["antigravity", "codex", "opencode", "ollama"],
    "clipping": ["codex", "opencode", "antigravity", "ollama"],
}
TASK_PRIORITIES = {
    "clogz:research": ["antigravity", "codex", "opencode", "ollama"],
    "clogz:content": ["antigravity", "codex", "opencode", "ollama"],
    "clogz:creative": ["antigravity", "codex", "opencode", "ollama"],
    "clogz:shopify": ["codex", "opencode", "antigravity", "ollama"],
    "clogz:support": ["antigravity", "codex", "opencode", "ollama"],
    "kdp:research": ["antigravity", "codex", "opencode", "ollama"],
    "kdp:content": ["antigravity", "codex", "opencode", "ollama"],
    "kdp:kdp": ["antigravity", "codex", "opencode", "ollama"],
    "kdp:creative": ["antigravity", "codex", "opencode", "ollama"],
    "youtube:research": ["antigravity", "codex", "opencode", "ollama"],
    "youtube:content": ["antigravity", "codex", "opencode", "ollama"],
    "youtube:creative": ["antigravity", "codex", "opencode", "ollama"],
    "youtube:video": ["codex", "opencode", "antigravity", "ollama"],
    "clipping:clipping": ["codex", "opencode", "antigravity", "ollama"],
    "clipping:creative": ["antigravity", "codex", "opencode", "ollama"],
}
for _zone in ZONES:
    for _task in ("quality", "analytics", "publishing"):
        TASK_PRIORITIES.setdefault(f"{_zone}:{_task}", ["codex", "opencode", "antigravity", "ollama"])

_LOCK = threading.Lock()


def clean_order(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if isinstance(value, str) and value in PROVIDERS and value not in result:
            result.append(value)
    return result


def validate_priorities(zones: object, tasks: object) -> tuple[dict, dict]:
    if not isinstance(zones, dict) or not isinstance(tasks, dict):
        raise ValueError("Les priorités doivent être des objets JSON")
    if len(zones) > len(ZONES) or len(tasks) > len(ZONES) * len(AGENTS):
        raise ValueError("Trop de règles de routage")
    result_zones = {}
    result_tasks = {}
    for zone, values in zones.items():
        if zone not in ZONES or not isinstance(values, list) or not values or len(values) > len(PROVIDERS):
            raise ValueError("Priorité de zone invalide")
        order = clean_order(values)
        if len(order) != len(values):
            raise ValueError("Doublon ou moteur inconnu dans la zone")
        result_zones[zone] = order
    for key, values in tasks.items():
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("Clé de tâche invalide")
        zone, task = key.split(":", 1)
        if zone not in ZONES or task not in AGENTS or not isinstance(values, list) or not values or len(values) > len(PROVIDERS):
            raise ValueError("Priorité de tâche invalide")
        order = clean_order(values)
        if len(order) != len(values):
            raise ValueError("Doublon ou moteur inconnu dans la tâche")
        result_tasks[key] = order
    return result_zones, result_tasks


def initial_order(settings: dict, workflow: str = "", task: str = "") -> list[str]:
    if settings.get("selection_mode") == "manual":
        primary = settings.get("primary", "codex")
        fallback = settings.get("fallbacks", [])
        return clean_order([primary, *fallback] if settings.get("auto_fallback", True) else [primary])
    custom_tasks = settings.get("task_priorities") or {}
    custom_zones = settings.get("zone_priorities") or {}
    key = f"{workflow}:{task}"
    if key in custom_tasks:
        return clean_order(custom_tasks[key])
    if workflow in custom_zones:
        return clean_order(custom_zones[workflow])
    if key in TASK_PRIORITIES:
        return list(TASK_PRIORITIES[key])
    return list(ZONE_PRIORITIES.get(workflow, ["codex", "antigravity", "opencode", "ollama"]))


def read_metrics() -> dict:
    try:
        if METRICS_FILE.stat().st_size > 1_000_000:
            return {}
        data = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def provider_order(settings: dict, workflow: str = "", task: str = "") -> list[str]:
    order = initial_order(settings, workflow, task)
    if not settings.get("auto_fallback", True):
        order = order[:1]
    if settings.get("selection_mode") != "auto" or not settings.get("performance_learning", True):
        return order
    data = read_metrics().get(f"{workflow}:{task}", {})
    if not isinstance(data, dict):
        return order

    def score(provider: str) -> float:
        index = order.index(provider)
        row = data.get(provider, {})
        if not isinstance(row, dict) or row.get("attempts", 0) < 5:
            return 100.0 - index * 10.0
        attempts = max(1, int(row["attempts"]))
        successes = max(0, int(row.get("successes", 0)))
        # The task priority remains the dominant input. Only measured success
        # and runtime nudge the order; editorial quality is NOT inferred.
        reliability = successes / attempts
        latency = max(0.0, float(row.get("total_seconds", 0))) / attempts
        return (100.0 - index * 10.0) + (reliability - 0.5) * 24.0 - min(latency / 60.0, 6.0)

    return sorted(order, key=lambda item: (-score(item), order.index(item)))


def record_attempt(workflow: str, task: str, provider: str, succeeded: bool, seconds: float) -> None:
    if workflow not in ZONES or task not in AGENTS or provider not in PROVIDERS:
        return
    with _LOCK:
        data = read_metrics()
        key = f"{workflow}:{task}"
        profile = data.setdefault(key, {})
        row = profile.setdefault(provider, {"attempts": 0, "successes": 0, "total_seconds": 0.0})
        row["attempts"] = int(row.get("attempts", 0)) + 1
        row["successes"] = int(row.get("successes", 0)) + int(bool(succeeded))
        row["total_seconds"] = round(float(row.get("total_seconds", 0)) + max(0.0, min(float(seconds), 600.0)), 3)
        row["last_attempt_unix"] = int(time.time())
        try:
            temp = METRICS_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, METRICS_FILE)
        except OSError:
            pass


def route_snapshot(settings: dict) -> dict:
    return {
        "providers": list(PROVIDERS),
        "zones": list(ZONES),
        "tasks": list(AGENTS),
        "default_zones": ZONE_PRIORITIES,
        "default_tasks": TASK_PRIORITIES,
        "measured": read_metrics(),
    }
