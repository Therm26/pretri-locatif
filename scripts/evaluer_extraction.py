"""Mesure la qualité de l'extraction : passe les dossiers fictifs dans Claude
et compare champ par champ avec data/fake/verite.json.

Appelle la vraie API (payant, ~6 appels par dossier). Exemples :
    .\\.venv\\Scripts\\python.exe -m scripts.evaluer_extraction D01
    .\\.venv\\Scripts\\python.exe -m scripts.evaluer_extraction        (les 20 dossiers)

Les résultats bruts sont enregistrés dans data/fake/extraction/<id>.json.
"""

import json
import sys
from pathlib import Path

from src.extraction import creer_client, extraire_dossier, synthese_dossier

RACINE = Path(__file__).resolve().parent.parent / "data" / "fake"


def egal(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.01
    return a == b


def main(ids: list[str]) -> int:
    verite = {v["id"]: v for v in json.loads((RACINE / "verite.json").read_text("utf-8"))}
    ids = ids or sorted(verite)
    client = creer_client()
    sortie = RACINE / "extraction"
    sortie.mkdir(exist_ok=True)

    total = justes = 0
    for id_ in ids:
        pieces = extraire_dossier(RACINE / id_, client)
        (sortie / f"{id_}.json").write_text(
            json.dumps({n: p.model_dump() for n, p in pieces.items()}, ensure_ascii=False, indent=1),
            "utf-8")
        obtenu = synthese_dossier(pieces)
        erreurs = [f"{c}: attendu {v!r}, obtenu {obtenu[c]!r}"
                   for c, v in verite[id_]["attendu"].items() if not egal(v, obtenu[c])]
        n = len(verite[id_]["attendu"])
        total += n
        justes += n - len(erreurs)
        print(f"{id_} : {n - len(erreurs)}/{n}" + "".join(f"\n   - {e}" for e in erreurs))
    print(f"\nTotal : {justes}/{total} champs corrects")
    return 0 if justes == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
