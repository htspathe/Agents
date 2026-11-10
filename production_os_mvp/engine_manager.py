"""Multi-provider model routing for Production OS.

Secrets stay in environment variables or provider-native credential stores.
This module persists only non-sensitive routing preferences.
"""
from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ".production_os_engines.json"
PROVIDERS = ("codex", "antigravity", "ollama")
DEFAULT_SETTINGS = {
    "primary": "codex",
    "fallbacks": ["antigravity", "ollama"],
    "auto_fallback": True,
    "ollama_model": "",
    "allow_gemini_api_key": False,
    "switch_at_remaining_percent": 5,
    "quota_check_interval_seconds": 15,
}
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict | None = None
_STATUS_CACHE_AT = 0.0
STATUS_TTL_SECONDS = 15
_QUOTA_LOCK = threading.Lock()
_CODEX_QUOTA_CACHE: dict | None = None
_CODEX_QUOTA_CACHE_AT = 0.0
CODEX_QUOTA_TTL_SECONDS = 20
ANTIGRAVITY_QUOTA_MAX_AGE_SECONDS = 180


class ProviderError(RuntimeError):
    def __init__(self, provider: str, message: str, retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


def settings_path() -> Path:
    return ROOT / SETTINGS_FILE


def get_settings() -> dict:
    result = dict(DEFAULT_SETTINGS)
    result["fallbacks"] = list(DEFAULT_SETTINGS["fallbacks"])
    result["ollama_model"] = (
        os.environ.get("OLLAMA_MODEL", "").strip()
        or os.environ.get("STICKMAN_CODEX_MODEL", "").strip()
    )
    path = settings_path()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            primary = stored.get("primary")
            if primary in PROVIDERS:
                result["primary"] = primary
            fallbacks = stored.get("fallbacks")
            if isinstance(fallbacks, list):
                result["fallbacks"] = _clean_fallbacks(fallbacks, result["primary"])
            if isinstance(stored.get("auto_fallback"), bool):
                result["auto_fallback"] = stored["auto_fallback"]
            if isinstance(stored.get("ollama_model"), str):
                result["ollama_model"] = stored["ollama_model"].strip()[:120]
            if isinstance(stored.get("allow_gemini_api_key"), bool):
                result["allow_gemini_api_key"] = stored["allow_gemini_api_key"]
            threshold = stored.get("switch_at_remaining_percent")
            if type(threshold) is int and 1 <= threshold <= 50:
                result["switch_at_remaining_percent"] = threshold
            interval = stored.get("quota_check_interval_seconds")
            if type(interval) is int and 5 <= interval <= 120:
                result["quota_check_interval_seconds"] = interval
    except (OSError, ValueError, TypeError):
        pass
    return result


def _clean_fallbacks(values, primary: str) -> list[str]:
    clean = []
    if not isinstance(values, (list, tuple)):
        return clean
    for value in values:
        if value in PROVIDERS and value != primary and value not in clean:
            clean.append(value)
    return clean


def save_settings(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Configuration des moteurs invalide")
    primary = data.get("primary", "codex")
    if primary not in PROVIDERS:
        raise ValueError("Moteur principal inconnu")
    fallbacks = _clean_fallbacks(data.get("fallbacks", []), primary)
    auto_fallback = data.get("auto_fallback", True)
    if not isinstance(auto_fallback, bool):
        raise ValueError("Le basculement automatique doit être un booléen")
    model = data.get("ollama_model", "")
    if not isinstance(model, str) or len(model.strip()) > 120:
        raise ValueError("Nom de modèle Ollama invalide")
    allow_gemini_api_key = data.get("allow_gemini_api_key", False)
    if not isinstance(allow_gemini_api_key, bool):
        raise ValueError("L'autorisation Gemini API doit être un booléen")
    threshold = data.get("switch_at_remaining_percent", 5)
    if type(threshold) is not int or not 1 <= threshold <= 50:
        raise ValueError("Le seuil de quota doit être un entier entre 1 et 50 %")
    interval = data.get("quota_check_interval_seconds", 15)
    if type(interval) is not int or not 5 <= interval <= 120:
        raise ValueError("L'intervalle de contrôle du quota doit être compris entre 5 et 120 secondes")
    settings = {
        "primary": primary,
        "fallbacks": fallbacks,
        "auto_fallback": auto_fallback,
        "ollama_model": model.strip(),
        "allow_gemini_api_key": allow_gemini_api_key,
        "switch_at_remaining_percent": threshold,
        "quota_check_interval_seconds": interval,
    }
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    invalidate_status_cache()
    return settings


def invalidate_status_cache() -> None:
    global _STATUS_CACHE, _STATUS_CACHE_AT
    with _STATUS_LOCK:
        _STATUS_CACHE = None
        _STATUS_CACHE_AT = 0.0


def _find_binary(env_name: str, names: list[str], extra: list[str] | None = None) -> str | None:
    candidates = [os.environ.get(env_name, "").strip()]
    candidates.extend(shutil.which(name) for name in names)
    candidates.extend(extra or [])
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) or candidate
        try:
            path = Path(resolved).expanduser()
            if path.is_file():
                return str(path.resolve())
        except (OSError, ValueError):
            continue
    return None


def find_codex() -> str | None:
    appdata = os.environ.get("APPDATA", "").strip()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    extra = []
    configured_stickman = os.environ.get("STICKMAN_CODEX_BIN", "").strip()
    if configured_stickman:
        extra.append(configured_stickman)
    if appdata:
        extra.append(str(Path(appdata) / "npm" / "codex.cmd"))
    if local:
        extra.extend([
            str(Path(local) / "Programs" / "codex" / "codex.exe"),
            str(Path(local) / "Microsoft" / "WinGet" / "Links" / "codex.exe"),
        ])
    extra.append(str(Path.home() / "AppData" / "Roaming" / "npm" / "codex.cmd"))
    return _find_binary("PRODUCTION_CODEX_BIN", ["codex", "codex.cmd"], extra)


def find_antigravity() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    extra = []
    if local:
        extra.append(str(Path(local) / "agy" / "bin" / "agy.exe"))
    extra.append(str(Path.home() / ".local" / "bin" / "agy"))
    return _find_binary("ANTIGRAVITY_CLI_BIN", ["agy", "agy.exe"], extra)


def find_ollama() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    extra = [str(Path(local) / "Programs" / "Ollama" / "ollama.exe")] if local else []
    return _find_binary("OLLAMA_BIN", ["ollama", "ollama.exe"], extra)


def _command(executable: str, args: list[str]):
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        return subprocess.list2cmdline([executable, *args]), True
    return [executable, *args], False


def _antigravity_api_key_mode() -> bool:
    config_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return config.get("modelProvider") == "gemini" and bool(os.environ.get("GEMINI_API_KEY", "").strip())
    except (OSError, ValueError, TypeError):
        return False


def _version(executable: str | None) -> str | None:
    if not executable:
        return None
    command, shell = _command(executable, ["--version"])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=6, check=False, shell=shell
        )
        lines = (result.stdout or result.stderr).strip().splitlines()
        return lines[0][:100] if result.returncode == 0 and lines else None
    except (OSError, subprocess.SubprocessError):
        return None


