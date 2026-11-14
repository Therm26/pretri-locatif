"""Étape 5a : la base SQLite (biens, dossiers, pièces, résultats, journal).

SQLite garde tout dans un seul fichier (`data/pretri.db` par défaut). Ce module est
le seul à écrire du SQL : le reste du code appelle ses fonctions.

Règles appliquées ici :
- `connecter()` active PRAGMA foreign_keys = ON à chaque connexion (SQLite ne le fait
  pas tout seul, et sans lui les ON DELETE CASCADE / SET NULL sont ignorés).
- Argent en centimes (INTEGER) : 800,00 € est stocké 80000.
- Dates en ISO 8601 à la seconde, sans fuseau : « 2026-11-02T09:00:00 ». Sous cette
  forme, l'ordre alphabétique est l'ordre chronologique, donc ORDER BY marche.
- Un dossier est gardé 3 mois après son dépôt (`a_supprimer_le`) ; `purger()` supprime
  ensuite le dossier, ses pièces et son résultat.
- Le journal ne contient que des références, des statuts et les critères du bien :
  jamais un montant lu sur une pièce, un employeur, un nom de fichier. `journaliser()`
  refuse toute autre clé (liste `CLES_JOURNAL`). Il survit à la purge : `dossier_id`
  passe à NULL, la colonne `reference` reste.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.extraction import PieceExtraite
from src.models import Bien
from src.regles import Resultat, centimes, evaluer_dossier, groupe

CHEMIN_PAR_DEFAUT = Path("data/pretri.db")
DUREE_CONSERVATION_MOIS = 3

# AAAA-MM-JJTHH:MM:SS, vérifié par la base elle-même (CHECK ... GLOB).
_ISO = "'[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]'"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS bien (
  id               INTEGER PRIMARY KEY,
  nom              TEXT NOT NULL,
  loyer_cc         INTEGER NOT NULL CHECK (loyer_cc > 0),     -- en centimes
  ratio_min        REAL NOT NULL CHECK (ratio_min > 0),       -- au centième près
  garant_accepte   INTEGER NOT NULL CHECK (garant_accepte IN (0, 1)),
  visale_acceptee  INTEGER NOT NULL CHECK (visale_acceptee IN (0, 1)),
  pieces_exigees   TEXT NOT NULL,                             -- liste JSON, vérifiée par Bien()
  nb_bulletins     INTEGER NOT NULL CHECK (nb_bulletins BETWEEN 1 AND 3),
  cree_le          TEXT NOT NULL CHECK (cree_le GLOB {_ISO})
);

-- Une candidature déposée pour un bien. Aucun nom : une référence seulement.
CREATE TABLE IF NOT EXISTS dossier (
  id              INTEGER PRIMARY KEY,
  bien_id         INTEGER NOT NULL REFERENCES bien(id),
  reference       TEXT NOT NULL,                              -- « D01 »
  date_depot      TEXT NOT NULL CHECK (date_depot GLOB {_ISO}),
  a_supprimer_le  TEXT NOT NULL CHECK (a_supprimer_le GLOB {_ISO}),  -- dépôt + 3 mois
  visale          INTEGER NOT NULL DEFAULT 0 CHECK (visale IN (0, 1)),  -- vu par le gestionnaire
  importe_le      TEXT NOT NULL CHECK (importe_le GLOB {_ISO}),
  UNIQUE (bien_id, reference)
);

-- Ce que Claude a lu sur chaque fichier.
CREATE TABLE IF NOT EXISTS piece (
  id          INTEGER PRIMARY KEY,
  dossier_id  INTEGER NOT NULL REFERENCES dossier(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('candidat', 'garant')),
  fichier     TEXT NOT NULL,                                  -- « bulletin_2026_09.pdf »
  type_piece  TEXT NOT NULL,
  donnees     TEXT NOT NULL,  -- JSON de PieceExtraite : DONNÉES PERSONNELLES (salaires, employeur,
                              -- SIRET, dates de contrat) ; supprimées par purger() après 3 mois
  UNIQUE (dossier_id, role, fichier)
);

-- Les trois statuts calculés par regles.py ; recalculables à tout moment.
-- Les motifs et alertes citent des montants : ils sont purgés avec le dossier.
CREATE TABLE IF NOT EXISTS resultat (
  dossier_id         INTEGER PRIMARY KEY REFERENCES dossier(id) ON DELETE CASCADE,
  pieces             TEXT NOT NULL,
  pieces_motif       TEXT NOT NULL,
  pieces_manquantes  TEXT NOT NULL,                           -- liste JSON
  criteres           TEXT NOT NULL,
  criteres_motif     TEXT NOT NULL,
  via                TEXT CHECK (via IN ('garant', 'Visale')),
  controles          TEXT NOT NULL,
  alertes            TEXT NOT NULL,                           -- liste JSON
  revenus_mensuels   INTEGER,                                 -- en centimes
  ratio              REAL,                                    -- pour l'affichage seulement
  groupe             TEXT NOT NULL CHECK (groupe IN ('a', 'b', 'c', 'd')),
  calcule_le         TEXT NOT NULL CHECK (calcule_le GLOB {_ISO})
);

-- Journal horodaté : on ajoute des lignes, on n'en modifie ni n'en supprime jamais.
CREATE TABLE IF NOT EXISTS journal (
  id          INTEGER PRIMARY KEY,
  horodatage  TEXT NOT NULL CHECK (horodatage GLOB {_ISO}),
  action      TEXT NOT NULL,
  bien_id     INTEGER REFERENCES bien(id) ON DELETE SET NULL,
  dossier_id  INTEGER REFERENCES dossier(id) ON DELETE SET NULL,
  reference   TEXT,                                           -- reste lisible après la purge
  detail      TEXT NOT NULL                                   -- JSON, clés de CLES_JOURNAL
);

CREATE INDEX IF NOT EXISTS dossier_par_bien ON dossier (bien_id, date_depot);
CREATE INDEX IF NOT EXISTS dossier_a_purger ON dossier (a_supprimer_le);
"""

