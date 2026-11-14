"""Tests de src/db.py et scripts/importer_dossiers.py (étape 5a).

Chaque test travaille dans une base neuve, créée dans un dossier temporaire.
"""

import json
import sqlite3
from datetime import datetime

import pytest

from scripts.importer_dossiers import BIEN_FICTIF, dates_de_depot, importer
from src import db
from src.controles import euros
from src.models import Bien
from tests.test_regles import EXTRACTION, dossier, pas_de_donnees

BIEN = Bien(nom="T2 test", loyer_cc=800.0, ratio_min=3.0)
DEPOT = datetime(2026, 11, 2, 9, 0)


@pytest.fixture
def conn(tmp_path):
    connexion = db.connecter(tmp_path / "test.db")
    yield connexion
    connexion.close()


@pytest.fixture
def bien_id(conn):
    return db.creer_bien(conn, BIEN)


def journal(conn, action=None):
    lignes = conn.execute("SELECT * FROM journal ORDER BY id").fetchall()
    return [l for l in lignes if action is None or l["action"] == action]


# --- Connexion -------------------------------------------------------------------------

def test_cles_etrangeres_actives_a_chaque_connexion(tmp_path):
    chemin = tmp_path / "fk.db"
    for _ in range(2):                         # première ouverture, puis réouverture
        conn = db.connecter(chemin)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute("INSERT INTO dossier (bien_id, reference, date_depot, a_supprimer_le, "
                         "importe_le) VALUES (999, 'X', '2026-11-02T09:00:00', "
                         "'2027-02-02T09:00:00', '2026-11-02T09:00:00')")
        conn.close()


# --- Montants et dates ------------------------------------------------------------------

def test_loyer_stocke_en_centimes(conn, bien_id):
    ligne = conn.execute("SELECT loyer_cc, typeof(loyer_cc) AS t FROM bien").fetchone()
    assert (ligne["loyer_cc"], ligne["t"]) == (80000, "integer")
    assert db.charger_bien(conn, bien_id) == BIEN


def test_bien_au_centime_pres_relu_a_l_identique(conn):
    bien = Bien(nom="T3", loyer_cc=500.10, ratio_min=2.85)
    assert db.charger_bien(conn, db.creer_bien(conn, bien)) == bien


def test_dates_en_iso_8601(conn, bien_id):
    db.ajouter_dossier(conn, bien_id, "D01", DEPOT, dossier())
    ligne = conn.execute("SELECT date_depot, a_supprimer_le, importe_le FROM dossier").fetchone()
    assert ligne["date_depot"] == "2026-11-02T09:00:00"
    assert ligne["a_supprimer_le"] == "2027-02-02T09:00:00"
    assert len(ligne["importe_le"]) == 19 and ligne["importe_le"][10] == "T"


def test_la_base_refuse_une_date_pas_iso(conn, bien_id):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("INSERT INTO dossier (bien_id, reference, date_depot, a_supprimer_le, "
                     "importe_le) VALUES (?, 'X', '02/11/2026 09:00', '2027-02-02T09:00:00', "
                     "'2026-11-02T09:00:00')", (bien_id,))


def test_tri_par_texte_iso_egal_tri_chronologique():
    # Le piège évité : en JJ/MM/AAAA, « 02/12/2026 » < « 03/11/2026 » en texte.
    dates = [datetime(2026, 11, 3, 8), datetime(2026, 12, 2, 9), datetime(2026, 11, 3, 10, 5)]
    assert sorted(db.iso(d) for d in dates) == [db.iso(d) for d in sorted(dates)]


@pytest.mark.parametrize("depot, attendu", [
    (datetime(2026, 11, 2, 9, 0), datetime(2027, 2, 2, 9, 0)),
    (datetime(2026, 11, 30, 17, 45), datetime(2027, 2, 28, 17, 45)),   # pas de 30 février
    (datetime(2027, 11, 30, 8, 0), datetime(2028, 2, 29, 8, 0)),       # année bissextile
])
def test_a_supprimer_le_trois_mois_apres_le_depot(depot, attendu):
    assert db.ajouter_mois(depot, 3) == attendu


