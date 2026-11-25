"""Local-first video generation queue with explicit free-model allowlists.

FreeLLMAPI video requests return MP4 bytes synchronously (up to the gateway's
documented five-minute timeout). Agnes AI uses asynchronous create/poll. Both
providers are disabled unless a model is explicitly allowlisted as free by the
local operator. This module never silently falls back to a paid provider.
"""
from __future__ import annotations

import base64
import ipaddress
from contextlib import contextmanager
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".production_os_runtime"
DB_PATH = RUNTIME / "media_factory.sqlite3"
OUTPUT_DIR = ROOT / "outputs" / "video_factory"
REFERENCE_DIR = ROOT / "inbox" / "video_factory_refs"
VIDEO_TIMEOUT_SECONDS = 310
MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
ALLOWED_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
KNOWN_PAID_AGNES_MODELS = {"agnes-video-2.5"}
_LOCK = threading.RLock()
_WORKER = None
_STOP = threading.Event()
_STATUS_CACHE = None
_STATUS_CACHE_AT = 0.0


def _load_local_env():
    """Load only named local env files; process environment takes precedence."""
    for path in (ROOT / ".env.local", ROOT / ".env"):
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 64_000:
                continue
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in {
                    "FREELLMAPI_URL", "FREELLMAPI_API_KEY",
                    "FREELLMAPI_FREE_VIDEO_MODELS", "FREELLMAPI_VIDEO_AUTO_FREE_CONFIRMED",
                    "AGNES_BASE_URL", "AGNES_API_KEY",
                    "AGNES_FREE_VIDEO_MODELS", "AGNES_VIDEO_FREE_CONFIRMED",
                } and value:
                    os.environ.setdefault(key, value)
        except OSError:
            continue


_load_local_env()


def _csv_env(name: str) -> list[str]:
    return list(dict.fromkeys(
        value.strip()[:180] for value in os.environ.get(name, "").split(",")
        if value.strip()
    ))


def _freellmapi_base() -> str:
    base = os.environ.get("FREELLMAPI_URL", "http://127.0.0.1:3001").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path.rstrip("/") not in {"", "/v1"} or parsed.query or parsed.fragment:
        raise ValueError("FREELLMAPI_URL doit pointer vers un serveur HTTP local, avec ou sans suffixe /v1.")
    if parsed.path.rstrip("/") == "/v1":
        base = base[: -len("/v1")]
    return base.rstrip("/")


def _agnes_base() -> str:
    base = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("AGNES_BASE_URL doit être une URL HTTPS valide.")
    if not (parsed.hostname == "agnes-ai.com" or parsed.hostname.endswith(".agnes-ai.com")):
        raise ValueError("AGNES_BASE_URL doit utiliser le domaine officiel agnes-ai.com.")
    if parsed.path.rstrip("/") == "/v1":
        base = base[: -len("/v1")]
    elif parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("AGNES_BASE_URL doit être l’origine Agnes, avec ou sans suffixe /v1.")
    return base.rstrip("/")


def _json_request(url: str, key: str, timeout: float = 8.0, method: str = "GET", payload=None):
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Réponse JSON trop volumineuse.")
        return json.loads(raw.decode("utf-8")), response.headers


def _free_model_policy(provider: str) -> tuple[list[str], bool]:
    if provider == "freellmapi":
        models = _csv_env("FREELLMAPI_FREE_VIDEO_MODELS")
        auto_confirmed = os.environ.get("FREELLMAPI_VIDEO_AUTO_FREE_CONFIRMED") == "1"
        if "auto" in models and not auto_confirmed:
            models.remove("auto")
        return models, bool(models)
    if provider == "agnes":
        models = [model for model in _csv_env("AGNES_FREE_VIDEO_MODELS") if model not in KNOWN_PAID_AGNES_MODELS]
        confirmed = os.environ.get("AGNES_VIDEO_FREE_CONFIRMED") == "1"
        return models if confirmed else [], confirmed and bool(models)
    return [], False


