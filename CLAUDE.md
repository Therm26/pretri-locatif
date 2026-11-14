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
  - `Bien` : loyer CC, ratio minimum (défaut 3), `garant_accepte`, `visale_acceptee`, pièces exigées, nombre de bulletins (1 à 3). Refuse à la création une pièce hors décret 2015-1437 (`PieceNonAutorisee`) ou un critère absurde (`CritereInvalide`).
  - Pièces exigibles : identité, contrat, bulletins, avis d'imposition (pièces du décret que l'outil reconnaît).
  - `evaluer_dossier(pieces, bien, garant=None, visale=False)` : `garant` = pièces extraites du garant ; `visale` = case cochée par le gestionnaire après avoir vu le certificat (reconnaissance automatique plus tard).
  - Trois statuts indépendants (révisé le 14/11/2026) :
    - `pieces` : Complet / Incomplet + liste des manquantes.
    - `criteres` : Conforme / Non conforme / Non évaluable + motif chiffré. Revenus = moyenne du net à payer des bulletins, ratio = revenus / loyer CC. Sous le ratio : Conforme « via garant » si garant accepté et au ratio, sinon « via Visale » si fournie et acceptée, sinon Non conforme. Revenus illisibles (candidat, ou garant quand il est le dernier recours) : Non évaluable, car c'est l'extraction qui a échoué.
    - `controles` : OK / À vérifier + alertes (contrôles de l'étape 3 non OK, ceux du garant préfixés « garant : », « revenus illisibles, vérifier les bulletins à la main »).
  - Aucun libellé ne dit « Refusé » (test dédié).
  - Tri (`groupe`, `cle_de_tri`) : (a) Complet + Conforme + OK, (b) Complet + Conforme + À vérifier, (c) Incomplet ou Non évaluable, (d) Non conforme ; dans chaque groupe, date de dépôt la plus ancienne d'abord.
  - Sur les 20 dossiers fictifs (loyer 800 €) : 13 en (a), D05 et D17 en (b), D01, D09, D13, D19, D20 en (d).
  - Tests : `tests/test_regles.py`. Total : 77 tests.

- **Compléments** (14/11/2026) :
  - Motif `criteres` : il dit d'où vient le revenu, « moyenne du net à payer avant impôt des 3 derniers bulletins ». Si un net est illisible, la moyenne porte sur les bulletins lisibles et le motif le dit (« de 2 bulletin(s) sur 3, net illisible sur bulletin_2026_09.pdf ») ; le contrôle net/brut passe en non vérifiable, donc le dossier est « À vérifier ».
  - Générateur : 22 dossiers. `DEFAUTS` (sans fraude) : D21 sans avis d'imposition, D22 net à payer couvert par une tache sur le bulletin de septembre (la valeur n'est pas écrite sous la tache). D21 et D22 sont tirés après les 20 premiers : D01 à D20 restent identiques octet pour octet. `verite.json` a un champ `defaut`.
  - Le générateur n'efface plus `data/fake/extraction/` (réponses de Claude déjà payées).
  - Extraction D21 et D22 : 20/20 ; Claude renvoie `null` pour le net taché. Total `taux_par_champ` : 550/550.
  - D21 : Incomplet, Non conforme (1,89), À vérifier → groupe (c). D22 : Complet, Conforme (4,61), À vérifier → groupe (b).
  - Tests : 86.

