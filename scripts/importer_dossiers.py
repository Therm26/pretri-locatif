"""Importe les 22 dossiers fictifs dans la base SQLite, sans appeler l'API.

Les pièces viennent des réponses de Claude déjà enregistrées (data/fake/extraction/).
Chaque dossier reçoit une date de dépôt fictive entre le 02/11/2026 9 h et le
04/11/2026 18 h, tirée avec une graine fixe et dans un ordre mélangé (D01 n'est pas
forcément le premier déposé), pour qu'on voie le tri par date de dépôt.

Relancer le script ne crée pas de doublon : les dossiers déjà présents sont ignorés.

Usage :
    .\\.venv\\Scripts\\python.exe -m scripts.importer_dossiers
    .\\.venv\\Scripts\\python.exe -m scripts.importer_dossiers --base autre.db
"""

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from src import db
from src.extraction import PieceExtraite
from src.models import Bien

RACINE = Path(__file__).resolve().parent.parent / "data" / "fake"
BIEN_FICTIF = Bien(nom="T2 fictif", loyer_cc=800.0, ratio_min=3.0)
PREMIER_JOUR = datetime(2026, 11, 2)
NB_JOURS = 3
HEURE_OUVERTURE, HEURE_FERMETURE = 9, 18


def dates_de_depot(references: list[str], graine: int = 42) -> dict[str, datetime]:
    """Une date de dépôt par dossier, étalée sur 3 jours, de 9 h à 18 h, à la minute."""
    rng = random.Random(graine)
    minutes_par_jour = (HEURE_FERMETURE - HEURE_OUVERTURE) * 60
    instants = sorted(
        PREMIER_JOUR + timedelta(days=rng.randrange(NB_JOURS), hours=HEURE_OUVERTURE,
                                 minutes=rng.randrange(minutes_par_jour + 1))
        for _ in references)
    ordre = list(references)
    rng.shuffle(ordre)                  # ordre de dépôt différent de l'ordre des numéros
    return dict(zip(ordre, instants))


def charger_extraction(fichier: Path) -> dict[str, PieceExtraite]:
    brut = json.loads(fichier.read_text("utf-8"))
    return {nom: PieceExtraite(**d) for nom, d in brut.items()}


def importer(conn, extraction: Path = RACINE / "extraction", graine: int = 42) -> list[str]:
    """Importe et évalue chaque dossier ; renvoie les références nouvellement importées."""
    bien_id = db.trouver_bien(conn, BIEN_FICTIF.nom) or db.creer_bien(conn, BIEN_FICTIF)
    fichiers = {f.stem: f for f in sorted(extraction.glob("D[0-9][0-9].json"))}
    dates = dates_de_depot(sorted(fichiers), graine)
    importes = []
    for reference, fichier in sorted(fichiers.items()):
        try:
            dossier_id = db.ajouter_dossier(conn, bien_id, reference, dates[reference],
                                            charger_extraction(fichier))
        except db.DossierExistant:
            continue
        db.evaluer(conn, dossier_id)
        importes.append(reference)
    return importes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, default=db.CHEMIN_PAR_DEFAUT)
    args = parser.parse_args()
    conn = db.connecter(args.base)
    importes = importer(conn)
    print(f"{len(importes)} dossier(s) importé(s) dans {args.base}.\n")
    bien_id = db.trouver_bien(conn, BIEN_FICTIF.nom)
    print("Groupe  Dossier  Dépôt                Pièces     Critères       Contrôles")
    for d in db.liste_triee(conn, bien_id):
        print(f"  ({d['groupe']})   {d['reference']}    {d['date_depot']}  {d['pieces']:<10} "
              f"{d['criteres']:<14} {d['controles']}")


if __name__ == "__main__":
    main()