# --- Dossiers, pièces, résultats --------------------------------------------------------

def test_pieces_relues_a_l_identique(conn, bien_id):
    pieces, garant = dossier(net=2000.0), dossier(net=3000.0)
    dossier_id = db.ajouter_dossier(conn, bien_id, "D01", DEPOT, pieces, garant=garant)
    assert db.charger_pieces(conn, dossier_id) == (pieces, garant)


def test_reimport_refuse(conn, bien_id):
    db.ajouter_dossier(conn, bien_id, "D01", DEPOT, dossier())
    with pytest.raises(db.DossierExistant):
        db.ajouter_dossier(conn, bien_id, "D01", DEPOT, dossier())


def test_resultat_enregistre(conn, bien_id):
    dossier_id = db.ajouter_dossier(conn, bien_id, "D01", DEPOT, dossier(net=2000.0),
                                    garant=dossier(net=3000.0))
    r = db.evaluer(conn, dossier_id)
    ligne = conn.execute("SELECT * FROM resultat WHERE dossier_id = ?", (dossier_id,)).fetchone()
    assert (ligne["criteres"], ligne["via"], ligne["groupe"]) == ("Conforme", "garant", "a")
    assert ligne["revenus_mensuels"] == 200000                 # en centimes
    assert ligne["criteres_motif"] == r.criteres_motif
    db.evaluer(conn, dossier_id)                               # recalcul : remplace, ne duplique pas
    assert conn.execute("SELECT COUNT(*) FROM resultat").fetchone()[0] == 1


# --- Journal --------------------------------------------------------------------------

def test_evaluation_journalise_criteres_et_statuts(conn):
    bien_id = db.creer_bien(conn, Bien(nom="T2", loyer_cc=800.0, ratio_min=3.0,
                                       garant_accepte=False, visale_acceptee=True))
    dossier_id = db.ajouter_dossier(conn, bien_id, "D07", DEPOT, dossier(net=2000.0), visale=True)
    db.evaluer(conn, dossier_id)
    [ligne] = journal(conn, "evaluation")
    assert (ligne["reference"], ligne["dossier_id"], ligne["bien_id"]) == ("D07", dossier_id, bien_id)
    assert json.loads(ligne["detail"]) == {
        "loyer_cc_centimes": 80000, "ratio_min": 3.0,
        "garant_accepte": False, "visale_acceptee": True,
        "garant_fourni": False, "visale_fournie": True,
        "pieces": "Complet", "criteres": "Conforme", "controles": "OK",
        "via": "Visale", "groupe": "a",
    }


def valeurs_lues(pieces) -> set[str]:
    """Tout ce qui a été lu sur les pièces, sous les formes où on pourrait l'écrire."""
    textes = set()
    for fichier, p in pieces.items():
        textes.add(fichier)
        for valeur in p.model_dump().values():
            if isinstance(valeur, float):
                textes |= {str(valeur), f"{valeur:.2f}", euros(valeur), str(round(valeur * 100))}
            elif isinstance(valeur, str) and valeur != p.type_piece:
                textes.add(valeur)
    return textes


def test_journal_sans_aucune_donnee_lue(conn, bien_id):
    pieces, garant = dossier(net=2345.67), dossier(net=3456.78)
    dossier_id = db.ajouter_dossier(conn, bien_id, "D01", DEPOT, pieces, garant=garant)
    db.evaluer(conn, dossier_id)
    lignes = journal(conn)
    assert [l["action"] for l in lignes] == ["creation_bien", "import", "evaluation"]
    interdits = valeurs_lues(pieces) | valeurs_lues(garant)
    for ligne in lignes:
        detail = json.loads(ligne["detail"])
        assert set(detail) <= db.CLES_JOURNAL
        for texte in interdits:
            assert texte not in ligne["detail"], (ligne["action"], texte)


