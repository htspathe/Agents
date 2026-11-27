"""Safe local discovery for agent CLIs and production tools.

Discovery never launches an agent task. Only a small allowlist receives --version.
FCC wrappers and desktop-control tools are detected by path only.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import json
from pathlib import Path

_LOCK = threading.Lock()
_CACHE = None
_CACHE_AT = 0.0
CACHE_SECONDS = 20

AGENT_SPECS = {
    "codex": {"label": "Codex CLI", "env": "PRODUCTION_CODEX_BIN", "names": ("codex", "codex.cmd"), "adapter": "text-router"},
    "opencode": {"label": "OpenCode", "env": "PRODUCTION_OPENCODE_BIN", "names": ("opencode", "opencode.cmd", "opencode.exe"), "adapter": "text-router"},
    "antigravity": {"label": "Antigravity / Gemini", "env": "ANTIGRAVITY_CLI_BIN", "names": ("agy", "agy.exe"), "adapter": "text-router"},
    "dsh": {"label": "DeepSeek Harness", "env": "PRODUCTION_DSH_BIN", "names": ("dsh", "dsh.cmd"), "adapter": "discovery-only"},
    "pi": {"label": "Pi Agent", "env": "PRODUCTION_PI_BIN", "names": ("pi", "pi.cmd"), "adapter": "discovery-only"},
    "claude": {"label": "Claude Code", "env": "PRODUCTION_CLAUDE_BIN", "names": ("claude", "claude.cmd"), "adapter": "discovery-only"},
    "fcc-codex": {"label": "FCC Codex", "env": "FCC_CODEX_BIN", "names": ("fcc-codex", "fcc-codex.exe"), "adapter": "discovery-only"},
    "fcc-opencode": {"label": "FCC OpenCode", "env": "FCC_OPENCODE_BIN", "names": ("fcc-opencode", "fcc-opencode.exe"), "adapter": "discovery-only"},
    "fcc-dsh": {"label": "FCC DeepSeek Harness", "env": "FCC_DSH_BIN", "names": ("fcc-dsh", "fcc-dsh.exe"), "adapter": "discovery-only"},
    "fcc-claude": {"label": "FCC Claude", "env": "FCC_CLAUDE_BIN", "names": ("fcc-claude", "fcc-claude.exe"), "adapter": "discovery-only"},
    "agent-desktop": {"label": "Agent Desktop", "env": "AGENT_DESKTOP_BIN", "names": ("agent-desktop", "agent-desktop.cmd"), "adapter": "desktop-mcp"},
    "agent-browser": {"label": "Agent Browser", "env": "AGENT_BROWSER_BIN", "names": ("agent-browser", "agent-browser.cmd"), "adapter": "browser-mcp"},
}

TOOL_SPECS = {
    "node": ("NODE_BIN", ("node", "node.exe")),
    "git": ("GIT_BIN", ("git", "git.exe")),
    "docker": ("DOCKER_BIN", ("docker", "docker.exe")),
    "ffmpeg": ("FFMPEG_BIN", ("ffmpeg", "ffmpeg.exe")),
    "ffprobe": ("FFPROBE_BIN", ("ffprobe", "ffprobe.exe")),
    "rojo": ("ROJO_BIN", ("rojo", "rojo.exe")),
    "blender": ("BLENDER_BIN", ("blender", "blender.exe")),
}


def _candidates(env_name: str, names: tuple[str, ...]) -> list[str]:
    values = []
    configured = os.environ.get(env_name, "").strip()
    if configured:
        values.append(configured)
    for name in names:
        found = shutil.which(name)
        if found:
            values.append(found)
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    common = {
        "codex": [appdata / "npm" / "codex.cmd", local / "Programs" / "codex" / "codex.exe"],
        "opencode": [appdata / "npm" / "opencode.cmd", local / "Programs" / "opencode" / "opencode.exe"],
        "dsh": [appdata / "npm" / "dsh.cmd", local / "Programs" / "dsh" / "dsh.exe"],
        "pi": [home / ".pi" / "agent" / "bin" / "pi.CMD", home / ".pi" / "agent" / "bin" / "pi.cmd"],
        "claude": [appdata / "npm" / "claude.cmd", local / "Programs" / "claude" / "claude.exe"],
        "fcc-codex": [home / ".local" / "bin" / "fcc-codex.exe", home / ".local" / "bin" / "fcc-codex"],
        "fcc-opencode": [home / ".local" / "bin" / "fcc-opencode.exe", home / ".local" / "bin" / "fcc-opencode"],
        "fcc-dsh": [home / ".local" / "bin" / "fcc-dsh.exe", home / ".local" / "bin" / "fcc-dsh"],
        "fcc-claude": [home / ".local" / "bin" / "fcc-claude.exe", home / ".local" / "bin" / "fcc-claude"],
        "agent-desktop": [appdata / "npm" / "agent-desktop.cmd"],
        "agent-browser": [appdata / "npm" / "agent-browser.cmd"],
    }
    for name in names:
        stem = name.lower().replace(".cmd", "").replace(".exe", "")
        for path in common.get(stem, []):
            values.append(str(path))
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    local_programs = local / "Programs"
    if any(name.lower().startswith("blender") for name in names):
        for base in (program_files / "Blender Foundation", local_programs / "Blender Foundation"):
            try:
                values.extend(str(folder / "blender.exe") for folder in base.glob("Blender*") if (folder / "blender.exe").is_file())
            except OSError:
                pass
    return list(dict.fromkeys(values))


def _find(env_name: str, names: tuple[str, ...]) -> str | None:
    for candidate in _candidates(env_name, names):
        candidate = candidate.strip().strip('"')
        resolved = shutil.which(candidate) or candidate
        try:
            path = Path(resolved).expanduser()
            if path.is_file():
                return str(path.resolve())
        except (OSError, ValueError):
            continue
    return None


def _version(path: str | None, probe: bool) -> str | None:
    if not path or not probe:
        return None
    args = [path, "--version"]
    shell = os.name == "nt" and Path(path).suffix.lower() in {".cmd", ".bat"}
    command = subprocess.list2cmdline(args) if shell else args
    try:
        result = subprocess.run(
            command, shell=shell, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=4, check=False,
        )
        if result.returncode != 0:
            return None
        output = (result.stdout or result.stderr).strip().splitlines()
        return output[0][:120] if output else None
    except (OSError, subprocess.SubprocessError):
        return None


def _tool_status():
    tools = {}
    for key, (env_name, names) in TOOL_SPECS.items():
        path = _find(env_name, names)
        tools[key] = {"installed": bool(path), "path": path}
    try:
        import engine_manager
        ollama_path = engine_manager.find_ollama()
    except Exception:
        ollama_path = _find("OLLAMA_BIN", ("ollama", "ollama.exe"))
    tools["ollama"] = {"installed": bool(ollama_path), "path": ollama_path}
    whisper_cli = shutil.which("whisper")
    try:
        whisper_module = importlib.util.find_spec("whisper") is not None
    except (ImportError, ValueError):
        whisper_module = False
    tools["whisper"] = {
        "installed": bool(whisper_cli or whisper_module),
        "path": whisper_cli,
        "python_module": whisper_module,
    }
    return tools


def _service_json(url: str, api_key: str = "", timeout: float = 2.0):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("response_too_large")
        return json.loads(raw.decode("utf-8")), response.status


def _freellmapi_status():
    base = os.environ.get("FREELLMAPI_URL", "http://127.0.0.1:3001").rstrip("/")
    result = {"configured_url": base, "identity_verified": False, "ready": False, "model_count": None, "models": []}
    # Never permit a configured remote host from this local diagnostic.
    from urllib.parse import urlsplit
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.query or parsed.fragment:
        result["note"] = "Diagnostic limité à localhost, avec ou sans suffixe /v1."
        return result
    if parsed.path.rstrip("/") == "/v1":
        base = base[: -len("/v1")]
    try:
        payload, _ = _service_json(base + "/v1/models", os.environ.get("FREELLMAPI_API_KEY", ""))
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            result["note"] = "Endpoint /v1/models accessible, mais format non reconnu."
            return result
        models = [str(row.get("id"))[:160] for row in rows if isinstance(row, dict) and row.get("id")]
        result.update({
            "identity_verified": True, "ready": True,
            "model_count": len(models), "models": models[:60],
            "note": "FreeLLMAPI identifié par son endpoint OpenAI-compatible /v1/models. Les tarifs et capacités vidéo restent à vérifier par modèle.",
        })
    except urllib.error.HTTPError as exc:
        result["note"] = "Réponse HTTP " + str(exc.code) + " sur /v1/models ; identité du service non confirmée."
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        result["note"] = "Aucun endpoint FreeLLMAPI confirmé sur cette adresse. Vérifier le port 3001 et le conflit éventuel avec BrainToBillions Studio."
    return result


def scan(refresh=False) -> dict:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not refresh and _CACHE is not None and time.monotonic() - _CACHE_AT < CACHE_SECONDS:
            return _CACHE
    agents = {}
    for key, spec in AGENT_SPECS.items():
        path = _find(spec["env"], spec["names"])
        probe = key in {"codex", "opencode", "dsh", "pi", "claude"}
        version = _version(path, probe)
        integrated = spec["adapter"] == "text-router"
        agents[key] = {
            "label": spec["label"], "installed": bool(path), "path": path,
            "version": version, "adapter": spec["adapter"],
            "adapter_ready": bool(path and version and integrated),
            "status": "ready" if path and version and integrated else
                      "detected_adapter_pending" if path and integrated else
                      "detected_not_integrated" if path else "not_found",
        }
    tools = _tool_status()
    services = {"freellmapi": _freellmapi_status()}
    result = {
        "agents": agents,
        "tools": tools,
        "services": services,
        "summary": {
            "agents_detected": sum(1 for row in agents.values() if row["installed"]),
            "agents_with_existing_adapter": sum(1 for row in agents.values() if row["adapter_ready"]),
            "tools_detected": sum(1 for row in tools.values() if row["installed"]),
            "services_verified": sum(1 for row in services.values() if row["identity_verified"]),
        },
        "updated_unix": int(time.time()),
        "note": "Détection locale uniquement. Une présence CLI ne confirme pas l'authentification, le quota, l'accès MCP ou la capacité d'exécuter une tâche.",
    }
    with _LOCK:
        _CACHE, _CACHE_AT = result, time.monotonic()
    return result


def get_status(refresh=False) -> dict:
    return scan(refresh=refresh)
