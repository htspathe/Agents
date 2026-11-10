# Brain to Billions — voix officielle « Confident Male » (Qwen3-TTS)

**Sélection validée par le créateur.** L'identité vocale de toutes les vidéos Brain to Billions est **Confident Male — Sample 04** (Hangry Labs, référence synthétique Qwen3-TTS). Elle remplace la voix Windows SAPI Microsoft Paul dans le workflow `brain_to_billions`.

## Ce qui est intégré au code

- `brain_to_billions_voice.json` : configuration officielle, approuvée, versionnée.
- `qwen_btb_voice.py` : charge cette configuration, télécharge **explicitement** la référence FLAC à l'installation, vérifie son SHA-256 et bloque toute substitution de locuteur.
- `run_qwen_voice.py` : exécute Qwen3-TTS localement dans un environnement Python isolé.
- `stickman_btb.py` : utilise cette voix sur les narrations, avant `validate` / `preview` Stickman ; exporte des MP4 de revue en 9:16 et 16:9.
- Interface Production OS : choix **Français** ou **English** ; les deux langues utilisent **la même référence audio EN**. L'identité visée est cohérente, sans garantie d'une prononciation ou d'un timbre parfaitement identique dans les deux langues.
- Le profil, son identifiant, sa langue et l'empreinte de la référence sont enregistrés dans `stickman_report.json`.
- **Aucun appel à une API TTS payante, aucun upload YouTube.**

La référence de démonstration « Confident Male » est **synthétique** et non exclusive : d'autres personnes peuvent réutiliser le même profil. Les sources et le texte de référence sont publiés par Hangry Labs :
- [Écouter le Sample 04](https://hangry-labs.github.io/faster-qwen3-tts-studio/examples/)
- [Manifest des références, empreintes et modèle](https://hangry-labs.github.io/faster-qwen3-tts-studio/examples/assets/manifest.json)
- [Conditions d'utilisation des modèles](https://hangry-labs.github.io/faster-qwen3-tts-studio/docs/LICENSING.html)

## Prérequis (Windows)

1. Python **3.12 recommandé**, Node.js 20+, FFmpeg et FFprobe.
2. Dépôt `kaomei/stickman-video-director` déjà installé, avec `npm ci` exécuté dans `skills/directing-stickman-videos` et un `doctor` valide.
3. Espace disque pour les dépendances et les poids **Qwen/Qwen3-TTS-12Hz-1.7B-Base**, téléchargés lors du premier rendu. L'inférence est locale ensuite si les poids sont intégralement en cache.
4. Puissance CPU/GPU et RAM/VRAM suffisantes **à vérifier sur la machine actuelle**. Un GPU NVIDIA compatible est préférable, mais le programme peut tenter un mode CPU beaucoup plus lent. Aucune bascule silencieuse vers une autre voix.

Le dépôt GitHub contient **le connecteur, pas les poids du modèle ni l'audio de référence**. Un commit n'installe rien automatiquement sur le PC.

### Installation du moteur vocal, une seule fois

Ouvrir PowerShell dans le répertoire `production_os_mvp` de la branche d'intégration :

```powershell
py -3.12 -m venv .venv-qwen
.\.venv-qwen\Scripts\python.exe -m pip install --upgrade pip
.\.venv-qwen\Scripts\python.exe -m pip install "qwen-tts==0.1.1"
py -3 qwen_btb_voice.py
```

La dernière commande télécharge uniquement la référence FLAC officielle dans `voice_assets/confident_male.flac` et refuse le fichier si son empreinte ne correspond pas au profil. Cette référence reste locale et n'est pas committée. Aucun modèle TTS n'est téléchargé par cette commande : il sera récupéré au **premier rendu vocal** via Hugging Face par `qwen-tts`.

Si Python 3.12 n'est pas installé, installer une version prise en charge, puis recréer le venv ; ne pas mélanger la bibliothèque TTS avec les autres projets. Si les poids du modèle sont déjà téléchargés dans un dossier local, on pourra ajouter un chemin de modèle local sans changer de narrateur.

### Lancement de Production OS

```powershell
$env:STICKMAN_SKILL_DIR = 'C:\Users\CHATA\Production-OS\Outils\Stickman-Video-Director\skills\directing-stickman-videos'
$env:BTB_BOTH_FORMATS = '1'
py -3 app.py
```

Dans le tableau de bord, sélectionner **Brain to Billions · Vidéo Stickman**, choisir `Français` ou `English`, renseigner le sujet et idéalement un **script original relu**, puis lancer la mission. Après vérification, récupérer les MP4 dans **Mes productions**. La limite du pilote reste **8–59 secondes**. Le workflow utilisera automatiquement `.venv-qwen` pour la voix sans recourir à SAPI.

Si un autre emplacement pour le venv est requis, définir `BTB_QWEN_PYTHON` vers le chemin **absolu de l'exécutable Python**. La variable `STICKMAN_VOICE_WAV` est refusée par Voice Lock.

### Vérifications

```powershell
py -3 -m unittest discover -s tests -v
```

Lancer ensuite une **mission réelle sur la machine** : aucune vérification dans GitHub ne prouve que les poids Qwen sont installés, que l'inférence tient en mémoire, ou que le moteur Stickman réalise les plans. Écouter les deux langues et vérifier la durée, les MP4, la synchronisation des sous-titres et la cohérence vocale. Les sous-titres restent **approximativement temporisés** et nécessitent un contrôle.

## Limites restantes

- Le style graphique B1 du MVP n'est pas encore la direction premium noir/or Brain to Billions.
- ComfyUI, Remotion, Whisper et la correction ciblée des scènes ne sont pas encore raccordés.
- La recherche dans Production OS est locale et ne garantit pas la vérification externe des faits financiers.
- Les modèles Qwen sont sous Apache-2.0 ; la galerie et les fichiers audio ont leur propre provenance. Vérifier les droits applicables aux médias et au contenu avant monétisation.
- **YouTube : publication toujours manuelle, aucune connexion de compte.**
