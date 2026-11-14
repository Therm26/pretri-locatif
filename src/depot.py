"""Étape 5b : déposer un dossier reçu (zip ou fichiers) pour un bien.

C'est la logique de l'écran « Importer un dossier », sans Streamlit, pour pouvoir la
tester. L'interface (app.py) ne fait qu'appeler `lire_envoi()` puis `deposer()`.

Déroulé de `deposer()` :
1. vérifier la date de dépôt (pas dans le futur) et qu'il y a au moins un fichier ;
2. attribuer une référence automatique (D01, D02…) : jamais de nom de candidat ;
3. écrire les fichiers dans un dossier temporaire et les faire lire par Claude, un par un ;
4. enregistrer le dossier et les valeurs lues (db.ajouter_dossier), puis l'évaluer (db.evaluer).
Les PDF eux-mêmes ne sont pas conservés : le dossier temporaire est effacé à la fin,
la base ne garde que les valeurs extraites (purgées à 3 mois).

Si Claude ne peut pas lire un fichier, rien n'est enregistré : le journal note l'échec
(référence et nombre de pièces seulement) et le gestionnaire vérifie le fichier.
"""

from __future__ import annotations

import io
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from src import db
from src.extraction import TYPES_MIME, ErreurExtraction, PieceExtraite, extraire_piece
from src.regles import Resultat

# Garde-fous contre un zip piégé ou démesuré (un dossier fait une dizaine de pièces).
NB_FICHIERS_MAX = 30
TAILLE_MAX = 50 * 1024 * 1024        # 50 Mo décompressés, tous fichiers confondus

Fichiers = dict[str, bytes]            # {nom du fichier: contenu}
Extracteur = Callable[[Path], PieceExtraite]
Progression = Callable[[int, int, str], None]   # (n° du fichier, total, nom)


class DepotInvalide(ValueError):
    """Envoi inutilisable : vide, trop gros, noms en double, date dans le futur…"""


# --- Lecture de l'envoi ------------------------------------------------------------

def lire_envoi(envoi: list[tuple[str, bytes]]) -> tuple[Fichiers, list[str]]:
    """Transforme ce qu'a envoyé le gestionnaire en {nom: contenu}.

    Un zip est ouvert et ses PDF/images sont pris ; les autres fichiers sont ignorés et
    renvoyés dans la 2e liste pour être affichés. Les sous-dossiers du zip sont aplatis :
    seul le nom du fichier compte (« dossier/bulletin.pdf » -> « bulletin.pdf »), ce qui
    empêche aussi un nom piégé comme « ../../x.pdf » d'écrire hors du dossier temporaire.
    """
    fichiers: Fichiers = {}
    ignores: list[str] = []
    taille = 0

    def ajouter(nom: str, contenu: bytes) -> None:
        nonlocal taille
        if Path(nom).suffix.lower() not in TYPES_MIME:
            ignores.append(nom)
            return
        if nom in fichiers:
            raise DepotInvalide(f"deux fichiers s'appellent « {nom} » : renommer l'un des deux")
        taille += len(contenu)
        if len(fichiers) >= NB_FICHIERS_MAX or taille > TAILLE_MAX:
            raise DepotInvalide(f"envoi trop gros : au plus {NB_FICHIERS_MAX} fichiers "
                                f"et {TAILLE_MAX // (1024 * 1024)} Mo")
        fichiers[nom] = contenu

    for nom, contenu in envoi:
        if Path(nom).suffix.lower() != ".zip":
            ajouter(Path(nom).name, contenu)
            continue
        try:
            archive = zipfile.ZipFile(io.BytesIO(contenu))
        except zipfile.BadZipFile:
            raise DepotInvalide(f"« {nom} » n'est pas un zip lisible") from None
        with archive:
            for info in archive.infolist():
                chemin = PurePosixPath(info.filename.replace("\\", "/"))
                if info.is_dir() or chemin.name.startswith(".") or "__MACOSX" in chemin.parts:
                    continue                    # dossiers et fichiers cachés du Mac
                if Path(chemin.name).suffix.lower() not in TYPES_MIME:
                    ignores.append(chemin.name)
                    continue
                if taille + info.file_size > TAILLE_MAX:   # taille annoncée, avant de décompresser
                    raise DepotInvalide(f"envoi trop gros : au plus {TAILLE_MAX // (1024 * 1024)} Mo")
                ajouter(chemin.name, archive.read(info))
    return fichiers, ignores