def _ollama_info(model: str) -> dict:
    executable = find_ollama()
    version = _version(executable)
    models = []
    running = False
    try:
        request = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
        models = [
            str(item.get("name") or item.get("model") or "")
            for item in payload.get("models", [])
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        ]
        running = True
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        pass
    selected = model.strip()
    if not selected and models:
        selected = ""
    installed_model = selected in models if selected else False
    codex_harness = bool(find_codex())
    ready = bool(executable and running and installed_model)
    agent_ready = bool(ready and codex_harness)
    if not executable:
        note = "Ollama CLI introuvable."
    elif not running:
        note = "Ollama est installé mais son service local ne répond pas."
    elif not selected:
        note = "Service Ollama actif. Choisis un modèle déjà téléchargé dans le panneau."
    elif not installed_model:
        note = f"Le modèle {selected} n'est pas présent dans ollama list."
    else:
        note = f"Modèle local prêt : {selected}."
    return {
        "installed": bool(executable),
        "ready": ready,
        "agent_ready": agent_ready,
        "version": version,
        "service_running": running,
        "model": selected or None,
        "models": models,
        "note": note,
        "requires_codex_harness": True,
        "agent_note": ("Codex CLI requis pour le mode agent local." if not codex_harness else "Harness Codex disponible pour le modèle local."),
    }