def provider_status(refresh: bool = False) -> dict:
    global _STATUS_CACHE, _STATUS_CACHE_AT
    with _LOCK:
        if not refresh and _STATUS_CACHE is not None and time.monotonic() - _STATUS_CACHE_AT < 15:
            return _STATUS_CACHE
    result = {
        "free_only": True,
        "paid_fallback": False,
        "providers": {},
        "generation_enabled": False,
        "notes": [
            "Seuls les modèles inscrits dans les allowlists locales sont utilisables.",
            "La gratuité doit être confirmée dans le compte fournisseur ; l'application ne peut pas déduire un prix nul d'un nom de modèle.",
            "Aucun fallback automatique entre fournisseurs vidéo n'est effectué.",
        ],
    }
    free_models, free_confirmed = _free_model_policy("freellmapi")
    freellm = {
        "id": "freellmapi",
        "label": "FreeLLMAPI",
        "configured_models": free_models,
        "identity_verified": False,
        "video_endpoint_verified": False,
        "api_key_configured": bool(os.environ.get("FREELLMAPI_API_KEY")),
        "catalog_video_candidates": [],
        "ready": False,
        "free_confirmed": free_confirmed,
        "note": "Ajoute une clé unifiée et une allowlist de modèles confirmés gratuits.",
    }
    try:
        base = _freellmapi_base()
        key = os.environ.get("FREELLMAPI_API_KEY", "").strip()
        if key:
            payload, _ = _json_request(base + "/v1/models", key, timeout=5)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if isinstance(rows, list):
                freellm["identity_verified"] = True
                candidates = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    model_id = str(row.get("id") or "")
                    capabilities = row.get("capabilities") or row.get("modalities") or []
                    text_caps = " ".join(str(value) for value in capabilities) if isinstance(capabilities, list) else str(capabilities)
                    if "video" in model_id.lower() or "video" in text_caps.lower():
                        candidates.append(model_id[:180])
                freellm["catalog_video_candidates"] = sorted(set(candidates))[:100]
                try:
                    openapi, _ = _json_request(base + "/v1/openapi.json", key, timeout=5)
                    paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
                    freellm["video_endpoint_verified"] = isinstance(paths, dict) and any(
                        path in paths for path in ("/v1/videos/generations", "/videos/generations")
                    )
                except (HTTPError, OSError, ValueError, TypeError, URLError):
                    freellm["video_endpoint_verified"] = False
                freellm["ready"] = bool(free_models and freellm["video_endpoint_verified"])
                if not free_models:
                    freellm["note"] = "API identifiée, mais aucune allowlist de modèles vidéo gratuits n'est configurée."
                elif not freellm["video_endpoint_verified"]:
                    freellm["note"] = "API identifiée, mais l'endpoint vidéo n'est pas confirmé dans OpenAPI."
                else:
                    freellm["note"] = "API FreeLLMAPI et endpoint vidéo identifiés. Les modèles autorisés proviennent de l'allowlist locale, pas d'une supposition sur leur prix."
        else:
            freellm["note"] = "FREELLMAPI_API_KEY absent. Ajoute la clé unifiée dans .env.local ; elle ne sera jamais affichée."
    except HTTPError as exc:
        freellm["note"] = "FreeLLMAPI a répondu HTTP " + str(exc.code) + " sur /v1/models ; identité ou authentification non validée."
    except (OSError, ValueError, TypeError, URLError) as exc:
        freellm["note"] = "FreeLLMAPI non vérifié : " + str(exc)[:180]
    result["providers"]["freellmapi"] = freellm

    agnes_models, agnes_confirmed = _free_model_policy("agnes")
    agnes = {
        "id": "agnes",
        "label": "Agnes AI",
        "configured_models": agnes_models,
        "api_key_configured": bool(os.environ.get("AGNES_API_KEY")),
        "identity_verified": False,
        "catalog_models": [],
        "ready": False,
        "free_confirmed": agnes_confirmed,
        "note": "Agnes est bloqué par défaut. Il faut une clé et une confirmation explicite de gratuité par modèle.",
    }
    try:
        base = _agnes_base()
        key = os.environ.get("AGNES_API_KEY", "").strip()
        if key:
            payload, _ = _json_request(base + "/v1/models", key, timeout=8)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if isinstance(rows, list):
                agnes["identity_verified"] = True
                agnes["catalog_models"] = [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]
                catalog_match = all(model in agnes["catalog_models"] for model in agnes_models)
                agnes["ready"] = bool(agnes_models and agnes_confirmed and catalog_match)
                if not agnes_models or not agnes_confirmed:
                    agnes["note"] = "API identifiée, mais aucun modèle Agnes n'est autorisé en mode gratuit."
                elif not catalog_match:
                    agnes["note"] = "Un ou plusieurs modèles de l'allowlist ne figurent pas dans le catalogue Agnes."
                else:
                    agnes["note"] = "API Agnes identifiée ; les modèles autorisés sont présents dans le catalogue et confirmés localement gratuits."
        elif agnes_models:
            agnes["note"] = "Allowlist présente, mais AGNES_API_KEY est absent."
    except HTTPError as exc:
        agnes["note"] = "Agnes AI a répondu HTTP " + str(exc.code) + " ; identité ou authentification non validée."
    except (OSError, ValueError, TypeError, URLError) as exc:
        agnes["note"] = "Agnes AI non vérifié : " + str(exc)[:180]
    result["providers"]["agnes"] = agnes
    result["generation_enabled"] = any(item.get("ready") for item in result["providers"].values())
    with _LOCK:
        _STATUS_CACHE, _STATUS_CACHE_AT = result, time.monotonic()
    return result


