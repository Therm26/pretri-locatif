# Projet : pré-tri des dossiers locatifs (prototype)

## Objectif
Aider un gestionnaire locatif à traiter 100+ dossiers pour un même bien. L'outil vérifie que chaque dossier est complet, extrait les chiffres clés des justificatifs, signale les incohérences qui ressemblent à une fraude et applique les critères financiers du bien. La décision finale reste au gestionnaire.

## Règles non négociables
- Données fictives uniquement. Aucun vrai dossier de locataire dans le repo ni dans les tests.
- Critères autorisés : revenus, ratio loyer/revenus, type et ancienneté du contrat, présence d'un garant ou d'une garantie (Visale, etc.), complétude des pièces. Rien d'autre.
- Ne jamais utiliser ni afficher comme critère : âge, sexe, nationalité, nom, adresse d'origine, situation familiale, photo.
- Les pièces demandées se limitent à la liste du décret n° 2015-1437. L'outil refuse d'ajouter une pièce hors liste.
- Pas de note globale opaque. Chaque dossier affiche, critère par critère, pourquoi il passe ou non.
- Chaque action (import, extraction, décision) est écrite dans un journal horodaté.

## Stack
- Python 3.11+
- Streamlit pour l'interface
- API Claude (SDK `anthropic`) pour lire les PDF et images et renvoyer du JSON
- SQLite pour stocker biens, dossiers, résultats et journal
- pytest pour les tests

## Structure cible
```
app.py                  # interface Streamlit
src/
  models.py             # dataclasses : Bien, Dossier, Piece, Resultat
  extraction.py         # appel Claude : PDF -> JSON structuré
  controles.py          # contrôles de cohérence (anti-fraude)
  regles.py             # critères du bien appliqués au dossier
  journal.py            # écriture du log
  db.py                 # accès SQLite
scripts/
  generer_dossiers.py   # crée de faux dossiers PDF pour les tests
data/fake/              # dossiers fictifs générés
tests/
```

## Étapes (une à la fois, tester avant de passer à la suivante)
1. `generer_dossiers.py` : 20 faux dossiers (bulletins de salaire, avis d'imposition, contrat de travail, pièce d'identité factice). Dont 4 volontairement faux : net incohérent avec le brut, SIRET invalide, revenus du bulletin différents de l'avis d'imposition, dates impossibles.
2. `extraction.py` : envoyer un PDF à Claude, récupérer un JSON validé (employeur, SIRET, salaire brut, net, période, type de contrat, revenu fiscal de référence).
3. `controles.py` : contrôles déterministes en Python (clé de Luhn du SIRET, ratio net/brut entre 0,70 et 0,85, cohérence bulletin vs avis, dates).
4. `regles.py` : critères saisis par bien (loyer CC, ratio minimum, garant accepté ou non). Statut : Complet / Incomplet / À vérifier, avec motif par critère.
5. `app.py` : créer un bien, importer un dossier (zip ou PDF), voir la liste triée par statut puis par ratio, ouvrir le détail.
6. Journal + export CSV de la sélection.

## Conventions
- Code et noms de variables en français ou anglais, mais cohérents dans un même fichier.
- Clé API dans `.env` (jamais commitée). `.gitignore` inclut `.env` et `data/`.
- Expliquer chaque fichier créé en quelques lignes : je débute en code et je veux comprendre.
- Lancer les commandes avec l'environnement virtuel : `.\.venv\Scripts\python.exe -m pytest`.

## Avancement

