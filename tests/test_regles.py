"""Tests de src/regles.py et src/models.py (étape 4).

Trois statuts indépendants par dossier :
    pieces     Complet / Incomplet
    criteres   Conforme / Non conforme / Non évaluable (+ motif chiffré)
    controles  OK / À vérifier (+ liste des alertes)

1. Le bien : critères saisis par le gestionnaire, pièces limitées au décret 2015-1437.
2. Les dossiers fictifs réels (réponses de Claude enregistrées, sans API).
3. Cas inventés : complétude, ratio, garant, Visale, revenus illisibles, tri.
"""

import json
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import pytest

from src.extraction import PieceExtraite
from src.models import Bien, CritereInvalide, PieceNonAutorisee
from src.regles import ALERTE_REVENUS_ILLISIBLES, cle_de_tri, evaluer_dossier, groupe

EXTRACTION = Path("data/fake/extraction")
VERITE = Path("data/fake/verite.json")

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


def dossier(net: float | None = 2400.0, nb_bulletins: int = 3, avec_avis: bool = True,
            siret: str = "73282932000074"):
    """Petit dossier cohérent, net mensuel `net` (brut = net / 0,78). net=None : illisible."""
    brut = round((net or 2400.0) / 0.78, 2)
    imposable = round((net or 2400.0) + brut * 0.029, 2)
    commun = dict(employeur="ACME", siret=siret, type_contrat="CDI",
                  date_debut_contrat="01/01/2020")
    pieces = {
        "piece_identite.pdf": piece(type_piece="piece_identite", validite_identite="01/01/2032"),
        "contrat_travail.pdf": piece(type_piece="contrat_travail", **commun),
    }
    for mois in range(10, 10 - nb_bulletins, -1):
        pieces[f"bulletin_2026_{mois:02d}.pdf"] = piece(
            type_piece="bulletin_salaire", **commun,
            periode_debut=f"01/{mois:02d}/2026", periode_fin=f"28/{mois:02d}/2026",
            salaire_brut=brut, salaire_net=net, net_imposable=imposable)
    if avec_avis:
        annuel = round(imposable * 12)
        pieces["avis_imposition_2026.pdf"] = piece(
            type_piece="avis_imposition", annee_revenus=2025,
            salaires_declares=annuel, revenu_fiscal_reference=round(annuel * 0.9))
    return pieces


BIEN = Bien(nom="T2 test", loyer_cc=800.0, ratio_min=3.0)
SANS_GARANT = Bien(nom="T2", loyer_cc=800.0, garant_accepte=False)
SANS_VISALE = Bien(nom="T2", loyer_cc=800.0, visale_acceptee=False)


def statuts(r):
    return r.pieces, r.criteres, r.controles


# --- 1. Le bien -------------------------------------------------------------------

def test_bien_par_defaut():
    assert set(BIEN.pieces_exigees) == {
        "piece_identite", "contrat_travail", "bulletin_salaire", "avis_imposition"}
    assert BIEN.nb_bulletins == 3
    assert BIEN.garant_accepte and BIEN.visale_acceptee


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


# --- 2. Les vrais dossiers fictifs (loyer 800 €, ratio 3) ---------------------------

@pas_de_donnees
def test_les_vingt_deux_dossiers():
    non_conformes = {"D01", "D09", "D13", "D19", "D20", "D21"}   # ratio < 3
    a_verifier = {"D05", "D09", "D13", "D17",   # dossiers piégés
                  "D21", "D22"}                 # sans avis ; net illisible sur un bulletin
    for v in json.loads(VERITE.read_text("utf-8")):
        r = evaluer_dossier(charger(v["id"]), BIEN)
        attendu = ("Incomplet" if v["id"] == "D21" else "Complet",
                   "Non conforme" if v["id"] in non_conformes else "Conforme",
                   "À vérifier" if v["id"] in a_verifier else "OK")
        assert statuts(r) == attendu, (v["id"], r.criteres_motif, r.alertes)


@pas_de_donnees
def test_motif_chiffre_sur_un_vrai_dossier():
    r = evaluer_dossier(charger("D01"), BIEN)          # net 2 166,54 € par mois
    assert r.revenus_mensuels == pytest.approx(2166.54)
    assert r.ratio == pytest.approx(2166.54 / 800, abs=0.001)
    for morceau in ("2 166,54 €", "2,71", "800,00 €", "3,00"):
        assert morceau in r.criteres_motif


@pas_de_donnees
def test_source_du_revenu_dans_le_motif():
    r = evaluer_dossier(charger("D01"), BIEN)
    assert "(moyenne du net à payer avant impôt des 3 derniers bulletins)" in r.criteres_motif


@pas_de_donnees
def test_d21_sans_avis():
    r = evaluer_dossier(charger("D21"), BIEN)
    assert r.pieces_manquantes == ["avis d'imposition"]
    assert any(a.startswith("bulletins/avis (non vérifiable)") for a in r.alertes)