# Seules clés permises dans journal.detail : critères du bien, statuts, références, compteurs.
CLES_JOURNAL = {
    # critères du bien
    "nom_bien", "loyer_cc_centimes", "ratio_min", "garant_accepte", "visale_acceptee",
    "pieces_exigees", "nb_bulletins",
    # ce que le gestionnaire a fourni ou coché (oui / non)
    "garant_fourni", "visale_fournie",
    # statuts
    "pieces", "criteres", "controles", "via", "groupe",
    # dates de gestion et compteurs
    "date_depot", "a_supprimer_le", "nb_pieces", "nb_pieces_garant",
}


class DossierExistant(ValueError):
    """Un dossier avec cette référence existe déjà pour ce bien."""


class DetailInterdit(ValueError):
    """Tentative d'écrire au journal une donnée qui n'a rien à y faire."""


# --- Connexion et dates ----------------------------------------------------------

def connecter(chemin: Path | str = CHEMIN_PAR_DEFAUT) -> sqlite3.Connection:
    """Ouvre (ou crée) la base, active les clés étrangères, crée les tables si besoin."""
    if str(chemin) != ":memory:":
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row           # lignes lisibles par nom de colonne : ligne["nom"]
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def iso(moment: datetime) -> str:
    """datetime(2026, 11, 2, 9, 0) -> « 2026-11-02T09:00:00 »."""
    if moment.tzinfo is not None:
        raise ValueError("dates sans fuseau horaire attendues (heure locale)")
    return moment.replace(microsecond=0).isoformat()


def maintenant() -> str:
    return iso(datetime.now())


def ajouter_mois(moment: datetime, mois: int) -> datetime:
    """30/11 + 3 mois -> 28/02 (ou 29/02) : on s'arrête au dernier jour du mois."""
    total = moment.month - 1 + mois
    annee, mois_cible = moment.year + total // 12, total % 12 + 1
    jour = min(moment.day, calendar.monthrange(annee, mois_cible)[1])
    return moment.replace(year=annee, month=mois_cible, day=jour)


# --- Journal ------------------------------------------------------------------------

def journaliser(conn: sqlite3.Connection, action: str, detail: dict,
                bien_id: Optional[int] = None, dossier_id: Optional[int] = None,
                reference: Optional[str] = None) -> None:
    interdites = set(detail) - CLES_JOURNAL
    if interdites:
        raise DetailInterdit(f"clé(s) non autorisée(s) dans le journal : {sorted(interdites)}")
    conn.execute(
        "INSERT INTO journal (horodatage, action, bien_id, dossier_id, reference, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (maintenant(), action, bien_id, dossier_id, reference,
         json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def criteres_du_bien(bien: Bien) -> dict:
    return {"loyer_cc_centimes": bien.loyer_centimes, "ratio_min": bien.ratio_min,
            "garant_accepte": bien.garant_accepte, "visale_acceptee": bien.visale_acceptee}


# --- Biens ----------------------------------------------------------------------------

def creer_bien(conn: sqlite3.Connection, bien: Bien) -> int:
    with conn:   # transaction : tout est écrit, ou rien
        curseur = conn.execute(
            "INSERT INTO bien (nom, loyer_cc, ratio_min, garant_accepte, visale_acceptee, "
            "pieces_exigees, nb_bulletins, cree_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (bien.nom, bien.loyer_centimes, bien.ratio_min, int(bien.garant_accepte),
             int(bien.visale_acceptee), json.dumps(list(bien.pieces_exigees)),
             bien.nb_bulletins, maintenant()))
        bien_id = curseur.lastrowid
        journaliser(conn, "creation_bien",
                    {"nom_bien": bien.nom, **criteres_du_bien(bien),
                     "pieces_exigees": list(bien.pieces_exigees), "nb_bulletins": bien.nb_bulletins},
                    bien_id=bien_id)
    return bien_id


