"""
Phase 1b — Collecte ciblée DBA_INFRA
Mots-clés élargis pour compenser le faible volume de cette classe sur Adzuna.
Usage : python -m src.collect_dba
"""

import os
import json
import time
import logging
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from .utils import make_job_id, normalize_text, retry_request

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        # Mode append pour ne pas écraser le log de la Phase 1
        logging.FileHandler(RAW_DIR / "collection_log.txt", mode="a", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")
ADZUNA_DELAY = 1.5

# Mots-clés volontairement plus larges que dans collect.py pour capturer
# des profils DBA/infra qui n'utilisent pas explicitement le terme "DBA".
# Ces offres seront remappées vers NOT_RELEVANT en Phase 3 si elles ne
# correspondent pas à un profil Data Engineering ou BI.
DBA_KEYWORDS = [
    "oracle",
    "SQL server",
    "admin base de données",
    "administrateur base",
    "database",
    "infrastructure data",
    "data management",
    "MySQL administrator",
    "MongoDB",
    "NoSQL",
    "ingénieur base de données",
    "data infrastructure",
    "système information",
    "administrateur système",
    "sysadmin",
]



def collect_adzuna(keyword, max_pages=5):
    """Collecte les offres Adzuna pour un mot-clé donné (Suisse entière)."""
    jobs = []
    for page in range(1, max_pages + 1):
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": keyword,
            "results_per_page": 50,
            "content-type": "application/json",
        }

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
                "date_posted": r.get("created", "")[:10] if r.get("created") else "",
            }
            jobs.append(job)

        log.info(f"  Adzuna [{keyword}] page {page}: {len(results)} offres")
        time.sleep(ADZUNA_DELAY)

        if len(results) < 50:
            break

    return jobs


def main():
    log.info("=" * 60)
    log.info("PHASE 1b — Collecte ciblée DBA_INFRA")
    log.info("=" * 60)

    # Chargement du fichier de collecte existant pour enrichissement incrémental
    existing_file = None
    for f in sorted(RAW_DIR.glob("jobs_raw_*.json"), reverse=True):
        existing_file = f
        break

    if existing_file:
        with open(existing_file, "r", encoding="utf-8") as f:
            existing_jobs = json.load(f)
        log.info(f"Fichier existant chargé: {existing_file.name} ({len(existing_jobs)} offres)")
    else:
        existing_jobs = []
        log.warning("Aucun fichier existant trouvé. Collecte à partir de zéro.")

    # Collecte des nouvelles offres DBA
    new_jobs = []
    for keyword in DBA_KEYWORDS:
        jobs = collect_adzuna(keyword)
        new_jobs.extend(jobs)
        log.info(f"  '{keyword}': {len(jobs)} offres")

    log.info(f"\nTotal nouvelles offres brutes: {len(new_jobs)}")

    # Fusion et déduplication avec les données existantes.
    # Les données existantes sont placées en premier pour que la déduplication
    # conserve les entrées d'origine (first-seen wins).
    all_jobs = existing_jobs + new_jobs

    seen = set()
    unique = []
    for job in all_jobs:
        key = (normalize_text(job["title"]), normalize_text(job["company"]))
        if key not in seen:
            seen.add(key)
            unique.append(job)

    added = len(unique) - len(existing_jobs)

    # Écrasement du fichier du jour avec les données enrichies
    output_file = RAW_DIR / f"jobs_raw_{date.today().isoformat()}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    log.info("\n" + "=" * 60)
    log.info("RÉSUMÉ")
    log.info("=" * 60)
    log.info(f"  Offres existantes:       {len(existing_jobs)}")
    log.info(f"  Nouvelles brutes:        {len(new_jobs)}")
    log.info(f"  Nouvelles uniques:       {added}")
    log.info(f"  Total final:             {len(unique)}")
    log.info(f"  Fichier: {output_file}")


if __name__ == "__main__":
    main()
