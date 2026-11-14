"""Étape 5b : l'interface Streamlit du pré-tri.

Lancer :
    .\\.venv\\Scripts\\python.exe -m streamlit run app.py
puis ouvrir http://localhost:8501 dans le navigateur.
Autre base que data/pretri.db : définir la variable PRETRI_BASE avant de lancer.

Streamlit relance ce fichier de haut en bas à chaque clic : il n'y a pas de boucle,
chaque « st.xxx » dessine un élément de la page. Ce qui doit survivre d'un clic à
l'autre est dans la base SQLite (data/pretri.db) ou dans `st.session_state`.

Ce fichier ne contient que l'affichage. La logique est ailleurs :
    src/depot.py     zip ou PDF -> lecture par Claude -> base
    src/db.py        biens, dossiers, résultats, journal, purge à 3 mois
    src/regles.py    les trois statuts et leurs motifs
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from scripts.importer_dossiers import importer as importer_dossiers_fictifs
from src import db
from src.depot import DepotInvalide, deposer, lire_envoi, prochaine_reference
from src.extraction import ErreurExtraction, TYPES_MIME
from src.models import PIECES_EXIGIBLES, Bien, CritereInvalide, PieceNonAutorisee
from src.regles import LIBELLES_CONTROLES, evaluer_dossier

GROUPES = {
    "a": "a · complet, conforme, sans alerte",
    "b": "b · complet, conforme, à vérifier",
    "c": "c · incomplet ou non évaluable",
    "d": "d · non conforme",
}
STATUT_CONTROLE = {"ok": "OK", "anomalie": "Anomalie", "non_verifiable": "Non vérifiable"}
FORMATS = [ext.lstrip(".") for ext in TYPES_MIME] + ["zip"]

st.set_page_config(page_title="Pré-tri locatif", layout="wide")
load_dotenv()


def date_fr(texte_iso: str | None) -> str:
    """« 2026-11-02T09:00:00 » -> « 02/11/2026 09:00 »."""
    return datetime.fromisoformat(texte_iso).strftime("%d/%m/%Y %H:%M") if texte_iso else "—"


def euros(centimes: int | None) -> str:
    if centimes is None:
        return "—"
    return f"{centimes / 100:,.2f} €".replace(",", " ").replace(".", ",")


# Une connexion neuve à chaque passage : SQLite est un simple fichier, c'est instantané,
# et Streamlit change de fil d'exécution d'un clic à l'autre (une connexion n'y survit pas).
conn = db.connecter(os.getenv("PRETRI_BASE", db.CHEMIN_PAR_DEFAUT))

# Purge à chaque passage : un dossier dont la date de conservation est dépassée disparaît.
purges = db.purger(conn)
if purges:
    st.toast(f"Purge à 3 mois : {', '.join(purges)} supprimé(s).")


# --- Barre latérale : choisir ou créer un bien ---------------------------------------

with st.sidebar:
    st.header("Biens")
    biens = db.lister_biens(conn)
    bien_id = None
    if biens:
        noms = {b["id"]: f"{b['nom']} · {euros(b['loyer_cc'])} CC" for b in biens}
        choix = st.session_state.get("bien_id")
        bien_id = st.selectbox("Bien affiché", list(noms), format_func=noms.get,
                               index=list(noms).index(choix) if choix in noms else 0)
        st.session_state["bien_id"] = bien_id
    else:
        st.info("Aucun bien pour l'instant : créez-en un ci-dessous.")

    with st.expander("Nouveau bien", expanded=not biens):
        with st.form("nouveau_bien", clear_on_submit=True):
            nom = st.text_input("Nom du logement", placeholder="T2 rue des Lilas")
            loyer = st.number_input("Loyer charges comprises (€/mois)", min_value=0.0,
                                    value=800.0, step=10.0, format="%.2f")
            ratio = st.number_input("Ratio minimum revenus / loyer", min_value=0.0, value=3.0,
                                    step=0.1, format="%.2f")
            garant_ok = st.checkbox("Garant accepté", value=True)
            visale_ok = st.checkbox("Visale acceptée", value=True)
            exigees = st.multiselect(
                "Pièces exigées (décret n° 2015-1437)", list(PIECES_EXIGIBLES),
                default=list(PIECES_EXIGIBLES), format_func=lambda t: PIECES_EXIGIBLES[t][0])
            nb_bulletins = st.selectbox("Nombre de bulletins exigés", [3, 2, 1])
            if st.form_submit_button("Créer le bien"):
                try:
                    bien = Bien(nom=nom.strip(), loyer_cc=round(loyer, 2),
                                ratio_min=round(ratio, 2), garant_accepte=garant_ok,
                                visale_acceptee=visale_ok, pieces_exigees=tuple(exigees),
                                nb_bulletins=nb_bulletins)
                    if not bien.nom:
                        raise CritereInvalide("donnez un nom au logement")
                    st.session_state["bien_id"] = db.creer_bien(conn, bien)
                    st.rerun()
                except (CritereInvalide, PieceNonAutorisee) as e:
                    st.error(str(e))

    st.divider()
    st.caption("Démonstration, sans appel à l'API : le bien « T2 fictif » et les 22 "
               "dossiers fictifs déjà lus par Claude.")
    if st.button("Charger les dossiers fictifs"):
        nouveaux = importer_dossiers_fictifs(conn)
        st.session_state["bien_id"] = db.trouver_bien(conn, "T2 fictif")
        st.toast(f"{len(nouveaux)} dossier(s) fictif(s) importé(s).")
        st.rerun()

if bien_id is None:
    st.title("Pré-tri des dossiers locatifs")
    st.write("Créez un bien dans la barre de gauche pour commencer.")
    st.stop()

bien = db.charger_bien(conn, bien_id)


# --- En-tête : les critères du bien ---------------------------------------------------

st.title(bien.nom)
recours = [r for r, ok in (("garant", bien.garant_accepte), ("Visale", bien.visale_acceptee)) if ok]
st.caption(
    f"Loyer CC {euros(bien.loyer_centimes)} · revenus exigés ≥ {bien.ratio_min:g} × loyer "
    f"· recours acceptés : {', '.join(recours) or 'aucun'} "
    f"· pièces : {', '.join(PIECES_EXIGIBLES[t][0] for t in bien.pieces_exigees)} "
    f"({bien.nb_bulletins} bulletin(s)). La décision reste au gestionnaire.")

onglet_liste, onglet_import = st.tabs(["Dossiers", "Importer un dossier"])


# --- Onglet 1 : la liste triée et le détail ---------------------------------------------

def afficher_detail(dossier_id: int) -> None:
    d = db.charger_dossier(conn, dossier_id)
    pieces, garant = db.charger_pieces(conn, dossier_id)
    st.subheader(f"Dossier {d['reference']}")
    st.caption(f"Déposé le {date_fr(d['date_depot'])} · importé le {date_fr(d['importe_le'])} "
               f"· supprimé automatiquement après le {date_fr(d['a_supprimer_le'])}")
    if d["groupe"] is None:
        st.warning("Ce dossier n'a pas encore été évalué.")
        return

    # Les trois statuts, chacun avec son motif : pas de note globale.
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Pièces", d["pieces"])
        st.write(d["pieces_motif"])
    with c2:
        st.metric("Critères financiers", d["criteres"] + (f" (via {d['via']})" if d["via"] else ""))
        st.write(d["criteres_motif"])
    with c3:
        st.metric("Contrôles de cohérence", d["controles"])
        alertes = json.loads(d["alertes"])
        if alertes:
            for a in alertes:
                st.write(f"- {a}")
        else:
            st.write("aucune alerte")

    visale = st.toggle("Certificat Visale vu par le gestionnaire", value=bool(d["visale"]),
                       key=f"visale_{dossier_id}",
                       help="Cochez après avoir vérifié le certificat ; le dossier est réévalué.")
    if visale != bool(d["visale"]):
        db.modifier_visale(conn, dossier_id, visale)
        st.rerun()

    # Détail des 4 contrôles du candidat (recalculés à l'affichage, c'est déterministe).
    detail = evaluer_dossier(pieces, bien, garant=garant, visale=bool(d["visale"])).detail_controles
    st.markdown("**Contrôles du candidat**")
    st.dataframe(pd.DataFrame([{"Contrôle": LIBELLES_CONTROLES.get(c.nom, c.nom),
                                "Résultat": STATUT_CONTROLE[c.statut], "Motif": c.motif}
                               for c in detail]), hide_index=True, width="stretch")

    for titre, lot in (("Valeurs lues par Claude, candidat", pieces),
                       ("Valeurs lues par Claude, garant", garant)):
        if lot:
            with st.expander(titre):
                lignes = [{"Fichier": nom, **{k: v for k, v in p.model_dump().items()
                                              if v is not None}} for nom, p in sorted(lot.items())]
                st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch")


with onglet_liste:
    lignes = db.liste_triee(conn, bien_id)
    if not lignes:
        st.info("Aucun dossier pour ce bien : utilisez l'onglet « Importer un dossier ».")
    else:
        par_groupe = pd.Series([l["groupe"] for l in lignes]).value_counts()
        st.write(" · ".join(f"**{par_groupe.get(g, 0)}** en {GROUPES[g]}" for g in GROUPES))
        tableau = pd.DataFrame([{
            "Groupe": l["groupe"] or "non évalué",
            "Dossier": l["reference"],
            "Dépôt": date_fr(l["date_depot"]),
            "Pièces": l["pieces"],
            "Critères": l["criteres"],
            "Contrôles": l["controles"],
            "Ratio": f"{l['ratio']:.2f}".replace(".", ",") if l["ratio"] is not None else "—",
        } for l in lignes])
        st.caption("Trié par groupe, puis du dépôt le plus ancien au plus récent. "
                   "Cliquez sur une ligne pour ouvrir le dossier.")
        # La sélection est un numéro de ligne. Si l'ordre change (Visale cochée, nouvel
        # import), ce numéro désignerait un autre dossier : la clé du tableau dépend donc
        # de l'ordre, ce qui remet la sélection à zéro. Le dossier ouvert, lui, est retenu
        # par son id dans st.session_state et reste affiché.
        ids = [l["id"] for l in lignes]
        cle = f"liste_{bien_id}_{hash(tuple(ids))}"
        selection = st.dataframe(tableau, hide_index=True, width="stretch",
                                 height=min(38 + 35 * len(tableau), 740), key=cle,
                                 on_select="rerun", selection_mode="single-row")
        rangs = selection.selection.rows
        if rangs:
            st.session_state["ouvert"] = ids[rangs[0]]
        elif st.session_state.get("cle_liste") == cle:
            st.session_state["ouvert"] = None          # ligne décochée par le gestionnaire
        st.session_state["cle_liste"] = cle
        if st.session_state.get("ouvert") in ids:
            st.divider()
            afficher_detail(st.session_state["ouvert"])


# --- Onglet 2 : importer un dossier ----------------------------------------------------

with onglet_import:
    # Les champs sont propres à chaque bien (un fichier choisi ne suit pas un changement
    # de bien) ; changer ce numéro les vide après un import réussi.
    n = st.session_state.setdefault("envoi_n", 0)
    reference = prochaine_reference(conn, bien_id)
    st.write(f"Le dossier recevra la référence **{reference}** (jamais le nom du candidat).")

    envoi_candidat = st.file_uploader("Pièces du candidat : un zip, ou les PDF / images",
                                      type=FORMATS, accept_multiple_files=True,
                                      key=f"candidat_{bien_id}_{n}")
    envoi_garant = st.file_uploader("Pièces du garant (facultatif)", type=FORMATS,
                                    accept_multiple_files=True, key=f"garant_{bien_id}_{n}")
    if envoi_garant and not bien.garant_accepte:
        st.warning("Ce bien n'accepte pas de garant : ses pièces seront lues mais ne "
                   "compteront pas dans les critères.")
    visale = st.checkbox("Certificat Visale vu par le gestionnaire", key=f"visale_{bien_id}_{n}")
    c1, c2 = st.columns(2)
    jour = c1.date_input("Date de dépôt", value=datetime.now().date(), format="DD/MM/YYYY",
                         max_value=datetime.now().date(), key=f"jour_{bien_id}_{n}")
    heure = c2.time_input("Heure de dépôt", value=datetime.now().time().replace(second=0,
                          microsecond=0), key=f"heure_{bien_id}_{n}")

    try:
        candidat, ignores = lire_envoi([(f.name, f.getvalue()) for f in envoi_candidat or []])
        garant, ignores_g = lire_envoi([(f.name, f.getvalue()) for f in envoi_garant or []])
    except DepotInvalide as e:
        st.error(str(e))
        candidat, garant, ignores, ignores_g = {}, {}, [], []
    if ignores or ignores_g:
        st.caption(f"Fichiers ignorés (ni PDF ni image) : {', '.join(ignores + ignores_g)}")

    total = len(candidat) + len(garant)
    cle_api = bool(os.getenv("ANTHROPIC_API_KEY"))
    if total:
        st.write(f"{len(candidat)} pièce(s) du candidat, {len(garant)} du garant : "
                 f"{total} lecture(s) par Claude (payantes).")
    if not cle_api:
        st.warning("Clé ANTHROPIC_API_KEY absente du fichier .env : import impossible.")

    if st.button("Lire et importer", type="primary", disabled=not (candidat and cle_api)):
        barre = st.progress(0.0)
        try:
            dossier_id, ref, resultat = deposer(
                conn, bien_id, datetime.combine(jour, heure), candidat, garant or None, visale,
                progression=lambda i, t, nom: barre.progress(i / t, text=f"Lecture de {nom} ({i}/{t})"))
        except (DepotInvalide, ErreurExtraction) as e:
            barre.empty()
            st.error(f"Rien n'a été enregistré : {e}")
        else:
            st.session_state["envoi_n"] = n + 1
            st.toast(f"{ref} importé : {resultat.pieces}, {resultat.criteres}, {resultat.controles}.")
            st.rerun()
