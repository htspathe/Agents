"""Standalone CPU-only Qwen3-TTS runtime (Python 3.12 isolated environment).

Usage: python qwen_voice_worker.py input.json
This is launched only on explicit voice generation, never while the dashboard polls.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        raise ValueError("Un fichier de demande vocale est obligatoire.")
    path = Path(sys.argv[1]).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    text = data["text"]
    speaker = data["speaker"]
    output = Path(data["output"]).resolve()
    if speaker not in {"Uncle_Fu", "Ryan", "Aiden"}:
        raise ValueError("Voix Qwen3-TTS non autorisée.")
    if not isinstance(text, str) or not 12 <= len(text) <= 12000:
        raise ValueError("Texte vocal invalide.")
    if not output.parent.is_dir() or output.suffix.lower() != ".wav":
        raise ValueError("Chemin de sortie audio invalide.")
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel

    model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        device_map="cpu", dtype=torch.float32
    )
    # The 0.6B CustomVoice variant supports fixed speaker presets, not full
    # natural-language style control. A separate 1.7B model is needed for that.
    result, sr = model.generate_custom_voice(
        text=text, language="French", speaker=speaker
    )
    sf.write(str(output), result[0], sr, subtype="PCM_16")
    print(json.dumps({"success": True, "audio_file": output.name, "sample_rate": sr}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