### Fait
- **Mise en place** (11/11/2026) : dossiers `src/`, `scripts/`, `tests/`, `data/fake/` ; `.gitignore`, `requirements.txt`, `README.md`, `.env.example`, `pyproject.toml` ; environnement virtuel `.venv` ; `git init` (aucun commit pour l'instant).
- **Étape 1** (11/11/2026) : `scripts/generer_dossiers.py` avec reportlab (PDF) et Faker `fr_FR` (données), graine fixe 42.
  - 20 dossiers dans `data/fake/D01` à `D20`, 6 PDF chacun : pièce d'identité, contrat, 3 bulletins (08 à 10/2026), avis d'imposition 2026 ; un zip par dossier dans `data/fake/zips/`.
  - Anomalies volontaires : D05 net incohérent, D09 SIRET invalide, D13 avis incohérent avec les bulletins, D17 dates impossibles.
  - `data/fake/verite.json` : valeurs attendues par dossier, pour tester l'extraction.
  - Pièce d'identité et avis sans date de naissance, sexe, nationalité, adresse, situation familiale ni photo ; postes neutres (non genrés).
  - Tests : `tests/test_generer_dossiers.py`, 9 tests qui passent.
  - Régénérer : `.\.venv\Scripts\python.exe -m scripts.generer_dossiers`.

- **Étape 2, code** (13/11/2026) : `src/extraction.py`, un appel Claude par fichier (PDF ou image), modèle `claude-opus-5-5`, effort `low`, sortie structurée validée par le modèle pydantic `PieceExtraite`, fallback serveur en cas de refus.
  - Dates recopiées telles qu'imprimées (texte), pour que l'étape 3 détecte « 31/09/2026 ». Aucun nom, adresse ni autre donnée personnelle dans le schéma.
  - `synthese_dossier()` regroupe les pièces au format de `verite.json`.
  - `scripts/evaluer_extraction.py` : compare à `verite.json` champ par champ (vraie API) ; résultats bruts dans `data/fake/extraction/`.
  - Tests : `tests/test_extraction.py`, 8 tests avec un faux client (sans API).
  - Dépendances ajoutées à `requirements.txt` : `anthropic`, `pydantic`, `python-dotenv`.
  - Champs du schéma tous obligatoires (null si absent) : avec des champs facultatifs, l'API répondait « Schema is too complex ».
- **Étape 2, évaluation** (13/11/2026) : 20 dossiers passés dans l'API, réponses dans `data/fake/extraction/`.
  - `verite.json` contient aussi `bulletins` : par bulletin (08, 09, 10), période (texte tel qu'imprimé), brut, net, net imposable. Produit par `generer_dossiers.py` (PDF inchangés).
  - `scripts/taux_par_champ.py` : taux par champ sans API, à partir des JSON enregistrés. Résultat : 500/500.
  - Date impossible : stockée en texte brut (`"31/09/2026"`) dans `PieceExtraite` ; `date_iso()` renvoie `None` pour une date invalide comme pour une date absente.
  - Tests : 19 qui passent.

- **Étape 3** (13/11/2026) : `src/controles.py`, 4 contrôles déterministes, toujours dans cet ordre : `siret`, `net_brut`, `bulletins_vs_avis`, `dates`. Statut `ok` / `anomalie` / `non_verifiable` (donnée manquante, pas une accusation), motif en clair.
  - SIRET : Luhn sur le SIREN et le SIRET (exception La Poste : somme des chiffres multiple de 5), même SIRET sur toutes les pièces. `luhn_valide` est désormais dans `src/controles.py` (le générateur l'importe).
  - Net/brut entre 0,70 et 0,85 (bornes incluses) sur chaque bulletin.
  - Avis : salaires déclarés comparés à 12 fois le net imposable moyen, anomalie si écart > 20 %.
  - Dates : `lire_date()` renvoie `None` si absente, lève `DateInvalide` si impossible ; embauche après le premier bulletin, période ou contrat à l'envers.
  - Tests écrits avant le code : `tests/test_controles.py`. Les 4 pièges lèvent leur anomalie, les 16 sains n'en lèvent aucune. Total : 41 tests.
  - Marges sur les dossiers fictifs : sains net/brut 0,76 à 0,80 et avis/bulletins 0,95 à 1,00 ; D05 donne 0,83 sur l'avis (sous le seuil, non signalé).

- **Étape 4** (13/11/2026) : `src/models.py` (`Bien`) et `src/regles.py` (`evaluer_dossier`, `cle_de_tri`).
  - `Bien` : loyer CC, ratio minimum (défaut 3), garant accepté, pièces exigées, nombre de bulletins (1 à 3). Refuse à la création une pièce hors décret 2015-1437 (`PieceNonAutorisee`) ou un critère absurde (`CritereInvalide`).
  - Pièces exigibles : identité, contrat, bulletins, avis d'imposition (pièces du décret que l'outil reconnaît).
  - Critères affichés, dans l'ordre : `completude`, `revenus`, puis les 4 contrôles de l'étape 3. Chacun `respecte` / `non_respecte` / `a_verifier` avec motif.
  - Revenus = moyenne du net à payer des bulletins ; ratio = revenus / loyer CC.
  - Garantie (`visale` ou `garant_physique`) déclarée par le gestionnaire : si revenus insuffisants, « à vérifier » si le bien accepte les garants, sinon « non respecté ».
  - Statut : Incomplet (pièce manquante) > À vérifier (contrôle en anomalie ou garantie à examiner) > Complet. Le ratio insuffisant ne change PAS le statut : c'est un critère à part.
  - Tri : statut (Complet, À vérifier, Incomplet) puis ratio décroissant.
  - Tests écrits avant le code : `tests/test_regles.py`. Total : 67 tests.

### Prochaine étape
- **Étape 5** : `app.py` (Streamlit) : créer un bien, importer un dossier (zip ou PDF), voir la liste triée, ouvrir le détail. Nécessite `db.py` (SQLite) pour garder biens, dossiers et résultats.
- Pas encore commité : étapes 3 et 4, et mise à jour de ce fichier.
