"""Read MCP server declarations without exposing credentials or executing servers."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

try:
    import tomllib
except ImportError:  # Python 3.10 fallback: TOML is skipped, JSON remains supported.
    tomllib = None

_LOCK = threading.Lock()
_CACHE = None
_CACHE_AT = 0.0
CACHE_SECONDS = 30


def config_paths(home=None):
    home = Path(home or Path.home())
    appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
    return (
        home / ".claude.json",
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
        home / ".pi" / "agent" / "settings.json",
        home / ".config" / "opencode" / "opencode.json",
        home / ".opencode" / "opencode.json",
        home / ".codex" / "config.toml",
        appdata / "opencode" / "opencode.json",
    )


def _server_map(data):
    if not isinstance(data, dict):
        return {}
    for key in ("mcpServers", "mcp_servers"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        for key in ("servers", "mcpServers", "mcp_servers"):
            value = mcp.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _safe_server(name, config, source):
    if not isinstance(config, dict):
        return None
    command = config.get("command")
    url = config.get("url")
    if isinstance(command, str) and command.strip():
        command_name = Path(command.strip().strip('"')).name
        found = shutil.which(command.strip()) or (
            command.strip() if Path(command.strip()).is_file() else None
        )
        transport = "stdio"
        available = bool(found)
        endpoint = None
    elif isinstance(url, str) and url.strip():
        parsed = urlsplit(url.strip())
        command_name = None
        transport = str(config.get("type") or "http")[:24]
        available = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        endpoint = parsed.hostname + ((":" + str(parsed.port)) if parsed.port else "") if parsed.hostname else None
    else:
        command_name = None
        transport = str(config.get("type") or "unknown")[:24]
        available = False
        endpoint = None
    return {
        "name": str(name)[:100],
        "source": source,
        "transport": transport,
        "command_name": command_name,
        "command_available": available,
        "endpoint_host": endpoint,
        "configured": True,
    }


def scan(paths=None) -> dict:
    servers = {}
    checked = []
    for path in paths or config_paths():
        path = Path(path).expanduser()
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 512_000:
                continue
            raw = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() == ".toml":
                if tomllib is None:
                    continue
                data = tomllib.loads(raw)
                mapping = data.get("mcp_servers", {})
            else:
                data = json.loads(raw)
                mapping = _server_map(data)
            if not isinstance(mapping, dict):
                continue
            checked.append(str(path))
            for name, config in mapping.items():
                row = _safe_server(name, config, str(path))
                if row:
                    key = row["name"].lower()
                    servers.setdefault(key, row)
        except (OSError, ValueError, TypeError):
            continue
    rows = sorted(servers.values(), key=lambda item: item["name"].lower())
    return {
        "server_count": len(rows),
        "config_files_checked": len(checked),
        "servers": rows,
        "names": [item["name"] for item in rows],
        "note": "Lecture des déclarations MCP uniquement. Aucun serveur n'est démarré, aucune clé ni variable env n'est exposée, aucune connexion n'est présumée active.",
    }


def get_status(refresh=False) -> dict:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not refresh and _CACHE is not None and time.monotonic() - _CACHE_AT < CACHE_SECONDS:
            return _CACHE
    result = scan()
    result["updated_unix"] = int(time.time())
    with _LOCK:
        _CACHE, _CACHE_AT = result, time.monotonic()
    return result
