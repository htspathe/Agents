# Production OS — Plan technique de réalisation

**Version datée : 10 novembre 2026**  
**Périmètre :** 12 agents fondateurs, 4 zones CLOGZ / KDP / YouTube / Clipping.  
**Budget logiciel local du MVP :** 0 €. Les fournisseurs externes d'images/voix peuvent être payants ; aucun appel payant, abonnement ou achat de crédits n'est déclenché sans autorisation explicite.

## 1. Décisions d'architecture

### Un orchestrateur, douze rôles, quatre workflows

Chaque **agent** est un rôle logique : une description, des responsabilités, un ensemble d'outils autorisés, un format d'entrée et un format de sortie. Il ne correspond pas nécessairement à un processus ou à un LLM distinct.

```text
                     Interface locale / Console
                               │
                            REST API
                               │
          ┌────────────────────┴────────────────────┐
          │          Orchestrator Python             │
          │  file | exécution | historique | statuts  │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────┴───────────────────────┐
          │ Registre des agents + workflows séquentiels │
          └─────────┬───────────┬──────────┬───────────┘
                    │           │          │
               Ollama*       FFmpeg      SQLite
                    │           │          │
              Prompts*       Vidéos      Historique
                               │
             Dossiers locaux (inbox, knowledge, outputs)
```

`*` Ollama est facultatif. Sans modèle, les scénarios de démonstration utilisent des gabarits. **Ne pas présenter les gabarits comme des résultats d'analyse IA.**

### Deux niveaux d'orchestration

- **V0.1 actuelle :** Python coordonne les tâches de bout en bout avec une file limitée et des événements persistants.
- **V1 envisagée :** n8n déclenche des missions (webhooks, horaires, connecteurs), tandis que Python demeure le moteur d'exécution de médias et des agents. Éviter la duplication des états dans n8n et SQLite.

### Cycle d'une mission

`queued → running → awaiting_external → awaiting_preview_approval → render_authorized → awaiting_approval → approved` pour YouTube/Stickman ; les autres workflows passent de `running` à `awaiting_approval`. Les deux branches peuvent aussi passer à `changes_requested` ou `rejected` ; les erreurs passent à `failed`. Le pont `stickman_bridge.py` synchronise le preview et le rendu final avec SQLite. Une correction crée une mission de révision distincte, conserve les livrables imbriqués et reprend au rôle choisi.

`render_authorized` autorise uniquement le rendu complet après validation de l'aperçu. `approved` signifie que le rendu final est accepté par revue humaine, **pas qu'il est publié**. `changes_requested` empêche l'approbation de l'ancienne version. Chaque décision (action, note, agent cible, date) est persistée dans SQLite et visible dans le journal. Aucun upload ou post externe n'est déclenché.

## 2. Contrat de chaque agent

| ID | Entrée | Opération V0.1 | Sortie et validation |
|---|---|---|---|
| Orchestrator | Brief + workflow | Création des jobs, orchestration, événements | Job SQLite + historique |
| Research | Brief + notes internes | Récupération très simple de notes locales | `research.md`, sources internes explicites |
| Content Strategist | Brief + contexte | Ollama si disponible, sinon gabarit | `content.md`, vérification humaine |
| Creative | Titre + marque | Composition SVG originale | `visual.svg`, maquette uniquement |
| Video Production | Brief YouTube + contenu + configuration Stickman | Handoff Stickman, diagnostic local, initialisation du projet si runtime prêt | `stickman_request.md`, `stickman_job.json`, `stickman_runtime_status.json`, storyboard ; pas de MP4 final automatique |
| Clipping | Source autorisée + segment | Recadrage vertical 540×960 | `short_vertical.mp4`, contrôle ffprobe |
| Publishing | Livrables existants | Création du manifeste de livraison | `publishing_manifest.json`, non publié |
| Shopify Commerce | CSV fictif | Vérifications de valeurs et seuils | `shopify_offline_audit.json` |
| Customer Support | Question / campagne | Proposition de réponse | `customer_reply_draft.md`, non envoyé |
| KDP Publishing | Idée éditoriale | Métadonnées, sommmaire, maquette | `kdp_metadata_draft.json`, non soumis |
| Analytics | Livrables et statuts | Compte les artefacts et exports | `analytics.json` local |
| Quality Control | Dossier de job | Vérification des pièces et réserves | `qa_report.json`, jamais de prêt-à-publier automatique |

## 3. Couplages de workflows

### CLOGZ — Priorité commerciale

`Research → Content → Shopify (CSV démo) → Customer Support → Creative → Video → Publishing → Quality → Analytics`

**Test de réception V0.1 :** une mission fabrique une proposition de campagne, un visuel SVG, un brouillon de réponse et un audit de stock clairement marqué démo. Aucune modification Shopify et aucun message WhatsApp.

**V0.5 cible :** lire les vrais produits/variantes/stocks via Shopify GraphQL Admin API, avec des permissions minimales. Les opérations d'écriture devront être séparées du lecteur et chacune soumise à approbation. Ne jamais lancer une migration du numéro WhatsApp Business comme effet de bord.

### KDP — Priorité éditoriale

`Research → Content → KDP Publishing → Creative → Publishing → Quality → Analytics`

**Test de réception V0.1 :** sommaire, draft de métadonnées, maquette ; l'état `NOT_READY` empêche toute confusion avec un fichier commercialisable.

**V0.7 cible :** vérification originale, rédaction, relecture éditoriale, export ePub/PDF, couverture conforme dimensions/bleed, contrôle du droit d'auteur et des déclarations IA, puis soumission manuelle. Source officielle : https://kdp.amazon.com/fr_FR/help/topic/G200672390

