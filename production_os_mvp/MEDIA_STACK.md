# Production OS — Media Stack / Quality Gate

## Architecture
- yt-dlp: acquisition explicite de contenus autorisés. No playlists or automatic publication.
- faster-whisper: Whisper local CTranslate2, transcription FR/EN, SRT and word timestamps.
- FFmpeg/FFprobe: existing resource_manager for job encoding; media_stack for probing and full decode validation.
- HyperFrames: local HTML/CSS composition renderer (Node >=22), optional alongside Remotion/Stickman, not a wholesale replacement.
- ComfyUI: existing local diagnostics; API workflow inference and B-roll assembly remain to be validated.

## Windows setup (explicit, no downloads at server startup)
From production_os_mvp:
```powershell
python .\media_stack.py doctor
python -m pip install -U "yt-dlp[default]"
# Use a separate Python 3.12 environment if faster-whisper wheels are unavailable for the main Python.
py -3.12 -m venv "$env:LOCALAPPDATA\ProductionOS\Whisper\.venv"
$whisper = "$env:LOCALAPPDATA\ProductionOS\Whisper\.venv\Scripts\python.exe"
& $whisper -m pip install faster-whisper
```
Obtain a compatible local CTranslate2 Whisper model deliberately (for example faster-whisper-small, after checking model license/storage). No auto model download. CPU int8 is default, explicit CUDA only when tested.

For HTML rendering use a trusted, separate project folder and Node >=22:
```powershell
$version = npm view hyperframes version
npx "hyperframes@$version" init btb-hyperframes --resolution portrait --skip-skills
cd btb-hyperframes
npm install --save-exact "hyperframes@$version"
npx hyperframes lint
npx hyperframes check
```
The CLI adapter requires the project's local npm install and an authored index.html. Do not execute untrusted compositions. Lock and review dependencies before production.

## Commands
From production_os_mvp (substitute real paths; no secrets):
```powershell
python .\media_stack.py doctor
python .\media_stack.py acquire "https://example.org/your-authorized-video" "$PWD\inbox\acquired" --rights-confirmed
& $whisper .\media_stack.py transcribe ".\inbox\clip.mp4" ".\inbox\captions" --model-path "C:\Models\faster-whisper-small" --language fr
python .\media_stack.py render "C:\Projects\btb-hyperframes" "C:\Exports\btb.mp4"
python .\media_stack.py qa "C:\Exports\btb.mp4" --full-hd --report "C:\Exports\qa.json"
```
For a silent B-roll, use --silent-ok. For a landscape render, use --any-ratio. Imports/transcription/renders are explicit CLI or Media Studio actions, not automatically scheduled by the core orchestrator. No auto-publishing and no cloud AI charges initiated.

## Whole-project 18/20 release criterion (not a measured current score)
| Category | Max | Evidence |
|---|---:|---|
| Functional end-to-end | 5 | Real outputs per workflow, no placeholders, failure recovery |
| Editorial/design quality | 4 | Human review against brand, accuracy and usability |
| Reliability/performance | 4 | Reproducible tests, resource/timeout limits, logs, CPU fallback |
| Security/licensing/privacy | 4 | Rights, input validation, no secrets, permission separation |
| Operational UX | 3 | Clear status, one-step workflows, documentation |
Release requires at least 18/20 and no blocking defect, measured by review and recorded evidence. No automatic editorial score from FFprobe. Manual publication remains required.

## Next end-to-end acceptance
1. Real authorized input: import, transcribe FR and EN, boundary detection, captions.
2. Build canonical BTB composition: black/gold frame, dynamic headline, readable safe-zone subtitles, ComfyUI B-roll with original narration continuity, bottom progress and fade.
3. Compare HyperFrames and existing rendering output on the actual Windows PC. Keep the fastest acceptable verified engine per job. Coordinate those engines with shared GPU resource manager before automated scheduling.
4. Integrate media actions into dashboard and orchestrator with persistent checkpoints, no accidental duplicate renders or downloads.
5. Verify every zone using real inputs: CLOGZ (real consented catalog), KDP (complete book and print-ready PDF), YouTube, Clipping, analytics and customer support. Verify security and recoverability before merging. No claims of a real render or 18/20 until evidence exists.

References: https://github.com/heygen-com/hyperframes ; https://github.com/SYSTRAN/faster-whisper ; https://github.com/yt-dlp/yt-dlp ; https://ffmpeg.org/

## Media Studio (interface locale)
Connect the existing Windows environment before launching app.py from the repository. Example paths must be replaced by your actual local directories:
```powershell
$env:PRODUCTION_WHISPER_PYTHON = "$env:LOCALAPPDATA\ProductionOS\Whisper\.venv\Scripts\python.exe"
$env:PRODUCTION_WHISPER_MODEL = "C:\Models\faster-whisper-small"
$env:PRODUCTION_HYPERFRAMES_PROJECT = "C:\Projects\btb-hyperframes"
python .\app.py
```
Open http://127.0.0.1:8765 then Media Studio. Upload one video (up to 300 MiB), transcribe (FR/EN/auto), or verify the selected source. Acquire public YouTube/Vimeo sources only after attesting rights; verify platform terms and licensing independently. Render only a configured, trusted local HyperFrames project; that project can execute code, so never render unknown compositions. This screen does not yet build the composition from the uploaded file. It runs one media task at a time, persists its status and outputs in `.production_os_runtime/media_studio`, and never resumes interrupted jobs automatically. Reports are downloadable. Technical failure is explicitly reported; technical success is not editorial approval. Existing pipeline workers have their own scheduling; inter-worker GPU arbitration is still required for concurrent heavy renders. Do not start simultaneous heavy jobs. No automatic model downloads or paid cloud rendering.

Tests: `python -m unittest discover -s tests -v`. This change has not yet been exercised on the target Windows PC.
