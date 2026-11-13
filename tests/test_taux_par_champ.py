"""Tests de scripts/taux_par_champ.py sur une petite vérité inventée (sans API)."""

import json

from scripts.taux_par_champ import comparer
from src.extraction import PieceExtraite

VIDE = {nom: None for nom in PieceExtraite.model_fields}


def test_comparer_compte_les_erreurs_par_champ(tmp_path):
    verite = [{
        "id": "X1",
        "attendu": {"salaire_net": 2000.0, "employeur": None},
        "bulletins": {"bulletin_2026_09.pdf": {"periode_fin": "31/09/2026", "salaire_net": 2000.0}},
    }, {"id": "X2", "attendu": {}, "bulletins": {}}]
    extraction = {"bulletin_2026_09.pdf": {**VIDE, "type_piece": "bulletin_salaire",
                                           "periode_fin": "30/09/2026", "salaire_net": 2000.004}}
    (tmp_path / "X1.json").write_text(json.dumps(extraction), "utf-8")

    stats, erreurs, manquants = comparer(verite, tmp_path)
    assert stats["salaire_net"] == [1, 1]                      # écart < 1 centime toléré
    assert stats["bulletin_2026_09.periode_fin"] == [0, 1]     # date « corrigée » = erreur
    assert erreurs == [("X1", "bulletin_2026_09.periode_fin", "31/09/2026", "30/09/2026")]
    assert manquants == ["X2"]
