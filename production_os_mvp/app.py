"""Local-only Production OS server. Python standard library, no cloud setup."""
from __future__ import annotations
import argparse
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import engine
from agents import AGENTS, LABELS, ROOT, WORKFLOW_STEPS

TOKEN = secrets.token_urlsafe(36)
MAX_UPLOAD = 300 * 1024 * 1024
ALLOWED_UPLOADS = {".mp4", ".mov", ".mkv", ".webm"}
ALLOWED_ARTIFACTS = {".json", ".md", ".txt", ".svg", ".mp4"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProductionOS/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[HTTP] " + fmt % args + "\n")

    def respond(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def send_blob(self, raw, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        return secrets.compare_digest(self.headers.get("X-Production-Token", ""), TOKEN)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            raw = (ROOT / "index.html").read_text(encoding="utf-8").replace("__PRODUCTION_TOKEN__", TOKEN).encode("utf-8")
            self.send_blob(raw, "text/html; charset=utf-8")
            return
        # Do not allow read APIs without session token.
        if not self.authorized():
            self.respond(403, {"error": "Jeton de session local manquant"})
            return
        if path == "/api/state":
            self.respond(200, {"jobs": engine.list_jobs(), "stats": engine.stats(),
                               "agents": [{"id": i, "name": n, "role": r} for i, n, r in AGENTS],
                               "workflows": LABELS, "ffmpeg_installed": shutil.which("ffmpeg") is not None,
                               "ollama_model": os.environ.get("OLLAMA_MODEL") or None})
            return
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path)
        if m:
            detail = engine.job_detail(m.group(1))
            return self.respond(200, detail) if detail else self.respond(404, {"error": "Mission introuvable"})
        m = re.fullmatch(r"/api/files/([0-9a-f]{12})/([^/]+)", path)
        if m:
            job_id, filename = m.groups()
            filename = unquote(filename)
            root = (ROOT / "outputs" / job_id).resolve()
            target = (root / filename).resolve()
            if not target.is_file() or target.parent != root or target.suffix.lower() not in ALLOWED_ARTIFACTS:
                self.respond(404, {"error": "Fichier indisponible"})
                return
            typ = {".svg": "image/svg+xml", ".md": "text/markdown", ".mp4": "video/mp4", ".json": "application/json"}.get(target.suffix.lower(), "text/plain")
            # SVG produced by this program but opening it in same origin is not necessary.
            if target.suffix.lower() == ".svg":
                typ = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with target.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, length=256*1024)
            return
        self.respond(404, {"error": "Route inconnue"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.authorized():
            self.respond(403, {"error": "Jeton de session local manquant"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.respond(400, {"error": "Taille invalide"})
        if length < 1 or length > MAX_UPLOAD:
            return self.respond(413, {"error": "Fichier ou requête trop volumineux"})
        if path == "/api/upload":
            original = Path(self.headers.get("X-Filename", "video.mp4")).name
            suffix = Path(original).suffix.lower()
            if suffix not in ALLOWED_UPLOADS:
                return self.respond(400, {"error": "Vidéo attendue : MP4, MOV, MKV ou WebM"})
            filename = uuid.uuid4().hex[:10] + suffix
            target = ROOT / "inbox" / filename
            try:
                with target.open("wb") as f:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024*1024, remaining))
                        if not chunk:
                            raise IOError("Téléversement incomplet")
                        f.write(chunk)
                        remaining -= len(chunk)
                self.respond(201, {"source_file": filename, "bytes": length})
            except OSError as exc:
                target.unlink(missing_ok=True)
                self.respond(400, {"error": str(exc)[:200]})
            return
        if path == "/api/jobs":
            if length > 20_000:
                return self.respond(413, {"error": "Brief trop volumineux"})
            try:
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Objet JSON attendu")
                job_id = engine.new_job(data)
                engine.run_async(job_id)
                self.respond(201, {"id": job_id, "status": "queued"})
            except (ValueError, TypeError) as exc:
                self.respond(400, {"error": str(exc)[:200]})
            return
        self.respond(404, {"error": "Route inconnue"})


def make_demo_video():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Installez FFmpeg pour créer une vidéo de démonstration")
    path = ROOT / "inbox" / "source_demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=24", "-t", "12", "-c:v", "libx264",
                    "-crf", "34", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=120)
    return path


def main():
    parser = argparse.ArgumentParser(description="Production OS MVP — 12 agents, local, zero cloud")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Créer trois missions de démonstration")
    parser.add_argument("--demo-video", action="store_true", help="Créer une source vidéo synthétique pour tester Clipping")
    args = parser.parse_args()
    engine.init()
    if args.demo_video:
        print("Vidéo synthétique disponible :", make_demo_video())
    if args.demo:
        for typ, brief in [("clogz", "Campagne pour les sabots Spider-Man"),
                           ("kdp", "Guide débutant de gestion du budget personnel"),
                           ("youtube", "Comprendre comment fonctionne la valorisation DCF")]:
            id_ = engine.new_job({"workflow": typ, "brief": brief})
            engine.run_job(id_)
            print(f"Démo {typ}: {id_}")
    if not (1 <= args.port <= 65535):
        parser.error("Port invalide")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Production OS démarré : http://127.0.0.1:{args.port}")
    print("Local uniquement. CTRL+C pour arrêter. Aucun compte connecté, aucune publication automatique.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
        server.server_close()


if __name__ == "__main__":
    main()
