# BOOK_001 — Road Trip Activity Book for Kids

Premier livre pilote de la KDP Factory de Production OS.

- **Audience :** enfants de 6 à 10 ans
- **Langue :** anglais (prévue)
- **Marché :** Amazon.com (à confirmer)
- **Format :** broché, 8.5 × 11 pouces, intérieur noir et blanc
- **Longueur :** exactement 80 pages, pages de début incluses
- **Statut :** plan page par page enregistré ; aucune activité ni illustration finale n'a encore été produite.

## Fichiers de référence

- `BOOK_SPEC.md` — spécifications et règles non négociables.
- `PAGE_PLAN.md` — répartition et contenu prévu des pages 1 à 80.
- `PAGE_MANIFEST.csv` — registre structuré des 80 pages et contrôles associés.
- `KDP_PROGRESS_STATE.json` — état lisible par les agents et l'orchestrateur.
- `QA_CHECKLIST.md` — critères de validation des activités et du paquet KDP.

## Règle de pagination

PAGE_PLAN.md et PAGE_MANIFEST.csv définissent ensemble la pagination officielle du pilote. Toute modification doit mettre à jour les deux fichiers et KDP_PROGRESS_STATE.json. Le total doit rester à 80 pages.

## Workflow cible

1. Recherche de marché et bibliothèque de références.
2. Bible éditoriale et validation humaine du concept.
3. Génération des puzzles et corrigés par code.
4. Production des illustrations originales.
5. Assemblage de l'intérieur et de la couverture.
6. Contrôles automatisés, inspection visuelle et correction.
7. Préparation d'un dossier KDP ; soumission manuelle uniquement.
