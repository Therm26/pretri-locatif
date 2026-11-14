"""Génère de faux dossiers locatifs (PDF) pour tester l'outil.

Chaque dossier contient 6 pièces : pièce d'identité factice, contrat de travail,
3 bulletins de salaire (août, septembre, octobre 2026) et l'avis d'imposition 2026
(revenus 2025). Tout est fictif et marqué « DOCUMENT FICTIF ».

4 dossiers contiennent volontairement une anomalie (voir FRAUDES ci-dessous).
2 dossiers ont un défaut sans fraude (voir DEFAUTS) : D21 sans avis d'imposition,
D22 avec le net à payer illisible (tache) sur le bulletin de septembre.
Le fichier `verite.json` décrit, pour chaque dossier, les valeurs attendues et
l'anomalie éventuelle : il servira à vérifier l'extraction et les contrôles.

Usage :
    python -m scripts.generer_dossiers                 # écrit dans data/fake/
    python -m scripts.generer_dossiers --sortie autre/ --graine 7
"""

import argparse
import calendar
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

from faker import Faker
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from src.controles import luhn_valide

NB_DOSSIERS = 22
MOIS_BULLETINS = [(2026, 8), (2026, 9), (2026, 10)]
ANNEE_REVENUS_AVIS = 2025

# Numéro du dossier -> type d'anomalie volontaire.
FRAUDES = {
    5: "net_incoherent",      # net à payer = 95 % du brut (impossible avec les cotisations)
    9: "siret_invalide",      # dernier chiffre du SIRET modifié : clé de Luhn fausse
    13: "avis_incoherent",    # salaires de l'avis = 45 % de ce que disent les bulletins
    17: "dates_impossibles",  # embauche après les bulletins + période au 31/09
}

# Numéro du dossier -> défaut sans fraude (dossier incomplet ou mal scanné).
# Ces dossiers sont tirés APRÈS les 20 premiers : D01 à D20 restent identiques.
DEFAUTS = {
    21: "sans_avis",          # l'avis d'imposition n'a pas été fourni
    22: "net_illisible",      # net à payer masqué par une tache sur le bulletin de septembre
}
BULLETIN_ILLISIBLE = (2026, 9)

# Intitulés neutres : les métiers de Faker sont parfois genrés (« infirmière »),
# ce qui laisserait deviner le sexe, une donnée que l'outil ne doit pas voir.
POSTES = ["Comptable", "Technicien support", "Développeur", "Assistant commercial",
          "Chef de projet", "Gestionnaire de stock", "Juriste", "Analyste de données"]


# ---------------------------------------------------------------------------
# SIRET et clé de Luhn
# ---------------------------------------------------------------------------

# luhn_valide vient de src/controles.py : une seule version de l'algorithme.


def completer_luhn(prefixe: str) -> str:
    """Ajoute le chiffre final qui rend le nombre valide selon Luhn."""
    for cle in "0123456789":
        if luhn_valide(prefixe + cle):
            return prefixe + cle
    raise ValueError("impossible")  # n'arrive jamais : une clé convient toujours


def generer_siret(fake: Faker) -> str:
    """SIRET = SIREN (9 chiffres) + NIC (5 chiffres), chacun valide selon Luhn.

    On part du SIREN de Faker mais on recalcule nous-mêmes les clés :
    ainsi la validité ne dépend pas de la version de Faker.
    """
    siren = completer_luhn(fake.siren().replace(" ", "")[:8])
    nic_debut = "000" + fake.random.choice("123456789")
    return completer_luhn(siren + nic_debut)


def casser_siret(siret: str) -> str:
    """Change le dernier chiffre : modifier un seul chiffre casse toujours Luhn."""
    dernier = (int(siret[-1]) + 1) % 10
    return siret[:-1] + str(dernier)


# ---------------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------------

