"""Vérifie que le générateur produit bien les 20 dossiers attendus."""

import json
import zipfile

import pytest

from scripts.generer_dossiers import (
    FRAUDES, casser_siret, completer_luhn, generer, luhn_valide,
)

PIECES = {
    "piece_identite.pdf", "contrat_travail.pdf",
    "bulletin_2026_08.pdf", "bulletin_2026_09.pdf", "bulletin_2026_10.pdf",
    "avis_imposition_2026.pdf",
}


@pytest.fixture(scope="module")
def sortie(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("fake")
    generer(dossier, graine=42)
    return dossier


@pytest.fixture(scope="module")
def verite(sortie):
    return json.loads((sortie / "verite.json").read_text(encoding="utf-8"))


def test_luhn_sur_un_siret_connu():
    assert luhn_valide("79927398713")       # exemple classique de Luhn
    assert not luhn_valide("79927398710")
    siret = completer_luhn("1234567800012")
    assert luhn_valide(siret)
    assert not luhn_valide(casser_siret(siret))


def test_vingt_dossiers_complets(sortie):
    dossiers = sorted(d for d in sortie.iterdir() if d.is_dir() and d.name != "zips")
    assert len(dossiers) == 20
    for d in dossiers:
        assert {f.name for f in d.iterdir()} == PIECES
        for f in d.iterdir():
            assert f.read_bytes().startswith(b"%PDF")


def test_un_zip_par_dossier(sortie):
    zips = sorted((sortie / "zips").glob("*.zip"))
    assert len(zips) == 20
    with zipfile.ZipFile(zips[0]) as z:
        assert set(z.namelist()) == PIECES


def test_quatre_fraudes_differentes(verite):
    fraudes = {v["id"]: v["fraude"] for v in verite if v["fraude"]}
    assert len(fraudes) == 4
    assert set(fraudes.values()) == set(FRAUDES.values())


def test_dossiers_sains_coherents(verite):
    for v in verite:
        if v["fraude"]:
            continue
        a = v["attendu"]
        assert luhn_valide(a["siret"]), v["id"]
        assert 0.70 <= a["salaire_net"] / a["salaire_brut"] <= 0.85, v["id"]
        rapport = a["salaires_declares"] / (a["net_imposable"] * 12)
        assert 0.9 <= rapport <= 1.0, v["id"]
        assert a["date_debut_contrat"] < "2026-08-01", v["id"]


def test_chaque_fraude_est_detectable(verite):
    par_type = {v["fraude"]: v["attendu"] for v in verite if v["fraude"]}
    assert par_type["net_incoherent"]["salaire_net"] / par_type["net_incoherent"]["salaire_brut"] > 0.85
    assert not luhn_valide(par_type["siret_invalide"]["siret"])
    a = par_type["avis_incoherent"]
    assert a["salaires_declares"] / (a["net_imposable"] * 12) < 0.6
    assert par_type["dates_impossibles"]["date_debut_contrat"] > "2026-10-31"


def test_meme_graine_memes_dossiers(tmp_path, sortie, verite):
    bis = tmp_path / "bis"
    assert generer(bis, graine=42) == verite
    # Les PDF eux-mêmes sont identiques, octet par octet.
    for nom in PIECES:
        assert (bis / "D01" / nom).read_bytes() == (sortie / "D01" / nom).read_bytes()


def test_graine_differente_dossiers_differents(tmp_path, verite):
    assert generer(tmp_path / "autre", graine=7) != verite


def test_aucune_donnee_sensible_dans_la_verite(verite):
    # La vérité terrain ne contient que des champs autorisés comme critères.
    interdits = {"nom", "prenom", "age", "date_naissance", "sexe",
                 "nationalite", "adresse", "situation_familiale", "photo"}
    for v in verite:
        assert not interdits & set(v["attendu"])


def test_verite_par_bulletin(verite):
    for v in verite:
        assert set(v["bulletins"]) == {n for n in PIECES if n.startswith("bulletin_")}
        for b in v["bulletins"].values():
            assert b["salaire_net"] == v["attendu"]["salaire_net"]
    d17 = next(v for v in verite if v["fraude"] == "dates_impossibles")
    assert d17["bulletins"]["bulletin_2026_09.pdf"]["periode_fin"] == "31/09/2026"
    d01 = next(v for v in verite if v["id"] == "D01")
    assert d01["bulletins"]["bulletin_2026_08.pdf"]["periode_debut"] == "01/08/2026"