def get_engine_status(refresh: bool = False) -> dict:
    global _STATUS_CACHE, _STATUS_CACHE_AT
    with _STATUS_LOCK:
        if not refresh and _STATUS_CACHE is not None and time.monotonic() - _STATUS_CACHE_AT < STATUS_TTL_SECONDS:
            return json.loads(json.dumps(_STATUS_CACHE))
    codex = find_codex()
    agy = find_antigravity()
    settings = get_settings()
    codex_version = _version(codex)
    agy_version = _version(agy)
    gemini_api_key_mode = _antigravity_api_key_mode()
    allow_gemini_api_key = settings.get("allow_gemini_api_key", False)
    antigravity_ready = bool(agy and agy_version and (not gemini_api_key_mode or allow_gemini_api_key))
    antigravity_note = (
        "Mode Gemini API par clé détecté : appels potentiellement facturables. Autorisation désactivée dans Production OS."
        if gemini_api_key_mode and not allow_gemini_api_key
        else "Antigravity CLI détecté ; authentification Google vérifiée au premier lancement."
    )
    result = {
        "codex": {
            "installed": bool(codex and codex_version),
            "ready": bool(codex and codex_version),
            "version": codex_version,
            "authentication": "not_checked",
            "note": "Codex CLI détecté ; authentification et quota vérifiés au premier lancement.",
        } if codex and codex_version else {
            "installed": bool(codex), "ready": False, "version": codex_version,
            "authentication": "not_checked", "note": "Codex CLI introuvable ou non exécutable.",
        },
        "antigravity": {
            "installed": bool(agy and agy_version),
            "ready": antigravity_ready,
            "version": agy_version,
            "authentication": "api_key" if gemini_api_key_mode else "account_or_unknown",
            "note": antigravity_note,
        } if agy and agy_version else {
            "installed": bool(agy), "ready": False, "version": agy_version,
            "authentication": "not_checked", "note": "Antigravity CLI introuvable ou non exécutable.",
        },
        "ollama": _ollama_info(settings.get("ollama_model", "")),
    }
    with _STATUS_LOCK:
        _STATUS_CACHE = result
        _STATUS_CACHE_AT = time.monotonic()
    return json.loads(json.dumps(result))




def _unknown_quota(provider: str, note: str) -> dict:
    return {
        "provider": provider, "state": "unknown", "remaining_percent": None,
        "source": None, "observed_at": None, "note": note, "windows": []
    }


def _read_jsonrpc_response(messages: queue.Queue, request_id: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex app-server quota request timed out")
        line = messages.get(timeout=remaining)
        if line is None:
            raise RuntimeError("Codex app-server closed its output")
        try:
            message = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if message.get("error"):
            error = message["error"]
            raise RuntimeError(str(error.get("message") or error)[:240])
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Codex app-server returned no quota result")
        return result


def _codex_app_server_request(method: str, params: dict | None = None, timeout: float = 8.0) -> dict:
    executable = find_codex()
    if not executable:
        raise RuntimeError("Codex CLI not found")
    command, shell = _command(executable, ["app-server"])
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace", bufsize=1, shell=shell
    )
    messages: queue.Queue = queue.Queue()

    def read_output():
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def send(payload: dict):
        if process.stdin is None:
            raise RuntimeError("Codex app-server stdin unavailable")
        process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        process.stdin.flush()

    try:
        send({
            "method": "initialize", "id": 0,
            "params": {"clientInfo": {
                "name": "production_os", "title": "Production OS", "version": "0.4.0"
            }}
        })
        _read_jsonrpc_response(messages, 0, timeout)
        send({"method": "initialized", "params": {}})
        request = {"method": method, "id": 1}
        if params is not None:
            request["params"] = params
        send(request)
        return _read_jsonrpc_response(messages, 1, timeout)
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
        reader.join(timeout=0.5)