- **Étape 5a** (14/11/2026) : `src/db.py` (SQLite) et `scripts/importer_dossiers.py`. Pas d'interface.
  - Règle ajoutée dans `regles.py` : moins de 2 nets lisibles (1 si le bien n'exige qu'un bulletin) → Non évaluable, motif « 1 net(s) lisible(s) sur 3 bulletin(s), il en faut au moins 2 ».
  - Ratio comparé en entiers : somme des nets (centimes) × 100 ≥ ratio en centièmes × loyer (centimes) × nombre de nets. Plus de tolérance en float. `Bien` refuse un loyer ou un ratio à plus de 2 décimales ; propriétés `loyer_centimes`, `ratio_centiemes`.
  - Tables : `bien` (loyer en centimes), `dossier` (référence sans nom, `date_depot`, `a_supprimer_le` = dépôt + 3 mois, `visale`), `piece` (JSON de `PieceExtraite` = données personnelles), `resultat` (3 statuts, motifs, groupe, revenus en centimes), `journal`.
  - `connecter()` active `PRAGMA foreign_keys = ON` à chaque connexion. Dates en ISO 8601 à la seconde (`2026-11-02T09:00:00`), vérifiées par un CHECK de la base.
  - `purger()` supprime les dossiers dont `a_supprimer_le` est dépassé ; pièces et résultat partent en cascade ; le journal garde ses lignes (`dossier_id` à NULL, `reference` conservée) et note la purge.
  - Journal : seulement références, statuts, critères du bien ; `journaliser()` refuse toute clé hors `CLES_JOURNAL` (pas de montant lu, d'employeur, de motif ni de nom de fichier). Action `evaluation` : loyer, ratio_min, garant accepté/fourni, Visale acceptée/fournie, les 3 statuts, via et groupe.
  - Import : bien « T2 fictif » (800 €, ratio 3), D01 à D22 depuis `data/fake/extraction/` (sans API), dépôts du 02/11/2026 9 h au 04/11/2026 18 h, graine 42, ordre mélangé. Relancer n'ajoute rien. Base : `data/pretri.db`.
  - Résultat : 13 en (a), D05, D17, D22 en (b), D21 en (c), D01, D09, D13, D19, D20 en (d).
  - Lancer : `.\.venv\Scripts\python.exe -m scripts.importer_dossiers`.
  - Tests : `tests/test_db.py`. Total : 120.
  - Décision du gestionnaire : prévue à l'étape 6.

- **Étape 5b** (14/11/2026) : `app.py` (Streamlit) et `src/depot.py`.
  - Lancer : `.\.venv\Scripts\python.exe -m streamlit run app.py`, puis http://localhost:8501. Autre base : variable `PRETRI_BASE`.
  - Barre latérale : choisir un bien, créer un bien (erreurs de `Bien` affichées), bouton « Charger les dossiers fictifs » (import 5a, sans API).
  - Onglet Dossiers : compteurs par groupe, tableau trié par groupe puis dépôt, clic sur une ligne = détail (3 statuts + motifs, alertes, 4 contrôles du candidat, valeurs lues par Claude, date de suppression), bascule « Certificat Visale vu » qui réévalue le dossier.
  - Le dossier ouvert est retenu par son id (`st.session_state["ouvert"]`) ; la clé du tableau dépend de l'ordre des lignes, pour qu'une sélection par numéro de ligne ne désigne jamais un autre dossier après un réordonnancement.
  - Onglet Importer : zip ou PDF/images du candidat, pièces du garant (facultatif), Visale, date et heure de dépôt (pas dans le futur). Annonce le nombre de lectures payantes ; bouton désactivé sans clé API. Champs propres à chaque bien.
  - `src/depot.py` : `lire_envoi()` ouvre les zip (noms aplatis, fichiers cachés et `__MACOSX` ignorés, autres formats listés comme ignorés, doublons refusés, au plus 30 fichiers et 50 Mo) ; `prochaine_reference()` attribue D01, D02… (jamais un nom, jamais une référence déjà vue dans le journal) ; `deposer()` lit chaque fichier avec Claude dans un dossier temporaire, puis enregistre et évalue. Les PDF ne sont pas conservés, seulement les valeurs extraites.
  - Si un fichier est illisible, rien n'est enregistré ; le journal note `extraction_echec` (référence et nombre de pièces). Succès : `extraction`, `import`, `evaluation`.
  - `db.py` : `lister_biens`, `charger_dossier`, `modifier_visale` (journal `visale` + réévaluation). Purge à 3 mois lancée à chaque affichage.
  - Vérifié dans le navigateur sur une base d'essai : 13/3/1/5 par groupe, Visale sur D01 → groupe a puis retour en d, création de bien, ratio 0 refusé. Import réel via l'API non testé depuis l'interface (logique testée avec un faux extracteur).
  - Tests : `tests/test_depot.py`. Total : 135.

- **Étape 6** (14/11/2026) : décision du gestionnaire, bien Ouvert / Loué, journal consultable, export CSV.
  - Schéma : `dossier.decision` (`retenu` / `non_retenu` / NULL = sans décision), `decision_le`, `a_supprimer_le` désormais NULL possible ; `bien.statut` (`Ouvert` / `Loué`), `cloture_le`. CHECK : une décision a toujours sa date.
  - `db.migrer()` met à jour une base d'avant l'étape 6 à l'ouverture (ajout de colonnes au bien, table `dossier` recréée et recopiée, clés étrangères vérifiées). Essayé sur une copie de `data/pretri.db` : 22 dossiers, 131 pièces, 45 lignes de journal conservés.
  - Conservation : sans décision = dépôt + 3 mois ; non retenu = décision + 3 mois sans jamais raccourcir (`plus_tardive`) ; retenu = `a_supprimer_le` NULL et `purger()` exclut les retenus même avec une date remplie ; retenu puis non retenu = décision + 3 mois.
  - `decider(conn, dossier_id, decision, quand=None)` : refuse une décision inconnue ou datée d'avant le dépôt. Journal `decision` (décision, date, date de suppression).
  - `changer_statut_bien(conn, bien_id, "Loué" | "Ouvert", quand=None)` : Loué bascule les dossiers sans décision en non retenu à la date de clôture (journal `non_retenu_auto`, une ligne par dossier, plus `cloture_bien`) ; les décisions déjà prises ne bougent pas. Rouvrir (`reouverture_bien`) garde les décisions. Un bien loué refuse tout nouveau dossier (`BienLoue`), y compris dans `deposer()` avant toute lecture payante.
  - Journal : nouvelles clés `decision`, `decision_le`, `statut_bien`, `cloture_le`, `nb_dossiers`. `lister_journal()` pour l'onglet Journal.
  - `src/export.py` : CSV `;` en UTF-8 avec BOM (Excel). Colonnes : dossier, dépôt, groupe, 3 statuts, décision, date de décision, suppression. Pas de ratio, revenus ni motif, car le fichier sort de l'outil et n'est pas purgé. Chaque export est journalisé (`export_csv`, nombre de dossiers).
  - Interface : encart « Marquer le bien comme loué » (nombre de dossiers basculés annoncé, case de confirmation), « Rouvrir le bien » ; dans le détail, Retenir / Ne pas retenir (boutons neutres) et date de suppression ; filtres par groupe et par décision, colonne Décision, bouton d'export de la sélection affichée ; onglet Journal avec filtre par action et libellés lisibles.
  - Vérifié dans le navigateur sur une copie migrée de la base : retenir D07, ne pas retenir D08, filtre + export (2 dossiers), Loué (20 dossiers basculés), import bloqué, journal, réouverture.
  - Tests : `tests/test_decision.py` (16, dont migration et export). Total : 151.
  - Pas de retour à « sans décision » : on peut seulement passer de retenu à non retenu et inversement.

### Prochaine étape
- Essayer un import réel depuis l'interface (un zip fictif, 6 lectures payantes).
- Les 6 étapes du plan sont faites. Pistes : reconnaître le certificat Visale automatiquement, critère d'ancienneté et type de contrat (autorisé, pas encore appliqué), `journal.py` séparé comme dans la structure cible.
