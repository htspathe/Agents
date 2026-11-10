# Brain to Billions — Stickman local (MVP de raccordement)

Cette intégration ajoute le workflow `brain_to_billions` au moteur **Agents/production_os_mvp**, sans changer le dépôt d'origine `kaomei/stickman-video-director`.

## Périmètre réel

- Entrée : idée (brief) et, de préférence, script de narration français déjà relu.
- Si aucun script n'est fourni : Ollama local peut proposer un brouillon, sinon un **texte générique de démonstration** est produit. Ce brouillon n'est pas un reportage factuellement vérifié.
- Voix : Windows SAPI en français, ou fichier WAV local explicitement fourni via `STICKMAN_VOICE_WAV`.
- Réalisation : storyboard animé de type **B1 minimaliste**, moteur vidéo Stickman Autopilot, sous-titres temporisés et export H.264/AAC.
- Exports de revue : `brain_to_billions_9x16.mp4`, `brain_to_billions_16x9.mp4`, WAV, narration TXT, SRT/VTT si fournis par le runtime, `stickman_report.json` et autres artefacts de mission.
- **Aucun upload, aucune connexion et aucune publication YouTube.**
- Les sous-titres sont placés par estimation proportionnelle ; ils doivent être contrôlés et recalés avant publication.
- Le style premium noir/or, l'alignement Whisper, ComfyUI et les gabarits Remotion ne sont **pas encore raccordés**. B1 est le premier test technique ; il ne remplace pas l'identité visuelle officielle.

## Prérequis sur la machine Windows

1. Python 3.11+ ; Node 20+ ; FFmpeg et FFprobe dans PATH.
2. Skill Stickman installé, dépendances lancées avec `npm ci` dans `skills/directing-stickman-videos`.
3. `node scripts/autopilot.mjs doctor` doit passer.
4. Une voix française Windows SAPI disponible, ou un fichier WAV déjà enregistré, sous licence appropriée.
5. Aucun accès payant et aucune clé API requis pour le MVP.

Le dossier du Skill est trouvé via `STICKMAN_SKILL_DIR`, puis `%USERPROFILE%\.codex\skills\directing-stickman-videos` ou les emplacements Production OS déjà documentés.

### Démarrage

```powershell
cd <chemin-du-repo-Agents>\production_os_mvp
$env:STICKMAN_SKILL_DIR = 'C:\Users\CHATA\Production-OS\Outils\Stickman-Video-Director\skills\directing-stickman-videos'
$env:BTB_BOTH_FORMATS = '1'
py -3 app.py
```

Dans **Lancer une mission**, choisir **Brain to Billions · Vidéo Stickman**, renseigner le sujet, et coller de préférence un script français original de 8 à 59 secondes. Lancer puis ouvrir **Mes productions** pour récupérer les fichiers. Le moteur n'appelle pas `autopilot render` : il génère un **aperçu complet encodé** et exige une validation humaine avant une publication manuelle.

Pour utiliser une voix déjà enregistrée (et éviter Windows SAPI) :

```powershell
$env:STICKMAN_VOICE_WAV = 'C:\Videos\voix_originale.wav'
py -3 app.py
```

Le WAV doit correspondre **exactement** au script. Son existence ne prouve pas cette correspondance : écouter et vérifier les sous-titres.

### Tests hors rendu

```powershell
py -3 -m unittest discover -s tests -v
```

Ce test ne constitue pas une validation du rendu sur la machine. Effectuer un test de mission réel, vérifier le rapport `stickman_report.json`, lire les fichiers audio/MP4 et contrôler la longueur et l'alignement des sous-titres. Le fichier final ne doit pas être considéré comme prêt à la publication tant que les contrôles éditoriaux et les droits ne sont pas validés.

## Suite prévue

1. Bibliothèque de personnages Brain to Billions avec design récurrent.
2. Détection/alignement de paroles par Whisper local et découpage par sens.
3. Génération d'illustrations ComfyUI si modèle d'image compatible présent.
4. Templates Remotion pour titres, courbes, flux financiers et transitions.
5. Contrôle visuel automatisé + correction d'une scène isolée, sans régénérer le film entier.

Les services externes restent optionnels, sans dépense implicite. La publication YouTube demeure entièrement manuelle.
