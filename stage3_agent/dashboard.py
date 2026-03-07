"""
dashboard.py — Tableau de bord de recherche d'emploi
=====================================================
Deux onglets :
  1. Offres pipeline   — offres reçues par email, marquage candidature en un clic
  2. Mes candidatures  — suivi manuel (ajout, édition état, suppression)

Usage :
    cd stage3_agent
    python -m streamlit run dashboard.py
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent
_REPO = _ROOT.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_REPO))

from config.settings import DB_PATH
from src.deduplicator import Deduplicator
from src.tracker import ApplicationTracker

# ── Config ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Job Dashboard", page_icon="🎯", layout="wide")

st.markdown("""
<style>
  .stApp { background-color: #F8F9FB; }

  .hero {
    background: linear-gradient(135deg, #5B21B6 0%, #7C3AED 60%, #A78BFA 100%);
    border-radius: 14px;
    padding: 1.6rem 2rem 1.4rem;
    margin-bottom: 1.5rem;
    color: white;
  }
  .hero h1 { font-size: 1.7rem; font-weight: 700; margin: 0 0 0.2rem; }
  .hero p  { font-size: 0.9rem; opacity: 0.85; margin: 0; }

  [data-testid="metric-container"] {
    background: white;
    border: 1px solid #E9ECF3;
    border-radius: 12px;
    padding: 1rem 1.2rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  }
  [data-testid="metric-container"] label {
    color: #6B7280; font-size: 0.78rem; font-weight: 500;
  }
  [data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #111827; font-size: 1.7rem; font-weight: 700;
  }

  .stTabs [data-baseweb="tab-list"] {
    gap: 6px; background: transparent; border-bottom: 2px solid #E9ECF3;
  }
  .stTabs [data-baseweb="tab"] {
    border-radius: 8px 8px 0 0;
    padding: 0.5rem 1.2rem;
    font-weight: 500;
    color: #6B7280;
    background: transparent;
  }
  .stTabs [aria-selected="true"] {
    color: #5B21B6 !important;
    border-bottom: 2px solid #5B21B6;
    background: white !important;
  }

  div[data-testid="stButton"] button[kind="primary"] {
    background: #5B21B6; border: none; border-radius: 8px; font-weight: 600;
  }
  div[data-testid="stButton"] button[kind="primary"]:hover { background: #4C1D95; }

  hr { border-color: #E9ECF3; }
</style>
""", unsafe_allow_html=True)

# ── Instances ─────────────────────────────────────────────────────────────────
dedup   = Deduplicator()
tracker = ApplicationTracker()

LABEL_NAMES = {"DATA_ENGINEERING": "Data Eng.", "BI_ANALYTICS": "BI / Analytics"}


@st.cache_data(ttl=30)
def load_pipeline_jobs() -> pd.DataFrame:
    import sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT id, title, company, url, sent_date, score, label, applied_at "
            "FROM seen_jobs WHERE in_email=1 ORDER BY sent_date DESC",
            conn,
        )
    if df.empty:
        return df
    df["candidaté"] = df["applied_at"].notna()
    df["score_pct"] = (df["score"].fillna(0) * 100).round(0).astype("Int64")
    df["label_fmt"] = df["label"].map(LABEL_NAMES).fillna(df["label"].fillna("—"))
    return df


@st.cache_data(ttl=15)
def load_applications() -> pd.DataFrame:
    rows = tracker.get_all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date_envoi"] = pd.to_datetime(df["date_envoi"], errors="coerce").dt.date
    # Supprimer les dates aberrantes (avant 2020)
    df.loc[df["date_envoi"].notna() & (df["date_envoi"] < date(2020, 1, 1)), "date_envoi"] = None
    return df


# ── Hero header ───────────────────────────────────────────────────────────────
stats_db   = dedup.get_stats()
stats_apps = tracker.get_stats()

st.markdown("""
<div class="hero">
  <h1>🎯 Job Dashboard — Alois Tendil</h1>
  <p>Suivi de la recherche d'emploi · Lausanne / Genève</p>
</div>
""", unsafe_allow_html=True)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Offres vues (pipeline)", stats_db["total"])
c2.metric("Envoyées par email",     stats_db["in_email"])
c3.metric("Candidatures pipeline",  stats_db["applied"])
c4.metric("Candidatures manuelles", stats_apps["total"])
c5.metric("Entretiens", stats_apps["by_etat"].get("J'ai un entretien", 0))

st.markdown("<br>", unsafe_allow_html=True)
tab1, tab2 = st.tabs(["📬  Offres pipeline", "📋  Mes candidatures"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Offres pipeline
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    df_pipe = load_pipeline_jobs()

    if df_pipe.empty:
        st.info("Aucune offre envoyée par email. Lance un run du pipeline.")
    else:
        fc1, fc2, fc3 = st.columns([2, 2, 2])
        filtre   = fc1.radio("Afficher", ["Toutes", "À traiter", "Candidatées"], horizontal=True)
        date_min = fc2.date_input("Depuis le", value=None)
        search   = fc3.text_input("Rechercher", placeholder="titre, entreprise…")

        filtered = df_pipe.copy()
        if filtre == "À traiter":
            filtered = filtered[~filtered["candidaté"]]
        elif filtre == "Candidatées":
            filtered = filtered[filtered["candidaté"]]
        if date_min:
            filtered = filtered[filtered["sent_date"] >= str(date_min)]
        if search:
            mask = (
                filtered["title"].fillna("").str.contains(search, case=False)
                | filtered["company"].fillna("").str.contains(search, case=False)
            )
            filtered = filtered[mask]

        st.caption(f"{len(filtered)} offre(s) — cocher **Candidaté** pour enregistrer, décocher pour annuler.")

        if not filtered.empty:
            original = filtered.set_index("url")["candidaté"].to_dict()

            edited = st.data_editor(
                filtered[["candidaté", "sent_date", "score_pct", "label_fmt", "title", "company", "url"]],
                column_config={
                    "candidaté":  st.column_config.CheckboxColumn("Candidaté",  width="small"),
                    "sent_date":  st.column_config.TextColumn("Date",           width="small"),
                    "score_pct":  st.column_config.NumberColumn("Score %",      width="small"),
                    "label_fmt":  st.column_config.TextColumn("Catégorie",      width="medium"),
                    "title":      st.column_config.TextColumn("Titre",          width="large"),
                    "company":    st.column_config.TextColumn("Entreprise",     width="medium"),
                    "url":        st.column_config.LinkColumn("Lien",           width="small"),
                },
                disabled=["sent_date", "score_pct", "label_fmt", "title", "company", "url"],
                hide_index=True,
                use_container_width=True,
            )

            changed = 0
            for _, row in edited.iterrows():
                url = row["url"]
                if url and url in original and row["candidaté"] != original[url]:
                    if row["candidaté"]:
                        dedup.mark_applied(url)
                    else:
                        dedup.unmark_applied(url)
                    changed += 1

            if changed:
                st.success(f"✓ {changed} modification(s) enregistrée(s).")
                st.cache_data.clear()
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Mes candidatures
# ══════════════════════════════════════════════════════════════════════════════
with tab2:

    # Stats par état
    by_etat = stats_apps["by_etat"]
    if by_etat:
        cols_etat = st.columns(len(ApplicationTracker.ETATS))
        for i, etat in enumerate(ApplicationTracker.ETATS):
            count = by_etat.get(etat, 0)
            color = ApplicationTracker.ETAT_COLORS[etat]
            cols_etat[i].markdown(
                f'<div style="background:white;border:1px solid #E9ECF3;border-radius:10px;'
                f'padding:0.7rem 0.8rem;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.05);">'
                f'<div style="font-size:1.5rem;font-weight:700;color:{color};">{count}</div>'
                f'<div style="font-size:0.72rem;color:#6B7280;margin-top:2px;line-height:1.3;">{etat}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)

    # Formulaire d'ajout
    with st.expander("➕  Nouvelle candidature", expanded=False):
        with st.form("add_application", clear_on_submit=True):
            r1c1, r1c2 = st.columns(2)
            entreprise  = r1c1.text_input("Entreprise *")
            poste       = r1c2.text_input("Poste *")

            r2c1, r2c2, r2c3 = st.columns(3)
            lieu        = r2c1.text_input("Lieu")
            etat_new    = r2c2.selectbox("État", ApplicationTracker.ETATS)
            date_envoi  = r2c3.date_input("Date d'envoi", value=date.today())

            url_new     = st.text_input("URL de l'offre")
            contact     = st.text_input("Contact (nom / email / tél)")
            commentaire = st.text_area("Commentaire", height=80)

            submitted = st.form_submit_button(
                "Ajouter la candidature", type="primary", use_container_width=True
            )
            if submitted:
                if entreprise.strip() and poste.strip():
                    tracker.add(
                        entreprise.strip(), poste.strip(),
                        url_new.strip(), lieu.strip(),
                        etat_new, str(date_envoi),
                        contact.strip(), commentaire.strip(),
                    )
                    st.success(f"✓ Candidature ajoutée : {entreprise} — {poste}")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Entreprise et Poste sont obligatoires.")

    # Table des candidatures
    df_apps = load_applications()

    if df_apps.empty:
        st.info("Aucune candidature. Utilisez le formulaire ci-dessus pour en ajouter.")
    else:
        fa1, fa2, fa3 = st.columns([2, 2, 2])
        filtre_etat = fa1.selectbox("Filtrer par état", ["Tous"] + ApplicationTracker.ETATS)
        search_app  = fa2.text_input("Rechercher", placeholder="entreprise, poste, lieu…", key="s_app")
        show_delete = fa3.checkbox("Mode suppression", value=False)

        display = df_apps.copy()
        if filtre_etat != "Tous":
            display = display[display["etat"] == filtre_etat]
        if search_app:
            mask = (
                display["entreprise"].fillna("").str.contains(search_app, case=False)
                | display["poste"].fillna("").str.contains(search_app, case=False)
                | display["lieu"].fillna("").str.contains(search_app, case=False)
            )
            display = display[mask]

        st.caption(f"{len(display)} candidature(s) · Modifiez l'état directement dans la colonne État.")

        if display.empty:
            st.info("Aucune candidature pour ce filtre.")
        else:
            original_etats = display.set_index("id")["etat"].to_dict()
            display["_supprimer"] = False

            base_cols = ["date_envoi", "entreprise", "poste", "etat", "lieu", "url", "commentaire"]
            cols_show = (["_supprimer"] if show_delete else []) + base_cols
            cols_show = [c for c in cols_show if c in display.columns]

            col_cfg = {
                "_supprimer": st.column_config.CheckboxColumn("🗑️",           width="small"),
                "date_envoi": st.column_config.DateColumn("Date", format="DD/MM/YYYY", width="small"),
                "entreprise": st.column_config.TextColumn("Entreprise",       width="medium"),
                "poste":      st.column_config.TextColumn("Poste",            width="large"),
                "etat":       st.column_config.SelectboxColumn(
                                  "État", options=ApplicationTracker.ETATS,   width="medium"),
                "lieu":       st.column_config.TextColumn("Lieu",             width="small"),
                "url":        st.column_config.LinkColumn("Lien",             width="small"),
                "commentaire":st.column_config.TextColumn("Commentaire",      width="medium"),
            }

            edited_apps = st.data_editor(
                display[cols_show],
                column_config=col_cfg,
                disabled=["date_envoi", "entreprise", "poste", "lieu", "url", "commentaire"],
                hide_index=True,
                use_container_width=True,
                height=min(600, 60 + len(display) * 38),
                key="apps_editor",
            )

            # Détecter les changements d'état
            changed_etat = 0
            for i, row in edited_apps.iterrows():
                app_id = display.iloc[i]["id"]
                new_etat = row.get("etat")
                if new_etat and new_etat != original_etats.get(app_id):
                    tracker.update_etat(app_id, new_etat)
                    changed_etat += 1

            if changed_etat:
                st.success(f"✓ {changed_etat} état(s) mis à jour.")
                st.cache_data.clear()
                st.rerun()

            # Suppression
            if show_delete and "_supprimer" in edited_apps.columns:
                to_del = edited_apps[edited_apps["_supprimer"]].index.tolist()
                if to_del:
                    st.warning(f"{len(to_del)} candidature(s) sélectionnée(s).")
                    if st.button(f"🗑️ Confirmer la suppression ({len(to_del)})", type="primary"):
                        for i in to_del:
                            tracker.delete(display.iloc[i]["id"])
                        st.success(f"✓ {len(to_del)} supprimée(s).")
                        st.cache_data.clear()
                        st.rerun()
