"""Tests de l'étape 6 : décision du gestionnaire, bien Ouvert / Loué, purge, export CSV.

Chaque test part d'une base neuve. Les dates sont passées explicitement (`quand=`) pour
simuler le temps qui passe sans attendre 3 mois.
"""

import csv
import io
import json
import sqlite3
from datetime import datetime

import pytest

from src import db
from src.depot import deposer
from src.export import COLONNES, csv_selection
from src.models import Bien
from tests.test_regles import dossier as pieces_saines

BIEN = Bien(nom="T2 test", loyer_cc=800.0, ratio_min=3.0)
DEPOT = datetime(2026, 11, 2, 9, 0)                  # suppression par défaut : 2027-02-02T09:00:00


@pytest.fixture
def conn(tmp_path):
    connexion = db.connecter(tmp_path / "test.db")
    yield connexion
    connexion.close()


@pytest.fixture
def bien_id(conn):
    return db.creer_bien(conn, BIEN)


def ajouter(conn, bien_id, reference="D01", depot=DEPOT):
    dossier_id = db.ajouter_dossier(conn, bien_id, reference, depot, pieces_saines())
    db.evaluer(conn, dossier_id)
    return dossier_id


def dossier(conn, dossier_id):
    return db.charger_dossier(conn, dossier_id)


def references(conn):
    return [l["reference"] for l in conn.execute("SELECT reference FROM dossier ORDER BY reference")]


def journal(conn, action):
    return conn.execute("SELECT * FROM journal WHERE action = ? ORDER BY id", (action,)).fetchall()


# --- Sans décision : le délai de secours ----------------------------------------------

def test_sans_decision_purge_juste_apres_depot_plus_3_mois(conn, bien_id):
    ajouter(conn, bien_id)
    assert dossier(conn, 1)["a_supprimer_le"] == "2027-02-02T09:00:00"
    assert db.purger(conn, datetime(2027, 2, 2, 9, 0)) == []          # pas avant
    assert db.purger(conn, datetime(2027, 2, 2, 9, 1)) == ["D01"]     # juste après


# --- Non retenu ---------------------------------------------------------------------------

def test_non_retenu_decision_plus_3_mois(conn, bien_id):
    d = ajouter(conn, bien_id)
    assert db.decider(conn, d, "non_retenu", quand=datetime(2026, 12, 15, 10, 0)) \
        == "2027-03-15T10:00:00"
    ligne = dossier(conn, d)
    assert (ligne["decision"], ligne["decision_le"]) == ("non_retenu", "2026-12-15T10:00:00")
    # Pas purgé 3 mois après le dépôt, seulement 3 mois après la décision.
    assert db.purger(conn, datetime(2027, 2, 2, 9, 1)) == []
    assert db.purger(conn, datetime(2027, 3, 15, 10, 0)) == []
    assert db.purger(conn, datetime(2027, 3, 15, 10, 1)) == ["D01"]


def test_non_retenu_ne_raccourcit_jamais(conn, bien_id):
    d = ajouter(conn, bien_id)
    db.decider(conn, d, "non_retenu", quand=datetime(2027, 1, 10, 9, 0))       # -> 2027-04-10
    # Décision antidatée : + 3 mois tomberait avant la date fixée, qui ne bouge pas.
    assert db.decider(conn, d, "non_retenu", quand=datetime(2026, 12, 1, 9, 0)) \
        == "2027-04-10T09:00:00"
    # Une décision plus tardive repousse la date.
    assert db.decider(conn, d, "non_retenu", quand=datetime(2027, 2, 20, 9, 0)) \
        == "2027-05-20T09:00:00"
    assert dossier(conn, d)["decision_le"] == "2027-02-20T09:00:00"


