$ErrorActionPreference = "Stop"

$SkillRepo = Join-Path $PSScriptRoot "tools\stickman-video-director"
$SkillDir = Join-Path $SkillRepo "skills\directing-stickman-videos"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git est requis. Installe Git for Windows puis relance ce script."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 20+ est requis. Installe Node.js LTS puis relance ce script."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm est introuvable. Réinstalle Node.js LTS avec npm."
}

$NodeVersion = (& node -p "process.versions.node").Trim()
$NodeMajor = [int]($NodeVersion.Split(".")[0])
if ($NodeMajor -lt 20) {
    throw "Node.js 20+ requis. Version détectée : $NodeVersion"
}

New-Item -ItemType Directory -Force -Path (Split-Path $SkillRepo -Parent) | Out-Null
if (Test-Path $SkillRepo) {
    Write-Host "Mise à jour du dépôt Stickman existant..."
    & git -C $SkillRepo pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "Impossible de mettre à jour le dépôt Stickman sans conflit." }
} else {
    Write-Host "Clonage de Stickman Video Director..."
    & git clone --depth 1 https://github.com/kaomei/stickman-video-director.git $SkillRepo
    if ($LASTEXITCODE -ne 0) { throw "Le clonage du dépôt Stickman a échoué." }
}
if (-not (Test-Path (Join-Path $SkillDir "SKILL.md"))) {
    throw "Le Skill directing-stickman-videos est introuvable dans le dépôt cloné."
}

Push-Location $SkillDir
try {
    Write-Host "Installation des dépendances locales (npm ci)..."
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci a échoué dans le runtime Stickman." }

    Write-Host "Diagnostic du runtime Stickman..."
    & node scripts/autopilot.mjs doctor
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Le Skill est installé, mais certains prérequis du runtime sont manquants. Lis le diagnostic ci-dessus."
    }
}
finally {
    Pop-Location
}

# Les deux chemins exposent le même Skill aux agents locaux.
# Les jonctions évitent de dupliquer le runtime et ses dépendances.
foreach ($AgentFolder in @(".agents\skills", ".claude\skills")) {
    $Parent = Join-Path $PSScriptRoot $AgentFolder
    $Target = Join-Path $Parent "directing-stickman-videos"
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null

    if (Test-Path $Target) {
        $Item = Get-Item $Target -Force
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Remove-Item $Target -Force
        } else {
            throw "Le chemin existe déjà et n'est pas une jonction : $Target. Renomme-le ou sauvegarde-le avant de relancer."
        }
    }
    New-Item -ItemType Junction -Path $Target -Target $SkillDir | Out-Null
}

Write-Host ""
Write-Host "Installation du Skill terminée."
Write-Host "Skill source : $SkillDir"
Write-Host "Production OS peut maintenant détecter ce runtime au prochain redémarrage."
Write-Host "Les services de génération d'images/voix peuvent nécessiter une configuration ou des crédits séparés."
