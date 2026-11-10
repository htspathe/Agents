param(
    [string]$Voice = "fr_FR-siwis-medium"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machinePath;$userPath"
}

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "FFmpeg/FFprobe sont absents et winget est introuvable. Installe FFmpeg puis relance ce script."
    }
    Write-Host "Installation de FFmpeg/FFprobe via winget..."
    & winget install --id Gyan.FFmpeg.Shared -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Installation FFmpeg échouée. Vérifie winget puis relance le script."
    }
    Refresh-Path
    $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    if (-not $ffmpeg -or -not $ffprobe) {
        throw "FFmpeg a été installé, mais le PATH n'est pas actualisé. Ferme et rouvre PowerShell, puis relance install_media_stack_windows.ps1."
    }
}

Write-Host "FFmpeg et FFprobe détectés."
& ffmpeg -version | Select-Object -First 1
& ffprobe -version | Select-Object -First 1

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw "Python Launcher introuvable. Installe Python 3.12 puis relance ce script."
}
& py -3.12 --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "Python 3.12 est requis pour isoler Piper du Python principal de Production OS."
    Write-Host "Installe-le avec : winget install --id Python.Python.3.12 -e"
    throw "Python 3.12 n'est pas disponible via le lanceur py."
}

Write-Host "Installation et configuration de Piper TTS..."
& (Join-Path $ProjectRoot "install_piper_windows.ps1") -Voice $Voice
if ($LASTEXITCODE -ne 0) {
    throw "L'installation Piper a échoué."
}

Write-Host ""
Write-Host "Prérequis audio prêts : FFmpeg, FFprobe et Piper."
Write-Host "Redémarre Production OS pour actualiser le diagnostic."
