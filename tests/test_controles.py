"""Tests de src/controles.py (étape 3), écrits AVANT le code.

Deux familles :
1. Les vrais dossiers fictifs, à partir des réponses de Claude déjà enregistrées
   dans data/fake/extraction/ (aucun appel API). Chaque dossier piégé doit lever
   SON anomalie ; les 16 dossiers sains ne doivent en lever aucune.
2. De petits cas inventés pour les limites (seuils, données manquantes, dates).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from src.controles import DateInvalide, controler_dossier, lire_date
from src.extraction import PieceExtraite

EXTRACTION = Path("data/fake/extraction")
VERITE = Path("data/fake/verite.json")

# Dossier piégé -> contrôle qui doit le signaler.
PIEGES = {
    "D05": "net_brut",
    "D09": "siret",
    "D13": "bulletins_vs_avis",
    "D17": "dates",
}

pas_de_donnees = pytest.mark.skipif(
    not EXTRACTION.exists() or not VERITE.exists(),
    reason="lancer d'abord scripts.generer_dossiers puis scripts.evaluer_extraction",
)


def charger(id_: str) -> dict[str, PieceExtraite]:
    brut = json.loads((EXTRACTION / f"{id_}.json").read_text("utf-8"))
    return {nom: PieceExtraite(**d) for nom, d in brut.items()}


def anomalies(controles) -> set[str]:
    return {c.nom for c in controles if c.statut == "anomalie"}


def piece(**champs) -> PieceExtraite:
    vides = {nom: None for nom in PieceExtraite.model_fields if nom != "type_piece"}
    return PieceExtraite(**{**vides, **champs})


def dossier_sain(**modifs_bulletin) -> dict[str, PieceExtraite]:
    """Petit dossier cohérent : contrat, un bulletin, un avis."""
    bulletin = dict(type_piece="bulletin_salaire", employeur="ACME", siret="73282932000074",
                    type_contrat="CDI", date_debut_contrat="01/01/2020",
                    periode_debut="01/09/2026", periode_fin="30/09/2026",
                    salaire_brut=3000.0, salaire_net=2340.0, net_imposable=2427.0)
    bulletin.update(modifs_bulletin)
    return {
        "contrat_travail.pdf": piece(type_piece="contrat_travail", employeur="ACME",
                                     siret="732 829 320 00074", type_contrat="CDI",
                                     date_debut_contrat="01/01/2020"),
        "bulletin_2026_09.pdf": piece(**bulletin),
        "avis_imposition_2026.pdf": piece(type_piece="avis_imposition",
                                          salaires_declares=28500, revenu_fiscal_reference=25650),
    }


# --- 1. Les dossiers piégés --------------------------------------------------

@pas_de_donnees
@pytest.mark.parametrize("id_, controle", sorted(PIEGES.items()))
def test_dossier_piege_signale(id_, controle):
    resultat = controler_dossier(charger(id_))
    assert controle in anomalies(resultat), [(c.nom, c.statut, c.motif) for c in resultat]


@pas_de_donnees
@pytest.mark.parametrize("id_, controle", sorted(PIEGES.items()))
def test_motif_lisible(id_, controle):
    """Pas de note opaque : l'anomalie dit pourquoi, en clair."""
    c = next(c for c in controler_dossier(charger(id_)) if c.nom == controle)
    assert c.motif and len(c.motif) > 15


@pas_de_donnees
def test_d17_les_deux_dates_impossibles():
    motif = next(c for c in controler_dossier(charger("D17")) if c.nom == "dates").motif
    assert "31/09/2026" in motif          # date qui n'existe pas
    assert "01/03/2027" in motif          # embauche après les bulletins


@pas_de_donnees
def test_dossiers_sains_sans_anomalie():
    verite = json.loads(VERITE.read_text("utf-8"))
    for v in verite:
        if v["fraude"]:
            continue
        resultat = controler_dossier(charger(v["id"]))
        assert anomalies(resultat) == set(), (v["id"], [(c.nom, c.motif) for c in resultat])
        # D21 (sans avis) et D22 (net taché) : une donnée manque, aucune accusation.
        non_verifiables = {c.nom for c in resultat if c.statut == "non_verifiable"}
        attendu = {"D21": {"bulletins_vs_avis"}, "D22": {"net_brut"}}.get(v["id"], set())
        assert non_verifiables == attendu, v["id"]


# --- 2. Cas limites inventés ---------------------------------------------------

def test_quatre_controles_toujours_presents():
    assert [c.nom for c in controler_dossier(dossier_sain())] == [
        "siret", "net_brut", "bulletins_vs_avis", "dates"]
    assert anomalies(controler_dossier(dossier_sain())) == set()


@pytest.mark.parametrize("net, attendu", [
    (2100.0, "ok"),        # 0,70 pile : accepté
    (2550.0, "ok"),        # 0,85 pile : accepté
    (2090.0, "anomalie"),  # sous 0,70
    (2560.0, "anomalie"),  # au-dessus de 0,85
])
def test_seuils_net_brut(net, attendu):
    c = controler_dossier(dossier_sain(salaire_net=net))[1]
    assert (c.nom, c.statut) == ("net_brut", attendu)


def test_siret_mal_forme_ou_different():
    assert "siret" in anomalies(controler_dossier(dossier_sain(siret="7328293200007")))  # 13 chiffres
    # Deux SIRET valides mais différents entre contrat et bulletin.
    assert "siret" in anomalies(controler_dossier(dossier_sain(siret="73282932000082")))


def test_siret_la_poste():
    """SIREN de La Poste (356 000 000) : la clé de Luhn ne s'applique pas au SIRET,
    la somme des chiffres doit être multiple de 5. Ce SIRET échoue à Luhn mais est valide."""
    d = dossier_sain(siret="35600000000015")
    d["contrat_travail.pdf"] = d["contrat_travail.pdf"].model_copy(update={"siret": "35600000000015"})
    assert controler_dossier(d)[0].statut == "ok"


def test_avis_absent_non_verifiable():
    d = dossier_sain()
    del d["avis_imposition_2026.pdf"]
    c = controler_dossier(d)[2]
    assert (c.nom, c.statut) == ("bulletins_vs_avis", "non_verifiable")


def test_avis_trop_bas():
    d = dossier_sain()
    d["avis_imposition_2026.pdf"] = d["avis_imposition_2026.pdf"].model_copy(
        update={"salaires_declares": 13000})
    assert "bulletins_vs_avis" in anomalies(controler_dossier(d))


def test_lire_date_absente_ou_invalide():
    assert lire_date("01/12/2015") == date(2015, 12, 1)
    assert lire_date(None) is None
    assert lire_date("") is None
    with pytest.raises(DateInvalide):
        lire_date("31/09/2026")
    with pytest.raises(DateInvalide):
        lire_date("septembre 2026")


def test_periode_a_l_envers():
    c = controler_dossier(dossier_sain(periode_debut="30/09/2026", periode_fin="01/09/2026"))[3]
    assert c.statut == "anomalie"


def test_date_absente_n_est_pas_une_anomalie():
    """Une date manquante rend le contrôle non vérifiable, sans accuser le dossier."""
    c = controler_dossier(dossier_sain(periode_fin=None))[3]
    assert c.statut == "non_verifiable"
