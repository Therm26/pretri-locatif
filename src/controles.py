"""Étape 3 : contrôles de cohérence d'un dossier (repérer ce qui ressemble à une fraude).

Tout est déterministe, en Python pur : aucun appel à Claude ici. On part des pièces
extraites à l'étape 2 (`PieceExtraite`) et on renvoie QUATRE contrôles, toujours
dans le même ordre, chacun avec un statut et un motif écrit en clair :

    siret              clé de contrôle du SIRET, et même SIRET sur toutes les pièces
    net_brut           net à payer / brut entre 0,70 et 0,85 sur chaque bulletin
    bulletins_vs_avis  salaires de l'avis proches de 12 mois de net imposable
    dates              dates qui existent, dans un ordre possible

Statuts :
    ok              le contrôle passe
    anomalie        incohérence trouvée : le gestionnaire doit vérifier
    non_verifiable  une donnée manque, on ne peut pas conclure (ce n'est pas une accusation)

Une anomalie n'est pas une preuve de fraude : la décision reste au gestionnaire.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from src.extraction import PieceExtraite

Statut = Literal["ok", "anomalie", "non_verifiable"]

NET_BRUT_MIN = 0.70
NET_BRUT_MAX = 0.85
# L'avis porte sur l'année précédente : une augmentation ou un changement d'emploi
# crée un écart légitime. On ne signale qu'au-delà de 20 %.
ECART_AVIS_MAX = 0.20
SIREN_LA_POSTE = "356000000"


@dataclass(frozen=True)
class Controle:
    nom: str
    statut: Statut
    motif: str


class DateInvalide(ValueError):
    """Le texte ressemble à une date mais n'en est pas une (ex. « 31/09/2026 »)."""


# --- Outils -------------------------------------------------------------------

def luhn_valide(nombre: str) -> bool:
    """Vrai si la suite de chiffres respecte l'algorithme de Luhn (SIREN, SIRET)."""
    total = 0
    for i, caractere in enumerate(reversed(nombre)):
        chiffre = int(caractere)
        if i % 2 == 1:  # un chiffre sur deux, en partant de la droite, est doublé
            chiffre *= 2
            if chiffre > 9:
                chiffre -= 9
        total += chiffre
    return total % 10 == 0


def siret_valide(siret: str) -> bool:
    """14 chiffres, SIREN (9 premiers) valide selon Luhn, et SIRET complet valide.

    Exception connue : les établissements de La Poste (SIREN 356 000 000) ont un
    SIRET dont la somme des chiffres doit être un multiple de 5, au lieu de Luhn.
    """
    if len(siret) != 14 or not siret.isdigit():
        return False
    if siret[:9] == SIREN_LA_POSTE:
        return sum(int(c) for c in siret) % 5 == 0
    return luhn_valide(siret[:9]) and luhn_valide(siret)


def lire_date(texte: str | None) -> date | None:
    """« 01/12/2015 » -> date(2015, 12, 1).

    Renvoie None si la date est ABSENTE ; lève DateInvalide si elle est présente
    mais impossible ou mal écrite. Les deux cas ne doivent pas être confondus.
    """
    if texte is None or not texte.strip():
        return None
    try:
        jour, mois, annee = (int(x) for x in texte.strip().split("/"))
        return date(annee, mois, jour)
    except ValueError as erreur:
        raise DateInvalide(texte) from erreur


def euros(montant: float) -> str:
    return f"{montant:,.2f} €".replace(",", " ").replace(".", ",")


def virgule(nombre: float) -> str:
    """0.95 -> « 0,95 » (écriture française)."""
    return f"{nombre:.2f}".replace(".", ",")


def bulletins(pieces: dict[str, PieceExtraite]) -> list[tuple[str, PieceExtraite]]:
    return [(nom, p) for nom, p in sorted(pieces.items()) if p.type_piece == "bulletin_salaire"]


def resultat(nom: str, anomalies: list[str], manques: list[str], motif_ok: str) -> Controle:
    """Anomalie si au moins une anomalie ; sinon non vérifiable s'il manque une donnée."""
    if anomalies:
        return Controle(nom, "anomalie", " ; ".join(anomalies))
    if manques:
        return Controle(nom, "non_verifiable", " ; ".join(manques))
    return Controle(nom, "ok", motif_ok)


# --- Les quatre contrôles -------------------------------------------------------

def controle_siret(pieces: dict[str, PieceExtraite]) -> Controle:
    sirets = {}  # fichier -> SIRET (chiffres seuls)
    for nom, p in sorted(pieces.items()):
        if p.siret:
            sirets[nom] = "".join(c for c in p.siret if c.isdigit())
    if not sirets:
        return Controle("siret", "non_verifiable", "aucun SIRET trouvé sur les pièces")

    # Regroupe les pièces par SIRET, pour ne pas répéter le même motif 4 fois.
    par_siret: dict[str, list[str]] = {}
    for nom, s in sirets.items():
        par_siret.setdefault(s, []).append(nom)
    anomalies = [f"SIRET {s} invalide, clé de contrôle fausse ou longueur différente de 14 "
                 f"({', '.join(noms)})"
                 for s, noms in par_siret.items() if not siret_valide(s)]
    if len(set(sirets.values())) > 1:
        detail = ", ".join(f"{nom} = {s}" for nom, s in sirets.items())
        anomalies.append(f"SIRET différents selon les pièces ({detail})")
    unique = next(iter(sirets.values()))
    return resultat("siret", anomalies, [],
                    f"SIRET {unique} valide et identique sur {len(sirets)} pièce(s)")


