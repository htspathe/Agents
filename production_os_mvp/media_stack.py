"""Local-first media operations. Never publishes or installs dependencies implicitly."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a"}
ROOT = Path(__file__).resolve().parent


def executable(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Missing dependency: {name}")
    return path


def run(args, *, cwd=None, timeout=600):
    result = subprocess.run([str(x) for x in args], cwd=cwd, timeout=timeout,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} failed: {result.stderr[-2500:]}")
    return result.stdout


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def probe(path):
    return json.loads(run([executable("ffprobe"), "-v", "error", "-show_streams",
                           "-show_format", "-of", "json", Path(path)], timeout=30))


def doctor():
    return {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "node": shutil.which("node"),
        "yt_dlp": bool(importlib.util.find_spec("yt_dlp")),
        "faster_whisper": bool(importlib.util.find_spec("faster_whisper")),
        "hyperframes": "per-project npm dependency, verify with render",
        "verified_generation": False,
    }


def acquire(url, directory, *, rights_confirmed=False):
    if not rights_confirmed:
        raise ValueError("Explicit rights confirmation required")
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("An HTTPS media URL is required")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # No playlists, credentials, cookies, shell expansion or automatic upload.
    before = set(directory.iterdir())
    run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
         "--no-overwrites", "--no-progress", "--restrict-filenames",
         "--max-filesize", "500M", "--format", "bv*[height<=1080]+ba/b[height<=1080]",
         "--merge-output-format", "mp4", "-o", str(directory / "%(id)s.%(ext)s"),
         "--", url], timeout=1200)
    files = sorted(p.name for p in set(directory.iterdir()) - before
                   if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS)
    if not files:
        raise RuntimeError("No new media file was acquired")
    write_json(directory / "source_manifest.json",
               {"url": url, "rights_attested_by_operator": True, "files": files,
                "publication_approved": False})
    return files


def srt_time(seconds):
    ms = max(0, round(float(seconds) * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def transcribe(source, directory, model_path, *, language=None, device="cpu"):
    from faster_whisper import WhisperModel
    source, directory, model_path = map(Path, (source, directory, model_path))
    if not source.is_file() or not model_path.is_dir():
        raise ValueError("Existing media and local CTranslate2 Whisper model directory required")
    directory.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(str(model_path.resolve()), device=device,
                         compute_type="int8" if device == "cpu" else "float16",
                         local_files_only=True)
    segments, info = model.transcribe(str(source.resolve()), language=language,
                                      word_timestamps=True, vad_filter=True)
    words, rows = [], []
    for seg in segments:
        for word in seg.words or []:
            if word.start is not None and word.end is not None and word.end > word.start:
                words.append({"start": word.start, "end": word.end, "text": word.word,
                              "probability": word.probability})
        if seg.end > seg.start and seg.text.strip():
            rows.append((seg.start, seg.end, seg.text.strip()))
    if not rows:
        raise RuntimeError("No speech detected; human review required")
    write_json(directory / "word_timestamps.json", {"language": info.language, "words": words})
    (directory / "captions.srt").write_text("\n\n".join(
        f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}"
        for i, (a, b, t) in enumerate(rows, 1)) + "\n", encoding="utf-8")
    return {"segments": len(rows), "words": len(words), "language": info.language}


def hyperframes_bin(project):
    root = project / "node_modules" / "hyperframes"
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    binary = package["bin"]
    if isinstance(binary, dict):
        binary = binary["hyperframes"]
    path = (root / binary).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError("Invalid HyperFrames CLI path")
    return path


def render(project, output):
    # Only trusted local compositions: HTML may execute code in the renderer.
    project, output = Path(project).resolve(), Path(output).resolve()
    if not (project / "index.html").is_file() or output.suffix.lower() != ".mp4":
        raise ValueError("Trusted HyperFrames project with index.html and MP4 output required")
    node = executable("node")
    if int(run([node, "-p", "process.versions.node.split('.')[0]"]).strip()) < 22:
        raise RuntimeError("HyperFrames requires Node.js >= 22")
    executable("ffmpeg")
    cli = hyperframes_bin(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    temp = output.with_name(output.stem + ".partial.mp4")
    if temp.exists():
        raise FileExistsError(temp)
    try:
        for command in ("lint", "check"):
            run([node, cli, command], cwd=project, timeout=300)
        run([node, cli, "render", "--output", temp], cwd=project, timeout=1800)
        report = video_qa(temp, require_audio=False, vertical=False)
        if not report["technical_pass"]:
            raise RuntimeError("The rendered file failed technical QC")
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return str(output)


def video_qa(path, *, require_audio=True, vertical=True, expected_size=None):
    path = Path(path)
    checks = {}
    try:
        m = probe(path)
        streams = m.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        duration = float(m.get("format", {}).get("duration", 0))
        checks["nonempty"] = path.is_file() and path.stat().st_size > 0
        checks["video_stream"] = bool(video) and width > 0 and height > 0
        checks["duration"] = math.isfinite(duration) and duration > 0
        checks["aspect_ratio"] = (width * 16 == height * 9) if vertical else True
        checks["target_resolution"] = (width, height) == expected_size if expected_size else True
        checks["audio_stream"] = any(s.get("codec_type") == "audio" for s in streams) if require_audio else True
        # Decode the complete video, not just inspect the MP4 header.
        run([executable("ffmpeg"), "-v", "error", "-xerror", "-i", path,
             "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"], timeout=300)
        checks["decode"] = True
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"technical_pass": False, "checks": checks, "error": str(exc),
                "publication_ready": False, "editorial_score": None}
    return {"technical_pass": all(checks.values()), "checks": checks,
            "width": width, "height": height, "duration": duration,
            "publication_ready": False, "editorial_score": None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    a = sub.add_parser("acquire")
    a.add_argument("url")
    a.add_argument("directory", type=Path)
    a.add_argument("--rights-confirmed", action="store_true")
    t = sub.add_parser("transcribe")
    t.add_argument("source", type=Path)
    t.add_argument("directory", type=Path)
    t.add_argument("--model-path", type=Path, required=True)
    t.add_argument("--language")
    t.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    r = sub.add_parser("render")
    r.add_argument("project", type=Path)
    r.add_argument("output", type=Path)
    q = sub.add_parser("qa")
    q.add_argument("source", type=Path)
    q.add_argument("--silent-ok", action="store_true")
    q.add_argument("--any-ratio", action="store_true")
    q.add_argument("--full-hd", action="store_true")
    q.add_argument("--report", type=Path)
    args = p.parse_args()
    try:
        if args.command == "doctor":
            data = doctor()
        elif args.command == "acquire":
            data = acquire(args.url, args.directory, rights_confirmed=args.rights_confirmed)
        elif args.command == "transcribe":
            data = transcribe(args.source, args.directory, args.model_path,
                              language=args.language, device=args.device)
        elif args.command == "render":
            data = render(args.project, args.output)
        else:
            data = video_qa(args.source, require_audio=not args.silent_ok,
                            vertical=not args.any_ratio,
                            expected_size=(1080, 1920) if args.full_hd else None)
            if args.report:
                write_json(args.report, data)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 1 if isinstance(data, dict) and data.get("technical_pass") is False else 0
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
