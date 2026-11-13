"""Taux de réussite de l'extraction, champ par champ, SANS appeler l'API.

Relit les réponses déjà enregistrées par `evaluer_extraction` (data/fake/extraction/)
et les compare à data/fake/verite.json :
- la synthèse du dossier (contrat + dernier bulletin + avis), champs « attendu » ;
- chaque bulletin un par un (août, septembre, octobre), y compris les dates de période,
  comparées en texte, telles qu'imprimées.

Usage :
    .\\.venv\\Scripts\\python.exe -m scripts.taux_par_champ
"""

import json
import sys
from pathlib import Path

from src.extraction import PieceExtraite, synthese_dossier

RACINE = Path(__file__).resolve().parent.parent / "data" / "fake"


def egal(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.01
    return a == b


def comparer(verite: list[dict], extraction: Path):
    """Renvoie ({champ: [corrects, total]}, [erreurs], [dossiers sans extraction])."""
    stats, erreurs, manquants = {}, [], []

    def noter(id_, champ, attendu, obtenu):
        s = stats.setdefault(champ, [0, 0])
        s[1] += 1
        if egal(attendu, obtenu):
            s[0] += 1
        else:
            erreurs.append((id_, champ, attendu, obtenu))

    for v in sorted(verite, key=lambda v: v["id"]):
        fichier = extraction / f"{v['id']}.json"
        if not fichier.exists():
            manquants.append(v["id"])
            continue
        brut = json.loads(fichier.read_text("utf-8"))
        pieces = {nom: PieceExtraite(**d) for nom, d in brut.items()}

        synthese = synthese_dossier(pieces)
        for champ, attendu in v["attendu"].items():
            noter(v["id"], champ, attendu, synthese[champ])

        for nom, attendus in v["bulletins"].items():
            piece = pieces.get(nom)
            for champ, attendu in attendus.items():
                obtenu = getattr(piece, champ) if piece else None
                noter(v["id"], f"{nom.removesuffix('.pdf')}.{champ}", attendu, obtenu)
    return stats, erreurs, manquants


def main() -> int:
    verite = json.loads((RACINE / "verite.json").read_text("utf-8"))
    stats, erreurs, manquants = comparer(verite, RACINE / "extraction")

    print("| Champ | Corrects | Taux |\n|---|---|---|")
    justes = total = 0
    for champ, (j, t) in stats.items():
        justes, total = justes + j, total + t
        print(f"| {champ} | {j}/{t} | {100 * j / t:.0f} % |")
    print(f"| **Total** | **{justes}/{total}** | **{100 * justes / total:.1f} %** |")

    print(f"\nErreurs ({len(erreurs)}) :")
    for id_, champ, attendu, obtenu in erreurs:
        print(f"  {id_} {champ} : attendu {attendu!r}, obtenu {obtenu!r}")
    if manquants:
        print(f"\nDossiers sans extraction enregistrée : {', '.join(manquants)}")
    return 0 if not erreurs and not manquants else 1


if __name__ == "__main__":
    sys.exit(main())
