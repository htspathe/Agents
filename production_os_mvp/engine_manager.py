"""Multi-provider model routing for Production OS.

Secrets stay in environment variables or provider-native credential stores.
This module persists only non-sensitive routing preferences.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import smart_router

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ".production_os_engines.json"
PROVIDERS = ("codex", "antigravity", "opencode", "ollama")
DEFAULT_SETTINGS = {
    "primary": "codex",
    "fallbacks": ["antigravity", "opencode", "ollama"],
    "selection_mode": "auto",
    "performance_learning": True,
    "zone_priorities": {},
    "task_priorities": {},
    "allow_paid_calls": False,
    "opencode_model": "",
    "auto_fallback": True,
    "ollama_model": "qwen2.5:3b",
    "allow_gemini_api_key": False,
    "switch_at_remaining_percent": 5,
}
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict | None = None
_STATUS_CACHE_AT = 0.0
STATUS_TTL_SECONDS = 15


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
    result["zone_priorities"] = {}
    result["task_priorities"] = {}
    result["ollama_model"] = (
        os.environ.get("OLLAMA_MODEL", "").strip()
        or os.environ.get("STICKMAN_CODEX_MODEL", "").strip()
        or DEFAULT_SETTINGS["ollama_model"]
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
            if isinstance(stored.get("ollama_model"), str) and stored["ollama_model"].strip():
                result["ollama_model"] = stored["ollama_model"].strip()[:120]
            if isinstance(stored.get("allow_gemini_api_key"), bool):
                result["allow_gemini_api_key"] = stored["allow_gemini_api_key"]
            if stored.get("selection_mode") in {"auto", "manual"}:
                result["selection_mode"] = stored["selection_mode"]
            if type(stored.get("performance_learning")) is bool:
                result["performance_learning"] = stored["performance_learning"]
            if type(stored.get("allow_paid_calls")) is bool:
                result["allow_paid_calls"] = stored["allow_paid_calls"]
            if isinstance(stored.get("opencode_model"), str):
                result["opencode_model"] = stored["opencode_model"].strip()[:160]
            try:
                zones, tasks = smart_router.validate_priorities(
                    stored.get("zone_priorities", {}), stored.get("task_priorities", {})
                )
                result["zone_priorities"], result["task_priorities"] = zones, tasks
            except ValueError:
                pass
            threshold = stored.get("switch_at_remaining_percent")
            if type(threshold) is int and 1 <= threshold <= 50:
                result["switch_at_remaining_percent"] = threshold
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
    model = data.get("ollama_model", DEFAULT_SETTINGS["ollama_model"]) or DEFAULT_SETTINGS["ollama_model"]
    if not isinstance(model, str) or len(model.strip()) > 120:
        raise ValueError("Nom de modèle Ollama invalide")
    allow_gemini_api_key = data.get("allow_gemini_api_key", False)
    if not isinstance(allow_gemini_api_key, bool):
        raise ValueError("L'autorisation Gemini API doit être un booléen")
    threshold = data.get("switch_at_remaining_percent", 5)
    if type(threshold) is not int or not 1 <= threshold <= 50:
        raise ValueError("Le seuil de quota doit être un entier entre 1 et 50 %")
    selection_mode = data.get("selection_mode", "auto")
    if selection_mode not in {"auto", "manual"}:
        raise ValueError("Mode de sélection inconnu")
    learning = data.get("performance_learning", True)
    allow_paid = data.get("allow_paid_calls", False)
    if type(learning) is not bool or type(allow_paid) is not bool:
        raise ValueError("Options de routage invalides")
    opencode_model = data.get("opencode_model", "")
    if not isinstance(opencode_model, str) or len(opencode_model.strip()) > 160:
        raise ValueError("Modèle OpenCode invalide")
    zones, tasks = smart_router.validate_priorities(
        data.get("zone_priorities", {}), data.get("task_priorities", {})
    )
    settings = {
        "primary": primary,
        "selection_mode": selection_mode,
        "performance_learning": learning,
        "allow_paid_calls": allow_paid,
        "opencode_model": opencode_model.strip(),
        "zone_priorities": zones,
        "task_priorities": tasks,
        "fallbacks": fallbacks,
        "auto_fallback": auto_fallback,
        "ollama_model": model.strip(),
        "allow_gemini_api_key": allow_gemini_api_key,
        "switch_at_remaining_percent": threshold,
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


def find_opencode() -> str | None:
    appdata = os.environ.get("APPDATA", "").strip()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    extra = []
    if appdata:
        extra.append(str(Path(appdata) / "npm" / "opencode.cmd"))
    if local:
        extra.extend([
            str(Path(local) / "Programs" / "opencode" / "opencode.exe"),
            str(Path(local) / "Microsoft" / "WinGet" / "Links" / "opencode.exe"),
        ])
    extra.append(str(Path.home() / ".opencode" / "bin" / "opencode.exe"))
    extra.append(str(Path.home() / ".opencode" / "bin" / "opencode"))
    return _find_binary("PRODUCTION_OPENCODE_BIN", ["opencode", "opencode.exe", "opencode.cmd"], extra)


def _opencode_model(settings: dict) -> str:
    return (settings.get("opencode_model") or ("ollama/" + settings.get("ollama_model", "qwen2.5:3b"))).strip()


def _model_allowed(model: str, settings: dict) -> bool:
    if settings.get("allow_paid_calls", False):
        return True
    if model.startswith(("ollama/", "lmstudio/", "vllm/")):
        return True
    free_models = {
        name.strip() for name in os.environ.get("PRODUCTION_OPENCODE_FREE_MODELS", "").split(",")
        if name.strip()
    }
    return model in free_models


def find_ollama() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files").strip()
    extra = []
    if local:
        extra.extend([
            str(Path(local) / "Programs" / "Ollama" / "ollama.exe"),
            str(Path(local) / "Microsoft" / "WinGet" / "Links" / "ollama.exe"),
        ])
        winget = Path(local) / "Microsoft" / "WinGet" / "Packages"
        try:
            extra.extend(str(path) for path in winget.glob("Ollama.Ollama_*/*/ollama.exe"))
            extra.extend(str(path) for path in winget.glob("Ollama.Ollama_*/ollama.exe"))
        except OSError:
            pass
    if program_files:
        extra.append(str(Path(program_files) / "Ollama" / "ollama.exe"))
    extra.append(str(Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"))
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
    selected = str(model or DEFAULT_SETTINGS["ollama_model"]).strip()
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
    installed_model = selected in models if selected else False
    codex_harness = bool(find_codex())
    # The local HTTP API is authoritative for text generation. The CLI is
    # optional for direct /api/generate calls; Codex is still required as the
    # agentic harness for Stickman/Ollama mode.
    ready = bool(running and installed_model)
    agent_ready = bool(ready and codex_harness)
    if not running:
        note = (
            "Service Ollama local injoignable sur 127.0.0.1:11434. "
            "Démarre Ollama puis actualise le diagnostic."
        )
    elif not selected:
        note = "Service Ollama actif. Choisis un modèle déjà téléchargé dans le panneau."
    elif not installed_model:
        note = f"Le modèle {selected} n'est pas présent dans ollama list."
    elif not executable:
        note = f"API Ollama prête avec {selected}. CLI introuvable, mais inutile pour la génération de texte ; Codex CLI reste requis pour le mode agent."
    else:
        note = f"Modèle local prêt : {selected}."
    return {
        "installed": bool(executable or running),
        "cli_installed": bool(executable),
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
    oc = find_opencode()
    settings = get_settings()
    codex_version = _version(codex)
    agy_version = _version(agy)
    oc_version = _version(oc)
    oc_model = _opencode_model(settings)
    oc_allowed = _model_allowed(oc_model, settings)
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
        "opencode": {
            "installed": bool(oc and oc_version),
            "ready": bool(oc and oc_version and oc_allowed and (
                not oc_model.startswith("ollama/") or _ollama_info(oc_model.split("/", 1)[1]).get("ready")
            )),
            "version": oc_version,
            "model": oc_model,
            "authentication": "not_checked",
            "note": (
                "OpenCode introuvable : installe le CLI puis sélectionne un modèle."
                if not oc or not oc_version else
                "Modèle potentiellement facturable bloqué par la politique budget zéro."
                if not oc_allowed else
                "OpenCode détecté. Authentification validée seulement à la première mission."
            ),
        },
        "ollama": _ollama_info(settings.get("ollama_model", "")),
    }
    with _STATUS_LOCK:
        _STATUS_CACHE = result
        _STATUS_CACHE_AT = time.monotonic()
    return json.loads(json.dumps(result))


def provider_order(settings: dict | None = None, ctx: dict | None = None) -> list[str]:
    settings = settings or get_settings()
    ctx = ctx or {}
    return smart_router.provider_order(
        settings,
        str(ctx.get("workflow") or ""),
        str(ctx.get("active_agent") or "content"),
    )


def available_providers(settings: dict | None = None, status: dict | None = None, agentic: bool = False, ctx: dict | None = None) -> list[str]:
    status = status or get_engine_status()
    field = "agent_ready" if agentic else "ready"
    return [provider for provider in provider_order(settings, ctx) if status.get(provider, {}).get(field, status.get(provider, {}).get("ready"))]


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


def _generate_opencode(prompt: str, timeout: int = 150) -> str:
    """Read-only OpenCode agent with explicit model and no permission to mutate files."""
    executable = find_opencode()
    if not executable:
        raise ProviderError("opencode", "OpenCode CLI introuvable", True)
    settings = get_settings()
    model = _opencode_model(settings)
    if not _model_allowed(model, settings):
        raise ProviderError("opencode", "Modèle OpenCode non autorisé par le budget zéro", False)
    perms = {"edit": "deny", "bash": "deny", "webfetch": "deny",
             "websearch": "deny", "external_directory": "deny"}
    env = dict(os.environ)
    env["OPENCODE_PERMISSION"] = json.dumps(perms)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission": perms})
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    args = ["run", "--agent", "plan", "--model", model, "--format", "json", prompt[:9000]]
    command, shell = _command(executable, args)
    try:
        result = subprocess.run(
            command, cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False, shell=shell
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError("opencode", "OpenCode : délai dépassé", True) from exc
    except OSError as exc:
        raise ProviderError("opencode", "OpenCode : lancement impossible", True) from exc
    if result.returncode:
        raise ProviderError("opencode", (result.stderr or result.stdout)[-2000:], True)
    parts = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
        elif event.get("type") == "text" and isinstance(event.get("text"), str):
            parts.append(event["text"])
    answer = "\n".join(parts).strip()
    if not answer:
        raise ProviderError("opencode", "OpenCode : aucune réponse textuelle exploitable", True)
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
        "opencode": _generate_opencode,
        "ollama": _generate_ollama,
    }
    # Late import avoids a cycle: quota_monitor uses engine_manager's binary discovery.
    import quota_monitor
    for provider in provider_order(settings, ctx):
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
        started = time.monotonic()
        try:
            answer = generators[provider](prompt[:9000])
            smart_router.record_attempt(str(ctx.get("workflow", "")), str(ctx.get("active_agent", "content")), provider, True, time.monotonic() - started)
            ctx.setdefault("notes", []).append(f"Génération de texte via {provider}.")
            if attempted:
                ctx["notes"].append("Moteurs indisponibles ou en échec avant succès : " + "; ".join(attempted))
            return answer[:18000]
        except ProviderError as exc:
            smart_router.record_attempt(str(ctx.get("workflow", "")), str(ctx.get("active_agent", "content")), provider, False, time.monotonic() - started)
            attempted.append(f"{provider}: {str(exc)[:240]}")
        except Exception as exc:
            smart_router.record_attempt(str(ctx.get("workflow", "")), str(ctx.get("active_agent", "content")), provider, False, time.monotonic() - started)
            attempted.append(f"{provider}: {type(exc).__name__}")
    if attempted:
        ctx.setdefault("notes", []).append("Aucun moteur IA utilisable ; gabarit déterministe conservé. " + "; ".join(attempted))
    else:
        ctx.setdefault("notes", []).append("Aucun moteur IA n'est configuré ; gabarit déterministe conservé.")
    return fallback
