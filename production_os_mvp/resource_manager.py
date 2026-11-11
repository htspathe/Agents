"""Local hardware monitoring and safe CPU/NVIDIA video-encoding policy.

Only H.264 NVENC has been runtime-probed. CUDA decoding, ComfyUI and Ollama
inference remain separate capabilities and are never assumed to be active.
"""
from __future__ import annotations

import csv
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ".production_os_resources.json"
DEFAULT_SETTINGS = {
    "gpu_enabled": True,
    "cpu_fallback": True,
    "max_vram_percent": 70,
    "min_free_vram_mb": 768,
    "cpu_threads": 6,
}
_ALLOWED_VRAM_LIMITS = {50, 60, 65, 70, 75, 80, 85, 90}
_ALLOWED_FREE_VRAM = {256, 512, 768, 1024, 1536}
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict | None = None
_STATUS_CACHE_AT = 0.0
_STATUS_TTL_SECONDS = 3.0
_NVENC_PROBE_LOCK = threading.Lock()
_NVENC_PROBE_CACHE_KEY: tuple | None = None
_NVENC_PROBE_CACHE_RESULT: tuple[bool, str] | None = None
_NVENC_PROBE_CACHE_AT = 0.0
_NVENC_PROBE_TTL_SECONDS = 120.0
_CPU_SAMPLE_LOCK = threading.Lock()
_CPU_SAMPLE: tuple[int, int] | None = None


def settings_path() -> Path:
    return ROOT / SETTINGS_FILE


def get_settings() -> dict:
    result = dict(DEFAULT_SETTINGS)
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            return result
        if type(stored.get("gpu_enabled")) is bool:
            result["gpu_enabled"] = stored["gpu_enabled"]
        if type(stored.get("cpu_fallback")) is bool:
            result["cpu_fallback"] = stored["cpu_fallback"]
        if type(stored.get("max_vram_percent")) is int and stored["max_vram_percent"] in _ALLOWED_VRAM_LIMITS:
            result["max_vram_percent"] = stored["max_vram_percent"]
        if type(stored.get("min_free_vram_mb")) is int and stored["min_free_vram_mb"] in _ALLOWED_FREE_VRAM:
            result["min_free_vram_mb"] = stored["min_free_vram_mb"]
        if type(stored.get("cpu_threads")) is int and 0 <= stored["cpu_threads"] <= 32:
            result["cpu_threads"] = stored["cpu_threads"]
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_settings(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Configuration matérielle invalide")
    gpu_enabled = data.get("gpu_enabled", True)
    cpu_fallback = data.get("cpu_fallback", True)
    max_vram_percent = data.get("max_vram_percent", 70)
    min_free_vram_mb = data.get("min_free_vram_mb", 768)
    cpu_threads = data.get("cpu_threads", 6)
    if type(gpu_enabled) is not bool or type(cpu_fallback) is not bool:
        raise ValueError("Les options GPU et CPU doivent être des booléens")
    if type(max_vram_percent) is not int or max_vram_percent not in _ALLOWED_VRAM_LIMITS:
        raise ValueError("Le seuil VRAM doit être une valeur autorisée entre 50 et 90 %")
    if type(min_free_vram_mb) is not int or min_free_vram_mb not in _ALLOWED_FREE_VRAM:
        raise ValueError("La réserve VRAM doit être de 256, 512, 768, 1024 ou 1536 Mo")
    if type(cpu_threads) is not int or not 0 <= cpu_threads <= 32:
        raise ValueError("Le nombre de threads CPU doit être compris entre 0 et 32")
    settings = {
        "gpu_enabled": gpu_enabled,
        "cpu_fallback": cpu_fallback,
        "max_vram_percent": max_vram_percent,
        "min_free_vram_mb": min_free_vram_mb,
        "cpu_threads": cpu_threads,
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


def _number(value: str):
    value = str(value or "").strip()
    if not value or value.upper() in {"N/A", "[NOT SUPPORTED]"}:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except ValueError:
        return None


def _find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
    ]
    return next((str(path) for path in candidates if path.is_file()), None)


def _gpu_info() -> dict:
    executable = _find_nvidia_smi()
    empty = {
        "available": False, "name": None, "driver_version": None,
        "memory_total_mb": None, "memory_used_mb": None, "memory_free_mb": None,
        "utilization_percent": None, "temperature_c": None, "note": "NVIDIA non détecté.",
    }
    if not executable:
        return empty
    query = (
        "name,driver_version,memory.total,memory.used,memory.free,"
        "utilization.gpu,temperature.gpu"
    )
    try:
        cp = subprocess.run(
            [executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
        if cp.returncode != 0 or not cp.stdout.strip():
            return {**empty, "note": (cp.stderr or "nvidia-smi n'a retourné aucune carte.")[-300:]}
        row = next(csv.reader([cp.stdout.strip().splitlines()[0]], skipinitialspace=True))
        if len(row) < 7:
            return {**empty, "note": "Réponse nvidia-smi incomplète."}
        return {
            "available": True,
            "name": row[0].strip(),
            "driver_version": row[1].strip(),
            "memory_total_mb": _number(row[2]),
            "memory_used_mb": _number(row[3]),
            "memory_free_mb": _number(row[4]),
            "utilization_percent": _number(row[5]),
            "temperature_c": _number(row[6]),
            "note": "Carte NVIDIA détectée par nvidia-smi.",
        }
    except (OSError, subprocess.SubprocessError, StopIteration, csv.Error) as exc:
        return {**empty, "note": f"Diagnostic NVIDIA indisponible ({type(exc).__name__})."}


def _cpu_percent() -> float | None:
    global _CPU_SAMPLE
    try:
        if os.name == "nt":
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

            idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
            ok = ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
            if not ok:
                return None

            def value(item):
                return (int(item.dwHighDateTime) << 32) | int(item.dwLowDateTime)

            idle_ticks = value(idle)
            total_ticks = value(kernel) + value(user)
        else:
            line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
            fields = [int(item) for item in line.split()[1:]]
            idle_ticks = fields[3] + (fields[4] if len(fields) > 4 else 0)
            total_ticks = sum(fields)
        with _CPU_SAMPLE_LOCK:
            previous = _CPU_SAMPLE
            _CPU_SAMPLE = (idle_ticks, total_ticks)
        if previous is None:
            return None
        idle_delta = idle_ticks - previous[0]
        total_delta = total_ticks - previous[1]
        if total_delta <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * (1 - idle_delta / total_delta))), 1)
    except (OSError, AttributeError, ValueError, IndexError):
        return None


