"""Standalone local Qwen3-TTS inference process.

Only executed by qwen_btb_voice.synthesize after config and reference validation.
Model is pinned by revision. Model weights download once on first use via HF;
later use is offline if the cache is complete.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def run(request_file: Path) -> None:
    request = json.loads(request_file.read_text(encoding="utf-8"))
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel

    torch.manual_seed(int(request["seed"]))
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model = Qwen3TTSModel.from_pretrained(
        request["model"],
        revision=request["revision"],
        device_map=device,
        dtype=torch.float16 if device.startswith("cuda") else torch.float32,
        attn_implementation="sdpa",
    )
    audio, rate = model.generate_voice_clone(
        text=request["script"],
        language=request["language"],
        ref_audio=request["reference"],
        ref_text=request["reference_text"],
        x_vector_only_mode=False,
    )
    target = Path(request["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), audio[0], rate, subtype="PCM_16")
    print(f"Qwen3-TTS WAV generated: {target} | {rate} Hz | {device}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_qwen_voice.py <request.json>")
    run(Path(sys.argv[1]))
