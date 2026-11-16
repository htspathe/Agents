# Explicit local Whisper setup. Downloads a multilingual CTranslate2 model (~0.5 GB).
$ErrorActionPreference = "Stop"
$root = Join-Path $env:LOCALAPPDATA "ProductionOS\Whisper"
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$model = Join-Path $root "models\small"
New-Item -ItemType Directory -Force -Path $root | Out-Null
if (-not (Test-Path -LiteralPath $py)) {
    & py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 manquant. Installer Python 3.12 avant de relancer." }
}
& $py -m pip install faster-whisper huggingface-hub
if ($LASTEXITCODE -ne 0) { throw "Installation de Whisper en echec." }
# Use explicit paths as arguments; no credentials or implicit inference.
& $py -c "from huggingface_hub import snapshot_download; import sys; snapshot_download(repo_id='Systran/faster-whisper-small', local_dir=sys.argv[1])" $model
if ($LASTEXITCODE -ne 0) { throw "Telechargement du modele en echec." }
& $py -c "import faster_whisper; print('Faster-Whisper import OK')"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $model "model.bin"))) {
    throw "Installation incomplete."
}
Write-Host "Whisper detecte. Redemarrer Production OS, puis tester un court clip." -ForegroundColor Green
Write-Host "La generation reelle doit encore etre testee."
