# Production OS — MVP local V0.1

**Statut : prototype fonctionnel testé localement, pas plateforme de production publique.**

Production OS centralise quatre workflows pour les 12 rôles : Orchestrator, Research, Content Strategist, Creative, Video Production, Clipping, Publishing, Shopify Commerce, Customer Support, KDP Publishing, Analytics, Quality Control.

## Ce qui marche dans ce MVP

- **Dashboard local** sur `127.0.0.1:8765` : créer une mission, voir son état, consulter le journal et télécharger les livrables.
- **Mes productions** : médiathèque centrale filtrable par zone, format et statut ; aperçu des vidéos, images, documents et données ; téléchargement individuel et export ZIP complet par mission.
- **Orchestrator** : file d'exécution limitée, rôles en séquence, journal d'événements, statuts persistants SQLite.
- **Revue humaine** : actions Approuver / Demander une correction / Rejeter. Une correction crée une nouvelle mission, conserve les livrables précédents et reprend à l'agent choisi. Une approbation ne publie rien.
- **Research** : recherche très simple dans les notes locales `knowledge/` ; aucune recherche Internet réelle.
- **Content Strategist** : brouillons générés avec Ollama si configuré ; sans Ollama, gabarits déterministes et clairement signalés.
- **Creative** : maquettes SVG originales, modifiables, non imprimables en l'état pour KDP.
- **Video Production + Stickman** : le workflow YouTube crée une passerelle Stickman (`stickman_request.md`, configuration, statut runtime et projet initial si le Skill est installé). Le rendu complet reste exécuté par un agent local compatible Skill (Codex / Claude Code), pas par le serveur Python seul.
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

## Installer Stickman Video Director (Windows)

Depuis PowerShell, dans le dossier `production_os_mvp` :

```powershell
powershell -ExecutionPolicy Bypass -File .\install_stickman_windows.ps1
```

Le script clone le dépôt officiel dans `tools/stickman-video-director`, installe les dépendances du runtime avec `npm ci`, lance le diagnostic, puis expose le même Skill à Codex et Claude Code via des jonctions locales. Le dossier du runtime et les jonctions sont ignorés par Git.

Prérequis : Git, Node.js 20+, npm, FFmpeg et FFprobe. Si le diagnostic indique des dépendances manquantes, corrige-les puis relance le script. Après installation, redémarre Production OS pour actualiser l'état affiché sur le dashboard.

### Utiliser Stickman dans Production OS

1. Choisis `YouTube` dans Zone de production.
2. Sélectionne `Autopilot` et le style `B1` pour le premier pilote vertical Brain to Billions (9:16, 60 secondes, français).
3. Lance la mission. Production OS crée `stickman_request.md`, `stickman_job.json`, `stickman_runtime_status.json` et, si le runtime local est prêt, un projet Stickman initial.
4. Ouvre le dossier de mission avec Codex ou Claude Code et demande-lui d'exécuter `stickman_request.md` avec le Skill installé. Le Skill Autopilot a besoin d'un agent capable de lire/écrire des fichiers et d'exécuter des commandes.
5. Exige un aperçu d'environ 20 secondes avec son, puis valide cet aperçu avant le rendu complet. La validation Production OS reste une étape séparée.
6. Récupère le MP4, les sous-titres et le projet dans `Mes productions`.

**Limites à connaître :**
- La passerelle et le projet initial sont intégrés ; le serveur Python ne pilote pas lui-même un agent Codex/Claude et ne génère pas encore le film final.
- Les services de génération d'images et de voix peuvent demander une clé ou des crédits. Aucun appel payant, abonnement ou achat de crédits n'est déclenché sans autorisation.
- B1 est le point de départ recommandé pour le format vertical. B5 possède une route spécifique et son portrait demande une nouvelle validation ; A6 utilise Director et un fournisseur vidéo distinct.
- La direction Brain to Billions est originale : personnage récurrent, explications financières et accents ambrés. Les références B1/B5/A6 ne doivent pas conduire à copier un personnage ou à mélanger leurs contrats techniques.

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

Les tests sont isolés dans un dossier temporaire et couvrent : 12 rôles, campagnes CLOGZ, KDP, passerelle YouTube/Stickman, décisions humaines, révisions ciblées, absence de publication, refus sans déclaration de droits, protection des chemins et export vertical FFmpeg si installé.

## Structure

```text
production_os_mvp/
  app.py                # Serveur HTTP local + dashboard + API
  engine.py             # SQLite, orchestration, états et journal
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
2. **V0.3 — Vidéo** : passerelle Stickman intégrée ; prochaine étape : exécution assistée par agent local et test d'un MP4 réel, puis adaptateurs OpenShorts, Whisper local et Remotion.
3. **V0.4 — Visuels** : connecteur ComfyUI local ; gestion et réutilisation des modèles et du style de marque.
4. **V0.5 — Shopify** : lecture seule GraphQL avec droits minimaux et coffre local pour tokens ; comparaison données réelles/CSV.
5. **V0.6 — Publishing** : connecteurs API officiels, brouillons privés et demandes d'approbation spécifiques par publication.
6. **V0.7 — KDP** : mise en page export conforme, outils de contrôle des droits et des déclarations IA, soumission manuelle.
7. **V1 — Production OS** : supervision unifiée, indicateurs réels, n8n auto-hébergé si les workflows le justifient.

**Budget logiciel initial : 0 € hors consommation électrique/ordinateur/internet.** Les API externes, comptes, éventuels médias sous licence, puissance GPU et coûts de diffusion ne sont pas inclus.