def charger_bien(conn: sqlite3.Connection, bien_id: int) -> Bien:
    ligne = conn.execute("SELECT * FROM bien WHERE id = ?", (bien_id,)).fetchone()
    if ligne is None:
        raise KeyError(f"bien {bien_id} introuvable")
    # Repasser par Bien() revérifie les critères (pièces du décret, etc.).
    return Bien(nom=ligne["nom"], loyer_cc=ligne["loyer_cc"] / 100, ratio_min=ligne["ratio_min"],
                garant_accepte=bool(ligne["garant_accepte"]),
                visale_acceptee=bool(ligne["visale_acceptee"]),
                pieces_exigees=tuple(json.loads(ligne["pieces_exigees"])),
                nb_bulletins=ligne["nb_bulletins"])


def trouver_bien(conn: sqlite3.Connection, nom: str) -> Optional[int]:
    ligne = conn.execute("SELECT id FROM bien WHERE nom = ? ORDER BY id LIMIT 1", (nom,)).fetchone()
    return ligne["id"] if ligne else None


def lister_biens(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Tous les biens, le plus récent d'abord."""
    return conn.execute("SELECT id, nom, loyer_cc, ratio_min FROM bien ORDER BY id DESC").fetchall()


# --- Dossiers et pièces ---------------------------------------------------------------

def ajouter_dossier(conn: sqlite3.Connection, bien_id: int, reference: str, date_depot: datetime,
                    pieces: dict[str, PieceExtraite],
                    garant: Optional[dict[str, PieceExtraite]] = None,
                    visale: bool = False) -> int:
    """Enregistre un dossier et ses pièces déjà extraites. Ne calcule pas le résultat."""
    if conn.execute("SELECT 1 FROM dossier WHERE bien_id = ? AND reference = ?",
                    (bien_id, reference)).fetchone():
        raise DossierExistant(f"dossier {reference} déjà importé pour le bien {bien_id}")
    suppression = ajouter_mois(date_depot, DUREE_CONSERVATION_MOIS)
    with conn:
        dossier_id = conn.execute(
            "INSERT INTO dossier (bien_id, reference, date_depot, a_supprimer_le, visale, importe_le) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (bien_id, reference, iso(date_depot), iso(suppression), int(visale), maintenant()),
        ).lastrowid
        for role, lot in (("candidat", pieces), ("garant", garant or {})):
            for fichier, p in sorted(lot.items()):
                conn.execute(
                    "INSERT INTO piece (dossier_id, role, fichier, type_piece, donnees) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (dossier_id, role, fichier, p.type_piece, p.model_dump_json()))
        journaliser(conn, "import",
                    {"date_depot": iso(date_depot), "a_supprimer_le": iso(suppression),
                     "nb_pieces": len(pieces), "nb_pieces_garant": len(garant or {}),
                     "visale_fournie": visale},
                    bien_id=bien_id, dossier_id=dossier_id, reference=reference)
    return dossier_id


def charger_pieces(conn: sqlite3.Connection, dossier_id: int):
    """Renvoie (pièces du candidat, pièces du garant ou None)."""
    lots: dict[str, dict[str, PieceExtraite]] = {"candidat": {}, "garant": {}}
    for ligne in conn.execute("SELECT role, fichier, donnees FROM piece WHERE dossier_id = ?",
                              (dossier_id,)):
        lots[ligne["role"]][ligne["fichier"]] = PieceExtraite.model_validate_json(ligne["donnees"])
    return lots["candidat"], (lots["garant"] or None)


def charger_dossier(conn: sqlite3.Connection, dossier_id: int) -> sqlite3.Row:
    """Le dossier et son dernier résultat (colonnes du résultat à NULL s'il n'est pas évalué)."""
    ligne = conn.execute(
        "SELECT d.*, r.pieces, r.pieces_motif, r.pieces_manquantes, r.criteres, r.criteres_motif, "
        "r.via, r.controles, r.alertes, r.revenus_mensuels, r.ratio, r.groupe, r.calcule_le "
        "FROM dossier d LEFT JOIN resultat r ON r.dossier_id = d.id WHERE d.id = ?",
        (dossier_id,)).fetchone()
    if ligne is None:
        raise KeyError(f"dossier {dossier_id} introuvable")
    return ligne


def modifier_visale(conn: sqlite3.Connection, dossier_id: int, visale: bool) -> Resultat:
    """Le gestionnaire coche ou décoche « certificat Visale vu » ; le dossier est réévalué."""
    dossier = charger_dossier(conn, dossier_id)
    with conn:
        conn.execute("UPDATE dossier SET visale = ? WHERE id = ?", (int(visale), dossier_id))
        journaliser(conn, "visale", {"visale_fournie": visale}, bien_id=dossier["bien_id"],
                    dossier_id=dossier_id, reference=dossier["reference"])
    return evaluer(conn, dossier_id)


# --- Évaluation -------------------------------------------------------------------------

def evaluer(conn: sqlite3.Connection, dossier_id: int) -> Resultat:
    """Applique les critères du bien au dossier, enregistre le résultat et le journalise."""
    dossier = conn.execute("SELECT * FROM dossier WHERE id = ?", (dossier_id,)).fetchone()
    if dossier is None:
        raise KeyError(f"dossier {dossier_id} introuvable")
    bien = charger_bien(conn, dossier["bien_id"])
    pieces, garant = charger_pieces(conn, dossier_id)
    r = evaluer_dossier(pieces, bien, garant=garant, visale=bool(dossier["visale"]))
    g = groupe(r)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO resultat (dossier_id, pieces, pieces_motif, pieces_manquantes, "
            "criteres, criteres_motif, via, controles, alertes, revenus_mensuels, ratio, groupe, "
            "calcule_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dossier_id, r.pieces, r.pieces_motif,
             json.dumps(r.pieces_manquantes, ensure_ascii=False), r.criteres, r.criteres_motif,
             r.via, r.controles, json.dumps(r.alertes, ensure_ascii=False),
             centimes(r.revenus_mensuels) if r.revenus_mensuels is not None else None,
             r.ratio, g, maintenant()))
        # Critères utilisés + statuts obtenus : ni montant lu, ni motif (qui en contient).
        journaliser(conn, "evaluation",
                    {**criteres_du_bien(bien), "garant_fourni": garant is not None,
                     "visale_fournie": bool(dossier["visale"]),
                     "pieces": r.pieces, "criteres": r.criteres, "controles": r.controles,
                     "via": r.via, "groupe": g},
                    bien_id=dossier["bien_id"], dossier_id=dossier_id,
                    reference=dossier["reference"])
    return r


def liste_triee(conn: sqlite3.Connection, bien_id: int) -> list[sqlite3.Row]:
    """Dossiers d'un bien : groupe a, b, c, d, puis dépôt le plus ancien d'abord.

    Même ordre que regles.cle_de_tri ; le tri par date marche car elle est en ISO 8601.
    Un dossier pas encore évalué arrive en dernier.
    """
    return conn.execute(
        "SELECT d.id, d.reference, d.date_depot, r.groupe, r.pieces, r.criteres, r.controles, "
        "r.ratio FROM dossier d LEFT JOIN resultat r ON r.dossier_id = d.id "
        "WHERE d.bien_id = ? ORDER BY r.groupe IS NULL, r.groupe, d.date_depot, d.reference",
        (bien_id,)).fetchall()


# --- Purge ---------------------------------------------------------------------------------

def purger(conn: sqlite3.Connection, quand: Optional[datetime] = None) -> list[str]:
    """Supprime les dossiers dont la date `a_supprimer_le` est dépassée.

    Pièces et résultat partent avec (ON DELETE CASCADE). Le journal garde ses lignes :
    dossier_id devient NULL (ON DELETE SET NULL), la référence reste.
    Renvoie les références supprimées.
    """
    limite = iso(quand or datetime.now())
    perimes = conn.execute(
        "SELECT id, bien_id, reference FROM dossier WHERE a_supprimer_le < ? ORDER BY reference",
        (limite,)).fetchall()
    with conn:
        for d in perimes:
            conn.execute("DELETE FROM dossier WHERE id = ?", (d["id"],))
            journaliser(conn, "purge", {}, bien_id=d["bien_id"], reference=d["reference"])
    return [d["reference"] for d in perimes]
