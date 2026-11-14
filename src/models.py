"""Objets métier partagés : le bien et ses critères, saisis par le gestionnaire.

`Bien` vérifie ses propres critères dès sa création : on ne peut pas créer un bien
qui exige une pièce hors du décret n° 2015-1437, ni un loyer nul ou négatif.
Les critères autorisés sont uniquement financiers (loyer, ratio de revenus,
garant, Visale) et la liste des pièces : aucun champ ne porte sur la personne.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pièces que le bailleur peut exiger selon le décret n° 2015-1437 du 5 novembre 2015
# ET que l'outil sait reconnaître (voir `TypePiece` dans extraction.py).
# Clé = type de pièce, valeur = libellé affiché et catégorie du décret.
PIECES_EXIGIBLES = {
    "piece_identite": ("pièce d'identité", "justificatif d'identité"),
    "contrat_travail": ("contrat de travail", "justificatif d'activité professionnelle"),
    "bulletin_salaire": ("bulletins de salaire", "justificatif de ressources"),
    "avis_imposition": ("avis d'imposition", "justificatif de ressources"),
}
# Le décret autorise « les trois derniers bulletins de salaire », pas davantage.
NB_BULLETINS_MAX = 3


class PieceNonAutorisee(ValueError):
    """Pièce demandée hors de la liste du décret n° 2015-1437 (ou non reconnue)."""


class CritereInvalide(ValueError):
    """Critère du bien impossible (loyer nul ou négatif, ratio nul…)."""


@dataclass(frozen=True)
class Bien:
    nom: str                       # libellé du logement, ex. « T2 rue des Lilas »
    loyer_cc: float                # loyer charges comprises, en euros par mois
    ratio_min: float = 3.0         # revenus mensuels nets ≥ ratio_min × loyer CC
    garant_accepte: bool = True    # un garant peut-il compenser des revenus trop justes ?
    visale_acceptee: bool = True   # la garantie Visale peut-elle les compenser ?
    pieces_exigees: tuple[str, ...] = tuple(PIECES_EXIGIBLES)
    nb_bulletins: int = 3          # nombre de bulletins exigés (1 à 3)

    def __post_init__(self):
        if self.loyer_cc <= 0:
            raise CritereInvalide(f"loyer CC impossible : {self.loyer_cc}")
        if self.ratio_min <= 0:
            raise CritereInvalide(f"ratio minimum impossible : {self.ratio_min}")
        for type_piece in self.pieces_exigees:
            if type_piece not in PIECES_EXIGIBLES:
                raise PieceNonAutorisee(
                    f"« {type_piece} » ne figure pas parmi les pièces exigibles du décret "
                    f"n° 2015-1437 reconnues par l'outil : {', '.join(PIECES_EXIGIBLES)}")
        if not 1 <= self.nb_bulletins <= NB_BULLETINS_MAX:
            raise PieceNonAutorisee(
                f"{self.nb_bulletins} bulletins demandés : le décret n° 2015-1437 limite "
                f"aux trois derniers bulletins de salaire")