def _memory_info() -> dict:
    total = available = None
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        try:
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys / (1024 * 1024))
                available = int(status.ullAvailPhys / (1024 * 1024))
        except (OSError, AttributeError):
            pass
    else:
        try:
            values = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0])
            total = int(values.get("MemTotal", 0) / 1024)
            available = int(values.get("MemAvailable", values.get("MemFree", 0)) / 1024)
        except (OSError, ValueError, IndexError):
            pass
    used = max(0, total - available) if total is not None and available is not None else None
    return {
        "total_mb": total,
        "available_mb": available,
        "used_mb": used,
        "used_percent": round(100 * used / total, 1) if total and used is not None else None,
    }


def _cached_nvenc_probe(executable: str, gpu: dict) -> tuple[bool, str]:
    """Run a short real MP4 encode using the same profile as production exports.

    A tiny 64x64 lavfi/null-output probe can be rejected by some NVENC/FFmpeg
    combinations even when normal 1080p MP4 encoding works. Match the user's
    production profile (H.264, p5, 6 Mbps, yuv420p) and verify a non-empty file.
    """
    global _NVENC_PROBE_CACHE_KEY, _NVENC_PROBE_CACHE_RESULT, _NVENC_PROBE_CACHE_AT
    key = (executable, gpu.get("driver_version"), gpu.get("name"))
    with _NVENC_PROBE_LOCK:
        if (
            _NVENC_PROBE_CACHE_KEY == key
            and _NVENC_PROBE_CACHE_RESULT is not None
            and time.monotonic() - _NVENC_PROBE_CACHE_AT < _NVENC_PROBE_TTL_SECONDS
        ):
            return _NVENC_PROBE_CACHE_RESULT
        try:
            with tempfile.TemporaryDirectory(prefix="production-os-nvenc-probe-") as temp_dir:
                output = Path(temp_dir) / "nvenc_probe.mp4"
                probe = subprocess.run(
                    [
                        executable, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
                        "-t", "1", "-an", "-c:v", "h264_nvenc",
                        "-preset", "p5", "-b:v", "6M", "-pix_fmt", "yuv420p",
                        str(output),
                    ],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=20, check=False,
                )
                ok = (
                    probe.returncode == 0
                    and output.is_file()
                    and output.stat().st_size > 0
                )
                if ok:
                    note = "Test réel H.264 NVENC 1080p MP4 (preset p5, 6 Mbps) réussi."
                elif probe.returncode == 0:
                    note = "Test NVENC invalide : FFmpeg a terminé sans produire de MP4."
                else:
                    note = "Test NVENC échoué : " + (
                        probe.stderr or probe.stdout or f"FFmpeg exit code {probe.returncode}"
                    )[-420:]
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            ok = False
            note = f"Test NVENC indisponible ({type(exc).__name__}) : {str(exc)[:260]}"
        _NVENC_PROBE_CACHE_KEY = key
        _NVENC_PROBE_CACHE_RESULT = (ok, note)
        _NVENC_PROBE_CACHE_AT = time.monotonic()
        return _NVENC_PROBE_CACHE_RESULT


