"""Étape 4 : appliquer les critères d'un bien à un dossier.

Entrée : les pièces extraites (étape 2), le bien (critères du gestionnaire) et,
si le candidat en a déclaré une, sa garantie (Visale ou garant physique).

Sortie : un `Resultat` avec
- la liste des critères, chacun « respecte », « non_respecte » ou « a_verifier »,
  avec son motif en clair (pas de note globale opaque) ;
- un statut de dossier :
    Incomplet    une pièce exigée manque
    À vérifier   complet, mais un contrôle de cohérence (étape 3) a trouvé
                 un problème, ou une garantie doit être examinée
    Complet      complet et cohérent
  Le statut dit si le dossier est FIABLE. Le respect du ratio de revenus est un
  critère à part : un dossier complet aux revenus trop justes reste « Complet »,
  avec le critère « revenus » non respecté. La décision reste au gestionnaire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from src.controles import controler_dossier, euros, virgule
from src.extraction import PieceExtraite
from src.models import GARANTIES, PIECES_EXIGIBLES, Bien, CritereInvalide

StatutCritere = Literal["respecte", "non_respecte", "a_verifier"]
StatutDossier = Literal["Complet", "Incomplet", "À vérifier"]

ORDRE_STATUTS = {"Complet": 0, "À vérifier": 1, "Incomplet": 2}


@dataclass(frozen=True)
class Critere:
    nom: str
    statut: StatutCritere
    motif: str


@dataclass(frozen=True)
class Resultat:
    statut: StatutDossier
    criteres: list[Critere]
    revenus_mensuels: Optional[float]   # moyenne du net à payer des bulletins
    ratio: Optional[float]              # revenus mensuels / loyer CC


def critere_completude(pieces: dict[str, PieceExtraite], bien: Bien) -> Critere:
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

    non_reconnues = sorted(nom for nom, p in pieces.items() if p.type_piece == "autre")
    remarque = (f" ; pièce(s) non reconnue(s), ignorée(s) : {', '.join(non_reconnues)}"
                if non_reconnues else "")
    if manques:
        return Critere("completude", "non_respecte", "manque : " + ", ".join(manques) + remarque)
    return Critere("completude", "respecte", "toutes les pièces exigées sont présentes" + remarque)


def revenus_mensuels(pieces: dict[str, PieceExtraite]) -> Optional[float]:
    nets = [p.salaire_net for p in pieces.values()
            if p.type_piece == "bulletin_salaire" and p.salaire_net is not None]
    return round(sum(nets) / len(nets), 2) if nets else None


def critere_revenus(revenus: Optional[float], bien: Bien, garantie: Optional[str]) -> Critere:
    if revenus is None:
        return Critere("revenus", "a_verifier", "aucun salaire net lisible sur les bulletins")
    ratio = revenus / bien.loyer_cc
    texte = (f"revenus nets {euros(revenus)} par mois, soit {virgule(ratio)} fois le loyer CC "
             f"de {euros(bien.loyer_cc)} (minimum demandé : {virgule(bien.ratio_min)})")
    # Petite tolérance pour les arrondis au centime (2 400 / 800 doit donner 3 pile).
    if ratio >= bien.ratio_min - 1e-9:
        return Critere("revenus", "respecte", texte)
    if garantie is None:
        return Critere("revenus", "non_respecte", texte + ", sans garantie déclarée")
    libelle = GARANTIES[garantie]
    if not bien.garant_accepte:
        return Critere("revenus", "non_respecte",
                       f"{texte} ; garantie {libelle} déclarée, mais la garantie n'est pas "
                       f"acceptée pour ce bien")
    return Critere("revenus", "a_verifier",
                   f"{texte} ; garantie {libelle} déclarée : justificatifs de la garantie à vérifier")


def evaluer_dossier(pieces: dict[str, PieceExtraite], bien: Bien,
                    garantie: Optional[str] = None) -> Resultat:
    """Applique les critères du bien et les contrôles de cohérence à un dossier."""
    if garantie is not None and garantie not in GARANTIES:
        raise CritereInvalide(f"garantie inconnue : {garantie} (attendu : {', '.join(GARANTIES)})")

    revenus = revenus_mensuels(pieces)
    criteres = [critere_completude(pieces, bien), critere_revenus(revenus, bien, garantie)]
    # Les contrôles de l'étape 3 deviennent des critères : ok -> respecté, sinon à vérifier.
    for c in controler_dossier(pieces):
        criteres.append(Critere(c.nom, "respecte" if c.statut == "ok" else "a_verifier", c.motif))

    if criteres[0].statut == "non_respecte":
        statut = "Incomplet"
    elif any(c.statut == "a_verifier" for c in criteres):
        statut = "À vérifier"
    else:
        statut = "Complet"
    ratio = round(revenus / bien.loyer_cc, 4) if revenus is not None else None
    return Resultat(statut, criteres, revenus, ratio)


def cle_de_tri(resultat: Resultat) -> tuple[int, float]:
    """Pour trier une liste : Complet, puis À vérifier, puis Incomplet ; ratio décroissant."""
    return ORDRE_STATUTS[resultat.statut], -(resultat.ratio or 0)