def _connect():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


@contextmanager
def _db():
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init():
    with _db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS media_jobs (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                duration INTEGER NOT NULL,
                aspect_ratio TEXT NOT NULL,
                image_id TEXT,
                status TEXT NOT NULL,
                output_name TEXT,
                provider_used TEXT,
                model_used TEXT,
                metadata TEXT,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()


def _image_path(image_id: str | None) -> Path | None:
    if not image_id:
        return None
    if not re.fullmatch(r"[0-9a-f]{16}", str(image_id)):
        raise ValueError("Référence image invalide.")
    matches = list(REFERENCE_DIR.glob(image_id + ".*"))
    if len(matches) != 1 or matches[0].suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Image de référence introuvable.")
    path = matches[0].resolve()
    if REFERENCE_DIR.resolve() not in path.parents:
        raise ValueError("Chemin de référence invalide.")
    return path


def save_reference_image(filename: str, stream, length: int) -> dict:
    original = Path(str(filename or "reference.png")).name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Image attendue : PNG, JPG, JPEG ou WebP.")
    if length < 16 or length > MAX_REFERENCE_BYTES:
        raise ValueError("Image trop petite ou trop volumineuse (12 Mo maximum).")
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    image_id = uuid.uuid4().hex[:16]
    target = REFERENCE_DIR / (image_id + suffix)
    remaining = length
    first = b""
    try:
        with target.open("wb") as output:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise IOError("Téléversement incomplet.")
                if len(first) < 16:
                    first += chunk[:16 - len(first)]
                output.write(chunk)
                remaining -= len(chunk)
        valid = (
            (suffix == ".png" and first.startswith(b"\x89PNG\r\n\x1a\n")) or
            (suffix in {".jpg", ".jpeg"} and first.startswith(b"\xff\xd8\xff")) or
            (suffix == ".webp" and first.startswith(b"RIFF") and first[8:12] == b"WEBP")
        )
        if not valid:
            raise ValueError("Le contenu du fichier ne correspond pas à son extension.")
        return {"image_id": image_id, "bytes": length, "filename": original}
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _validate_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Objet JSON attendu.")
    provider = str(data.get("provider", "freellmapi")).lower()
    if provider not in {"freellmapi", "agnes"}:
        raise ValueError("Fournisseur vidéo inconnu.")
    model = str(data.get("model", "")).strip()[:180]
    allowed, confirmed = _free_model_policy(provider)
    if not confirmed or not model or model not in allowed:
        raise ValueError("Modèle bloqué : il doit figurer dans l'allowlist locale de modèles gratuits confirmés.")
    prompt = str(data.get("prompt", "")).strip()
    if len(prompt) < 8 or len(prompt) > 5000:
        raise ValueError("Le prompt doit contenir entre 8 et 5000 caractères.")
    duration = data.get("duration", 6)
    if type(duration) is not int or not 4 <= duration <= 12:
        raise ValueError("La durée doit être comprise entre 4 et 12 secondes.")
    ratio = str(data.get("aspect_ratio", "9:16"))
    if ratio not in ALLOWED_RATIOS:
        raise ValueError("Ratio vidéo non pris en charge.")
    image_id = data.get("image_id")
    if image_id:
        _image_path(str(image_id))
    return {
        "provider": provider, "model": model, "prompt": prompt,
        "duration": duration, "aspect_ratio": ratio,
        "image_id": str(image_id) if image_id else None,
    }


def create_jobs(data: dict, count: int = 1) -> list[dict]:
    validated = _validate_request(data)
    status = provider_status(refresh=True)
    provider = status["providers"].get(validated["provider"], {})
    if not provider.get("ready"):
        raise ValueError(provider.get("note") or "Fournisseur vidéo non prêt.")
    if validated["model"] not in provider.get("configured_models", []):
        raise ValueError("Le modèle n'est pas autorisé par le Cost Guardian.")
    if type(count) is not int or not 1 <= count <= 10:
        raise ValueError("Le nombre de variantes doit être compris entre 1 et 10.")
    init()
    jobs = []
    with _db() as db:
        for _ in range(count):
            job_id = uuid.uuid4().hex[:12]
            db.execute(
                "INSERT INTO media_jobs(id,provider,model,prompt,duration,aspect_ratio,image_id,status) VALUES(?,?,?,?,?,?,?,'queued')",
                (job_id, validated["provider"], validated["model"], validated["prompt"],
                 validated["duration"], validated["aspect_ratio"], validated["image_id"]),
            )
            jobs.append({"id": job_id, "status": "queued"})
        db.commit()
    _notify_worker()
    return jobs


def _notify_worker():
    # Wake the worker without making queue submission depend on worker availability.
    _WAKE.set()


_WAKE = threading.Event()


def _claim_next():
    init()
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM media_jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
        if not row:
            db.commit()
            return None
        db.execute("UPDATE media_jobs SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        db.commit()
        return dict(row)


def _update_job(job_id: str, **fields):
    allowed = {"status", "output_name", "provider_used", "model_used", "metadata", "error"}
    fields = {key: value for key, value in fields.items() if key in allowed}
    if not fields:
        return
    assignments = ", ".join(key + " = ?" for key in fields)
    values = list(fields.values()) + [job_id]
    with _db() as db:
        db.execute(
            "UPDATE media_jobs SET " + assignments + ", updated_at=CURRENT_TIMESTAMP WHERE id=?",
            values,
        )
        db.commit()


def _image_data_url(image_id: str | None) -> str | None:
    path = _image_path(image_id)
    if not path:
        return None
    raw = path.read_bytes()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
    return "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")


def _write_mp4_response(request: Request, target: Path, timeout: int) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".mp4.part")
    total = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" in content_type or "text/" in content_type:
                message = response.read(12000).decode("utf-8", errors="replace")
                raise RuntimeError("Le fournisseur n'a pas renvoyé de MP4 : " + message[:800])
            with temporary.open("wb") as output:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise RuntimeError("Vidéo supérieure à 200 Mo.")
                    output.write(chunk)
            with temporary.open("rb") as video_file:
                header = video_file.read(32)
            if total < 1024 or b"ftyp" not in header[:16]:
                raise RuntimeError("Réponse vidéo invalide : signature MP4 absente.")
            os.replace(temporary, target)
            return {
                "bytes": total,
                "provider": response.headers.get("X-Provider"),
                "model": response.headers.get("X-Model"),
            }
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _run_freellmapi(job: dict, target: Path) -> dict:
    base = _freellmapi_base()
    key = os.environ.get("FREELLMAPI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FREELLMAPI_API_KEY absent.")
    payload = {
        "model": job["model"],
        "prompt": job["prompt"],
        "duration": job["duration"],
        "aspect_ratio": job["aspect_ratio"],
    }
    image_data = _image_data_url(job.get("image_id"))
    if image_data:
        payload["image"] = image_data
    request = Request(
        base + "/v1/videos/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "video/mp4"},
        method="POST",
    )
    return _write_mp4_response(request, target, VIDEO_TIMEOUT_SECONDS)


