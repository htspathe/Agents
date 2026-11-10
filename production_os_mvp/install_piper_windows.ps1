param(
    [string]$Voice = "fr_FR-siwis-medium"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Join-Path $env:LOCALAPPDATA "ProductionOS\Piper"
$Venv = Join-Path $InstallRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$VoiceDir = Join-Path $InstallRoot "voices"
$SettingsFile = Join-Path $ProjectRoot ".production_os_piper.json"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher introuvable. Installe Python 3.12 puis relance ce script."
}

& py -3.12 --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "Python 3.12 est requis pour isoler Piper du Python principal de Production OS."
    Write-Host "Installe-le avec : winget install --id Python.Python.3.12 -e"
    throw "Python 3.12 n'est pas disponible via le lanceur py."
}

New-Item -ItemType Directory -Force -Path $InstallRoot, $VoiceDir | Out-Null
if (-not (Test-Path $Python)) {
    & py -3.12 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Impossible de créer l'environnement virtuel Piper." }
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Mise à jour de pip impossible." }

& $Python -m pip install piper-tts
if ($LASTEXITCODE -ne 0) { throw "Installation de piper-tts impossible." }

& $Python -m piper.download_voices $Voice --data-dir $VoiceDir
if ($LASTEXITCODE -ne 0) { throw "Téléchargement de la voix $Voice impossible." }

$Model = Join-Path $VoiceDir "$Voice.onnx"
$ModelConfig = Join-Path $VoiceDir "$Voice.onnx.json"
if (-not (Test-Path $Model) -or -not (Test-Path $ModelConfig)) {
    throw "Le modèle ONNX et son fichier JSON doivent tous deux être présents."
}

$Settings = @{
    enabled = $true
    voice = $Voice
    python_path = $Python
    voice_dir = $VoiceDir
    sentence_silence = 0.18
}
$Settings | ConvertTo-Json -Depth 4 | Set-Content -Path $SettingsFile -Encoding utf8

Write-Host ""
Write-Host "Piper installé et configuré pour Production OS."
Write-Host "Voix : $Voice"
Write-Host "Python : $Python"
Write-Host "Modèle : $Model"
Write-Host ""
& $Python (Join-Path $ProjectRoot "piper_tts.py") status
if ($LASTEXITCODE -ne 0) { throw "Le diagnostic Piper n'a pas validé l'installation." }
Write-Host ""
Write-Host "Redémarre Production OS pour actualiser le dashboard."