def euros(montant: float) -> str:
    """2345.6 -> '2 345,60 €' (format français)."""
    texte = f"{montant:,.2f}".replace(",", " ").replace(".", ",")
    return f"{texte} €"


def jj_mm_aaaa(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def siret_lisible(siret: str) -> str:
    return f"{siret[:3]} {siret[3:6]} {siret[6:9]} {siret[9:]}"


class PageFictive:
    """Une page A4 reportlab avec filigrane, et un « curseur » qui descend ligne à ligne.

    reportlab place le texte en coordonnées (x, y) en points, avec y = 0 en BAS
    de la page : on part donc du haut (y grand) et on soustrait à chaque ligne.
    """

    MARGE = 60
    COLONNE_VALEUR = 270

    def __init__(self, chemin: Path):
        # invariant=1 : même contenu -> PDF identique octet par octet (pas de date de création).
        self.c = canvas.Canvas(str(chemin), pagesize=A4, invariant=1)
        self.largeur, self.hauteur = A4
        self.y = self.hauteur - 70
        self._filigrane()

    def _filigrane(self):
        self.c.saveState()
        self.c.setFillGray(0.88)
        self.c.setFont("Helvetica-Bold", 38)
        self.c.translate(self.largeur / 2, self.hauteur / 2)
        self.c.rotate(35)
        self.c.drawCentredString(0, 0, "DOCUMENT FICTIF - SPECIMEN")
        self.c.restoreState()

    def titre(self, texte: str):
        self.c.setFont("Helvetica-Bold", 16)
        self.c.drawCentredString(self.largeur / 2, self.y, texte)
        self.y -= 36

    def ligne(self, libelle: str, valeur: str):
        self.c.setFont("Helvetica-Bold", 11)
        self.c.drawString(self.MARGE, self.y, libelle)
        self.c.setFont("Helvetica", 11)
        self.c.drawString(self.COLONNE_VALEUR, self.y, valeur)
        self.y -= 22

    def ligne_tachee(self, libelle: str):
        """Libellé lisible, valeur couverte par une tache d'encre.

        La valeur n'est PAS écrite sous la tache : sinon elle resterait dans le texte
        du PDF et Claude pourrait la lire quand même.
        """
        self.c.setFont("Helvetica-Bold", 11)
        self.c.drawString(self.MARGE, self.y, libelle)
        self.c.saveState()
        self.c.setFillGray(0.15)
        self.c.ellipse(self.COLONNE_VALEUR - 4, self.y - 6, self.COLONNE_VALEUR + 80, self.y + 13,
                       stroke=0, fill=1)
        self.c.restoreState()
        self.y -= 22

    def paragraphe(self, texte: str):
        self.c.setFont("Helvetica-Oblique", 10)
        self.c.drawString(self.MARGE, self.y, texte)
        self.y -= 26

    def espace(self):
        self.y -= 12

    def enregistrer(self):
        self.c.showPage()
        self.c.save()


# ---------------------------------------------------------------------------
# Création d'un dossier
# ---------------------------------------------------------------------------

def tirer_profil(numero: int, fake: Faker) -> dict:
    """Tire au hasard les données d'un candidat fictif (cohérentes entre elles)."""
    rng = fake.random  # générateur aléatoire de Faker, réglé par la graine

    type_contrat = "CDD" if rng.random() < 0.25 else "CDI"
    if type_contrat == "CDI":
        debut = date(rng.randint(2015, 2025), rng.randint(1, 12), 1)
        fin = None
    else:
        debut = date(2026, rng.randint(3, 6), 1)
        mois_fin = rng.randint(1, 6)
        fin = date(2027, mois_fin, calendar.monthrange(2027, mois_fin)[1])

    brut = round(rng.uniform(1800, 5500), 2)
    net = round(brut * rng.uniform(0.76, 0.80), 2)
    # Le net imposable est un peu au-dessus du net (CSG/CRDS non déductibles).
    net_imposable = round(net + brut * 0.029, 2)
    # Avis 2026 sur les revenus 2025 : environ 12 mois de net imposable
    # (un peu moins si augmentation depuis). Le RFR retire l'abattement de 10 %.
    salaires_declares = round(net_imposable * 12 * rng.uniform(0.94, 1.0))

    return {
        "id": f"D{numero:02d}",
        # Nom et prénom figurent sur les pièces (comme dans la réalité),
        # mais l'outil ne doit jamais s'en servir comme critère.
        "prenom": fake.first_name(),
        "nom": fake.last_name(),
        "numero_identite": fake.bothify("??#######", letters="ABCDEFGHJKLMNPRSTUVWXYZ"),
        "validite_identite": date(rng.randint(2028, 2034), rng.randint(1, 12), 1),
        "employeur": fake.company(),
        "siret": generer_siret(fake),
        "poste": rng.choice(POSTES),
        "type_contrat": type_contrat,
        "date_debut": debut,
        "date_fin": fin,
        "brut": brut,
        "net": net,
        "net_imposable": net_imposable,
        "salaires_declares": salaires_declares,
        "rfr": round(salaires_declares * 0.9),
        "numero_fiscal": fake.numerify("#############"),
        "fraude": None,
        "detail_fraude": None,
        "defaut": None,
        "periode_fin_forcee": {},  # (année, mois) -> texte de date impossible
    }


def appliquer_fraude(profil: dict, fraude: str) -> None:
    """Modifie le profil pour y introduire l'anomalie demandée."""
    profil["fraude"] = fraude
    if fraude == "net_incoherent":
        profil["net"] = round(profil["brut"] * 0.95, 2)
        profil["net_imposable"] = round(profil["net"] + profil["brut"] * 0.029, 2)
        profil["detail_fraude"] = "net à payer = 95 % du brut"
    elif fraude == "siret_invalide":
        profil["siret"] = casser_siret(profil["siret"])
        profil["detail_fraude"] = "clé de Luhn du SIRET fausse"
    elif fraude == "avis_incoherent":
        profil["salaires_declares"] = round(profil["net_imposable"] * 12 * 0.45)
        profil["rfr"] = round(profil["salaires_declares"] * 0.9)
        profil["detail_fraude"] = "salaires de l'avis = 45 % des bulletins x 12"
    elif fraude == "dates_impossibles":
        profil["type_contrat"] = "CDI"
        profil["date_debut"] = date(2027, 3, 1)
        profil["date_fin"] = None
        profil["periode_fin_forcee"] = {(2026, 9): "31/09/2026"}
        profil["detail_fraude"] = ("embauche le 01/03/2027, après les bulletins ; "
                                   "période du bulletin de septembre au 31/09/2026")
    else:
        raise ValueError(f"fraude inconnue : {fraude}")


def pdf_identite(p: dict, chemin: Path) -> None:
    # Volontairement sans date de naissance, sexe, nationalité, adresse ni photo.
    page = PageFictive(chemin)
    page.titre("CARTE D'IDENTITÉ FICTIVE")
    page.paragraphe("Spécimen généré pour des tests. Ce document n'a aucune valeur.")
    page.ligne("Nom :", p["nom"].upper())
    page.ligne("Prénom :", p["prenom"])
    page.ligne("Numéro du document :", p["numero_identite"])
    page.ligne("Valable jusqu'au :", jj_mm_aaaa(p["validite_identite"]))
    page.enregistrer()


def pdf_contrat(p: dict, chemin: Path) -> None:
    page = PageFictive(chemin)
    page.titre("CONTRAT DE TRAVAIL À DURÉE INDÉTERMINÉE" if p["type_contrat"] == "CDI"
               else "CONTRAT DE TRAVAIL À DURÉE DÉTERMINÉE")
    page.ligne("Employeur :", p["employeur"])
    page.ligne("SIRET :", siret_lisible(p["siret"]))
    page.ligne("Salarié :", f"{p['prenom']} {p['nom']}")
    page.ligne("Poste :", p["poste"])
    page.ligne("Type de contrat :", p["type_contrat"])
    page.ligne("Date de début :", jj_mm_aaaa(p["date_debut"]))
    if p["date_fin"]:
        page.ligne("Date de fin :", jj_mm_aaaa(p["date_fin"]))
    page.ligne("Salaire brut mensuel :", euros(p["brut"]))
    page.espace()
    page.paragraphe("Le salarié est engagé aux conditions ci-dessus. "
                    "Fait en deux exemplaires originaux.")
    page.enregistrer()


def nom_bulletin(annee: int, mois: int) -> str:
    return f"bulletin_{annee}_{mois:02d}.pdf"


def periode_bulletin(p: dict, annee: int, mois: int) -> tuple[str, str]:
    """Début et fin de période, en texte JJ/MM/AAAA, tels qu'imprimés sur le bulletin.

    La fin peut être une date impossible (dossier piégé « dates_impossibles »).
    """
    dernier_jour = calendar.monthrange(annee, mois)[1]
    fin = p["periode_fin_forcee"].get((annee, mois), jj_mm_aaaa(date(annee, mois, dernier_jour)))
    return jj_mm_aaaa(date(annee, mois, 1)), fin


def net_illisible(p: dict, annee: int, mois: int) -> bool:
    return p["defaut"] == "net_illisible" and (annee, mois) == BULLETIN_ILLISIBLE


def pdf_bulletin(p: dict, annee: int, mois: int, chemin: Path) -> None:
    debut_texte, fin_texte = periode_bulletin(p, annee, mois)
    cotisations = round(p["brut"] - p["net"], 2)
    # Cumul imposable depuis janvier, comme sur un vrai bulletin.
    cumul = round(p["net_imposable"] * mois, 2)

    page = PageFictive(chemin)
    page.titre("BULLETIN DE PAIE")
    page.ligne("Employeur :", p["employeur"])
    page.ligne("SIRET :", siret_lisible(p["siret"]))
    page.ligne("Salarié :", f"{p['prenom']} {p['nom']}")
    page.ligne("Emploi :", p["poste"])
    page.ligne("Date d'entrée :", jj_mm_aaaa(p["date_debut"]))
    page.ligne("Type de contrat :", p["type_contrat"])
    page.ligne("Période :", f"du {debut_texte} au {fin_texte}")
    page.ligne("Date de paiement :", fin_texte)
    page.espace()
    page.ligne("Salaire de base brut :", euros(p["brut"]))
    page.ligne("Total cotisations salariales :", "- " + euros(cotisations))
    if net_illisible(p, annee, mois):
        page.ligne_tachee("Net à payer avant impôt :")
    else:
        page.ligne("Net à payer avant impôt :", euros(p["net"]))
    page.ligne("Net imposable :", euros(p["net_imposable"]))
    page.ligne("Cumul net imposable annuel :", euros(cumul))
    page.enregistrer()


def pdf_avis(p: dict, chemin: Path) -> None:
    # Volontairement sans adresse ni situation familiale (nombre de parts).
    page = PageFictive(chemin)
    page.titre(f"AVIS D'IMPÔT {ANNEE_REVENUS_AVIS + 1} SUR LES REVENUS {ANNEE_REVENUS_AVIS}")
    page.paragraphe("Document fictif imitant la structure d'un avis d'imposition.")
    page.ligne("Déclarant :", f"{p['prenom']} {p['nom']}")
    page.ligne("Numéro fiscal :", p["numero_fiscal"])
    page.ligne("Année des revenus :", str(ANNEE_REVENUS_AVIS))
    page.ligne("Salaires et assimilés :", euros(p["salaires_declares"]))
    page.ligne("Revenu fiscal de référence :", euros(p["rfr"]))
    page.enregistrer()


def verite_du_profil(p: dict) -> dict:
    """Ce que l'extraction devrait retrouver, et l'anomalie éventuelle."""
    return {
        "id": p["id"],
        "fraude": p["fraude"],
        "detail_fraude": p["detail_fraude"],
        "defaut": p["defaut"],
        "attendu": {
            "employeur": p["employeur"],
            "siret": p["siret"],
            "salaire_brut": p["brut"],
            "salaire_net": p["net"],
            "net_imposable": p["net_imposable"],
            "type_contrat": p["type_contrat"],
            "date_debut_contrat": p["date_debut"].isoformat(),
            "date_fin_contrat": p["date_fin"].isoformat() if p["date_fin"] else None,
            # Sans avis fourni, l'extraction ne doit rien inventer.
            "salaires_declares": None if p["defaut"] == "sans_avis" else p["salaires_declares"],
            "revenu_fiscal_reference": None if p["defaut"] == "sans_avis" else p["rfr"],
        },
        # Une entrée par bulletin. Les dates de période sont en texte, comme imprimées,
        # car une date impossible (31/09/2026) ne peut pas s'écrire en ISO.
        "bulletins": {
            nom_bulletin(annee, mois): {
                "periode_debut": periode_bulletin(p, annee, mois)[0],
                "periode_fin": periode_bulletin(p, annee, mois)[1],
                "salaire_brut": p["brut"],
                "salaire_net": None if net_illisible(p, annee, mois) else p["net"],
                "net_imposable": p["net_imposable"],
            }
            for annee, mois in MOIS_BULLETINS
        },
    }


def generer(sortie: Path, graine: int = 42) -> list[dict]:
    """Crée les dossiers dans `sortie` et renvoie la vérité terrain."""
    fake = Faker("fr_FR")
    fake.seed_instance(graine)  # même graine -> mêmes dossiers
    # On n'efface que ce que le générateur produit : les réponses de Claude déjà
    # payées (data/fake/extraction/) sont conservées.
    for ancien in [*sortie.glob("D[0-9][0-9]"), sortie / "zips"]:
        if ancien.is_dir():
            shutil.rmtree(ancien)
    (sortie / "zips").mkdir(parents=True)

    verite = []
    for numero in range(1, NB_DOSSIERS + 1):
        profil = tirer_profil(numero, fake)
        if numero in FRAUDES:
            appliquer_fraude(profil, FRAUDES[numero])
        profil["defaut"] = DEFAUTS.get(numero)

        dossier = sortie / profil["id"]
        dossier.mkdir()
        pdf_identite(profil, dossier / "piece_identite.pdf")
        pdf_contrat(profil, dossier / "contrat_travail.pdf")
        for annee, mois in MOIS_BULLETINS:
            pdf_bulletin(profil, annee, mois, dossier / nom_bulletin(annee, mois))
        if profil["defaut"] != "sans_avis":
            pdf_avis(profil, dossier / f"avis_imposition_{ANNEE_REVENUS_AVIS + 1}.pdf")

        # Une archive zip par dossier, pour tester l'import dans l'interface.
        with zipfile.ZipFile(sortie / "zips" / f"{profil['id']}.zip", "w") as z:
            for fichier in sorted(dossier.glob("*.pdf")):
                z.write(fichier, fichier.name)

        verite.append(verite_du_profil(profil))

    (sortie / "verite.json").write_text(
        json.dumps(verite, ensure_ascii=False, indent=2), encoding="utf-8")
    return verite


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sortie", type=Path, default=Path("data/fake"))
    parser.add_argument("--graine", type=int, default=42)
    args = parser.parse_args()
    verite = generer(args.sortie, args.graine)
    nb_fraudes = sum(1 for v in verite if v["fraude"])
    nb_defauts = sum(1 for v in verite if v["defaut"])
    print(f"{len(verite)} dossiers créés dans {args.sortie} "
          f"(dont {nb_fraudes} avec anomalie et {nb_defauts} avec un défaut).")


if __name__ == "__main__":
    main()
