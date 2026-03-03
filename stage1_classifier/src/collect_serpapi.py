"""
Phase 1c — Collecte complémentaire SerpApi (paramètres corrigés)
Utilise des noms de villes en anglais (gl=ch + hl=fr).

Contexte : collect.py utilisait des noms français ("Lausanne, Suisse") qui
causaient des erreurs 400. Ce fichier corrige le problème en utilisant les
noms anglais attendus par l'API Google Jobs.

Usage : python -m src.collect_serpapi
"""

import os
import json
import time
import logging
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from .utils import make_job_id, normalize_text

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(RAW_DIR / "collection_log.txt", mode="a", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
SERPAPI_DELAY = 2.0

# Noms de villes en anglais — requis par l'API Google Jobs.
# "Switzerland" (sans ville) est inclus pour capturer les offres en remote
# ou sans localisation précise.
LOCATIONS = [
    "Geneva, Switzerland",
    "Lausanne, Switzerland",
    "Switzerland",
]

# Sous-ensemble des mots-clés de collect.py, sélectionné pour rester
# dans le budget (~150 requêtes sur les ~850 restants du quota mensuel).
QUERIES = {
    "DATA_ENGINEERING": ["data engineer", "ETL developer", "data pipeline", "airflow engineer"],
    "BI_ANALYTICS": ["BI developer", "data analyst", "power bi", "business intelligence"],
    "DBA_INFRA": ["database administrator", "DBA", "oracle", "SQL server"],
    "APP_SUPPORT": ["application support", "helpdesk", "support informatique"],
    "NOT_RELEVANT": ["frontend developer", "marketing manager", "chef de projet", "commercial"],
}

# Compteur global pour ne pas dépasser le budget alloué à ce script.
# Les ~850 requêtes restantes sont réservées pour l'agent de monitoring quotidien.
api_calls = 0
MAX_CALLS = 150



def collect_serpapi(keyword, location):
    """Effectue une requête SerpApi et retourne les offres trouvées.

    Contrairement à collect.py, ce script n'utilise pas retry_request car les
    erreurs SerpApi sont souvent liées aux paramètres (et non au réseau) et
    doivent être traitées différemment (log + continuer plutôt que retry).
    """
    global api_calls
    jobs = []

    if api_calls >= MAX_CALLS:
        return jobs

    params = {
        "engine": "google_jobs",
        "q": keyword,
        "location": location,
        "gl": "ch",   # Pays : Suisse
        "hl": "fr",   # Langue d'interface : français
        "api_key": SERPAPI_KEY,
    }

    try:
        resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
        api_calls += 1
        data = resp.json()

        # SerpApi retourne les erreurs dans le corps JSON plutôt qu'en HTTP 4xx
        if "error" in data:
            log.warning(f"  SerpApi erreur [{keyword} | {location}]: {data['error']}")
            return jobs

        results = data.get("jobs_results", [])
        for r in results:
            job = {
                "id": make_job_id("serpapi", r.get("title", ""), r.get("company_name", "")),
                "source": "serpapi",
                "title": r.get("title", "").strip(),
                "company": r.get("company_name", "").strip(),
                "location": r.get("location", "").strip(),
                "description": r.get("description", "").strip(),
                "url": r.get("share_link", ""),
                "date_collected": date.today().isoformat(),
                "date_posted": r.get("detected_extensions", {}).get("posted_at", ""),
            }
            jobs.append(job)

        log.info(f"  [{keyword} | {location}] {len(results)} offres (API call #{api_calls})")

    except Exception as e:
        log.error(f"  Exception [{keyword} | {location}]: {e}")
        # On incrémente quand même — la requête a potentiellement été comptabilisée
        api_calls += 1

    time.sleep(SERPAPI_DELAY)
    return jobs


def main():
    global api_calls

    log.info("=" * 60)
    log.info("PHASE 1c — Collecte complémentaire SerpApi")
    log.info(f"Budget: {MAX_CALLS} requêtes max")
    log.info("=" * 60)

    # Chargement du fichier existant pour enrichissement
    existing_file = None
    for f in sorted(RAW_DIR.glob("jobs_raw_*.json"), reverse=True):
        existing_file = f
        break

    if existing_file:
        with open(existing_file, "r", encoding="utf-8") as f:
            existing_jobs = json.load(f)
        log.info(f"Fichier existant: {existing_file.name} ({len(existing_jobs)} offres)")
    else:
        existing_jobs = []

    # Génération du plan de collecte : toutes les combinaisons (classe, keyword, location)
    # Le total est affiché pour vérifier qu'on reste dans le budget
    combos = []
    for target_class, keywords in QUERIES.items():
        for keyword in keywords:
            for location in LOCATIONS:
                combos.append((target_class, keyword, location))

    log.info(f"Combinaisons planifiées: {len(combos)} (plafond: {MAX_CALLS})")

    new_jobs = []
    stats_by_class = {}

    for target_class, keyword, location in combos:
        if api_calls >= MAX_CALLS:
            log.warning(f"Budget atteint ({api_calls}/{MAX_CALLS}). Arrêt.")
            break

        jobs = collect_serpapi(keyword, location)
        new_jobs.extend(jobs)
        stats_by_class[target_class] = stats_by_class.get(target_class, 0) + len(jobs)

    # Fusion et déduplication
    all_jobs = existing_jobs + new_jobs
    seen = set()
    unique = []
    for job in all_jobs:
        key = (normalize_text(job["title"]), normalize_text(job["company"]))
        if key not in seen:
            seen.add(key)
            unique.append(job)

    added = len(unique) - len(existing_jobs)

    # Sauvegarde (écrase le fichier du jour)
    output_file = RAW_DIR / f"jobs_raw_{date.today().isoformat()}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    log.info("\n" + "=" * 60)
    log.info("RÉSUMÉ")
    log.info("=" * 60)
    log.info(f"  Requêtes SerpApi utilisées: {api_calls}/{MAX_CALLS}")
    log.info(f"  Nouvelles offres brutes:    {len(new_jobs)}")
    log.info(f"  Nouvelles uniques ajoutées: {added}")
    log.info(f"  Total final:                {len(unique)}")
    log.info(f"  Par classe (SerpApi):")
    for cls, count in sorted(stats_by_class.items()):
        log.info(f"    {cls}: {count}")
    log.info(f"  Fichier: {output_file}")


if __name__ == "__main__":
    main()
