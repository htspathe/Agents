param()
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Join-Path $env:LOCALAPPDATA "ProductionOS\QwenTTS"
$Venv = Join-Path $InstallRoot ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

& py -3.12 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 est requis : winget install --id Python.Python.3.12 -e" }
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if (-not (Test-Path $Py)) {
    & py -3.12 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Impossible de créer le venv Qwen3-TTS." }
}
& $Py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Mise à jour de pip impossible." }
Write-Host "Installation de PyTorch pour processeur, sans CUDA..."
& $Py -m pip install torch --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw "Installation PyTorch CPU impossible." }
& $Py -m pip install qwen-tts soundfile
if ($LASTEXITCODE -ne 0) { throw "Installation Qwen3-TTS impossible." }
& $Py -c "import qwen_tts, soundfile, torch; print('QWEN_TTS_OK', torch.__version__)"
if ($LASTEXITCODE -ne 0) { throw "L'environnement Qwen3-TTS n'est pas fonctionnel." }
Write-Host ""
Write-Host "Qwen3-TTS installé. Le modèle 0.6B sera téléchargé lors de la première génération explicite."
Write-Host "Important : libère au moins 3,5 Go de RAM avant d'utiliser ce moteur sur CPU."
Write-Host "Le moteur sera automatiquement détecté au prochain rafraîchissement de Production OS."
