"""
Phase 1 — Collecte des offres d'emploi IT en Suisse romande
Sources : Adzuna API + SerpApi Google Jobs
Usage : python -m src.collect
"""

import os
import sys
import json
import time
import logging
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from .utils import make_job_id, normalize_text, retry_request

load_dotenv()

# ============================================================
# Configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = BASE_DIR / "data" / "raw" / "collection_log.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# API keys
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# Délais entre requêtes pour respecter les rate limits des APIs
ADZUNA_DELAY = 1.5   # secondes entre chaque appel Adzuna
SERPAPI_DELAY = 2.0  # secondes entre chaque appel SerpApi

# ============================================================
# Requêtes de recherche par classe cible
# ============================================================
# Stratégie : couvrir chaque classe avec des mots-clés variés pour maximiser
# la diversité des offres collectées. Les classes NOT_RELEVANT, DBA_INFRA et
# APP_SUPPORT sont incluses pour construire un jeu de données équilibré — elles
# seront regroupées sous NOT_RELEVANT lors de la Phase 3.

QUERIES = {
    "DATA_ENGINEERING": [
        "data engineer",
        "ETL developer",
        "dbt engineer",
        "data pipeline",
        "spark engineer",
        "airflow",
        "data platform engineer",
        "ingénieur données",
    ],
    "BI_ANALYTICS": [
        "BI developer",
        "data analyst",
        "power bi",
        "tableau developer",
        "reporting analyst",
        "business intelligence",
        "analyste données",
    ],
    "DBA_INFRA": [
        "database administrator",
        "DBA",
        "oracle DBA",
        "SQL server administrator",
        "postgresql administrator",
        "database engineer",
    ],
    "APP_SUPPORT": [
        "application support",
        "support applicatif",
        "helpdesk L2",
        "helpdesk L3",
        "QA fonctionnel",
        "support informatique",
        "technicien support",
    ],
    "NOT_RELEVANT": [
        "frontend developer",
        "UX designer",
        "marketing manager",
        "ressources humaines",
        "commercial",
        "chef de projet",
        "comptable",
        "vendeur",
        "assistant administratif",
        "développeur web",
    ],
}

# Adzuna : recherche sur toute la Suisse, sans filtre ville — meilleur volume
ADZUNA_LOCATIONS = [""]  # chaîne vide = pas de filtre géographique

# SerpApi : localisations spécifiques en anglais (les noms français causent
# des erreurs 400 côté API). Limitées à la Suisse romande pour notre profil cible.
SERPAPI_LOCATIONS = [
    "Lausanne, Switzerland",
    "Geneva, Switzerland",
    "Vaud, Switzerland",
    "Fribourg, Switzerland",
    "Neuchatel, Switzerland",
]

# ============================================================
# Collecteur Adzuna
# ============================================================


