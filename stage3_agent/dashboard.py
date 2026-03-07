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
from src.tracker import ApplicationTracker, backup_tracker_db

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
    today = date.today()
    df["_jours"] = df["date_envoi"].apply(lambda d: (today - d).days if pd.notna(d) else None)
    df["_relance"] = (
        (df["etat"] == "J'ai postulé")
        & df["_jours"].notna()
        & (df["_jours"] >= 10)
        & df["contact"].fillna("").str.strip().ne("")
    )
    return df


# ── Backup quotidien de tracker.db ───────────────────────────────────────────
if "backup_done" not in st.session_state:
    backed_up = backup_tracker_db()
    st.session_state["backup_done"] = True
    if backed_up:
        st.toast(f"💾 Backup tracker.db : {backed_up.name}")

# ── Clôture automatique des candidatures sans retour après 6 semaines ────────
_closed = tracker.auto_close_stale(days=42)
if _closed:
    st.toast(f"⏳ {_closed} candidature(s) classée(s) automatiquement en réponse négative (6 semaines écoulées).")

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
tab1, tab2, tab3 = st.tabs(["📬  Offres pipeline", "📋  Mes candidatures", "📊  Statistiques"])


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
            description = st.text_area("Description du poste", height=120)

            submitted = st.form_submit_button(
                "Ajouter la candidature", type="primary", use_container_width=False
            )
            if submitted:
                if entreprise.strip() and poste.strip():
                    tracker.add(
                        entreprise.strip(), poste.strip(),
                        url_new.strip(), lieu.strip(),
                        etat_new, str(date_envoi),
                        contact.strip(), commentaire.strip(),
                        description.strip(),
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
        fa1, fa2 = st.columns([3, 1])
        etats_defaut = [e for e in ApplicationTracker.ETATS if e != "J'ai reçu une réponse négative"]
        filtre_etats = fa1.multiselect(
            "États affichés",
            options=ApplicationTracker.ETATS,
            default=etats_defaut,
        )
        show_delete = fa2.checkbox("Mode suppression", value=False)

        # Déduplique les noms d'entreprise de façon insensible à la casse (première occurrence gagne)
        _seen: dict[str, str] = {}
        for name in df_apps["entreprise"].dropna().str.strip():
            if name.lower() not in _seen:
                _seen[name.lower()] = name
        entreprises_dispo = sorted(_seen.values(), key=str.lower)

        fb1, fb2 = st.columns([2, 4])
        filtre_entreprise = fb1.selectbox("Entreprise", ["Toutes"] + entreprises_dispo)
        search_app        = fb2.text_input("Rechercher", placeholder="poste, lieu, commentaire…", key="s_app")

        display = df_apps.copy()
        if filtre_etats:
            display = display[display["etat"].isin(filtre_etats)]
        else:
            display = display.iloc[0:0]  # rien sélectionné = rien affiché
        if filtre_entreprise != "Toutes":
            display = display[display["entreprise"].str.lower() == filtre_entreprise.lower()]
        if search_app:
            mask = (
                display["poste"].fillna("").str.contains(search_app, case=False)
                | display["lieu"].fillna("").str.contains(search_app, case=False)
                | display["commentaire"].fillna("").str.contains(search_app, case=False)
                | display["description"].fillna("").str.contains(search_app, case=False)
            )
            display = display[mask]

        # Alerte relances J+10 (calculée sur toutes les candidatures, pas seulement la vue filtrée)
        relance_global = df_apps[df_apps["_relance"]] if "_relance" in df_apps.columns else pd.DataFrame()
        if not relance_global.empty:
            noms = ", ".join(
                f"{r['entreprise']} ({r['_jours']}j)"
                for _, r in relance_global.sort_values("_jours", ascending=False).head(5).iterrows()
            )
            plus = f" + {len(relance_global) - 5} autres" if len(relance_global) > 5 else ""
            st.info(f"⏰ **{len(relance_global)} relance(s) à faire (J+10 dépassé)** : {noms}{plus}")

        # Export CSV + normalisation
        csv_bytes = display[["date_envoi", "entreprise", "poste", "etat", "lieu", "url", "contact", "commentaire"]].to_csv(index=False).encode("utf-8")
        tool_c1, tool_c2 = st.columns([2, 5])
        tool_c1.download_button("⬇️ Exporter CSV", data=csv_bytes, file_name="candidatures.csv", mime="text/csv")
        if tool_c2.button("🔤 Normaliser noms d'entreprise"):
            n = tracker.normalize_entreprises()
            st.success(f"✓ {n} entrée(s) normalisée(s).")
            st.cache_data.clear()
            st.rerun()

        st.caption(f"{len(display)} candidature(s) · États et commentaires éditables directement dans la table.")

        if display.empty:
            st.info("Aucune candidature pour ce filtre.")
        else:
            display = display.reset_index(drop=True)
            original_etats       = display.set_index("id")["etat"].to_dict()
            original_commentaires = display.set_index("id")["commentaire"].to_dict()
            display["_supprimer"] = False
            display["_alerte"]    = display["_relance"].map({True: "⚠️ J+10", False: ""})

            base_cols = ["_alerte", "date_envoi", "entreprise", "poste", "etat", "lieu", "url", "commentaire", "description"]
            cols_show = (["_supprimer"] if show_delete else []) + base_cols
            cols_show = [c for c in cols_show if c in display.columns]

            col_cfg = {
                "_supprimer": st.column_config.CheckboxColumn("🗑️",            width="small"),
                "_alerte":    st.column_config.TextColumn("Alerte",            width="small"),
                "date_envoi": st.column_config.DateColumn("Date", format="DD/MM/YYYY", width="small"),
                "entreprise": st.column_config.TextColumn("Entreprise",        width="small"),
                "poste":      st.column_config.TextColumn("Poste",             width="medium"),
                "etat":       st.column_config.SelectboxColumn(
                                  "État", options=ApplicationTracker.ETATS,    width="medium"),
                "lieu":       st.column_config.TextColumn("Lieu",              width="small"),
                "url":        st.column_config.LinkColumn("Lien",              width="small"),
                "commentaire":st.column_config.TextColumn("Commentaire",       width="small"),
                "description":st.column_config.TextColumn("Description offre", width="large"),
            }

            edited_apps = st.data_editor(
                display[cols_show],
                column_config=col_cfg,
                disabled=["_alerte", "date_envoi", "entreprise", "poste", "lieu", "url", "description"],
                hide_index=True,
                width="stretch",
                height=min(600, 60 + len(display) * 38),
                key="apps_editor",
            )

            # Détecter les changements d'état et de commentaire
            changed_etat = 0
            changed_commentaire = 0
            for i, row in edited_apps.iterrows():
                app_id = display.iloc[i]["id"]
                new_etat = row.get("etat")
                if new_etat and new_etat != original_etats.get(app_id):
                    tracker.update_etat(app_id, new_etat)
                    changed_etat += 1
                new_commentaire = row.get("commentaire", "")
                if new_commentaire != original_commentaires.get(app_id, ""):
                    tracker.update_commentaire(app_id, new_commentaire or "")
                    changed_commentaire += 1

            if changed_etat or changed_commentaire:
                parts = []
                if changed_etat:        parts.append(f"{changed_etat} état(s)")
                if changed_commentaire: parts.append(f"{changed_commentaire} commentaire(s)")
                st.success(f"✓ {' et '.join(parts)} mis à jour.")
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


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Statistiques
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    df_stat = load_applications()

    if df_stat.empty:
        st.info("Aucune candidature.")
    else:
        total      = len(df_stat)
        postule    = len(df_stat[df_stat["etat"].isin(["J'ai postulé", "J'ai relancé",
                                                        "J'ai un entretien",
                                                        "Je n'ai pas reçu de réponse",
                                                        "J'ai reçu une réponse négative"])])
        entretiens = stats_apps["by_etat"].get("J'ai un entretien", 0)
        negatives  = stats_apps["by_etat"].get("J'ai reçu une réponse négative", 0)
        taux_rep   = round(negatives / postule * 100) if postule else 0
        taux_conv  = round(entretiens / postule * 100) if postule else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total candidatures",  total)
        m2.metric("Entretiens obtenus",  entretiens)
        m3.metric("Taux de réponse",     f"{taux_rep} %",  help="Réponses négatives / envoyées")
        m4.metric("Taux de conversion",  f"{taux_conv} %", help="Entretiens / envoyées")

        st.markdown("---")

        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader("Répartition par état")
            etat_counts = df_stat["etat"].value_counts().rename("Candidatures")
            st.bar_chart(etat_counts)

        with col_b:
            st.subheader("Activité par semaine")
            df_wk = df_stat.dropna(subset=["date_envoi"]).copy()
            df_wk["semaine"] = pd.to_datetime(df_wk["date_envoi"]).dt.to_period("W").dt.start_time
            st.bar_chart(df_wk.groupby("semaine").size().rename("Candidatures envoyées"))

        st.markdown("---")
        st.subheader("Top entreprises (volume)")
        top_ent = (
            df_stat.groupby("entreprise")
            .size()
            .sort_values(ascending=False)
            .head(15)
            .rename("Candidatures")
        )
        st.bar_chart(top_ent)

        st.markdown("---")
        st.subheader("Backups tracker.db")
        from config.settings import TRACKER_DB_PATH
        backup_dir = TRACKER_DB_PATH.parent / "backups"
        if backup_dir.exists():
            backups = sorted(backup_dir.glob("tracker_*.db"), reverse=True)
            if backups:
                rows_b = [{"Fichier": f.name, "Taille": f"{f.stat().st_size // 1024} Ko"} for f in backups]
                st.dataframe(pd.DataFrame(rows_b), hide_index=True, use_container_width=True)
            else:
                st.caption("Aucun backup.")
        else:
            st.caption("Dossier backups/ introuvable.")