def _safe_result_url(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("URL vidéo Agnes non sécurisée.")
    if parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("URL vidéo locale refusée.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if not address.is_global:
            raise RuntimeError("Adresse vidéo privée refusée.")
    except ValueError:
        pass
    return str(value)


def _download_agnes_video(url: str, target: Path) -> dict:
    safe_url = _safe_result_url(url)
    request = Request(safe_url, headers={"Accept": "video/mp4"})
    return _write_mp4_response(request, target, 120)


def _run_agnes(job: dict, target: Path) -> dict:
    base = _agnes_base()
    key = os.environ.get("AGNES_API_KEY", "").strip()
    if not key:
        raise RuntimeError("AGNES_API_KEY absent.")
    image_data = _image_data_url(job.get("image_id"))
    payload = {
        "model": job["model"],
        "prompt": job["prompt"],
        "mode": "reference" if image_data else "text",
        "seconds": str(job["duration"]),
        "aspect_ratio": job["aspect_ratio"],
        "size": "720P",
        "n": 1,
    }
    if image_data:
        payload["images"] = [image_data]
    request = Request(
        base + "/v1/videos",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            created = json.loads(response.read(200_000).decode("utf-8"))
    except HTTPError as exc:
        message = exc.read(8000).decode("utf-8", errors="replace")
        raise RuntimeError("Agnes création HTTP " + str(exc.code) + " : " + message[:600])
    video_id = created.get("video_id") if isinstance(created, dict) else None
    if not video_id:
        raise RuntimeError("Agnes n'a pas renvoyé de video_id.")
    delay = 5.0
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        time.sleep(delay)
        query = urlencode({"video_id": str(video_id), "model_name": job["model"]})
        poll_url = base + "/agnesapi?" + query
        try:
            with urlopen(Request(poll_url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"}), timeout=30) as response:
                state = json.loads(response.read(200_000).decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code <= 599:
                delay = min(delay * 1.7, 25)
                continue
            raise RuntimeError("Agnes polling HTTP " + str(exc.code))
        if not isinstance(state, dict):
            raise RuntimeError("Réponse Agnes de polling invalide.")
        status = str(state.get("status", "")).lower()
        if status in {"failed", "error", "cancelled"}:
            raise RuntimeError("Génération Agnes échouée : " + str(state.get("error") or status)[:500])
        if status in {"completed", "complete", "succeeded", "success"}:
            result_url = state.get("url")
            if not result_url:
                raise RuntimeError("Agnes a terminé sans URL vidéo.")
            result = _download_agnes_video(str(result_url), target)
            result["provider"] = "agnes"
            result["model"] = job["model"]
            result["video_id"] = str(video_id)
            return result
        delay = min(delay * 1.25, 20)
    raise TimeoutError("Agnes n'a pas terminé dans le délai de 15 minutes.")


def _validate_mp4(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"validated": False, "method": "mp4_signature_only"}
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("FFprobe a rejeté le MP4 : " + (result.stderr or "")[:500])
    metadata = json.loads(result.stdout or "{}")
    duration = None
    try:
        duration = float(metadata.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    streams = metadata.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    if not video_stream:
        raise RuntimeError("Aucun flux vidéo dans le MP4.")
    return {
        "validated": True,
        "duration_seconds": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "method": "ffprobe",
    }


def _safe_error(value: object) -> str:
    message = str(value or type(value).__name__)
    for secret_name in ("FREELLMAPI_API_KEY", "AGNES_API_KEY"):
        secret = os.environ.get(secret_name, "").strip()
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:1200]


def _process_job(job: dict):
    job_id = job["id"]
    target = OUTPUT_DIR / (job_id + ".mp4")
    try:
        # Recheck authorization at dispatch, not only when queued.
        _validate_request(job)
        status = provider_status(refresh=True)["providers"].get(job["provider"], {})
        if not status.get("ready") or job["model"] not in status.get("configured_models", []):
            raise RuntimeError("Fournisseur ou modèle non autorisé au moment de l’exécution.")
        if job["provider"] == "freellmapi":
            result = _run_freellmapi(job, target)
        elif job["provider"] == "agnes":
            result = _run_agnes(job, target)
        else:
            raise RuntimeError("Fournisseur vidéo non pris en charge.")
        validation = _validate_mp4(target)
        metadata = {**result, **validation}
        _update_job(
            job_id, status="completed", output_name=target.name,
            provider_used=result.get("provider") or job["provider"],
            model_used=result.get("model") or job["model"],
            metadata=json.dumps(metadata, ensure_ascii=False),
            error=None,
        )
    except Exception as exc:
        target.unlink(missing_ok=True)
        _update_job(job_id, status="failed", error=_safe_error(exc))


def _worker_loop():
    while not _STOP.is_set():
        job = _claim_next()
        if not job:
            _WAKE.wait(1.0)
            _WAKE.clear()
            continue
        _process_job(job)


def start_worker():
    global _WORKER
    init()
    with _LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        with _db() as db:
            db.execute(
                "UPDATE media_jobs SET status='interrupted',error='Production OS s’est arrêté pendant cette génération ; relance manuelle requise pour éviter un doublon.',updated_at=CURRENT_TIMESTAMP WHERE status='running'"
            )
            db.commit()
        _STOP.clear()
        _WORKER = threading.Thread(target=_worker_loop, name="ProductionOS-VideoFactory", daemon=True)
        _WORKER.start()


def stop_worker(timeout: float = 3.0):
    _STOP.set()
    _WAKE.set()
    worker = _WORKER
    if worker and worker.is_alive():
        worker.join(timeout=max(0.1, min(timeout, 10.0)))


def list_jobs(limit: int = 30) -> list[dict]:
    init()
    limit = max(1, min(int(limit), 100))
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM media_jobs ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_public_job(row) for row in rows]


def _public_job(row) -> dict:
    item = dict(row)
    try:
        item["metadata"] = json.loads(item.get("metadata") or "{}")
    except (ValueError, TypeError):
        item["metadata"] = {}
    item.pop("image_id", None)
    return item


def get_job(job_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{12}", str(job_id or "")):
        return None
    init()
    with _db() as db:
        row = db.execute("SELECT * FROM media_jobs WHERE id=?", (job_id,)).fetchone()
    return _public_job(row) if row else None


def output_path(job_id: str) -> Path | None:
    job = get_job(job_id)
    if not job or job.get("status") != "completed":
        return None
    name = job.get("output_name") or ""
    if name != job_id + ".mp4":
        return None
    path = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in path.parents or not path.is_file():
        return None
    return path


def retry_job(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{12}", str(job_id or "")):
        raise ValueError("Identifiant de mission invalide.")
    init()
    with _db() as db:
        row = db.execute("SELECT * FROM media_jobs WHERE id=?", (job_id,)).fetchone()
    job = dict(row) if row else None
    if not job or job.get("status") not in {"failed", "interrupted"}:
        raise ValueError("Seules les générations échouées ou interrompues peuvent être relancées.")
    data = {
        "provider": job["provider"], "model": job["model"],
        "prompt": job["prompt"], "duration": job["duration"],
        "aspect_ratio": job["aspect_ratio"],
    }
    if job.get("image_id"):
        data["image_id"] = job["image_id"]
    return create_jobs(data, 1)[0]


def get_status(refresh: bool = False) -> dict:
    status = provider_status(refresh=refresh)
    jobs = list_jobs(30)
    counts = {}
    for item in jobs:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"providers": status["providers"], "free_only": True, "paid_fallback": False,
            "generation_enabled": status["generation_enabled"], "jobs": jobs, "counts": counts,
            "notes": status["notes"]}