def collect_adzuna(keyword: str, location: str = "", max_pages: int = 5) -> list[dict]:
    """Collecte les offres depuis l'API Adzuna pour la Suisse (pays 'ch').

    Pagine jusqu'à max_pages pages de 50 résultats chacune.
    S'arrête tôt si la dernière page retourne moins de 50 résultats
    (signal que l'on a atteint la fin du jeu de résultats).
    """
    jobs = []

    for page in range(1, max_pages + 1):
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": keyword,
            "results_per_page": 50,
            "content-type": "application/json",
        }
        if location:
            params["where"] = location

        # La closure est définie et appelée immédiatement via retry_request,
        # donc il n'y a pas de problème de liaison tardive sur la variable `page`.
        def do_request():
            resp = requests.get(
                f"https://api.adzuna.com/v1/api/jobs/ch/search/{page}",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_request(do_request)
        if not data:
            break

        results = data.get("results", [])
        if not results:
            break

        for r in results:
            job = {
                "id": make_job_id("adzuna", r.get("title", ""), r.get("company", {}).get("display_name", "")),
                "source": "adzuna",
                "title": r.get("title", "").strip(),
                "company": r.get("company", {}).get("display_name", "").strip(),
                "location": r.get("location", {}).get("display_name", "").strip(),
                "description": r.get("description", "").strip(),
                "url": r.get("redirect_url", ""),
                "date_collected": date.today().isoformat(),
                # Adzuna retourne un ISO datetime complet — on extrait juste la date
                "date_posted": r.get("created", "")[:10] if r.get("created") else "",
            }
            jobs.append(job)

        log.info(f"  Adzuna [{keyword}] page {page}: {len(results)} offres")
        time.sleep(ADZUNA_DELAY)

        # Moins de 50 résultats = dernière page atteinte, inutile de continuer
        if len(results) < 50:
            break

    return jobs


# ============================================================
# Collecteur SerpApi
# ============================================================

# Compteur global de requêtes SerpApi pour ne pas dépasser le budget mensuel.
# Partagé entre tous les appels à collect_serpapi() dans la même session.
serpapi_calls_count = 0


def collect_serpapi(keyword: str, location: str, max_pages: int = 2) -> list[dict]:
    """Collecte les offres depuis SerpApi (Google Jobs).

    SerpApi facture à la requête ; le compteur global serpapi_calls_count
    est incrémenté après chaque appel (réussi ou non) pour refléter la
    consommation réelle du quota.

    Pagination Google Jobs : les pages suivantes démarrent à start=10, 20...
    (contrairement à Adzuna qui numérote les pages 1, 2, 3...).
    """
    global serpapi_calls_count
    jobs = []
    start = 0

    for page in range(max_pages):
        # Vérification du budget avant chaque requête (marge de sécurité de 10)
        if serpapi_calls_count >= 190:
            log.warning("SerpApi budget limit reached (190/200). Stopping SerpApi collection.")
            break

        params = {
            "engine": "google_jobs",
            "q": keyword,
            "location": location,
            "hl": "fr",   # langue d'interface : français
            "gl": "ch",   # pays cible : Suisse
            "api_key": SERPAPI_KEY,
        }
        if start > 0:
            params["start"] = start

        def do_request():
            resp = requests.get(
                "https://serpapi.com/search",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_request(do_request)
        # On compte la requête même en cas d'échec — elle a été consommée
        serpapi_calls_count += 1

        if not data:
            break

        results = data.get("jobs_results", [])
        if not results:
            break

        for r in results:
            job = {
                "id": make_job_id("serpapi", r.get("title", ""), r.get("company_name", "")),
                "source": "serpapi",
                "title": r.get("title", "").strip(),
                "company": r.get("company_name", "").strip(),
                "location": r.get("location", "").strip(),
                "description": r.get("description", "").strip(),
                # SerpApi fournit share_link en priorité ; fallback sur le premier related_link
                "url": r.get("share_link", r.get("related_links", [{}])[0].get("link", "") if r.get("related_links") else ""),
                "date_collected": date.today().isoformat(),
                # La date est dans les extensions détectées, au format texte ("3 days ago", etc.)
                "date_posted": r.get("detected_extensions", {}).get("posted_at", ""),
            }
            jobs.append(job)

        log.info(f"  SerpApi [{keyword} | {location}] page {page + 1}: {len(results)} offres (total API calls: {serpapi_calls_count})")
        time.sleep(SERPAPI_DELAY)

        # Google Jobs utilise start=0, 10, 20... pour la pagination
        start += 10

        # Google Jobs retourne rarement plus de 3 pages utiles
        if len(results) < 10:
            break

    return jobs


# ============================================================
# Déduplication
# ============================================================


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Supprime les doublons en se basant sur (titre normalisé, entreprise normalisée).

    L'URL n'est pas utilisée comme clé de déduplication car le même poste peut
    apparaître sur Adzuna et SerpApi avec des URLs différentes. La paire
    (titre, entreprise) est un signal plus robuste, au prix d'un léger risque
    de faux positifs si une entreprise publie deux postes identiquement nommés.
    L'ordre d'insertion est préservé (first-seen wins).
    """
    seen = set()
    unique = []

    for job in jobs:
        key = (normalize_text(job["title"]), normalize_text(job["company"]))
        if key not in seen:
            seen.add(key)
            unique.append(job)

    return unique


# ============================================================
# Pipeline principal de collecte
# ============================================================


def main():
    log.info("=" * 60)
    log.info("PHASE 1 — Collecte des offres d'emploi")
    log.info("=" * 60)

    all_jobs = []
    stats = {"adzuna": 0, "serpapi": 0}
    stats_by_class = {}

    # --- Collecte Adzuna ---
    # Adzuna : pas de limite de quota stricte, donc on interroge toutes les classes
    # et tous les mots-clés avec pagination complète.
    log.info("\n--- Collecte Adzuna (Suisse entière) ---")
    for target_class, keywords in QUERIES.items():
        class_count = 0
        for keyword in keywords:
            jobs = collect_adzuna(keyword)
            all_jobs.extend(jobs)
            stats["adzuna"] += len(jobs)
            class_count += len(jobs)
        stats_by_class[f"adzuna_{target_class}"] = class_count
        log.info(f"  Adzuna {target_class}: {class_count} offres brutes")

    # --- Collecte SerpApi ---
    # SerpApi : budget limité (200 requêtes/mois sur le plan gratuit).
    # Stratégie : 1 seule page par combinaison (keyword, location), avec rotation
    # des localisations en round-robin pour couvrir toutes les villes romandes.
    log.info("\n--- Collecte SerpApi (villes romandes) ---")

    serpapi_combos = []
    for target_class, keywords in QUERIES.items():
        for i, keyword in enumerate(keywords):
            # Rotation circulaire des localisations pour équilibrer la couverture géographique
            loc = SERPAPI_LOCATIONS[i % len(SERPAPI_LOCATIONS)]
            serpapi_combos.append((target_class, keyword, loc))

    log.info(f"  Combinaisons SerpApi planifiées: {len(serpapi_combos)} (max 1 page chacune)")

    for target_class, keyword, location in serpapi_combos:
        if serpapi_calls_count >= 190:
            log.warning("Budget SerpApi atteint. Arrêt.")
            break
        jobs = collect_serpapi(keyword, location, max_pages=1)
        all_jobs.extend(jobs)
        stats["serpapi"] += len(jobs)
        key = f"serpapi_{target_class}"
        stats_by_class[key] = stats_by_class.get(key, 0) + len(jobs)

    # --- Déduplication globale ---
    # Effectuée après la collecte complète pour supprimer les doublons
    # inter-sources (même offre trouvée sur Adzuna ET SerpApi).
    total_raw = len(all_jobs)
    all_jobs = deduplicate(all_jobs)
    total_dedup = len(all_jobs)
    duplicates_removed = total_raw - total_dedup

    # --- Sauvegarde ---
    # Le nom de fichier inclut la date pour permettre des collectes incrémentales.
    output_file = RAW_DIR / f"jobs_raw_{date.today().isoformat()}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)

    # --- Résumé ---
    log.info("\n" + "=" * 60)
    log.info("RÉSUMÉ DE LA COLLECTE")
    log.info("=" * 60)
    log.info(f"  Total brut:              {total_raw}")
    log.info(f"  Doublons supprimés:      {duplicates_removed}")
    log.info(f"  Total après dédup:       {total_dedup}")
    log.info(f"  Répartition par source:")
    log.info(f"    Adzuna:  {stats['adzuna']}")
    log.info(f"    SerpApi: {stats['serpapi']} (requêtes API: {serpapi_calls_count}/200)")
    log.info(f"  Fichier sauvegardé: {output_file}")

    log.info(f"\n  Répartition par classe cible (approximative, basée sur les mots-clés):")
    for key, count in sorted(stats_by_class.items()):
        log.info(f"    {key}: {count}")

    # Seuil d'alerte : en dessous de 200 offres, le dataset sera trop petit
    # pour un fine-tuning de qualité (objectif : ≥ 300 exemples étiquetés).
    if total_dedup < 200:
        log.warning(
            f"\n⚠️  Seulement {total_dedup} offres collectées. "
            "Le marché suisse sur ces APIs est petit. Options :\n"
            "  1. Relancer avec des mots-clés plus larges (ex: 'informatique', 'IT')\n"
            "  2. Augmenter le budget SerpApi\n"
            "  3. Ajouter des localisations (Berne, Zürich — bilingues)\n"
        )

    log.info("\nPhase 1 terminée.")
    return all_jobs


if __name__ == "__main__":
    main()
