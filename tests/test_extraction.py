"""Tests de src/extraction.py SANS appeler l'API (un faux client simule Claude).

Le vrai test contre l'API se fait avec scripts/evaluer_extraction.py.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.extraction import (ErreurExtraction, PieceExtraite, bloc_fichier, date_iso,
                            extraire_dossier, extraire_piece, synthese_dossier)

D01 = Path("data/fake/D01")


def piece(**champs):
    """PieceExtraite avec null pour tous les champs non précisés."""
    vides = {nom: None for nom in PieceExtraite.model_fields if nom != "type_piece"}
    return PieceExtraite(**{**vides, **champs})


class FauxClient:
    """Imite client.beta.messages.parse et retient les arguments reçus."""

    def __init__(self, resultat=None, stop_reason="end_turn"):
        self.resultat = resultat or piece(type_piece="autre")
        self.stop_reason = stop_reason
        self.appels = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.appels.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.resultat)


def test_bloc_pdf_et_image(tmp_path):
    bloc = bloc_fichier(D01 / "contrat_travail.pdf")
    assert bloc["type"] == "document"
    assert bloc["source"]["media_type"] == "application/pdf"
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG")
    assert bloc_fichier(img)["type"] == "image"


def test_format_refuse(tmp_path):
    f = tmp_path / "notes.docx"
    f.write_bytes(b"x")
    with pytest.raises(ErreurExtraction):
        bloc_fichier(f)


def test_requete_envoyee():
    client = FauxClient()
    extraire_piece(D01 / "bulletin_2026_08.pdf", client)
    appel = client.appels[0]
    assert appel["output_format"] is PieceExtraite
    assert appel["messages"][0]["content"][0]["type"] == "document"


def test_refus_et_reponse_tronquee():
    with pytest.raises(ErreurExtraction):
        extraire_piece(D01 / "contrat_travail.pdf", FauxClient(stop_reason="refusal"))
    with pytest.raises(ErreurExtraction):
        extraire_piece(D01 / "contrat_travail.pdf", FauxClient(stop_reason="max_tokens"))


def test_extraire_dossier_lit_les_six_pdf():
    client = FauxClient()
    assert len(extraire_dossier(D01, client)) == 6
    assert len(client.appels) == 6


def test_schema_sans_donnees_personnelles():
    interdits = {"nom", "prenom", "adresse", "date_naissance", "sexe", "nationalite",
                 "situation_familiale", "photo"}
    assert not interdits & set(PieceExtraite.model_fields)


def test_date_iso():
    assert date_iso("01/12/2015") == "2015-12-01"
    assert date_iso("31/09/2026") is None  # date impossible
    assert date_iso(None) is None


def test_synthese_dossier():
    pieces = {
        "contrat_travail.pdf": piece(
            type_piece="contrat_travail", employeur="Guilbert S.A.", siret="863 794 020 00074",
            type_contrat="CDI", date_debut_contrat="01/12/2015"),
        "bulletin_2026_10.pdf": piece(
            type_piece="bulletin_salaire", salaire_brut=2817.61, salaire_net=2166.54,
            net_imposable=2248.25),
        "avis_imposition_2026.pdf": piece(
            type_piece="avis_imposition", salaires_declares=26552, revenu_fiscal_reference=23897),
    }
    s = synthese_dossier(pieces)
    assert s["siret"] == "86379402000074"
    assert s["date_debut_contrat"] == "2015-12-01"
    assert s["date_fin_contrat"] is None
    assert s["salaire_net"] == 2166.54
    assert s["revenu_fiscal_reference"] == 23897
