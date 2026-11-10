# Production OS — MVP local V0.4

**Statut : MVP local avec routage multi-moteur, Stickman et narration Piper TTS intégrée ; le premier test audio/vidéo de bout en bout reste à valider sur le PC Windows cible.**

Production OS centralise quatre workflows pour les 12 rôles : Orchestrator, Research, Content Strategist, Creative, Video Production, Clipping, Publishing, Shopify Commerce, Customer Support, KDP Publishing, Analytics, Quality Control.

## Ce qui marche dans ce MVP

- **Dashboard local** sur `127.0.0.1:8765` : créer une mission, voir son état, consulter le journal et télécharger les livrables.
- **Mes productions** : médiathèque centrale filtrable par zone, format et statut ; aperçu des vidéos, images, documents et données ; téléchargement individuel et export ZIP complet par mission.
- **Orchestrator** : file d'exécution limitée, rôles en séquence, journal d'événements, statuts persistants SQLite.
- **Revue humaine** : actions Approuver / Demander une correction / Rejeter. Une correction crée une nouvelle mission, conserve les livrables précédents et reprend à l'agent choisi. Une approbation ne publie rien.
- **Research** : recherche très simple dans les notes locales `knowledge/` ; aucune recherche Internet réelle.
- **Content Strategist** : génération de texte via la chaîne de moteurs configurée (Codex, Antigravity/Gemini, Ollama), avec gabarit déterministe honnêtement signalé si tous les moteurs sont indisponibles.
- **Creative** : maquettes SVG originales, modifiables, non imprimables en l'état pour KDP.
- **Video Production + Stickman + Piper** : le Content Strategist produit une section de voix off dédiée ; Piper génère un WAV local avec une voix fixe ; le worker Stickman reçoit ce WAV comme narration de référence, conserve les checkpoints, importe l'aperçu, attend l'approbation humaine et reprend le rendu final avec le même fichier audio. Aucun achat, crédit ou publication n'est déclenché.
- **Clipping** : importe une vidéo dont les droits sont confirmés, découpe et exporte une vidéo verticale 540×960 avec FFmpeg, durée max de clip 120 s. Pas de détection virale ni transcription dans cette version.
- **Publishing** : paquet de fichiers et manifeste de validation ; aucun upload ou post automatique.
- **Shopify Commerce** : audit hors ligne sur `samples/clogz_catalog_demo.csv` (fichier fictif ; ne pas interpréter comme stock ou prix live).
- **Customer Support** : brouillon de réponse client non envoyé.
- **KDP Publishing** : sommaire, métadonnées de brouillon et couverture conceptuelle. Pas de manuscrit complet.
- **Analytics** : métriques de production locales, pas de revenus ou statistiques de comptes connectés.
- **Quality Control** : vérification des fichiers requis et liste des vérifications humaines. `publication_ready` reste toujours `false`.

## Démarrage Windows (PowerShell)

1. Extraire l'archive ZIP.
2. Ouvrir PowerShell dans le dossier extrait.
3. Lancer :

```powershell
py -3 app.py --demo --demo-video
```