# --- Référence -------------------------------------------------------------------------

def prochaine_reference(conn: sqlite3.Connection, bien_id: int) -> str:
    """D01, D02… : un numéro de plus que le plus grand déjà utilisé pour ce bien.

    On regarde aussi le journal, pour ne jamais réattribuer la référence d'un dossier
    purgé ou dont l'extraction a échoué (le journal resterait ambigu).
    """
    references = [l[0] for l in conn.execute(
        "SELECT reference FROM dossier WHERE bien_id = ? UNION "
        "SELECT reference FROM journal WHERE bien_id = ? AND reference IS NOT NULL",
        (bien_id, bien_id))]
    numeros = [int(m.group(1)) for r in references if (m := re.fullmatch(r"D(\d+)", r))]
    return f"D{max(numeros, default=0) + 1:02d}"


# --- Dépôt -------------------------------------------------------------------------------

def extraire_tout(fichiers: Fichiers, extraire: Extracteur, progression: Optional[Progression],
                  deja: int, total: int) -> dict[str, PieceExtraite]:
    """Écrit les fichiers dans un dossier temporaire et les fait lire un par un."""
    pieces = {}
    with tempfile.TemporaryDirectory(prefix="pretri_") as dossier:
        for i, nom in enumerate(sorted(fichiers), start=deja + 1):
            if progression:
                progression(i, total, nom)
            chemin = Path(dossier) / nom
            chemin.write_bytes(fichiers[nom])
            pieces[nom] = extraire(chemin)
    return pieces


def deposer(conn: sqlite3.Connection, bien_id: int, date_depot: datetime,
            candidat: Fichiers, garant: Optional[Fichiers] = None, visale: bool = False,
            extraire: Optional[Extracteur] = None,
            progression: Optional[Progression] = None) -> tuple[int, str, Resultat]:
    """Extrait, enregistre et évalue un dossier. Renvoie (id, référence, résultat)."""
    if not candidat:
        raise DepotInvalide("aucun PDF ni image pour le candidat")
    if date_depot > datetime.now():
        raise DepotInvalide("la date de dépôt est dans le futur")
    db.charger_bien(conn, bien_id)                 # KeyError si le bien n'existe pas
    garant = garant or None                        # {} -> pas de garant
    extraire = extraire or extraire_piece          # par défaut : le vrai appel à Claude
    reference = prochaine_reference(conn, bien_id)
    total = len(candidat) + len(garant or {})

    try:
        pieces = extraire_tout(candidat, extraire, progression, 0, total)
        pieces_garant = (extraire_tout(garant, extraire, progression, len(candidat), total)
                         if garant else None)
    except ErreurExtraction:
        with conn:
            db.journaliser(conn, "extraction_echec",
                           {"nb_pieces": len(candidat), "nb_pieces_garant": len(garant or {})},
                           bien_id=bien_id, reference=reference)
        raise

    with conn:
        db.journaliser(conn, "extraction",
                       {"nb_pieces": len(pieces), "nb_pieces_garant": len(pieces_garant or {})},
                       bien_id=bien_id, reference=reference)
    dossier_id = db.ajouter_dossier(conn, bien_id, reference, date_depot, pieces,
                                    garant=pieces_garant, visale=visale)
    return dossier_id, reference, db.evaluer(conn, dossier_id)