@pytest.mark.parametrize("cle", ["salaire_net", "employeur", "siret", "criteres_motif", "alertes"])
def test_journaliser_refuse_une_donnee_lue(conn, cle):
    with pytest.raises(db.DetailInterdit):
        db.journaliser(conn, "evaluation", {"criteres": "Conforme", cle: "x"})
    assert journal(conn) == []


# --- Purge ------------------------------------------------------------------------------

def test_purger(conn, bien_id):
    ancien = db.ajouter_dossier(conn, bien_id, "D01", datetime(2026, 11, 2, 9, 0), dossier(),
                                garant=dossier())
    recent = db.ajouter_dossier(conn, bien_id, "D02", datetime(2026, 11, 20, 9, 0), dossier())
    for d in (ancien, recent):
        db.evaluer(conn, d)

    # Le jour même de la limite (02/02/2027 9 h) : rien n'est supprimé.
    assert db.purger(conn, datetime(2027, 2, 2, 9, 0)) == []
    # Juste après : D01 part, avec ses pièces (candidat et garant) et son résultat.
    assert db.purger(conn, datetime(2027, 2, 2, 9, 1)) == ["D01"]

    compter = lambda table: conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE dossier_id = ?", (ancien,)).fetchone()[0]
    assert conn.execute("SELECT reference FROM dossier").fetchall()[0]["reference"] == "D02"
    assert compter("piece") == 0 and compter("resultat") == 0
    assert conn.execute("SELECT COUNT(*) FROM piece").fetchone()[0] == 6    # les 6 de D02

    # Le journal de D01 est intact : dossier_id à NULL, référence conservée.
    lignes_d01 = [l for l in journal(conn) if l["reference"] == "D01"]
    assert [l["action"] for l in lignes_d01] == ["import", "evaluation", "purge"]
    assert all(l["dossier_id"] is None for l in lignes_d01)
    assert all(l["dossier_id"] == recent for l in journal(conn) if l["reference"] == "D02")


# --- Import des 22 dossiers fictifs -----------------------------------------------------

def test_dates_de_depot_sur_trois_jours():
    refs = [f"D{n:02d}" for n in range(1, 23)]
    dates = dates_de_depot(refs)
    assert dates == dates_de_depot(refs)                                  # graine fixe
    assert {d.date() for d in dates.values()} == {datetime(2026, 11, j).date() for j in (2, 3, 4)}
    assert all(9 <= d.hour < 18 or (d.hour, d.minute) == (18, 0) for d in dates.values())
    assert sorted(refs, key=dates.get) != refs                            # ordre mélangé


@pas_de_donnees
def test_importer_les_22_dossiers(conn):
    assert importer(conn, EXTRACTION) == [f"D{n:02d}" for n in range(1, 23)]
    assert importer(conn, EXTRACTION) == []                               # pas de doublon
    bien_id = db.trouver_bien(conn, BIEN_FICTIF.nom)

    liste = db.liste_triee(conn, bien_id)
    par_groupe = {}
    for d in liste:
        par_groupe.setdefault(d["groupe"], set()).add(d["reference"])
    assert par_groupe["b"] == {"D05", "D17", "D22"}
    assert par_groupe["c"] == {"D21"}
    assert par_groupe["d"] == {"D01", "D09", "D13", "D19", "D20"}
    assert len(par_groupe["a"]) == 13
    # Ordre : groupe, puis date de dépôt (comparée en vraies dates, pas en texte).
    cles = [(d["groupe"], datetime.fromisoformat(d["date_depot"])) for d in liste]
    assert cles == sorted(cles)

    actions = [l["action"] for l in journal(conn)]
    assert actions.count("import") == actions.count("evaluation") == 22
    assert actions.count("creation_bien") == 1