def test_decision_avant_le_depot_refusee(conn, bien_id):
    d = ajouter(conn, bien_id)
    with pytest.raises(db.DecisionInvalide, match="avant le dépôt"):
        db.decider(conn, d, "non_retenu", quand=datetime(2026, 11, 1))
    with pytest.raises(db.DecisionInvalide, match="inconnue"):
        db.decider(conn, d, "refuse", quand=datetime(2026, 12, 1))
    assert dossier(conn, d)["decision"] is None


# --- Retenu -----------------------------------------------------------------------------

def test_retenu_jamais_purge(conn, bien_id):
    d = ajouter(conn, bien_id)
    assert db.decider(conn, d, "retenu", quand=datetime(2026, 11, 20)) is None
    assert dossier(conn, d)["a_supprimer_le"] is None
    assert db.purger(conn, datetime(2040, 1, 1)) == []
    # Même avec une date remplie à la main par erreur.
    conn.execute("UPDATE dossier SET a_supprimer_le = '2027-01-01T00:00:00' WHERE id = ?", (d,))
    conn.commit()
    assert db.purger(conn, datetime(2040, 1, 1)) == []
    assert references(conn) == ["D01"]


def test_retenu_puis_non_retenu(conn, bien_id):
    d = ajouter(conn, bien_id)
    db.decider(conn, d, "retenu", quand=datetime(2026, 11, 20))
    assert db.decider(conn, d, "non_retenu", quand=datetime(2027, 6, 1, 9, 0)) \
        == "2027-09-01T09:00:00"
    assert db.purger(conn, datetime(2027, 9, 1, 9, 0)) == []
    assert db.purger(conn, datetime(2027, 9, 1, 9, 1)) == ["D01"]


def test_non_retenu_puis_retenu(conn, bien_id):
    d = ajouter(conn, bien_id)
    db.decider(conn, d, "non_retenu", quand=datetime(2026, 11, 20))
    db.decider(conn, d, "retenu", quand=datetime(2026, 11, 25))
    assert db.purger(conn, datetime(2040, 1, 1)) == []


# --- Bien passé à Loué -----------------------------------------------------------------

def test_loue_bascule_les_dossiers_sans_decision(conn, bien_id):
    ajouter(conn, bien_id, "D01")
    ajouter(conn, bien_id, "D02")
    cloture = datetime(2026, 12, 10, 18, 0)
    assert db.changer_statut_bien(conn, bien_id, "Loué", quand=cloture) == ["D01", "D02"]
    assert db.statut_bien(conn, bien_id) == ("Loué", "2026-12-10T18:00:00")
    for d in (1, 2):
        ligne = dossier(conn, d)
        assert ligne["decision"] == "non_retenu"
        assert ligne["decision_le"] == "2026-12-10T18:00:00"
        assert ligne["a_supprimer_le"] == "2027-03-10T18:00:00"
    # Plus purgés à dépôt + 3 mois, seulement après clôture + 3 mois.
    assert db.purger(conn, datetime(2027, 2, 2, 9, 1)) == []
    assert db.purger(conn, datetime(2027, 3, 10, 18, 0)) == []
    assert db.purger(conn, datetime(2027, 3, 10, 18, 1)) == ["D01", "D02"]


def test_loue_ne_raccourcit_pas_une_date_plus_tardive(conn, bien_id):
    d = ajouter(conn, bien_id)
    conn.execute("UPDATE dossier SET a_supprimer_le = '2027-08-01T00:00:00' WHERE id = ?", (d,))
    conn.commit()
    db.changer_statut_bien(conn, bien_id, "Loué", quand=datetime(2026, 12, 10))
    assert dossier(conn, d)["a_supprimer_le"] == "2027-08-01T00:00:00"


def test_loue_sans_effet_sur_les_decisions_deja_prises(conn, bien_id):
    retenu, non_retenu = ajouter(conn, bien_id, "D01"), ajouter(conn, bien_id, "D02")
    db.decider(conn, retenu, "retenu", quand=datetime(2026, 11, 20))
    db.decider(conn, non_retenu, "non_retenu", quand=datetime(2026, 11, 21, 9, 0))
    avant = [tuple(dossier(conn, d)) for d in (retenu, non_retenu)]
    assert db.changer_statut_bien(conn, bien_id, "Loué", quand=datetime(2027, 1, 5)) == []
    assert [tuple(dossier(conn, d)) for d in (retenu, non_retenu)] == avant


