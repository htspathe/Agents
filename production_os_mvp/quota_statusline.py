"""Optional Antigravity CLI statusLine bridge for Production OS.

Point Antigravity's statusLine.command at this script. It records only quota
fractions, the active model ID and timestamp; never tokens, email or prompts.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / ".antigravity_quota_status.json"


def main() -> int:
    try:
        raw = sys.stdin.read(128_001)
        if len(raw) > 128_000:
            print("Quota non disponible")
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            print("Quota non disponible")
            return 0
        quota = payload.get("quota")
        if not isinstance(quota, dict):
            print("Quota non disponible")
            return 0
        windows = {}
        for key, item in quota.items():
            if not isinstance(key, str) or len(key) > 80 or not isinstance(item, dict):
                continue
            fraction = item.get("remaining_fraction")
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
                continue
            if not math.isfinite(float(fraction)) or not (0 <= fraction <= 1):
                continue
            windows[key] = {
                "remaining_fraction": float(fraction),
                "reset_time": str(item.get("reset_time") or "")[:50],
                "disabled": item.get("disabled") is True,
            }
        model = payload.get("model")
        model_id = str(model.get("id") or "")[:120] if isinstance(model, dict) else ""
        snapshot = {
            "captured_at": time.time(),
            "model_id": model_id,
            "quota": windows,
        }
        temporary = DEST.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, DEST)
        if windows:
            least = min(item["remaining_fraction"] for item in windows.values() if not item["disabled"])
            print(f"Quota {least * 100:.0f}% (Production OS)")
        else:
            print("Quota inconnu (Production OS)")
    except (OSError, ValueError, TypeError):
        print("Quota non disponible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