def parse_codex_quota(payload: dict, threshold_percent: float = 5.0) -> dict:
    if not isinstance(payload, dict):
        return _unknown_quota("codex", "Réponse quota Codex invalide.")
    buckets = payload.get("rateLimitsByLimitId")
    if isinstance(buckets, dict) and buckets:
        selected = {
            str(key): value for key, value in buckets.items()
            if "codex" in str(key).lower() and isinstance(value, dict)
        }
    else:
        legacy = payload.get("rateLimits")
        selected = {}
        if isinstance(legacy, dict):
            selected[str(legacy.get("limitId") or "codex")] = legacy
    windows = []
    for bucket_id, bucket in selected.items():
        reached = bucket.get("rateLimitReachedType")
        if reached:
            windows.append({
                "bucket": bucket_id, "window": "reached",
                "remaining_percent": 0.0, "reached_type": str(reached)
            })
        for window_name in ("primary", "secondary"):
            window = bucket.get(window_name)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            if isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(float(used)) and 0 <= float(used) <= 100:
                windows.append({
                    "bucket": bucket_id, "window": window_name,
                    "remaining_percent": round(100.0 - float(used), 2),
                    "window_duration_minutes": window.get("windowDurationMins"),
                    "resets_at": window.get("resetsAt")
                })
    if not windows:
        return _unknown_quota("codex", "Codex n'a fourni aucun pourcentage de quota exploitable (authentification ou plan non pris en charge).")
    remaining = min(item["remaining_percent"] for item in windows)
    state = "low" if remaining <= threshold_percent else "ok"
    return {
        "provider": "codex", "state": state,
        "remaining_percent": remaining, "source": "codex_app_server",
        "observed_at": time.time(), "note": (
            f"{remaining:.1f} % restants ; bascule à {threshold_percent:g} %."
            if state == "low" else f"{remaining:.1f} % restants."
        ), "windows": windows
    }


def read_codex_quota(refresh: bool = False, threshold_percent: float | None = None) -> dict:
    global _CODEX_QUOTA_CACHE, _CODEX_QUOTA_CACHE_AT
    threshold = float(
        threshold_percent if threshold_percent is not None
        else get_settings().get("switch_at_remaining_percent", 5)
    )
    with _QUOTA_LOCK:
        if not refresh and _CODEX_QUOTA_CACHE is not None and time.monotonic() - _CODEX_QUOTA_CACHE_AT < CODEX_QUOTA_TTL_SECONDS:
            result = json.loads(json.dumps(_CODEX_QUOTA_CACHE))
            result["state"] = "low" if result.get("remaining_percent") is not None and result["remaining_percent"] <= threshold else ("unknown" if result.get("remaining_percent") is None else "ok")
            return result
    try:
        payload = _codex_app_server_request("account/rateLimits/read")
        result = parse_codex_quota(payload, threshold)
    except Exception as exc:
        result = _unknown_quota("codex", f"Quota Codex indisponible : {type(exc).__name__}.")
    with _QUOTA_LOCK:
        _CODEX_QUOTA_CACHE = json.loads(json.dumps(result))
        _CODEX_QUOTA_CACHE_AT = time.monotonic()
    return result


def antigravity_quota_path() -> Path:
    return ROOT / ".production_os_runtime" / "antigravity_quota.json"


def antigravity_settings_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def antigravity_hook_config_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "production-os-statusline.json"


def clear_antigravity_quota() -> None:
    try:
        antigravity_quota_path().unlink(missing_ok=True)
    except OSError:
        pass


