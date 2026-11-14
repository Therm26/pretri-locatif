"""Tests de src/depot.py et des fonctions de db.py utilisées par l'interface (étape 5b).

Pas d'appel à l'API : un faux extracteur renvoie, pour chaque nom de fichier, la réponse
de Claude déjà enregistrée dans data/fake/extraction/.
"""

import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.importer_dossiers import charger_extraction
from src import db
from src.depot import (NB_FICHIERS_MAX, DepotInvalide, deposer, lire_envoi,
                       prochaine_reference)
from src.extraction import ErreurExtraction
from src.models import Bien
from tests.test_regles import EXTRACTION, pas_de_donnees

ZIPS = Path("data/fake/zips")
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


def zip_de(fichiers: dict[str, bytes]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        for nom, contenu in fichiers.items():
            z.writestr(nom, contenu)
    return tampon.getvalue()


class FauxExtracteur:
    """Renvoie la réponse enregistrée du dossier `reference` ; retient les fichiers lus."""

    def __init__(self, reference: str, echec_sur: str | None = None):
        self.reponses = charger_extraction(EXTRACTION / f"{reference}.json")
        self.echec_sur = echec_sur
        self.lus: list[str] = []

    def __call__(self, chemin: Path):
        assert chemin.exists()                       # le fichier a bien été écrit sur disque
        self.lus.append(chemin.name)
        if chemin.name == self.echec_sur:
            raise ErreurExtraction(f"{chemin.name} : illisible")
        return self.reponses[chemin.name]


def actions(conn):
    return [l["action"] for l in conn.execute("SELECT action FROM journal ORDER BY id")]


# --- lire_envoi ----------------------------------------------------------------------

def test_fichiers_separes():
    fichiers, ignores = lire_envoi([("bulletin.pdf", b"%PDF"), ("scan.JPG", b"img"),
                                    ("notes.docx", b"x")])
    assert sorted(fichiers) == ["bulletin.pdf", "scan.JPG"]
    assert ignores == ["notes.docx"]


def test_zip_ouvert_et_aplati():
    contenu = zip_de({"dossier/bulletin.pdf": b"%PDF", "dossier/lisez-moi.txt": b"x",
                      "__MACOSX/dossier/._bulletin.pdf": b"x", ".DS_Store": b"x"})
    fichiers, ignores = lire_envoi([("D01.zip", contenu)])
    assert fichiers == {"bulletin.pdf": b"%PDF"}
    assert ignores == ["lisez-moi.txt"]


def test_zip_nom_piege_reste_un_simple_nom():
    fichiers, _ = lire_envoi([("x.zip", zip_de({"../../evil.pdf": b"%PDF"}))])
    assert list(fichiers) == ["evil.pdf"]


def test_doublon_refuse():
    with pytest.raises(DepotInvalide, match="deux fichiers"):
        lire_envoi([("a.zip", zip_de({"1/bulletin.pdf": b"a", "2/bulletin.pdf": b"b"}))])


def test_zip_illisible_refuse():
    with pytest.raises(DepotInvalide, match="zip lisible"):
        lire_envoi([("faux.zip", b"pas un zip")])


def test_trop_de_fichiers_refuse():
    with pytest.raises(DepotInvalide, match="trop gros"):
        lire_envoi([(f"p{i}.pdf", b"x") for i in range(NB_FICHIERS_MAX + 1)])


def test_vrai_zip_fictif():
    fichiers, ignores = lire_envoi([("D01.zip", (ZIPS / "D01.zip").read_bytes())])
    assert len(fichiers) == 6 and not ignores


# --- prochaine_reference -------------------------------------------------------------------

def test_reference_suit_le_journal(conn, bien_id):
    assert prochaine_reference(conn, bien_id) == "D01"
    db.journaliser(conn, "extraction_echec", {}, bien_id=bien_id, reference="D07")
    assert prochaine_reference(conn, bien_id) == "D08"
    autre = db.creer_bien(conn, Bien(nom="Autre", loyer_cc=500.0))
    assert prochaine_reference(conn, autre) == "D01"     # numérotation propre à chaque bien


# --- deposer ---------------------------------------------------------------------------

@pas_de_donnees
def test_depot_zip_complet(conn, bien_id):
    candidat, _ = lire_envoi([("D02.zip", (ZIPS / "D02.zip").read_bytes())])
    extracteur = FauxExtracteur("D02")
    vus = []
    dossier_id, ref, r = deposer(conn, bien_id, DEPOT, candidat, extraire=extracteur,
                                 progression=lambda i, t, nom: vus.append((i, t)))
    assert ref == "D01"                                   # référence attribuée par l'outil
    assert sorted(extracteur.lus) == sorted(candidat)
    assert vus == [(i, 6) for i in range(1, 7)]
    assert db.charger_dossier(conn, dossier_id)["groupe"] == "a"
    assert (r.pieces, r.criteres, r.controles) == ("Complet", "Conforme", "OK")
    assert actions(conn)[-3:] == ["extraction", "import", "evaluation"]


@pas_de_donnees
def test_depot_avec_garant_et_visale(conn, bien_id):
    # D01 est sous le ratio ; le garant (pièces de D02) le rend conforme.
    candidat, _ = lire_envoi([("D01.zip", (ZIPS / "D01.zip").read_bytes())])
    garant, _ = lire_envoi([("D02.zip", (ZIPS / "D02.zip").read_bytes())])
    reponses = {**FauxExtracteur("D01").reponses}
    reponses_garant = FauxExtracteur("D02").reponses
    lus = []

    def extraire(chemin):
        lus.append(chemin.name)
        # le candidat est lu en premier (6 fichiers), puis le garant
        return (reponses if len(lus) <= 6 else reponses_garant)[chemin.name]

    dossier_id, _, r = deposer(conn, bien_id, DEPOT, candidat, garant, extraire=extraire)
    assert (r.criteres, r.via) == ("Conforme", "garant")
    _, pieces_garant = db.charger_pieces(conn, dossier_id)
    assert len(pieces_garant) == 6


@pas_de_donnees
def test_visale_cochee_apres_coup(conn, bien_id):
    candidat, _ = lire_envoi([("D01.zip", (ZIPS / "D01.zip").read_bytes())])
    dossier_id, _, r = deposer(conn, bien_id, DEPOT, candidat, extraire=FauxExtracteur("D01"))
    assert r.criteres == "Non conforme"
    r = db.modifier_visale(conn, dossier_id, True)
    assert (r.criteres, r.via) == ("Conforme", "Visale")
    assert db.charger_dossier(conn, dossier_id)["visale"] == 1
    ligne = conn.execute("SELECT * FROM journal WHERE action = 'visale'").fetchone()
    assert json.loads(ligne["detail"]) == {"visale_fournie": True}


@pas_de_donnees
def test_echec_extraction_rien_enregistre(conn, bien_id):
    candidat, _ = lire_envoi([("D02.zip", (ZIPS / "D02.zip").read_bytes())])
    with pytest.raises(ErreurExtraction):
        deposer(conn, bien_id, DEPOT, candidat,
                extraire=FauxExtracteur("D02", echec_sur="bulletin_2026_09.pdf"))
    assert conn.execute("SELECT COUNT(*) FROM dossier").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM piece").fetchone()[0] == 0
    echec = conn.execute("SELECT * FROM journal WHERE action = 'extraction_echec'").fetchone()
    assert echec["reference"] == "D01"
    assert "bulletin" not in echec["detail"]              # pas de nom de fichier au journal
    assert prochaine_reference(conn, bien_id) == "D02"     # D01 n'est pas réattribuée


def test_depot_vide_refuse(conn, bien_id):
    with pytest.raises(DepotInvalide, match="aucun"):
        deposer(conn, bien_id, DEPOT, {}, extraire=lambda c: None)


def test_depot_dans_le_futur_refuse(conn, bien_id):
    with pytest.raises(DepotInvalide, match="futur"):
        deposer(conn, bien_id, datetime.now() + timedelta(hours=1), {"a.pdf": b"%PDF"},
                extraire=lambda c: None)


def test_lister_biens(conn, bien_id):
    autre = db.creer_bien(conn, Bien(nom="Autre", loyer_cc=500.0))
    assert [b["id"] for b in db.lister_biens(conn)] == [autre, bien_id]
