"""Étape 4 : appliquer les critères d'un bien à un dossier.

Entrée :
    pieces   pièces extraites du candidat (étape 2)
    bien     critères du gestionnaire (models.Bien)
    garant   pièces extraites du garant, s'il y en a un
    visale   True si le gestionnaire a vu le certificat Visale du candidat

Sortie : un `Resultat` avec TROIS statuts indépendants, chacun avec son motif :

    pieces     Complet / Incomplet            + liste des pièces manquantes
    criteres   Conforme / Non conforme / Non évaluable   + motif chiffré
    controles  OK / À vérifier                + liste des alertes

Règle des critères :
    1. revenus du candidat >= ratio minimum x loyer CC      -> Conforme
    2. sinon garant accepté par le bien et garant au ratio   -> Conforme « via garant »
    3. sinon Visale fournie et acceptée par le bien          -> Conforme « via Visale »
    4. sinon                                                 -> Non conforme
    Des revenus illisibles (candidat, ou garant quand il est le dernier recours)
    donnent « Non évaluable » : c'est l'extraction qui a échoué, cela ne prouve
    rien contre le candidat. Une alerte demande alors de vérifier à la main.

Aucun libellé ne dit « Refusé » : la décision reste au gestionnaire.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from src.controles import Controle, controler_dossier, euros, virgule
from src.extraction import PieceExtraite
from src.models import PIECES_EXIGIBLES, Bien

StatutPieces = Literal["Complet", "Incomplet"]
StatutCriteres = Literal["Conforme", "Non conforme", "Non évaluable"]
StatutControles = Literal["OK", "À vérifier"]

MOTIF_REVENUS_ILLISIBLES = "revenus illisibles, ratio impossible à calculer"
ALERTE_REVENUS_ILLISIBLES = "revenus illisibles, vérifier les bulletins à la main"

LIBELLES_CONTROLES = {"siret": "SIRET", "net_brut": "net/brut",
                      "bulletins_vs_avis": "bulletins/avis", "dates": "dates"}


@dataclass(frozen=True)
class Resultat:
    pieces: StatutPieces
    pieces_manquantes: list[str]
    pieces_motif: str
    criteres: StatutCriteres
    criteres_motif: str
    via: Optional[Literal["garant", "Visale"]]   # par où le dossier est conforme, si pas en direct
    controles: StatutControles
    alertes: list[str]
    detail_controles: list[Controle]             # les 4 contrôles du candidat, pour la vue détaillée
    revenus_mensuels: Optional[float]            # moyenne du net à payer des bulletins
    ratio: Optional[float]                       # revenus mensuels / loyer CC


# --- Pièces ---------------------------------------------------------------------

def pieces_manquantes(pieces: dict[str, PieceExtraite], bien: Bien) -> list[str]:
    presentes = [p.type_piece for p in pieces.values()]
    manques = []
    for type_piece in bien.pieces_exigees:
        libelle = PIECES_EXIGIBLES[type_piece][0]
        if type_piece == "bulletin_salaire":
            nb = presentes.count("bulletin_salaire")
            if nb < bien.nb_bulletins:
                manques.append(f"{libelle} : {nb} bulletin(s) sur {bien.nb_bulletins}")
        elif type_piece not in presentes:
            manques.append(libelle)
    return manques


def motif_pieces(pieces: dict[str, PieceExtraite], manques: list[str]) -> str:
    texte = ("manque : " + ", ".join(manques)) if manques else "toutes les pièces exigées sont présentes"
    non_reconnues = sorted(nom for nom, p in pieces.items() if p.type_piece == "autre")
    if non_reconnues:
        texte += f" ; pièce(s) non reconnue(s), ignorée(s) : {', '.join(non_reconnues)}"
    return texte


# --- Critères financiers --------------------------------------------------------------

def revenus_mensuels(pieces: dict[str, PieceExtraite]) -> Optional[float]:
    nets = [p.salaire_net for p in pieces.values()
            if p.type_piece == "bulletin_salaire" and p.salaire_net is not None]
    return round(sum(nets) / len(nets), 2) if nets else None


def atteint(revenus: float, bien: Bien) -> bool:
    # Petite tolérance pour les arrondis au centime (2 400 / 800 doit donner 3 pile).
    return revenus / bien.loyer_cc >= bien.ratio_min - 1e-9


def chiffres(qui: str, revenus: float, bien: Bien) -> str:
    """« candidat : revenus 2 000,00 €/mois = 2,50 × loyer CC 800,00 € (minimum 3,00) »"""
    return (f"{qui} : revenus {euros(revenus)}/mois = {virgule(revenus / bien.loyer_cc)} × "
            f"loyer CC {euros(bien.loyer_cc)} (minimum {virgule(bien.ratio_min)})")


def evaluer_criteres(revenus: Optional[float], revenus_garant: Optional[float], bien: Bien,
                     avec_garant: bool, visale: bool):
    """Renvoie (statut, motif, via)."""
    if revenus is None:
        return "Non évaluable", MOTIF_REVENUS_ILLISIBLES, None
    texte = chiffres("candidat", revenus, bien)
    if atteint(revenus, bien):
        return "Conforme", texte, None

    raisons = []          # pourquoi aucun recours n'a suffi, en clair
    garant_illisible = False
    if avec_garant and not bien.garant_accepte:
        raisons.append("garant fourni mais non accepté pour ce bien")
    elif avec_garant and revenus_garant is None:
        garant_illisible = True
        raisons.append("revenus du garant illisibles, ratio du garant impossible à calculer")
    elif avec_garant:
        texte_garant = chiffres("garant", revenus_garant, bien)
        if atteint(revenus_garant, bien):
            return "Conforme", f"{texte} ; via garant, {texte_garant}", "garant"
        raisons.append(f"{texte_garant}, sous le minimum")

    if visale and bien.visale_acceptee:
        return "Conforme", f"{texte} ; via Visale (certificat vu par le gestionnaire)", "Visale"
    if visale:
        raisons.append("Visale fournie mais non acceptée pour ce bien")

    if garant_illisible:
        # Le seul recours restant n'a pas pu être lu : on ne peut pas conclure.
        return "Non évaluable", f"{texte} ; " + " ; ".join(raisons), None
    if not raisons:
        raisons.append("ni garant ni Visale")
    return "Non conforme", f"{texte} ; " + " ; ".join(raisons), None


# --- Contrôles ------------------------------------------------------------------

def alertes_controles(controles: list[Controle], prefixe: str = "") -> list[str]:
    alertes = []
    for c in controles:
        if c.statut == "ok":
            continue
        libelle = LIBELLES_CONTROLES.get(c.nom, c.nom)
        if c.statut == "non_verifiable":
            libelle += " (non vérifiable)"
        alertes.append(f"{prefixe}{libelle} : {c.motif}")
    return alertes


# --- Évaluation complète -----------------------------------------------------------

def evaluer_dossier(pieces: dict[str, PieceExtraite], bien: Bien,
                    garant: Optional[dict[str, PieceExtraite]] = None,
                    visale: bool = False) -> Resultat:
    """Les trois statuts d'un dossier pour un bien."""
    manques = pieces_manquantes(pieces, bien)
    revenus = revenus_mensuels(pieces)
    # Le garant n'est examiné que si le bien accepte les garants.
    garant_utile = garant is not None and bien.garant_accepte
    revenus_garant = revenus_mensuels(garant) if garant_utile else None

    criteres, motif, via = evaluer_criteres(revenus, revenus_garant, bien,
                                            avec_garant=garant is not None, visale=visale)

    detail = controler_dossier(pieces)
    alertes = alertes_controles(detail)
    if revenus is None:
        alertes.append(ALERTE_REVENUS_ILLISIBLES)
    if garant_utile:
        alertes += alertes_controles(controler_dossier(garant), prefixe="garant : ")
        if revenus_garant is None:
            alertes.append(f"garant : {ALERTE_REVENUS_ILLISIBLES}")

    return Resultat(
        pieces="Incomplet" if manques else "Complet",
        pieces_manquantes=manques,
        pieces_motif=motif_pieces(pieces, manques),
        criteres=criteres,
        criteres_motif=motif,
        via=via,
        controles="À vérifier" if alertes else "OK",
        alertes=alertes,
        detail_controles=detail,
        revenus_mensuels=revenus,
        ratio=round(revenus / bien.loyer_cc, 4) if revenus is not None else None,
    )


# --- Tri -------------------------------------------------------------------------

def groupe(r: Resultat) -> Literal["a", "b", "c", "d"]:
    """(a) Complet + Conforme + OK, (b) Complet + Conforme + À vérifier,
    (c) Incomplet ou Non évaluable, (d) Non conforme (dossier complet)."""
    if r.pieces == "Complet" and r.criteres == "Conforme":
        return "a" if r.controles == "OK" else "b"
    if r.pieces == "Incomplet" or r.criteres == "Non évaluable":
        return "c"
    return "d"


def cle_de_tri(r: Resultat, date_depot: datetime) -> tuple[str, datetime]:
    """Groupe a, b, c, d ; dans chaque groupe, le dépôt le plus ancien en premier."""
    return groupe(r), date_depot