### YouTube Automation — Préproduction

`Research → Content → Creative → Video Production → Publishing → Quality → Analytics`

**Test d'intégration actuel :** la mission YouTube produit un paquet de transfert Stickman, détecte le runtime local et initialise un projet Autopilot lorsque Node 20+, dépendances, polices et FFmpeg/FFprobe sont disponibles. Le Skill est exposé à Codex et Claude Code par le script Windows.

**Limite explicite :** le serveur Python ne lance pas lui-même Codex/Claude Code et ne génère pas encore le film final. Un agent local avec accès fichiers/terminal exécute le Skill, vérifie le fournisseur, produit un aperçu audio/vidéo d'environ 20 s, attend l'approbation de cet aperçu puis rend le film complet. Les dépenses externes restent bloquées sans accord. À la date du 2 novembre 2026, la documentation de l'API YouTube indique que `status.privacyStatus` omis peut conduire à une publication `public`, même depuis un projet non vérifié : définir explicitement `private` dès l'intégration. Voir https://developers.google.com/youtube/data/revision_history

### Clipping — Priorité technique initiale

`Source autorisée → Clipping → Creative → Publishing → Quality → Analytics`

**Test de réception V0.1 :** import MP4/MOV/MKV/WebM avec déclaration de droits, découpe FFmpeg, vidéo 540×960 et contrôle de la résolution via FFprobe.

**V0.3 cible :** transcription Whisper locale, score des meilleurs passages, recadrage intelligent, sous-titres Remotion/OpenShorts, rendu 9:16, détection de débordements, synchro audio, validation humaine des droits et des transformations éditoriales.

## 4. Feuille de route par jalons et critères d'acceptation

| Jalon | Livrable | Critères de recette |
|---|---|---|
| J0 — Fondation | Projet Python/SQLite/dashboard | Serveur sur localhost, 12 rôles, missions historisées, médiathèque et tests automatisés |
| J1 — Clipping intelligent | FFmpeg + Whisper + OpenShorts/Remotion | Transcription horodatée, 3 extraits candidats, sous-titres et exports, droits confirmés |
| J2 — Création visuelle | Adaptateur ComfyUI | Image originale, versionnement, contrôle de format et licence des modèles |
| J3 — CLOGZ réel, lecture seule | Shopify GraphQL | Import d'informations réelles avec autorisation, journal d'accès, zéro mutation |
| J4 — Production YouTube | Passerelle Stickman + agent local | Installer le Skill, vérifier les fournisseurs sans dépense, générer un aperçu avec son, obtenir l'approbation, produire et contrôler un MP4 réel |
| J5 — CRM et publication | WhatsApp/YouTube avec permissions | Brouillon puis approbation ciblée, journal et prévention des doublons |
| J6 — KDP complet | Livre exploitable éditorialement | PDF/ePub et couverture conformes, droits vérifiés, état déclaré IA, revue humaine |
| J7 — Supervision entreprise | Dashboard KPI + n8n | Mesures par activité, relances limitées, alertes, budgets et sauvegardes |

*Les jalons ne constituent pas une estimation de durée. Ils décrivent une progression de réalisation.*

## 5. Sécurité, confiance et exploitation

**Séparation des permissions** :

- **lecture locale :** libre dans `knowledge/` et données de démonstration ;
- **écriture locale :** uniquement dans les dossiers de travail autorisés ;
- **accès réseau :** Ollama uniquement en localhost dans V0.1 ;
- **écritures externes :** désactivées par défaut ;
- **actions sensibles :** décisions humaines persistées avant révision ; approbation de publication séparée et journal détaillé avant tout envoi, paiement ou publication. Les connecteurs externes restent désactivés.

**Limites de sécurité V0.1 :** serveur localhost uniquement, pas de HTTPS, pas de système de comptes/permissions multi-utilisateurs, token de session pour l'interface locale. Ne jamais exposer ce serveur sur un VPS ou Internet sans architecture d'authentification renforcée, proxy HTTPS, quotas, antivirus et protections réseau.

**Fiabilité** : les erreurs restent visibles dans l'historique ; les fichiers restent dans les dossiers des jobs, jusqu'à suppression manuelle. Pour V1 : migrations SQL, sauvegardes, rétention, checksum d'artefacts, reprise après crash, gestion du quota disque, hooks d'agent, tests d'intégration.

**Confidentialité** : n'ajouter dans `knowledge/` que les données utiles à la production. Les données personnelles Shopify/clients seront traitées selon une politique explicite de minimisation, de stockage et de suppression lorsque les connecteurs seront construits.

## 6. Coûts et dépendances

Le MVP utilise Python et SQLite (stdlib) ; FFmpeg reste facultatif hors rendu. Ollama n'est utilisé que si un modèle local est installé et renseigné. Les capacités et vitesses réelles dépendront du **nouvel ordinateur** (ne pas utiliser les caractéristiques de l'ancien PC comme hypothèse).

Les postes pouvant coûter de l'argent ultérieurement : comptes de plateformes, stockage et sauvegarde, licences médias, GPU/électricité, bande passante, APIs payantes et hébergement externe. La stratégie 0 € concerne le logiciel local et l'absence de souscription obligatoire pour tester V0.1.

## 7. Décision immédiate après le test du MVP

Une fois que le dashboard et un vrai clip de démonstration tournent, la prochaine implémentation technique à ouvrir est **Whisper + OpenShorts + Remotion**. C'est l'enchaînement avec la meilleure réutilisation d'outils déjà connus et le moyen le plus concret de passer d'un découpage manuel à un agent de clipping intelligent.
