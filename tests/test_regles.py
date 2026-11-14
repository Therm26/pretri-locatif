"""Tests de src/regles.py et src/models.py (étape 4), écrits AVANT le code.

1. Le bien : critères saisis par le gestionnaire, pièces limitées au décret 2015-1437.
2. Les dossiers fictifs réels (réponses de Claude enregistrées, sans API).
3. Cas inventés : complétude, ratio de revenus, garantie, ordre de tri.
"""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from src.extraction import PieceExtraite
from src.models import Bien, CritereInvalide, PieceNonAutorisee
from src.regles import cle_de_tri, evaluer_dossier

EXTRACTION = Path("data/fake/extraction")
VERITE = Path("data/fake/verite.json")
PIEGES = {"D05", "D09", "D13", "D17"}

pas_de_donnees = pytest.mark.skipif(
    not EXTRACTION.exists() or not VERITE.exists(),
    reason="lancer d'abord scripts.generer_dossiers puis scripts.evaluer_extraction",
)


def charger(id_: str) -> dict[str, PieceExtraite]:
    brut = json.loads((EXTRACTION / f"{id_}.json").read_text("utf-8"))
    return {nom: PieceExtraite(**d) for nom, d in brut.items()}


def piece(**champs) -> PieceExtraite:
    vides = {nom: None for nom in PieceExtraite.model_fields if nom != "type_piece"}
    return PieceExtraite(**{**vides, **champs})


def dossier(net: float = 2400.0, nb_bulletins: int = 3, avec_avis: bool = True):
    """Petit dossier cohérent, net mensuel `net` (brut = net / 0,78)."""
    brut = round(net / 0.78, 2)
    commun = dict(employeur="ACME", siret="73282932000074", type_contrat="CDI",
                  date_debut_contrat="01/01/2020")
    pieces = {
        "piece_identite.pdf": piece(type_piece="piece_identite", validite_identite="01/01/2032"),
        "contrat_travail.pdf": piece(type_piece="contrat_travail", **commun),
    }
    for mois in range(10, 10 - nb_bulletins, -1):
        pieces[f"bulletin_2026_{mois:02d}.pdf"] = piece(
            type_piece="bulletin_salaire", **commun,
            periode_debut=f"01/{mois:02d}/2026", periode_fin=f"28/{mois:02d}/2026",
            salaire_brut=brut, salaire_net=net, net_imposable=round(net + brut * 0.029, 2))
    if avec_avis:
        annuel = round((net + brut * 0.029) * 12)
        pieces["avis_imposition_2026.pdf"] = piece(
            type_piece="avis_imposition", annee_revenus=2025,
            salaires_declares=annuel, revenu_fiscal_reference=round(annuel * 0.9))
    return pieces


def critere(resultat, nom):
    return next(c for c in resultat.criteres if c.nom == nom)


BIEN = Bien(nom="T2 test", loyer_cc=800.0, ratio_min=3.0, garant_accepte=True)


# --- 1. Le bien -------------------------------------------------------------------

def test_bien_par_defaut_exige_les_pieces_du_decret():
    assert set(BIEN.pieces_exigees) == {
        "piece_identite", "contrat_travail", "bulletin_salaire", "avis_imposition"}
    assert BIEN.nb_bulletins == 3


@pytest.mark.parametrize("interdite", ["releve_bancaire", "photo", "casier_judiciaire", "autre"])
def test_piece_hors_decret_refusee(interdite):
    with pytest.raises(PieceNonAutorisee, match="2015-1437"):
        Bien(nom="T2", loyer_cc=800, pieces_exigees=("piece_identite", interdite))


def test_plus_de_trois_bulletins_refuse():
    with pytest.raises(PieceNonAutorisee, match="trois derniers"):
        Bien(nom="T2", loyer_cc=800, nb_bulletins=4)


@pytest.mark.parametrize("modif", [{"loyer_cc": 0}, {"loyer_cc": -5}, {"ratio_min": 0}])
def test_criteres_absurdes_refuses(modif):
    with pytest.raises(CritereInvalide):
        Bien(**{"nom": "T2", "loyer_cc": 800, **modif})


def test_bien_sans_critere_interdit():
    """Le bien ne peut porter aucun critère discriminatoire."""
    interdits = {"age", "sexe", "nationalite", "nom_candidat", "adresse_origine",
                 "situation_familiale", "photo"}
    assert not interdits & {f.name for f in fields(Bien)}


# --- 2. Les vrais dossiers fictifs -------------------------------------------------

