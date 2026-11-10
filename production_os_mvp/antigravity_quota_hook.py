"""Antigravity statusLine hook that exposes quota telemetry to Production OS.

The CLI passes its live session state as JSON on stdin. This hook writes only
quota percentages and reset metadata to a local runtime file, then chains any
previous user-defined statusLine command.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path


def extract_quota(payload: dict) -> dict:
    quota = payload.get("quota")
    windows = []
    if isinstance(quota, dict):
        for bucket_id, item in quota.items():
            if not isinstance(item, dict) or item.get("disabled") is True:
                continue
            fraction = item.get("remaining_fraction")
            if (
                isinstance(fraction, (int, float))
                and not isinstance(fraction, bool)
                and math.isfinite(float(fraction))
                and 0 <= float(fraction) <= 1
            ):
                windows.append({
                    "bucket": str(bucket_id),
                    "remaining_percent": round(float(fraction) * 100, 2),
                    "reset_time": item.get("reset_time"),
                    "reset_in_seconds": item.get("reset_in_seconds"),
                })
    if not windows:
        return {
            "state": "unknown",
            "remaining_percent": None,
            "windows": [],
            "observed_at": time.time(),
        }
    remaining = min(item["remaining_percent"] for item in windows)
    return {
        "state": "ok",
        "remaining_percent": remaining,
        "windows": windows,
        "observed_at": time.time(),
    }


def write_snapshot(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    raw = sys.stdin.read(1_000_000)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = {}
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(config, dict):
            snapshot = extract_quota(payload)
            snapshot["source"] = "antigravity_statusline"
            write_snapshot(Path(config["quota_file"]), snapshot)
    except (OSError, ValueError, TypeError, KeyError):
        pass

    output = ""
    previous = config.get("previous_command") if "config" in locals() and isinstance(config, dict) else None
    if isinstance(previous, str) and previous.strip():
        try:
            result = subprocess.run(
                previous, input=raw, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=4,
                shell=True, check=False
            )
            output = (result.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            output = ""
    if not output:
        output = "Antigravity"
    remaining = snapshot.get("remaining_percent") if "snapshot" in locals() else None
    if isinstance(remaining, (int, float)):
        output += f" · quota {remaining:.1f}%"
    sys.stdout.write(output[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