@pas_de_donnees
def test_d22_net_illisible_sur_un_bulletin():
    pieces = charger("D22")
    r = evaluer_dossier(pieces, BIEN)
    nets = [pieces[n].salaire_net for n in ("bulletin_2026_08.pdf", "bulletin_2026_10.pdf")]
    assert pieces["bulletin_2026_09.pdf"].salaire_net is None
    assert r.revenus_mensuels == pytest.approx(sum(nets) / 2)       # moyenne des 2 lisibles
    assert ("de 2 bulletin(s) sur 3, net illisible sur bulletin_2026_09.pdf"
            in r.criteres_motif)
    assert r.alertes == ["net/brut (non vérifiable) : bulletin_2026_09.pdf : "
                         "net à payer absent ou illisible"]


@pas_de_donnees
def test_alertes_reprennent_les_controles():
    r = evaluer_dossier(charger("D09"), BIEN)
    assert len(r.alertes) == 1 and "SIRET" in r.alertes[0] and "82620450500068" in r.alertes[0]


@pas_de_donnees
def test_aucun_libelle_refuse():
    """Ni statut ni motif ni alerte ne dit « Refusé »."""
    jeux = [(charger(f"D{i:02d}"), b, g, v) for i in range(1, 21)
            for b in (BIEN, SANS_GARANT, SANS_VISALE)
            for g, v in ((None, False), (dossier(net=1000.0), True))]
    jeux.append((dossier(net=None, avec_avis=False), BIEN, dossier(net=None), False))
    for pieces, bien, garant, visale in jeux:
        r = evaluer_dossier(pieces, bien, garant=garant, visale=visale)
        textes = [r.pieces, r.pieces_motif, r.criteres, r.criteres_motif, r.controles, *r.alertes]
        assert not any("refus" in t.lower() for t in textes), textes


# --- 3. Cas inventés ------------------------------------------------------------------

def test_dossier_parfait():
    r = evaluer_dossier(dossier(), BIEN)
    assert statuts(r) == ("Complet", "Conforme", "OK")
    assert r.alertes == [] and r.via is None
    assert "3,00" in r.criteres_motif


@pytest.mark.parametrize("net, attendu", [(2400.0, "Conforme"), (2399.0, "Non conforme")])
def test_ratio_limite(net, attendu):
    assert evaluer_dossier(dossier(net=net), BIEN).criteres == attendu


def test_non_conforme_motif_chiffre():
    r = evaluer_dossier(dossier(net=2000.0), BIEN)
    assert r.criteres == "Non conforme"
    assert "2 000,00 €" in r.criteres_motif and "2,50" in r.criteres_motif
    assert "ni garant ni Visale" in r.criteres_motif


def test_source_du_revenu_cas_inventes():
    assert "des 3 derniers bulletins" in evaluer_dossier(dossier(), BIEN).criteres_motif
    un_seul = Bien(nom="T2", loyer_cc=800.0, nb_bulletins=1)
    assert "du dernier bulletin)" in evaluer_dossier(dossier(nb_bulletins=1), un_seul).criteres_motif
    pieces = dossier()
    pieces["bulletin_2026_09.pdf"] = pieces["bulletin_2026_09.pdf"].model_copy(
        update={"salaire_net": None})
    r = evaluer_dossier(pieces, BIEN)
    assert r.criteres == "Conforme" and r.controles == "À vérifier"
    assert "de 2 bulletin(s) sur 3, net illisible sur bulletin_2026_09.pdf" in r.criteres_motif


def test_source_du_revenu_du_garant():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=3000.0))
    assert r.criteres_motif.count("moyenne du net à payer avant impôt des 3 derniers bulletins") == 2


def test_piece_manquante_independante_des_criteres():
    r = evaluer_dossier(dossier(avec_avis=False), BIEN)
    assert r.pieces == "Incomplet" and "avis d'imposition" in r.pieces_motif
    assert r.criteres == "Conforme"
    # Sans avis, le contrôle bulletins/avis ne peut pas se faire : alerte.
    assert r.controles == "À vérifier"


def test_bulletins_insuffisants():
    r = evaluer_dossier(dossier(nb_bulletins=2), BIEN)
    assert r.pieces == "Incomplet"
    assert "2 bulletin(s) sur 3" in r.pieces_motif
    assert r.pieces_manquantes == ["bulletins de salaire : 2 bulletin(s) sur 3"]


def test_bien_moins_exigeant():
    bien = Bien(nom="Studio", loyer_cc=500, nb_bulletins=1,
                pieces_exigees=("piece_identite", "bulletin_salaire"))
    assert evaluer_dossier(dossier(nb_bulletins=1, avec_avis=False), bien).pieces == "Complet"