@pas_de_donnees
def test_statuts_des_vingt_dossiers():
    for v in json.loads(VERITE.read_text("utf-8")):
        r = evaluer_dossier(charger(v["id"]), BIEN)
        attendu = "À vérifier" if v["id"] in PIEGES else "Complet"
        assert r.statut == attendu, (v["id"], [(c.nom, c.statut, c.motif) for c in r.criteres])


@pas_de_donnees
def test_ratio_calcule_sur_le_net_des_bulletins():
    r = evaluer_dossier(charger("D01"), BIEN)          # net 2 166,54 € par mois
    assert r.revenus_mensuels == pytest.approx(2166.54)
    assert r.ratio == pytest.approx(2166.54 / 800, abs=0.001)
    assert critere(r, "revenus").statut == "non_respecte"   # 2,71 < 3


@pas_de_donnees
def test_anomalie_de_controle_reprise_dans_les_criteres():
    r = evaluer_dossier(charger("D09"), BIEN)
    c = critere(r, "siret")
    assert c.statut == "a_verifier" and "82620450500068" in c.motif


# --- 3. Cas inventés ----------------------------------------------------------------

def test_tous_les_criteres_affiches_avec_un_motif():
    r = evaluer_dossier(dossier(), BIEN)
    assert [c.nom for c in r.criteres] == [
        "completude", "revenus", "siret", "net_brut", "bulletins_vs_avis", "dates"]
    assert all(c.motif for c in r.criteres)
    assert r.statut == "Complet"


def test_piece_manquante_incomplet():
    r = evaluer_dossier(dossier(avec_avis=False), BIEN)
    assert r.statut == "Incomplet"
    c = critere(r, "completude")
    assert c.statut == "non_respecte" and "avis d'imposition" in c.motif


def test_bulletins_insuffisants_incomplet():
    r = evaluer_dossier(dossier(nb_bulletins=2), BIEN)
    assert r.statut == "Incomplet"
    assert "2 bulletin(s) sur 3" in critere(r, "completude").motif


def test_bien_moins_exigeant():
    bien = Bien(nom="Studio", loyer_cc=500, nb_bulletins=1,
                pieces_exigees=("piece_identite", "bulletin_salaire"))
    r = evaluer_dossier(dossier(nb_bulletins=1, avec_avis=False), bien)
    assert critere(r, "completude").statut == "respecte"


def test_incomplet_prime_sur_a_verifier():
    d = dossier(avec_avis=False)
    d["contrat_travail.pdf"] = d["contrat_travail.pdf"].model_copy(update={"siret": "123"})
    assert evaluer_dossier(d, BIEN).statut == "Incomplet"


@pytest.mark.parametrize("net, attendu", [(2400.0, "respecte"), (2399.0, "non_respecte")])
def test_ratio_limite(net, attendu):
    assert critere(evaluer_dossier(dossier(net=net), BIEN), "revenus").statut == attendu


def test_revenus_insuffisants_garantie_acceptee():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garantie="visale")
    c = critere(r, "revenus")
    assert c.statut == "a_verifier" and "Visale" in c.motif
    assert r.statut == "À vérifier"


def test_revenus_insuffisants_garant_refuse_par_le_bien():
    bien = Bien(nom="T2", loyer_cc=800, garant_accepte=False)
    c = critere(evaluer_dossier(dossier(net=2000.0), bien, garantie="garant_physique"), "revenus")
    assert c.statut == "non_respecte" and "pas accepté" in c.motif


def test_garantie_inconnue_refusee():
    with pytest.raises(CritereInvalide):
        evaluer_dossier(dossier(), BIEN, garantie="caution_de_tonton")


def test_revenus_suffisants_garantie_sans_effet():
    assert critere(evaluer_dossier(dossier(), BIEN, garantie="visale"), "revenus").statut == "respecte"


def test_revenus_complet_mais_insuffisants_reste_complet():
    """Le statut dit si le dossier est fiable ; le ratio est un critère à part."""
    r = evaluer_dossier(dossier(net=1500.0), BIEN)
    assert r.statut == "Complet"
    assert critere(r, "revenus").statut == "non_respecte"


def test_tri_par_statut_puis_ratio():
    complet_haut = evaluer_dossier(dossier(net=3000.0), BIEN)
    complet_bas = evaluer_dossier(dossier(net=2500.0), BIEN)
    a_verifier = evaluer_dossier(dossier(net=2000.0), BIEN, garantie="visale")
    incomplet = evaluer_dossier(dossier(net=5000.0, avec_avis=False), BIEN)
    ordre = sorted([incomplet, a_verifier, complet_bas, complet_haut], key=cle_de_tri)
    assert ordre == [complet_haut, complet_bas, a_verifier, incomplet]