def controle_net_brut(pieces: dict[str, PieceExtraite]) -> Controle:
    anomalies, manques, ratios = [], [], []
    for nom, b in bulletins(pieces):
        if not b.salaire_brut or b.salaire_net is None:
            absents = [libelle for libelle, valeur in
                       (("brut", b.salaire_brut), ("net à payer", b.salaire_net)) if not valeur]
            manques.append(f"{nom} : {' et '.join(absents)} absent ou illisible")
            continue
        ratio = round(b.salaire_net / b.salaire_brut, 4)
        ratios.append(ratio)
        if not NET_BRUT_MIN <= ratio <= NET_BRUT_MAX:
            anomalies.append(
                f"{nom} : net {euros(b.salaire_net)} / brut {euros(b.salaire_brut)} = "
                f"{virgule(ratio)}, hors de la plage {virgule(NET_BRUT_MIN)} à {virgule(NET_BRUT_MAX)}")
    if not ratios and not manques:
        manques.append("aucun bulletin de salaire")
    return resultat("net_brut", anomalies, manques,
                    f"net/brut entre {virgule(min(ratios, default=0))} et {virgule(max(ratios, default=0))} "
                    f"sur {len(ratios)} bulletin(s)")


def controle_bulletins_vs_avis(pieces: dict[str, PieceExtraite]) -> Controle:
    nets = [b.net_imposable for _, b in bulletins(pieces) if b.net_imposable is not None]
    avis = [p for p in pieces.values()
            if p.type_piece == "avis_imposition" and p.salaires_declares is not None]
    manques = []
    if not nets:
        manques.append("aucun net imposable sur les bulletins")
    if not avis:
        manques.append("pas d'avis d'imposition avec les salaires déclarés")
    if manques:
        return Controle("bulletins_vs_avis", "non_verifiable", " ; ".join(manques))

    annuel = sum(nets) / len(nets) * 12
    declares = avis[0].salaires_declares
    rapport = declares / annuel
    texte = (f"salaires déclarés {euros(declares)} pour {euros(annuel)} attendus "
             f"(12 fois le net imposable moyen), soit {rapport:.0%}")
    if abs(rapport - 1) > ECART_AVIS_MAX:
        return Controle("bulletins_vs_avis", "anomalie",
                        f"{texte} : écart de plus de {ECART_AVIS_MAX:.0%}")
    return Controle("bulletins_vs_avis", "ok", texte)


def controle_dates(pieces: dict[str, PieceExtraite]) -> Controle:
    anomalies, manques = [], []

    def lire(nom: str, champ: str, texte: str | None) -> date | None:
        try:
            return lire_date(texte)
        except DateInvalide:
            anomalies.append(f"{nom} : {champ} « {texte} » n'est pas une date valide")
            return None

    # Dates du contrat : sur le contrat et recopiées sur chaque bulletin.
    debuts: dict[date, str] = {}  # date d'embauche -> première pièce où elle figure
    for nom, p in sorted(pieces.items()):
        debut = lire(nom, "date de début de contrat", p.date_debut_contrat)
        fin = lire(nom, "date de fin de contrat", p.date_fin_contrat)
        if debut:
            debuts.setdefault(debut, nom)
        if debut and fin and fin < debut:
            anomalies.append(f"{nom} : contrat terminé le {fin:%d/%m/%Y} "
                             f"avant d'avoir commencé le {debut:%d/%m/%Y}")
    if not debuts and not anomalies:
        manques.append("date de début de contrat absente")

    # Périodes des bulletins.
    premier_bulletin: tuple[date, str] | None = None
    for nom, b in bulletins(pieces):
        debut = lire(nom, "début de période", b.periode_debut)
        fin = lire(nom, "fin de période", b.periode_fin)
        if b.periode_debut is None or b.periode_fin is None:
            manques.append(f"{nom} : période incomplète")
        if debut and fin and fin < debut:
            anomalies.append(f"{nom} : période qui finit ({fin:%d/%m/%Y}) "
                             f"avant de commencer ({debut:%d/%m/%Y})")
        if debut and (premier_bulletin is None or debut < premier_bulletin[0]):
            premier_bulletin = (debut, nom)

    # Embauche après le premier bulletin : impossible.
    if premier_bulletin:
        for embauche, nom in sorted(debuts.items()):
            if embauche > premier_bulletin[0]:
                anomalies.append(
                    f"embauche le {embauche:%d/%m/%Y} (lu sur {nom}), alors que le premier "
                    f"bulletin commence le {premier_bulletin[0]:%d/%m/%Y} ({premier_bulletin[1]})")

    return resultat("dates", anomalies, manques,
                    "dates valides, embauche avant les bulletins, périodes dans l'ordre")


def controler_dossier(pieces: dict[str, PieceExtraite]) -> list[Controle]:
    """Les quatre contrôles d'un dossier, toujours dans le même ordre."""
    return [
        controle_siret(pieces),
        controle_net_brut(pieces),
        controle_bulletins_vs_avis(pieces),
        controle_dates(pieces),
    ]