def test_loue_journal_une_ligne_par_dossier_sans_donnee_lue(conn, bien_id):
    ajouter(conn, bien_id, "D01")
    ajouter(conn, bien_id, "D02")
    d3 = ajouter(conn, bien_id, "D03")
    db.decider(conn, d3, "retenu", quand=datetime(2026, 11, 20))
    db.changer_statut_bien(conn, bien_id, "Loué", quand=datetime(2026, 12, 10))
    auto = journal(conn, "non_retenu_auto")
    assert [l["reference"] for l in auto] == ["D01", "D02"]
    for l in auto:
        detail = json.loads(l["detail"])
        assert detail["decision"] == "non_retenu"
        assert set(detail) == {"decision", "decision_le", "a_supprimer_le"}
    assert len(journal(conn, "cloture_bien")) == 1
    # Décision manuelle et bascule automatique sont deux actions distinctes.
    assert [l["reference"] for l in journal(conn, "decision")] == ["D03"]


def test_bien_loue_refuse_les_nouveaux_dossiers(conn, bien_id):
    db.changer_statut_bien(conn, bien_id, "Loué", quand=datetime(2026, 12, 10))
    with pytest.raises(db.BienLoue):
        ajouter(conn, bien_id)
    appels = []
    with pytest.raises(db.BienLoue):                 # refusé avant toute lecture payante
        deposer(conn, bien_id, DEPOT, {"a.pdf": b"%PDF"}, extraire=appels.append)
    assert appels == []


def test_reouverture(conn, bien_id):
    d = ajouter(conn, bien_id)
    db.changer_statut_bien(conn, bien_id, "Loué", quand=datetime(2026, 12, 10))
    db.changer_statut_bien(conn, bien_id, "Ouvert", quand=datetime(2027, 1, 2))
    assert db.statut_bien(conn, bien_id) == ("Ouvert", None)
    assert dossier(conn, d)["decision"] == "non_retenu"          # la décision reste
    ajouter(conn, bien_id, "D02")                                # de nouveau ouvert
    assert len(journal(conn, "reouverture_bien")) == 1


# --- Le journal survit aux purges ---------------------------------------------------------

def test_journal_survit_a_toutes_les_purges(conn, bien_id):
    sans, non, retenu_puis_non = (ajouter(conn, bien_id, r) for r in ("D01", "D02", "D03"))
    db.decider(conn, non, "non_retenu", quand=datetime(2026, 11, 20))
    db.decider(conn, retenu_puis_non, "retenu", quand=datetime(2026, 11, 20))
    db.decider(conn, retenu_puis_non, "non_retenu", quand=datetime(2026, 11, 21))
    db.changer_statut_bien(conn, bien_id, "Loué", quand=datetime(2026, 12, 1))   # bascule D01
    nb_lignes = conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
    assert db.purger(conn, datetime(2030, 1, 1)) == ["D01", "D02", "D03"]
    assert conn.execute("SELECT COUNT(*) FROM dossier").fetchone()[0] == 0
    lignes = conn.execute("SELECT * FROM journal WHERE action != 'purge'").fetchall()
    assert len(lignes) == nb_lignes                               # rien n'a disparu
    assert all(l["dossier_id"] is None for l in lignes)
    assert {l["reference"] for l in lignes if l["reference"]} == {"D01", "D02", "D03"}


# --- Migration d'une base créée avant l'étape 6 ------------------------------------------