def ensure_antigravity_quota_hook(
    settings_file: Path | None = None,
    hook_config_file: Path | None = None,
) -> dict:
    settings_file = settings_file or antigravity_settings_path()
    hook_config_file = hook_config_file or antigravity_hook_config_path()
    script = ROOT / "antigravity_quota_hook.py"
    if not script.is_file():
        return {"installed": False, "note": "Script de collecte du quota Antigravity absent."}
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
        if not isinstance(settings, dict):
            settings = {}
        previous = settings.get("statusLine")
        if isinstance(previous, dict) and previous.get("enabled") is False:
            return {"installed": False, "note": "Statusline Antigravity désactivée dans les préférences utilisateur."}
        command = subprocess.list2cmdline([
            sys.executable, str(script.resolve()), "--config", str(hook_config_file.resolve())
        ])
        if isinstance(previous, dict) and str(previous.get("command", "")).find(str(script)) >= 0:
            return {"installed": True, "note": "Collecte du quota Antigravity déjà configurée."}
        previous_command = previous.get("command") if isinstance(previous, dict) and isinstance(previous.get("command"), str) else None
        hook_config = {
            "quota_file": str(antigravity_quota_path().resolve()),
            "previous_command": previous_command,
        }
        hook_config_file.parent.mkdir(parents=True, exist_ok=True)
        hook_temp = hook_config_file.with_suffix(".tmp")
        hook_temp.write_text(json.dumps(hook_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(hook_temp, hook_config_file)
        previous_stack = previous.get("stack_with_default") if isinstance(previous, dict) else None
        settings["statusLine"] = {
            "type": "command", "command": command,
            "stack_with_default": bool(previous_stack) if previous_command else True
        }
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_temp = settings_file.with_suffix(".production-os.tmp")
        settings_temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(settings_temp, settings_file)
        return {"installed": True, "note": "Collecte du quota Antigravity configurée via statusLine."}
    except (OSError, ValueError, TypeError) as exc:
        return {"installed": False, "note": f"Configuration du quota Antigravity impossible ({type(exc).__name__})."}


def read_antigravity_quota(threshold_percent: float | None = None) -> dict:
    threshold = float(
        threshold_percent if threshold_percent is not None
        else get_settings().get("switch_at_remaining_percent", 5)
    )
    path = antigravity_quota_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        observed = float(data.get("observed_at", 0))
        if time.time() - observed > ANTIGRAVITY_QUOTA_MAX_AGE_SECONDS:
            return _unknown_quota("antigravity", "Quota Antigravity non actualisé récemment.")
        remaining = data.get("remaining_percent")
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool) or not math.isfinite(float(remaining)) or not 0 <= float(remaining) <= 100:
            return _unknown_quota("antigravity", "Antigravity n'a fourni aucun pourcentage de quota exploitable.")
        remaining = round(float(remaining), 2)
        state = "low" if remaining <= threshold else "ok"
        return {
            "provider": "antigravity", "state": state,
            "remaining_percent": remaining, "source": "antigravity_statusline",
            "observed_at": observed, "note": (
                f"{remaining:.1f} % restants ; bascule à {threshold:g} %."
                if state == "low" else f"{remaining:.1f} % restants."
            ), "windows": data.get("windows", [])
        }
    except (OSError, ValueError, TypeError):
        return _unknown_quota("antigravity", "Quota Antigravity en attente du signal statusLine.")


def get_provider_quota(provider: str, refresh: bool = False) -> dict:
    if provider == "codex":
        return read_codex_quota(refresh=refresh)
    if provider == "antigravity":
        return read_antigravity_quota()
    if provider == "ollama":
        return {
            "provider": "ollama", "state": "unlimited",
            "remaining_percent": None, "source": "local",
            "observed_at": time.time(),
            "note": "Moteur local : pas de quota cloud à surveiller.", "windows": []
        }
    return _unknown_quota(provider, "Fournisseur inconnu.")


def quota_should_switch(quota: dict, threshold_percent: float | None = None) -> bool:
    threshold = float(
        threshold_percent if threshold_percent is not None
        else get_settings().get("switch_at_remaining_percent", 5)
    )
    remaining = quota.get("remaining_percent") if isinstance(quota, dict) else None
    return (
        isinstance(remaining, (int, float))
        and not isinstance(remaining, bool)
        and math.isfinite(float(remaining))
        and float(remaining) <= threshold
    )


def provider_order(settings: dict | None = None) -> list[str]:
    settings = settings or get_settings()
    primary = settings.get("primary", "codex")
    if primary not in PROVIDERS:
        primary = "codex"
    order = [primary]
    if settings.get("auto_fallback", True):
        order.extend(_clean_fallbacks(settings.get("fallbacks", []), primary))
    return order


def available_providers(settings: dict | None = None, status: dict | None = None, agentic: bool = False) -> list[str]:
    status = status or get_engine_status()
    field = "agent_ready" if agentic else "ready"
    return [provider for provider in provider_order(settings) if status.get(provider, {}).get(field, status.get(provider, {}).get("ready"))]


def should_fallback(error_text: str) -> bool:
    value = str(error_text or "").lower()
    markers = (
        "429", "rate limit", "rate_limit", "quota", "resource exhausted",
        "usage limit", "limit reached", "exceeded your quota", "quota exceeded",
        "you've hit your usage limit", "you have hit your usage limit",
        "rate exceeded", "capacity exhausted", "billing", "insufficient_quota",
        "temporarily unavailable", "service unavailable", "overloaded",
        "502", "503", "504", "gateway timeout", "authentication required",
        "unauthorized", "invalid api key", "token expired", "sign in",
        "connection reset", "connection refused", "timed out", "timeout",
    )
    return any(marker in value for marker in markers)


def _run_capture(executable: str, args: list[str], prompt: str, timeout: int, provider: str) -> str:
    command, shell = _command(executable, args)
    try:
        result = subprocess.run(
            command, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False, shell=shell
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(provider, f"{provider}: délai dépassé après {timeout}s", True) from exc
    except OSError as exc:
        raise ProviderError(provider, f"{provider}: exécution impossible ({type(exc).__name__})", True) from exc
    combined = (result.stderr or "") + "\n" + (result.stdout or "")
    if result.returncode != 0:
        raise ProviderError(provider, combined[-2400:] or f"{provider}: code de sortie {result.returncode}", True)
    return result.stdout.strip()


def build_codex_text_args(output: Path) -> list[str]:
    return [
        "--cd", str(ROOT), "--sandbox", "read-only", "--ask-for-approval", "never",
        "exec", "--skip-git-repo-check", "--output-last-message", str(output),
        "--json", "-"
    ]


def _generate_codex(prompt: str, timeout: int = 150) -> str:
    executable = find_codex()
    if not executable:
        raise ProviderError("codex", "Codex CLI introuvable", True)
    with tempfile.TemporaryDirectory(prefix="production-os-codex-") as temp:
        output = Path(temp) / "answer.md"
        args = build_codex_text_args(output)
        _run_capture(executable, args, prompt, timeout, "codex")
        if not output.is_file():
            raise ProviderError("codex", "Codex n'a pas fourni de réponse finale.", True)
        answer = output.read_text(encoding="utf-8").strip()
        if not answer:
            raise ProviderError("codex", "Réponse Codex vide.", True)
        return answer


def _generate_antigravity(prompt: str, timeout: int = 150) -> str:
    executable = find_antigravity()
    if not executable:
        raise ProviderError("antigravity", "Antigravity CLI introuvable", True)
    seconds = max(1, int(timeout))
    args = [
        "-p", prompt, "--output-format", "json", "--print-timeout",
        f"{seconds}s", "--mode=plan", "--sandbox"
    ]
    raw = _run_capture(executable, args, "", timeout + 10, "antigravity")
    try:
        envelope = json.loads(raw)
        if envelope.get("status") != "SUCCESS":
            raise ProviderError("antigravity", envelope.get("error") or "Antigravity n'a pas terminé correctement.", True)
        answer = str(envelope.get("response") or "").strip()
    except json.JSONDecodeError:
        answer = raw
    if not answer:
        raise ProviderError("antigravity", "Réponse Antigravity vide.", True)
    return answer


def _generate_ollama(prompt: str, timeout: int = 180) -> str:
    settings = get_settings()
    model = settings.get("ollama_model") or os.environ.get("OLLAMA_MODEL", "").strip()
    if not model:
        raise ProviderError("ollama", "Aucun modèle Ollama sélectionné.", True)
    payload = json.dumps({
        "model": model, "prompt": prompt[:9000], "stream": False,
        "options": {"temperature": 0.45},
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(2_000_000).decode("utf-8"))
        answer = str(data.get("response") or "").strip()
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        raise ProviderError("ollama", f"Ollama indisponible ({type(exc).__name__}).", True) from exc
    if not answer:
        raise ProviderError("ollama", "Réponse Ollama vide.", True)
    return answer


def generate_text(prompt: str, fallback: str, ctx: dict) -> str:
    settings = get_settings()
    status = get_engine_status()
    attempted = []
    generators = {
        "codex": _generate_codex,
        "antigravity": _generate_antigravity,
        "ollama": _generate_ollama,
    }
    # Late import avoids a cycle: quota_monitor uses engine_manager's binary discovery.
    import quota_monitor
    for provider in provider_order(settings):
        if not status.get(provider, {}).get("ready"):
            attempted.append(f"{provider}: non disponible")
            continue
        if settings.get("auto_fallback", True):
            low, info = quota_monitor.quota_exhausted_soon(
                provider, settings.get("switch_at_remaining_percent", 5)
            )
            if low:
                attempted.append(
                    f"{provider}: quota {info['remaining_percent']} % restant, "
                    f"seuil {settings.get('switch_at_remaining_percent', 5)} %"
                )
                continue
        try:
            answer = generators[provider](prompt[:9000])
            ctx.setdefault("notes", []).append(f"Génération de texte via {provider}.")
            if attempted:
                ctx["notes"].append("Moteurs indisponibles ou en échec avant succès : " + "; ".join(attempted))
            return answer[:18000]
        except ProviderError as exc:
            attempted.append(f"{provider}: {str(exc)[:240]}")
        except Exception as exc:
            attempted.append(f"{provider}: {type(exc).__name__}")
    if attempted:
        ctx.setdefault("notes", []).append("Aucun moteur IA utilisable ; gabarit déterministe conservé. " + "; ".join(attempted))
    else:
        ctx.setdefault("notes", []).append("Aucun moteur IA n'est configuré ; gabarit déterministe conservé.")
    return fallback
