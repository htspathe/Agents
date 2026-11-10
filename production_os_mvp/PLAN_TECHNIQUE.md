# Production OS — Plan technique de réalisation

**Version datée : 10 novembre 2026**  
**Périmètre :** 12 agents fondateurs, 4 zones CLOGZ / KDP / YouTube / Clipping.  
**Budget des logiciels et des appels LLM du MVP :** 0 € (consommation matérielle et Internet non incluse).

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

`queued → running → awaiting_approval` ou `failed`.

Le statut `awaiting_approval` signifie qu'un dossier de livrables est prêt pour une revue manuelle. **Aucun bouton n'autorise une publication dans V0.1.** Dans la prochaine version, un composant d'approbation distinct devra signer les opérations autorisées et noter qui les a validées.

## 2. Contrat de chaque agent

| ID | Entrée | Opération V0.1 | Sortie et validation |
|---|---|---|---|
| Orchestrator | Brief + workflow | Création des jobs, orchestration, événements | Job SQLite + historique |
| Research | Brief + notes internes | Récupération très simple de notes locales | `research.md`, sources internes explicites |
| Content Strategist | Brief + contexte | Ollama si disponible, sinon gabarit | `content.md`, vérification humaine |
| Creative | Titre + marque | Composition SVG originale | `visual.svg`, maquette uniquement |
| Video Production | Intention vidéo | Storyboard + fond vidéo test FFmpeg | `storyboard.json`, vidéo de 3 s facultative |
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

**Test de réception V0.1 :** script de structure, maquette de miniature, storyboard, aperçu technique de 3 s, manifeste.

**V0.6 cible :** voix off, scènes originales et B-roll sous licence, sous-titres, transitions, rendu, revue éditoriale, upload privé explicitement configuré. À la date du 2 novembre 2026, la documentation de l'API YouTube indique que `status.privacyStatus` omis peut conduire à une publication `public`, même depuis un projet non vérifié : définir explicitement `private` dès l'intégration. Voir https://developers.google.com/youtube/data/revision_history

### Clipping — Priorité technique initiale

`Source autorisée → Clipping → Creative → Publishing → Quality → Analytics`

**Test de réception V0.1 :** import MP4/MOV/MKV/WebM avec déclaration de droits, découpe FFmpeg, vidéo 540×960 et contrôle de la résolution via FFprobe.

**V0.3 cible :** transcription Whisper locale, score des meilleurs passages, recadrage intelligent, sous-titres Remotion/OpenShorts, rendu 9:16, détection de débordements, synchro audio, validation humaine des droits et des transformations éditoriales.

## 4. Feuille de route par jalons et critères d'acceptation

| Jalon | Livrable | Critères de recette |
|---|---|---|
| J0 — Fondation | Projet Python/SQLite/dashboard | Serveur sur localhost, 12 rôles, missions historisées, 7 tests automatisés verts |
| J1 — Clipping intelligent | FFmpeg + Whisper + OpenShorts/Remotion | Transcription horodatée, 3 extraits candidats, sous-titres et exports, droits confirmés |
| J2 — Création visuelle | Adaptateur ComfyUI | Image originale, versionnement, contrôle de format et licence des modèles |
| J3 — CLOGZ réel, lecture seule | Shopify GraphQL | Import d'informations réelles avec autorisation, journal d'accès, zéro mutation |
| J4 — Production YouTube | Scripts vérifiés + vidéo montée | Rendu audiovisuel complet, narration, sources, sous-titres et revue manuelle |
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
- **actions sensibles :** approbation nominative et journal détaillé avant tout envoi, paiement ou publication, à développer.

**Limites de sécurité V0.1 :** serveur localhost uniquement, pas de HTTPS, pas de système de comptes/permissions multi-utilisateurs, token de session pour l'interface locale. Ne jamais exposer ce serveur sur un VPS ou Internet sans architecture d'authentification renforcée, proxy HTTPS, quotas, antivirus et protections réseau.

**Fiabilité** : les erreurs restent visibles dans l'historique ; les fichiers restent dans les dossiers des jobs, jusqu'à suppression manuelle. Pour V1 : migrations SQL, sauvegardes, rétention, checksum d'artefacts, reprise après crash, gestion du quota disque, hooks d'agent, tests d'intégration.

**Confidentialité** : n'ajouter dans `knowledge/` que les données utiles à la production. Les données personnelles Shopify/clients seront traitées selon une politique explicite de minimisation, de stockage et de suppression lorsque les connecteurs seront construits.

## 6. Coûts et dépendances

Le MVP utilise Python et SQLite (stdlib) ; FFmpeg reste facultatif hors rendu. Ollama n'est utilisé que si un modèle local est installé et renseigné. Les capacités et vitesses réelles dépendront du **nouvel ordinateur** (ne pas utiliser les caractéristiques de l'ancien PC comme hypothèse).

Les postes pouvant coûter de l'argent ultérieurement : comptes de plateformes, stockage et sauvegarde, licences médias, GPU/électricité, bande passante, APIs payantes et hébergement externe. La stratégie 0 € concerne le logiciel local et l'absence de souscription obligatoire pour tester V0.1.

## 7. Décision immédiate après le test du MVP

Une fois que le dashboard et un vrai clip de démonstration tournent, la prochaine implémentation technique à ouvrir est **Whisper + OpenShorts + Remotion**. C'est l'enchaînement avec la meilleure réutilisation d'outils déjà connus et le moyen le plus concret de passer d'un découpage manuel à un agent de clipping intelligent.
