# Brain to Billions — Stickman local (MVP de raccordement)

Cette intégration ajoute le workflow `brain_to_billions` au moteur **Agents/production_os_mvp**, sans changer le dépôt d'origine `kaomei/stickman-video-director`.

## Périmètre réel

- Entrée : idée (brief) et, de préférence, script de narration français déjà relu.
- Si aucun script n'est fourni : Ollama local peut proposer un brouillon, sinon un **texte générique de démonstration** est produit. Ce brouillon n'est pas un reportage factuellement vérifié.
- **Voice Lock Brain to Billions** : toutes les vidéos utilisent la voix SAPI exactement nommée dans `brain_to_billions_voice.json`, avec langue, débit et volume fixés. Aucune substitution silencieuse. La voix actuellement configurée, « Microsoft Paul » (fr-FR), est **provisoire** : elle avait été utilisée dans le premier MVP et nécessite encore ton approbation avant de devenir la signature définitive.
- Le verrou refuse toute variable `STICKMAN_VOICE_WAV` qui permettrait d'injecter une autre voix. Une future TTS locale multilingue devra posséder un identifiant de voix propre, une référence d'échantillon approuvée et une vérification de cohérence. Le MVP actuel est français.
- Réalisation : storyboard animé de type **B1 minimaliste**, moteur vidéo Stickman Autopilot, sous-titres temporisés et export H.264/AAC.
- Exports de revue : `brain_to_billions_9x16.mp4`, `brain_to_billions_16x9.mp4`, WAV, narration TXT, SRT/VTT si fournis par le runtime, `stickman_report.json` et autres artefacts de mission.
- **Aucun upload, aucune connexion et aucune publication YouTube.**
- Les sous-titres sont placés par estimation proportionnelle ; ils doivent être contrôlés et recalés avant publication.
- Le style premium noir/or, l'alignement Whisper, ComfyUI et les gabarits Remotion ne sont **pas encore raccordés**. B1 est le premier test technique ; il ne remplace pas l'identité visuelle officielle.

## Prérequis sur la machine Windows

1. Python 3.11+ ; Node 20+ ; FFmpeg et FFprobe dans PATH.
2. Skill Stickman installé, dépendances lancées avec `npm ci` dans `skills/directing-stickman-videos`.
3. `node scripts/autopilot.mjs doctor` doit passer.
4. La voix française Windows SAPI **Microsoft Paul**, nommée exactement ainsi, installée localement (ou sélectionner explicitement une autre voix dans le fichier de profil avant de lancer la mission). Ne jamais remplacer automatiquement la voix.
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

### Verrouillage du narrateur

Le fichier `brain_to_billions_voice.json` est la seule source de vérité pour la voix de cette chaîne :

```json
{
  "narrator_key": "brain-to-billions-main",
  "provider": "windows_sapi",
  "voice_name": "Microsoft Paul",
  "language": "fr-FR",
  "speech_rate": 0,
  "volume": 100,
  "approval_status": "provisional"
}
```

Cette configuration correspond **uniquement à la voix temporaire déjà utilisée dans le MVP**, et non à une sélection définitive du créateur. Après écoute d'un échantillon et validation, conserver le même nom, langue, débit et fournisseur pour tous les épisodes, puis modifier `approval_status` en `approved`. Le pipeline enregistre l'identifiant et l'empreinte du profil dans `stickman_report.json`.

**Si la voix fixe manque : la génération échoue explicitement.** Elle ne choisit plus la première voix disponible sur l'ordinateur. `STICKMAN_VOICE_WAV` est volontairement bloqué tant qu'un système d'import avec preuve d'identité vocale n'est pas implémenté.

Pour les productions futures en anglais avec **exactement la même identité vocale**, un modèle TTS multilingue utilisant une voix approuvée sera nécessaire. La voix SAPI française ne suffit pas à garantir le même timbre en anglais.

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