def _ffmpeg_info(gpu: dict, settings: dict) -> dict:
    executable = shutil.which("ffmpeg")
    base = {
        "installed": bool(executable), "version": None, "nvenc_compiled": False,
        "nvenc_runtime_ok": False, "nvenc_usable": False, "selected_encoder": "libx264",
        "note": "FFmpeg introuvable : encodeur CPU indisponible.",
    }
    if not executable:
        return base
    try:
        version = subprocess.run(
            [executable, "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5, check=False,
        )
        encoders = subprocess.run(
            [executable, "-hide_banner", "-encoders"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=8, check=False,
        )
        encoder_text = (encoders.stdout or "") + (encoders.stderr or "")
        compiled = encoders.returncode == 0 and "h264_nvenc" in encoder_text
        runtime_ok = False
        probe_note = "Encodeur NVENC absent de cette compilation FFmpeg."
        if compiled and gpu.get("available") and settings["gpu_enabled"]:
            free = gpu.get("memory_free_mb")
            if free is not None and free < settings["min_free_vram_mb"]:
                probe_note = "Test NVENC différé : réserve VRAM libre insuffisante."
            else:
                runtime_ok, probe_note = _cached_nvenc_probe(executable, gpu)
        elif compiled and not gpu.get("available"):
            probe_note = "NVENC compilé, mais aucune carte NVIDIA n'est détectée."
        elif compiled and not settings["gpu_enabled"]:
            probe_note = "GPU désactivé dans les paramètres."
        total = gpu.get("memory_total_mb")
        used = gpu.get("memory_used_mb")
        used_percent = (100 * used / total) if total and used is not None else None
        memory_ok = (
            (gpu.get("memory_free_mb") is None or gpu["memory_free_mb"] >= settings["min_free_vram_mb"])
            and (used_percent is None or used_percent < settings["max_vram_percent"])
        )
        usable = bool(compiled and runtime_ok and gpu.get("available") and memory_ok and settings["gpu_enabled"])
        if not settings["gpu_enabled"]:
            selected, note = "libx264", "GPU désactivé dans les paramètres."
        elif usable:
            selected, note = "h264_nvenc", probe_note
        elif not gpu.get("available"):
            selected, note = "libx264", "GPU NVIDIA non détecté ; repli CPU."
        elif not compiled:
            selected, note = "libx264", probe_note
        elif not runtime_ok:
            selected, note = "libx264", probe_note
        elif not memory_ok:
            selected, note = "libx264", "VRAM sous le seuil de sécurité ; repli CPU."
        else:
            selected, note = "libx264", "NVENC désactivé ou indisponible ; repli CPU."
        return {
            "installed": True,
            "version": (version.stdout or version.stderr).splitlines()[0][:120] if (version.stdout or version.stderr) else None,
            "nvenc_compiled": compiled,
            "nvenc_runtime_ok": runtime_ok,
            "nvenc_usable": usable,
            "selected_encoder": selected,
            "note": note,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {**base, "note": f"Diagnostic FFmpeg indisponible ({type(exc).__name__})."}


def get_status(refresh: bool = False) -> dict:
    global _STATUS_CACHE, _STATUS_CACHE_AT
    with _STATUS_LOCK:
        if not refresh and _STATUS_CACHE is not None and time.monotonic() - _STATUS_CACHE_AT < _STATUS_TTL_SECONDS:
            return json.loads(json.dumps(_STATUS_CACHE))
    settings = get_settings()
    gpu = _gpu_info()
    memory = _memory_info()
    ffmpeg = _ffmpeg_info(gpu, settings)
    total_gb = round(memory["total_mb"] / 1024, 1) if memory["total_mb"] is not None else None
    available_gb = round(memory["available_mb"] / 1024, 1) if memory["available_mb"] is not None else None
    result = {
        "cpu": {
            "name": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or platform.machine(),
            "logical_cores": os.cpu_count() or 1,
            "usage_percent": _cpu_percent(),
        },
        "memory": {**memory, "total_gb": total_gb, "available_gb": available_gb},
        "gpu": gpu,
        "ffmpeg": ffmpeg,
        "settings": settings,
        "gpu_task_limit": 1,
    }
    with _STATUS_LOCK:
        _STATUS_CACHE = result
        _STATUS_CACHE_AT = time.monotonic()
    return json.loads(json.dumps(result))


def choose_video_encoder() -> str:
    return get_status().get("ffmpeg", {}).get("selected_encoder", "libx264")


def _cpu_thread_count(settings: dict) -> int:
    configured = settings.get("cpu_threads", 0)
    if configured:
        return configured
    return max(1, (os.cpu_count() or 4) // 2)


def video_encoder_args(profile: str = "final", encoder: str | None = None) -> list[str]:
    profile = profile if profile in {"preview", "clip", "final"} else "final"
    encoder = encoder or choose_video_encoder()
    if encoder == "h264_nvenc":
        preset = "p3" if profile == "preview" else "p5"
        bitrate = {"preview": "1800k", "clip": "4M", "final": "6M"}[profile]
        return ["-c:v", "h264_nvenc", "-preset", preset, "-b:v", bitrate]
    preset = {"preview": "ultrafast", "clip": "veryfast", "final": "veryfast"}[profile]
    crf = {"preview": "30", "clip": "27", "final": "22"}[profile]
    return [
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-threads", str(_cpu_thread_count(get_settings())),
    ]


def gpu_lock_path() -> Path:
    return Path(tempfile.gettempdir()) / "production_os_gpu_encode.lock"


@contextmanager
def gpu_task_lock(timeout: float = 180.0):
    """Cross-process lock: only one Production OS NVENC render at a time."""
    path = gpu_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Délai dépassé en attendant le verrou GPU")
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Délai dépassé en attendant le verrou GPU")
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_video_encode(
    input_args: list[str],
    output_path: str | Path,
    profile: str = "final",
    audio_args: list[str] | None = None,
    timeout: int = 240,
) -> dict:
    """Encode H.264 with NVENC when verified; retry once with libx264 if allowed."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg introuvable")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    preferred = choose_video_encoder()
    audio = list(audio_args or [])

    def execute(encoder: str):
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            *input_args, *video_encoder_args(profile, encoder),
            "-pix_fmt", "yuv420p", *audio, str(output),
        ]
        try:
            if encoder == "h264_nvenc":
                with gpu_task_lock(timeout=min(max(timeout, 5), 300)):
                    cp = subprocess.run(
                        command, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=timeout, check=False,
                    )
            else:
                cp = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout, check=False,
                )
            if cp.returncode == 0 and output.is_file() and output.stat().st_size > 0:
                return True, ""
            return False, (cp.stderr or cp.stdout or f"FFmpeg exit code {cp.returncode}")[-700:]
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:500]}"

    ok, error = execute(preferred)
    used_encoder = preferred
    fallback_used = False
    if not ok and preferred == "h264_nvenc" and settings["cpu_fallback"]:
        output.unlink(missing_ok=True)
        ok, error = execute("libx264")
        used_encoder = "libx264"
        fallback_used = True
    if not ok:
        output.unlink(missing_ok=True)
        raise RuntimeError("Échec FFmpeg : " + error)
    return {
        "encoder": used_encoder,
        "fallback_used": fallback_used,
        "note": "Repli CPU utilisé après échec NVENC." if fallback_used else f"Encodage terminé avec {used_encoder}.",
    }


def run_ffmpeg_command(
    ffmpeg_args: list[str],
    profile: str = "final",
    timeout: int = 600,
) -> dict:
    """Run a single-output FFmpeg command through the shared encoder policy.

    Existing video encoder/quality flags are replaced so the wrapper can apply
    the verified NVENC profile and perform one CPU fallback when necessary.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg introuvable")
    args = list(ffmpeg_args)
    if args and args[0] == "--":
        args = args[1:]
    if len(args) < 2:
        raise ValueError("Arguments FFmpeg incomplets : entrée et sortie requises")
    output = args[-1]
    base = []
    strip_next = False
    strip_options = {
        "-c:v", "-codec:v", "-vcodec", "-preset", "-crf", "-b:v",
        "-maxrate", "-bufsize", "-rc", "-cq", "-tune", "-profile:v",
        "-threads", "-pix_fmt",
    }
    for item in args[:-1]:
        if strip_next:
            strip_next = False
            continue
        if item in strip_options:
            strip_next = True
            continue
        base.append(item)
    settings = get_settings()
    preferred = choose_video_encoder()

    def execute(encoder: str):
        command = [
            ffmpeg, "-y", "-hide_banner", *base,
            *video_encoder_args(profile, encoder), "-pix_fmt", "yuv420p", output,
        ]
        try:
            if encoder == "h264_nvenc":
                with gpu_task_lock(timeout=min(max(timeout, 5), 300)):
                    cp = subprocess.run(
                        command, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=timeout, check=False,
                    )
            else:
                cp = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout, check=False,
                )
            if cp.returncode == 0 and output != "-" and Path(output).is_file() and Path(output).stat().st_size > 0:
                return True, ""
            if cp.returncode == 0 and output == "-":
                return True, ""
            return False, (cp.stderr or cp.stdout or f"FFmpeg exit code {cp.returncode}")[-900:]
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:600]}"

    ok, error = execute(preferred)
    used_encoder = preferred
    fallback_used = False
    if not ok and preferred == "h264_nvenc" and settings["cpu_fallback"]:
        if output != "-":
            Path(output).unlink(missing_ok=True)
        ok, error = execute("libx264")
        used_encoder = "libx264"
        fallback_used = True
    if not ok:
        if output != "-":
            Path(output).unlink(missing_ok=True)
        raise RuntimeError("Échec FFmpeg : " + error)
    return {
        "encoder": used_encoder,
        "fallback_used": fallback_used,
        "note": "Repli CPU utilisé après échec NVENC." if fallback_used else f"Encodage terminé avec {used_encoder}.",
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "ffmpeg":
        print("Usage: python resource_manager.py ffmpeg -- <arguments FFmpeg>", file=sys.stderr)
        return 2
    try:
        result = run_ffmpeg_command(args[1:])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def get_video_policy() -> dict:
    status = get_status()
    settings = get_settings()
    ffmpeg = status.get("ffmpeg", {})
    return {
        "preferred_encoder": ffmpeg.get("selected_encoder", "libx264"),
        "nvenc_usable": ffmpeg.get("nvenc_usable", False),
        "cpu_fallback": settings["cpu_fallback"],
        "max_vram_percent": settings["max_vram_percent"],
        "min_free_vram_mb": settings["min_free_vram_mb"],
        "cpu_threads": _cpu_thread_count(settings),
        "gpu_task_limit": 1,
        "note": ffmpeg.get("note", ""),
    }


def worker_environment() -> dict:
    policy = get_video_policy()
    settings = get_settings()
    return {
        "PRODUCTION_OS_VIDEO_ENCODER": policy["preferred_encoder"],
        "PRODUCTION_OS_VIDEO_CPU_FALLBACK": "1" if settings["cpu_fallback"] else "0",
        "PRODUCTION_OS_VIDEO_MAX_VRAM_PERCENT": str(settings["max_vram_percent"]),
        "PRODUCTION_OS_VIDEO_MIN_FREE_VRAM_MB": str(settings["min_free_vram_mb"]),
        "PRODUCTION_OS_CPU_THREADS": str(policy["cpu_threads"]),
        "PRODUCTION_OS_GPU_LOCK_PATH": str(gpu_lock_path()),
        "PRODUCTION_OS_FFMPEG_WRAPPER": str(Path(__file__).resolve()),
    }


if __name__ == "__main__":
    raise SystemExit(main())
