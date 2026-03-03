"""
test_collect.py — Script de test interactif pour le collector
=============================================================
Teste chaque source individuellement et affiche les résultats.
NE consomme que quelques requêtes SerpApi.

Usage (depuis la racine du projet job-alert-agent/) :
    python scripts/test_collect.py              # Toutes les sources
    python scripts/test_collect.py --source adzuna
    python scripts/test_collect.py --source serpapi
    python scripts/test_collect.py --source indeed
    python scripts/test_collect.py --source all --save  # Sauvegarde JSON
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Force UTF-8 sur Windows (évite les UnicodeEncodeError avec cp1252)
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# Racine du projet
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_collect")


def print_job(job: dict, i: int):
    """Affiche une offre de manière lisible."""
    print(f"\n  [{i+1}] {job['title']}")
    print(f"       {job['company']} | {job['location']}")
    print(f"       Date : {job['date_posted']} | Source : {job['source']}")
    print(f"       URL  : {job['url'][:80]}")
    desc = job.get("description", "")
    if desc:
        words = desc.split()
        preview = " ".join(words[:20])
        print(f"       Desc : {preview}{'...' if len(words) > 20 else ''}")


def test_adzuna():
    from config.settings import ADZUNA_APP_ID, ADZUNA_APP_KEY
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("⚠️  ADZUNA_APP_ID ou ADZUNA_APP_KEY manquant dans .env — skip")
        return []

    print("\n" + "="*60)
    print("SOURCE : Adzuna")
    print("="*60)

    # Test sur une seule requête pour limiter les appels
    import requests
    from config.settings import API_TIMEOUT, MAX_DAYS_OLD

    url = "https://api.adzuna.com/v1/api/jobs/ch/search/1"
    params = {
        "app_id":           ADZUNA_APP_ID,
        "app_key":          ADZUNA_APP_KEY,
        "what":             "data engineer",
        "where":            "Geneva",
        "max_days_old":     MAX_DAYS_OLD,
        "results_per_page": 10,
    }

    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        total = data.get("count", "?")
        print(f"  → {len(results)} résultats retournés (total Adzuna pour cette requête : {total})")

        jobs = []
        for item in results:
            from src.collector import _job_id, _normalize_date
            jobs.append({
                "id":          _job_id(item.get("redirect_url", ""), item.get("title", "")),
                "title":       item.get("title", ""),
                "company":     item.get("company", {}).get("display_name", ""),
                "location":    item.get("location", {}).get("display_name", ""),
                "description": item.get("description", ""),
                "url":         item.get("redirect_url", ""),
                "date_posted": _normalize_date(item.get("created", "")),
                "source":      "adzuna",
            })

        for i, job in enumerate(jobs[:5]):
            print_job(job, i)

        print(f"\n  ✓ Adzuna OK — {len(jobs)} offres (1 requête test)")
        return jobs

    except Exception as e:
        print(f"  ✗ Adzuna ERREUR : {e}")
        return []


def test_serpapi():
    from config.settings import SERPAPI_KEY
    if not SERPAPI_KEY:
        print("⚠️  SERPAPI_KEY manquant dans .env — skip")
        return []

    print("\n" + "="*60)
    print("SOURCE : SerpApi Google Jobs")
    print("="*60)

    import requests
    from config.settings import API_TIMEOUT
    from datetime import date

    url = "https://serpapi.com/search"
    params = {
        "engine":   "google_jobs",
        "q":        "data engineer",
        "location": "Geneva, Switzerland",
        "gl":       "ch",
        "hl":       "fr",
        "chips":    "date_posted:3days",
        "api_key":  SERPAPI_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        # Vérifier si erreur API
        if "error" in data:
            print(f"  ✗ SerpApi erreur API : {data['error']}")
            return []

        results = data.get("jobs_results", [])
        print(f"  → {len(results)} offres retournées")

        jobs = []
        for item in results:
            from src.collector import _job_id
            url_job = item.get("share_link", "")
            if not url_job:
                apply_opts = item.get("apply_options", [])
                url_job = apply_opts[0].get("link", "") if apply_opts else ""
            jobs.append({
                "id":          _job_id(item.get("job_id", "") or url_job, item.get("title", "")),
                "title":       item.get("title", ""),
                "company":     item.get("company_name", ""),
                "location":    item.get("location", ""),
                "description": item.get("description", ""),
                "url":         url_job,
                "date_posted": date.today().isoformat(),
                "source":      "serpapi",
            })

        for i, job in enumerate(jobs[:5]):
            print_job(job, i)

        # Affiche le quota restant si disponible
        search_info = data.get("search_information", {})
        credits = data.get("search_metadata", {})
        print(f"\n  ✓ SerpApi OK — {len(jobs)} offres (1 requête consommée)")
        return jobs

    except Exception as e:
        print(f"  ✗ SerpApi ERREUR : {e}")
        return []


def test_indeed():
    print("\n" + "="*60)
    print("SOURCE : Indeed RSS")
    print("="*60)

    import feedparser
    from datetime import datetime, date

    rss_url = "https://www.indeed.com/rss?q=data+engineer&l=Geneva&fromage=1&sort=date"
    print(f"  URL testée : {rss_url}")

    try:
        feed = feedparser.parse(rss_url)
        entries = feed.entries
        print(f"  → {len(entries)} entrées dans le flux RSS")

        if not entries:
            print("  ⚠️  Flux vide — Indeed bloque parfois les requêtes directes")
            print("      (Le scraping RSS Indeed est instable, les 2 autres sources suffisent)")
            return []

        jobs = []
        for entry in entries:
            from src.collector import _job_id
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                dt = datetime(*entry.published_parsed[:6])
                date_posted = dt.strftime("%Y-%m-%d")
            else:
                date_posted = date.today().isoformat()

            company = ""
            if hasattr(entry, "source") and hasattr(entry.source, "get"):
                company = entry.source.get("value", "")

            jobs.append({
                "id":          _job_id(entry.get("link", ""), entry.get("title", "")),
                "title":       entry.get("title", ""),
                "company":     company,
                "location":    "Geneva",
                "description": entry.get("summary", ""),
                "url":         entry.get("link", ""),
                "date_posted": date_posted,
                "source":      "indeed_rss",
            })

        for i, job in enumerate(jobs[:3]):
            print_job(job, i)

        print(f"\n  ✓ Indeed RSS OK — {len(jobs)} entrées")
        return jobs

    except Exception as e:
        print(f"  ✗ Indeed RSS ERREUR : {e}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["adzuna", "serpapi", "indeed", "all"], default="all")
    parser.add_argument("--save", action="store_true", help="Sauvegarde les résultats en JSON")
    args = parser.parse_args()

    print("\n[TEST] Collector — Phase 1")
    print(f"   Source(s) : {args.source}")
    print(f"   Repertoire : {ROOT}")

    all_jobs = []

    if args.source in ("adzuna", "all"):
        jobs = test_adzuna()
        all_jobs.extend(jobs)

    if args.source in ("serpapi", "all"):
        jobs = test_serpapi()
        all_jobs.extend(jobs)

    if args.source in ("indeed", "all"):
        jobs = test_indeed()
        all_jobs.extend(jobs)

    print("\n" + "="*60)
    print(f"RÉSUMÉ : {len(all_jobs)} offres collectées au total")
    print("="*60)

    if args.save and all_jobs:
        out = ROOT / "data" / "test_collect_output.json"
        out.write_text(json.dumps(all_jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Resultats sauvegardes : {out}")


if __name__ == "__main__":
    main()