4. Ouvrir **http://127.0.0.1:8765** dans le navigateur.
5. Pour tester le clipping, choisir **Clipping**, sélectionner le fichier `inbox/source_demo.mp4`, cocher la case des droits (c'est une source synthétique créée localement), sélectionner le début et la fin, et lancer la mission.

Démarrage normal :

```powershell
py -3 app.py
```

Alternatives : `python app.py` ou double-cliquer `start_windows.bat` (sans exemples).

Prérequis : Python **3.11+**. FFmpeg et FFprobe doivent être présents dans `PATH` pour les exports vidéo. Une installation de Python suffit pour les workflows sans rendu. Stickman nécessite en plus Node.js **20+**, Git et npm.

**Pour le clipping**, installer FFmpeg et vérifier :

```powershell
ffmpeg -version
ffprobe -version
```

Aucun Docker, n8n ou autre service n'est obligatoire au lancement de ce MVP.

### Installer Piper TTS (Windows)

Piper est le moteur de narration local de Brain to Billions. Il est isolé dans un environnement Python 3.12 séparé du Python principal de Production OS ; le dashboard peut donc continuer à utiliser Python 3.15.

Depuis PowerShell, dans le dossier `production_os_mvp` :

```powershell
winget install --id Python.Python.3.12 -e
powershell -ExecutionPolicy Bypass -File .\install_piper_windows.ps1
```

Le script crée `%LOCALAPPDATA%\ProductionOS\Piper\.venv`, installe `piper-tts`, télécharge `fr_FR-siwis-medium` et écrit la configuration locale `.production_os_piper.json`. Si tu as déjà suivi l'ancienne installation sous `C:\Tools\Piper`, le diagnostic sait aussi détecter ce chemin. Les modèles vocaux et paramètres locaux ne sont jamais poussés sur GitHub.

Vérifier le moteur :

```powershell
py -3 .\piper_tts.py status
py -3 .\piper_tts.py test
```

Le WAV de test est généré dans `.production_os_runtime\piper_test.wav`. Tu peux aussi enregistrer les paramètres et lancer **Générer un test vocal** depuis le panneau **Voix & narration — Piper TTS** du dashboard.

**Voix de Brain to Billions :** `fr_FR-siwis-medium`. Le modèle vocal est sous CC BY 4.0 : une attribution SIWIS est incluse automatiquement dans `voice_attribution.md` pour chaque mission. Le moteur logiciel Piper est sous GPL-3.0-or-later ; sa licence est distincte de celle du modèle vocal. Consulte la fiche du modèle avant toute redistribution du modèle lui-même : https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/siwis/medium.

## Moteurs IA et basculement automatique

Le panneau **Moteurs IA** du dashboard permet de choisir :
- le moteur principal : Codex, Antigravity/Gemini ou Ollama ;
- le premier et le deuxième moteur de secours ;
- l'activation du basculement automatique ;
- le modèle Ollama déjà téléchargé.

Ordre initial : **Codex → Antigravity/Gemini → Ollama**. Le worker Stickman utilise le même ordre. **Seuil par défaut : 5 % de quota restant**, ajustable entre 1 et 50 % dans le dashboard. Quand le quota du moteur actif est mesurable et atteint ce seuil, Production OS **ne lance pas la prochaine étape** sur ce moteur et passe au suivant. Il conserve les fichiers et checkpoints de la mission et laisse les validations humaines en place. Il ne stoppe pas brutalement une vidéo en cours de rendu, car une interruption pourrait corrompre les fichiers. Le nouveau moteur vérifie les ressources existantes avant de continuer.

Cette règle ne s'applique qu'aux **pourcentages de quotas réels**, jamais au pourcentage de fenêtre de contexte du modèle. Codex est interrogé via la méthode officielle `account/rateLimits/read` de `codex app-server` et le quota retenu est le minimum des fenêtres actives connues. Pour Antigravity, le tableau lit une capture locale et **récente (moins de 5 minutes)** de son `statusLine` officiel. Si ces données manquent, sont périmées ou si l'authentification empêche l'accès, l'interface indique **Quota inconnu** : aucune estimation n'est inventée, et le basculement sur erreur fournisseur reste actif.

En cas de quota épuisé, limitation de débit, erreur d'authentification ou indisponibilité réseau reconnue, le worker tente aussi le moteur suivant. Il ne bascule pas automatiquement sur une erreur de production ordinaire. Les sessions internes ne sont pas transférables d'un fournisseur à l'autre, mais les fichiers restent dans le dossier de mission. Si aucun moteur suivant n'est utilisable, la mission est bloquée sans consommer volontairement les 5 % réservés. Ollama étant local, il n'a pas de quota cloud en pourcentage.

### Installer et connecter les moteurs

**Codex CLI**
- Installe Codex CLI et connecte ton compte avant de démarrer Production OS.
- Le worker utilise `codex exec` dans le sandbox `workspace-write`, limité au dossier de la mission.
- Une authentification API par clé peut entraîner une facturation selon la configuration du compte ; surveille le mode d'authentification choisi.

**Antigravity CLI / Gemini**
- Installer sous Windows PowerShell :
  ```powershell
  irm https://antigravity.google/cli/install.ps1 | iex
  agy --version
  agy
  ```
- Lance `agy` une première fois dans PowerShell et connecte ton compte Google. Ferme ensuite cette session : Production OS utilisera les identifiants locaux mémorisés.
- Vérifie le mode headless :
  ```powershell
  agy -p "Réponds exactement READY" --output-format json --print-timeout 60s --mode=plan --sandbox
  ```
- Antigravity utilise les quotas de ton compte Google. Si le mode Gemini API par clé est configuré, il peut être facturable : l'option **Autoriser Gemini API via clé** reste désactivée par défaut dans Production OS. Ne l'active que si tu acceptes ces coûts.

**Capture de quota Antigravity (configuration locale facultative mais nécessaire pour la bascule à 5 % sur Gemini)**

Dans `production_os_mvp`, exécute une fois :

```powershell
py -3 setup_quota_statusline.py
agy
```

Le script fait une sauvegarde de `~/.gemini/antigravity-cli/settings.json` puis configure une ligne de statut qui copie uniquement les fractions de quota et l'identifiant du modèle dans `.antigravity_quota_status.json` (fichier ignoré par Git). Il ne copie pas tes identifiants, courriels ou discussions. Si un autre `statusLine` est déjà personnalisé, le script **refuse de le remplacer**. Tu peux alors intégrer la capture dans ton script existant. La commande `/usage` d'Antigravity affiche également les quotas, mais ouvre une interface interactive plutôt qu'un flux JSON exploitable directement. La capture doit être actualisée par Antigravity pour que le seuil de 5 % soit utilisé ; une capture périmée est marquée comme inconnue.

**Ollama local**
- Installe Ollama, lance le service, puis télécharge un modèle adapté à ton PC :
  ```powershell
  ollama pull NOM_DU_MODELE
  ollama list
  ```
- Choisis le nom exact du modèle dans le panneau Moteurs IA. Pour les tâches agentiques Stickman, Ollama utilise le harness local Codex avec `--oss --local-provider ollama` ; le CLI Codex doit donc aussi être installé, mais le modèle répond localement.
- La génération de texte simple utilise directement l'API locale Ollama.

### Règles de sécurité et limites
- Les clés et tokens ne sont jamais enregistrés dans le dépôt ni dans `.production_os_engines.json`. Les préférences locales ne contiennent que l'ordre des moteurs et le nom du modèle Ollama.
- Le mode Gemini API par clé est bloqué par défaut si une clé et le fournisseur API sont détectés ; l'utilisateur doit activer explicitement l'autorisation dans le dashboard.
- Les appels cloud peuvent consommer les quotas des comptes Codex ou Google. Aucun achat de crédits, abonnement, retry payant ou publication externe n'est effectué par le worker.
- Le fallback conserve les fichiers et l'état de mission, mais ne transfère pas l'historique de conversation privé entre Codex et Antigravity. Le nouveau moteur reprend à partir des livrables et journaux présents.
- Ollama ne consomme pas de quota cloud, mais nécessite assez de RAM/VRAM et peut être lent selon le matériel. Le modèle n'est pas choisi automatiquement.
- L'état d'authentification Codex/Antigravity est vérifié au premier vrai lancement, pas uniquement par la détection du binaire.

## Installer Stickman Video Director (Windows)

Depuis PowerShell, dans le dossier `production_os_mvp` :

```powershell
powershell -ExecutionPolicy Bypass -File .\install_stickman_windows.ps1
```

Le script clone le dépôt officiel dans `tools/stickman-video-director`, installe les dépendances du runtime avec `npm ci`, lance le diagnostic, puis expose le même Skill à Codex et Claude Code via des jonctions locales. Le dossier du runtime et les jonctions sont ignorés par Git.

Prérequis : Git, Node.js 20+, npm, FFmpeg et FFprobe. Si le diagnostic indique des dépendances manquantes, corrige-les puis relance le script. Après installation, redémarre Production OS pour actualiser l'état affiché sur le dashboard.

### Narration Piper dans le workflow YouTube

- Le Content Strategist doit fournir `## Voix off intégrale (à lire mot pour mot)` dans `content.md`.
- Video Production extrait cette section et génère `narration.wav`, `narration.md`, `narration_metadata.json` et `voice_attribution.md`.
- Si Piper n'est pas prêt, la mission reste créée avec `narration_blocker.md`. Après installation, le bouton de démarrage régénère automatiquement le WAV à partir de `content.md`, actualise `stickman_request.md` et `stickman_job.json`, puis lance Stickman ; aucune nouvelle mission n'est nécessaire. Tant que Piper n'est pas prêt, le worker refuse de démarrer et ne remplace pas la voix par une API cloud.
- Le WAV est copié dans `stickman_project/assets/audio/narration.wav` lorsque le projet Stickman existe. Le brief demande à l'agent d'utiliser exactement ce WAV, de le couper à 20 secondes pour l'aperçu et de réutiliser l'audio complet après approbation.
- La validation de l'aperçu ne régénère pas la voix : le même WAV et les mêmes métadonnées sont exigés pour le rendu final.
- Le panneau **Voix & narration — Piper TTS** affiche l'état du moteur, permet de choisir la voix, de renseigner les chemins Python/modèles si nécessaire et de générer un WAV de test.

### Utiliser Stickman depuis le dashboard

1. Démarre Production OS normalement avec `py -3 app.py`, puis ouvre `http://127.0.0.1:8765`.
2. Crée une mission YouTube. Pour le premier pilote Brain to Billions, choisis `Autopilot`, `B1`, `9:16`, `60 secondes` et le français.
3. Quand la mission passe à `En attente Stickman`, ouvre son détail et clique sur **Démarrer l'agent local**. Le dashboard lance le worker Windows en arrière-plan ; tu n'as pas à ouvrir Codex ni un terminal.
4. Le worker exécute `codex exec` depuis le dossier de la mission avec le sandbox `workspace-write`. Il lit le brief Stickman, le Skill installé et ses références, puis prépare l'aperçu. Le journal et l'état du worker restent visibles dans le détail de mission.
5. Quand `stickman_preview.mp4` arrive, le dashboard affiche **Lire l'aperçu**. Visionne et écoute l'aperçu, puis clique sur **Approuver l'aperçu et autoriser le rendu**.
6. L'approbation déclenche automatiquement la reprise de la même session Codex. Si la reprise ne démarre pas, le panneau affiche l'erreur et propose **Reprendre le rendu complet**.
7. Le worker importe le MP4 final et les sous-titres SRT/VTT. La mission passe à `Rendu final à valider` ; approuve, corrige ou rejette depuis le dashboard.
8. Les fichiers, journaux et rapports restent disponibles dans **Mes productions**. Une approbation finale ne publie rien sur YouTube.

**Prérequis et coûts :**
- Au moins un moteur agentique doit être prêt : Codex CLI connecté, Antigravity CLI authentifié, ou Ollama local avec le harness Codex installé. Le dashboard détecte les binaires ; l'authentification cloud est vérifiée au premier lancement.
- Le worker consomme les quotas du fournisseur sélectionné (Codex ou Antigravity). Pour un rendu agentique local, démarre Ollama, sélectionne un modèle téléchargé dans le panneau Moteurs IA et garde le CLI Codex installé comme harness local.
- La génération d'images et de voix dépend des fournisseurs que le Skill peut réellement utiliser. Le worker n'achète aucun crédit, n'ouvre aucun abonnement et n'effectue aucun appel payant. S'il ne trouve pas de fournisseur local/gratuit déjà configuré, il doit produire un rapport de blocage au lieu de fabriquer une vidéo.
- Codex et Antigravity nécessitent Internet pour les modèles cloud. Ollama est local ; le modèle doit être compatible avec les outils de l'agent et la mémoire disponible.
- Le worker web prend en charge Autopilot B1 à B4. B5 et Director/A6 ont des contrats et rendus spécifiques ; ils restent sur leur route dédiée tant que leur automatisation n'est pas intégrée.
- Codex utilise le sandbox `workspace-write` et Antigravity son mode `--sandbox`, sans contournement des permissions. Le worker ne publie pas, n'achète rien et ne modifie aucun compte externe.


## Activer Ollama (facultatif)

1. Installer Ollama, télécharger un modèle compatible avec ton nouveau PC et vérifier `ollama list`.
2. Définir le modèle local installé (remplacer `NOM_DU_MODELE` par un nom existant dans `ollama list`) :

```powershell
$env:OLLAMA_MODEL = "NOM_DU_MODELE"
py -3 app.py
```

3. Vérifier que le service est accessible à `http://127.0.0.1:11434`.

Le modèle n'est **pas choisi par défaut**, car nous n'avons pas encore les caractéristiques du PC actuel. Si Ollama est absent ou indisponible, le projet continue en mode gabarit et le signale dans `run_notes.json`.

## Exécuter les tests

```powershell
py -3 -m unittest discover -s tests -v
```

Les tests sont isolés dans un dossier temporaire et couvrent : 12 rôles, campagnes CLOGZ, KDP, passerelle YouTube/Stickman, indexation des sorties imbriquées, approbation de l'aperçu, autorisation du rendu final, revue finale, révisions ciblées, absence de publication, refus sans déclaration de droits, protection des chemins et export vertical FFmpeg si installé. La CI GitHub exécute les tests automatisés sur Python 3.11 et 3.12, avec vérification de syntaxe JavaScript du dashboard.

## Structure

```text
production_os_mvp/
  app.py                # Serveur HTTP local + dashboard + API
  engine.py             # SQLite, orchestration, états et journal
  engine_manager.py     # Sélection des moteurs, statuts, quotas et basculement
  stickman_bridge.py    # Synchronise aperçu, autorisation de rendu et MP4 final
  stickman_worker.py    # Lance/reprend Stickman via Codex, Antigravity ou Ollama
  piper_tts.py          # Détection Piper, extraction du script, génération WAV et attribution
  install_piper_windows.ps1 # Environnement Python 3.12 isolé + voix française
  agents.py             # Agents, tâches et adaptateurs locaux
  index.html            # Interface locale
  tests/                # Tests automatisés
  install_stickman_windows.ps1 # Installation locale du Skill Stickman
  knowledge/            # Base de connaissances locale modifiable
  samples/              # CSV fictifs pour tests
  inbox/                # Sources vidéo importées
  outputs/              # Livrables par mission
  production_os.sqlite3 # Généré au premier lancement
```

## Modèle de sécurité

- Bind HTTP sur `127.0.0.1` exclusivement, pas d'accès réseau distant.
- Jeton de session éphémère obligatoire sur toutes les routes API, y compris les téléchargements.
- Aucun secret Shopify, YouTube, Amazon ou WhatsApp fourni ou stocké.
- Aucune publication, paiement, modification de thème ni envoi de message déclenché.
- Téléversement vidéo limité à 300 Mo ; extensions autorisées ; clips limités à 120 secondes.
- FFmpeg exécuté avec une liste d'arguments (sans shell), sur des fichiers placés dans `inbox/`.
- Ne pas exposer directement l'application à Internet : le prototype ne possède ni authentification utilisateur complète, ni chiffrement HTTPS, ni protection multi-tenant, ni antivirus pour les fichiers importés.

## Limites connues

Le moteur Research ne navigue pas sur Internet ; le contenu produit hors Ollama est un gabarit. Les maquettes SVG n'utilisent pas ComfyUI. Le clip est un découpage réel, mais pas une sélection sémantique avec Whisper/OpenShorts. Shopify, YouTube, Amazon KDP, WhatsApp et n8n ne sont **pas connectés** dans cette version. La couverture KDP est une maquette, pas un PDF prêt à vendre. Ce MVP démontre un pipeline sûr et testable ; il ne prétend pas automatiser de bout en bout toutes les entreprises.

## Roadmap de branchement

1. **V0.2 — Moteur de tâches** : validation humaine, décisions persistantes, historique et révisions ciblées intégrés.
2. **V0.3 — Multi-moteur et vidéo** : routeur Codex / Antigravity / Ollama et worker Stickman.
3. **V0.4 — Narration locale** : Piper TTS, voix française fixe, WAV de référence, attribution et contrôle audio intégrés au workflow Stickman.
4. **V0.5 — Visuels** : connecteur ComfyUI local ; gestion et réutilisation des modèles et du style de marque.
5. **V0.6 — Shopify** : lecture seule GraphQL avec droits minimaux et coffre local pour tokens ; comparaison données réelles/CSV.
6. **V0.7 — Publishing** : connecteurs API officiels, brouillons privés et demandes d'approbation spécifiques par publication.
7. **V0.8 — KDP** : mise en page export conforme, outils de contrôle des droits et des déclarations IA, soumission manuelle.
8. **V1 — Production OS** : supervision unifiée, indicateurs réels, n8n auto-hébergé si les workflows le justifient.

**Objectif budget : 0 € de dépense API obligatoire.** Les moteurs cloud peuvent consommer les quotas ou abonnements déjà associés aux comptes connectés ; une clé Gemini API potentiellement facturable est bloquée par défaut. Les coûts de matériel, électricité, internet, médias sous licence et diffusion ne sont pas inclus.