ANCIEN_SCHEMA = """
CREATE TABLE bien (id INTEGER PRIMARY KEY, nom TEXT NOT NULL, loyer_cc INTEGER NOT NULL,
  ratio_min REAL NOT NULL, garant_accepte INTEGER NOT NULL, visale_acceptee INTEGER NOT NULL,
  pieces_exigees TEXT NOT NULL, nb_bulletins INTEGER NOT NULL, cree_le TEXT NOT NULL);
CREATE TABLE dossier (id INTEGER PRIMARY KEY, bien_id INTEGER NOT NULL REFERENCES bien(id),
  reference TEXT NOT NULL, date_depot TEXT NOT NULL, a_supprimer_le TEXT NOT NULL,
  visale INTEGER NOT NULL DEFAULT 0, importe_le TEXT NOT NULL, UNIQUE (bien_id, reference));
CREATE TABLE piece (id INTEGER PRIMARY KEY,
  dossier_id INTEGER NOT NULL REFERENCES dossier(id) ON DELETE CASCADE,
  role TEXT NOT NULL, fichier TEXT NOT NULL, type_piece TEXT NOT NULL, donnees TEXT NOT NULL);
INSERT INTO bien VALUES (1, 'Ancien', 80000, 3.0, 1, 1, '["bulletin_salaire"]', 3,
  '2026-11-01T09:00:00');
INSERT INTO dossier VALUES (7, 1, 'D07', '2026-11-02T09:00:00', '2027-02-02T09:00:00', 0,
  '2026-11-02T09:00:00');
INSERT INTO piece VALUES (1, 7, 'candidat', 'a.pdf', 'autre', '{}');
"""


def test_migration_d_une_ancienne_base(tmp_path):
    chemin = tmp_path / "ancienne.db"
    ancienne = sqlite3.connect(chemin)
    ancienne.executescript(ANCIEN_SCHEMA)
    ancienne.close()

    conn = db.connecter(chemin)
    assert db.statut_bien(conn, 1) == ("Ouvert", None)
    ligne = dossier(conn, 7)
    assert (ligne["reference"], ligne["a_supprimer_le"], ligne["decision"]) == \
        ("D07", "2027-02-02T09:00:00", None)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # Les nouvelles règles marchent, et la cascade vers les pièces aussi.
    assert db.decider(conn, 7, "retenu", quand=datetime(2026, 11, 20)) is None
    db.decider(conn, 7, "non_retenu", quand=datetime(2026, 11, 21))
    assert db.purger(conn, datetime(2030, 1, 1)) == ["D07"]
    assert conn.execute("SELECT COUNT(*) FROM piece").fetchone()[0] == 0
    conn.close()
    db.connecter(chemin).close()                      # rouvrir une base migrée : rien à faire


# --- Export CSV ----------------------------------------------------------------------------

def test_export_csv_sans_donnee_lue(conn, bien_id):
    d1, d2 = ajouter(conn, bien_id, "D01"), ajouter(conn, bien_id, "D02")
    db.decider(conn, d1, "retenu", quand=datetime(2026, 11, 20, 14, 30))
    db.decider(conn, d2, "non_retenu", quand=datetime(2026, 11, 21, 9, 0))
    contenu = csv_selection(db.liste_triee(conn, bien_id))
    assert contenu.startswith("﻿".encode("utf-8"))           # BOM pour Excel
    lignes = list(csv.reader(io.StringIO(contenu.decode("utf-8-sig")), delimiter=";"))
    assert lignes[0] == COLONNES
    par_ref = {l[0]: dict(zip(COLONNES, l)) for l in lignes[1:]}
    assert par_ref["D01"]["Décision"] == "retenu"
    assert par_ref["D01"]["Date de décision"] == "20/11/2026 14:30"
    assert par_ref["D01"]["Supprimé après le"] == "jamais (retenu)"
    assert par_ref["D02"]["Supprimé après le"] == "21/02/2027 09:00"
    texte = contenu.decode("utf-8-sig")
    for interdit in ("€", "ACME", ".pdf", "revenus", "Ratio", "2400"):
        assert interdit not in texte