def test_trois_statuts_independants():
    d = dossier(net=2000.0, avec_avis=False, siret="123")
    r = evaluer_dossier(d, BIEN, visale=True)
    assert statuts(r) == ("Incomplet", "Conforme", "À vérifier")


# Garant

def test_conforme_via_garant():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=3000.0))
    assert r.criteres == "Conforme" and r.via == "garant"
    assert "via garant" in r.criteres_motif and "3 000,00 €" in r.criteres_motif
    assert r.controles == "OK"


def test_garant_sous_le_ratio():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=2100.0))
    assert r.criteres == "Non conforme" and r.via is None
    assert "garant" in r.criteres_motif and "2,62" in r.criteres_motif


def test_garant_non_accepte_par_le_bien():
    r = evaluer_dossier(dossier(net=2000.0), SANS_GARANT, garant=dossier(net=5000.0))
    assert r.criteres == "Non conforme"
    assert "garant fourni mais non accepté pour ce bien" in r.criteres_motif


def test_alertes_du_garant_prefixees():
    r = evaluer_dossier(dossier(), BIEN, garant=dossier(net=3000.0, siret="123"))
    assert r.controles == "À vérifier"
    assert any(a.startswith("garant : SIRET") for a in r.alertes)


def test_candidat_suffisant_garant_sans_effet():
    r = evaluer_dossier(dossier(), BIEN, garant=dossier(net=5000.0))
    assert r.criteres == "Conforme" and r.via is None


# Visale

def test_conforme_via_visale():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, visale=True)
    assert r.criteres == "Conforme" and r.via == "Visale"
    assert "via Visale" in r.criteres_motif


def test_visale_non_acceptee():
    r = evaluer_dossier(dossier(net=2000.0), SANS_VISALE, visale=True)
    assert r.criteres == "Non conforme"
    assert "Visale fournie mais non acceptée pour ce bien" in r.criteres_motif


def test_garant_et_visale_le_garant_prime():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=3000.0), visale=True)
    assert r.via == "garant"


def test_garant_insuffisant_mais_visale():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=2100.0), visale=True)
    assert r.criteres == "Conforme" and r.via == "Visale"


# Revenus illisibles : l'extraction a échoué, cela ne prouve rien contre le candidat.

def test_revenus_illisibles_non_evaluable():
    r = evaluer_dossier(dossier(net=None), BIEN)
    assert r.criteres == "Non évaluable"
    assert r.criteres_motif == "revenus illisibles, ratio impossible à calculer"
    assert r.ratio is None and r.revenus_mensuels is None
    assert r.controles == "À vérifier"
    assert ALERTE_REVENUS_ILLISIBLES == "revenus illisibles, vérifier les bulletins à la main"
    assert ALERTE_REVENUS_ILLISIBLES in r.alertes


def test_revenus_du_garant_illisibles():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=None))
    assert r.criteres == "Non évaluable"
    assert "garant" in r.criteres_motif
    assert f"garant : {ALERTE_REVENUS_ILLISIBLES}" in r.alertes


def test_revenus_du_garant_illisibles_mais_visale():
    r = evaluer_dossier(dossier(net=2000.0), BIEN, garant=dossier(net=None), visale=True)
    assert r.criteres == "Conforme" and r.via == "Visale"
    assert f"garant : {ALERTE_REVENUS_ILLISIBLES}" in r.alertes


# Tri

def test_groupes():
    a = evaluer_dossier(dossier(), BIEN)
    b = evaluer_dossier(dossier(siret="123"), BIEN)
    c1 = evaluer_dossier(dossier(avec_avis=False), BIEN)
    c2 = evaluer_dossier(dossier(net=None), BIEN)
    c3 = evaluer_dossier(dossier(net=1000.0, avec_avis=False), BIEN)   # Incomplet + Non conforme
    d = evaluer_dossier(dossier(net=1000.0), BIEN)
    assert [groupe(r) for r in (a, b, c1, c2, c3, d)] == ["a", "b", "c", "c", "c", "d"]


def test_tri_par_groupe_puis_date_de_depot():
    jour = lambda j: datetime(2026, 11, j, 9, 0)
    a_recent = (evaluer_dossier(dossier(net=5000.0), BIEN), jour(10))
    a_ancien = (evaluer_dossier(dossier(net=2400.0), BIEN), jour(2))
    b = (evaluer_dossier(dossier(siret="123"), BIEN), jour(1))
    c = (evaluer_dossier(dossier(avec_avis=False), BIEN), jour(3))
    d = (evaluer_dossier(dossier(net=1000.0), BIEN), jour(1))
    melange = [d, c, a_recent, b, a_ancien]
    ordre = sorted(melange, key=lambda x: cle_de_tri(x[0], x[1]))
    # Dans le groupe (a), le plus ancien d'abord, même s'il a un ratio plus faible.
    assert ordre == [a_ancien, a_recent, b, c, d]
