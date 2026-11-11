param([switch]$TestOpenCode, [string]$Model = "opencode/mimo-v2.6-flash-free")
$ErrorActionPreference = "Stop"
Write-Host "=== OPENCODE + COMFYUI | SANS OLLAMA ===" -ForegroundColor Cyan
$oc = Get-Command opencode -ErrorAction SilentlyContinue
if (-not $oc) {
  Write-Warning "OpenCode absent. Installer : npm install -g @opencode/cli"
} else {
  Write-Host "[OpenCode] $($oc.Source)" -ForegroundColor Green
  & opencode --version
  try {
    $free = @(& opencode models | Where-Object { $_ -match '^opencode/[a-zA-Z0-9._-]+-free$' })
    Write-Host "Modeles gratuits references : $($free.Count)"
    $free | ForEach-Object { Write-Host "  $_" }
    if ($TestOpenCode) {
      if ($Model -notmatch '^opencode/[a-zA-Z0-9._-]+-free$') {
        throw "Le test accepte uniquement opencode/*-free."
      }
      if ($Model -notin $free) {
        throw "$Model n'apparait pas dans le catalogue OpenCode."
      }
      $env:OPENCODE_PERMISSION = '{"edit":"deny","bash":"deny","webfetch":"deny","websearch":"deny"}'
      $env:OPENCODE_CONFIG_CONTENT = '{"permission":{"edit":"deny","bash":"deny","webfetch":"deny","websearch":"deny"}}'
      $env:OPENCODE_DISABLE_AUTOUPDATE = "true"
      Write-Host "Test OpenCode en lecture seule : $Model"
      & opencode run --agent plan --model $Model "Reply only: PRODUCTION OS READY"
      if ($LASTEXITCODE -ne 0) { Write-Warning "Echec : authentification ou limite du modele a verifier." }
    }
  } catch { Write-Warning "[OpenCode] $($_.Exception.Message)" }
}
Write-Host "[ComfyUI] API locale"
$base = "http://127.0.0.1:8188"
try {
  $stats = Invoke-RestMethod "$base/system_stats" -TimeoutSec 5
  Write-Host "API accessible : $base" -ForegroundColor Green
  if ($stats.devices) {
    $stats.devices | ForEach-Object {
      Write-Host ("GPU : " + $_.name + " | VRAM libre : " + [math]::Round(($_.vram_free / 1MB), 0) + " Mo")
    }
  }
  foreach ($kind in @("checkpoints", "diffusion_models", "unet")) {
    try {
      $items = @(Invoke-RestMethod "$base/models/$kind" -TimeoutSec 5)
      Write-Host ("$kind : " + $items.Count + " fichiers")
      $items | Select-Object -First 5 | ForEach-Object { Write-Host "  $_" }
    } catch { Write-Host "$kind : lecture indisponible" }
  }
} catch { Write-Warning "ComfyUI est absent ou arrete sur $base." }
Write-Host "Fin : pas de rendu GPU, pas de changement de configuration." -ForegroundColor Cyan
