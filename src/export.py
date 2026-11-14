"""Étape 6 : export CSV de la sélection affichée (dossiers filtrés d'un bien).

Le fichier exporté sort de l'outil : il échappe à la purge à 3 mois. Il ne contient
donc que des références, des dates de gestion et des statuts, jamais une donnée lue
sur une pièce : ni revenus, ni ratio (calculé à partir des revenus), ni motif (qui
cite des montants), ni employeur, ni nom de fichier.

Format pensé pour Excel en français : séparateur « ; », encodage UTF-8 avec BOM
(sans le BOM, Excel affiche mal les accents), dates en JJ/MM/AAAA HH:MM.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Iterable, Mapping, Optional

from src.db import DECISIONS

COLONNES = ["Dossier", "Dépôt", "Groupe", "Pièces", "Critères", "Contrôles",
            "Décision", "Date de décision", "Supprimé après le"]


def date_fr(texte_iso: Optional[str]) -> str:
    """« 2026-11-02T09:00:00 » -> « 02/11/2026 09:00 » ; vide si absent."""
    return datetime.fromisoformat(texte_iso).strftime("%d/%m/%Y %H:%M") if texte_iso else ""


def ligne_csv(d: Mapping) -> list[str]:
    """Une ligne de db.liste_triee() -> les valeurs des COLONNES."""
    suppression = ("jamais (retenu)" if d["decision"] == "retenu"
                   else date_fr(d["a_supprimer_le"]))
    return [d["reference"], date_fr(d["date_depot"]), d["groupe"] or "", d["pieces"] or "",
            d["criteres"] or "", d["controles"] or "",
            DECISIONS.get(d["decision"], "sans décision"), date_fr(d["decision_le"]),
            suppression]


def csv_selection(lignes: Iterable[Mapping]) -> bytes:
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";", lineterminator="\r\n")
    ecrivain.writerow(COLONNES)
    for d in lignes:
        ecrivain.writerow(ligne_csv(d))
    return tampon.getvalue().encode("utf-8-sig")
