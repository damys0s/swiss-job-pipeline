"""
collector.py — Collecte des nouvelles offres d'emploi (dernières 24h)
======================================================================
Sources :
  1. Adzuna API (CH — offres dernières 24h)
  2. SerpApi Google Jobs (filtrage par date)
  3. Indeed RSS (entrées récentes)

Toutes les offres sont normalisées au format standard :
{
    "id":           str,   # hash unique
    "title":        str,
    "company":      str,
    "location":     str,
    "description":  str,
    "url":          str,
    "date_posted":  str,   # YYYY-MM-DD
    "source":       str,   # "adzuna" | "serpapi" | "indeed_rss"
}

Usage:
    from src.collector import JobCollector
    collector = JobCollector()
    jobs, stats = collector.collect()
"""

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import feedparser
import requests

from config.settings import (
    ADZUNA_APP_ID,
    ADZUNA_APP_KEY,
    API_TIMEOUT,
    MAX_DAYS_OLD,
    SEARCH_QUERIES_PATH,
    SERPAPI_KEY,
    USE_ADZUNA,
    USE_INDEED_RSS,
    USE_SERPAPI,
)

logger = logging.getLogger(__name__)


def _job_id(url: str, title: str = "", company: str = "") -> str:
    key = url or f"{title}|{company}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _normalize_date(raw: str) -> str:
    """Tente de normaliser une date en format YYYY-MM-DD. Retourne '' si échec."""
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


class JobCollector:
    """Collecte les offres d'emploi depuis Adzuna, SerpApi et Indeed RSS."""

    def __init__(self):
        self.queries = self._load_queries()
        self.cutoff  = date.today() - timedelta(days=MAX_DAYS_OLD)

    def _load_queries(self) -> list[dict]:
        return json.loads(SEARCH_QUERIES_PATH.read_text(encoding="utf-8"))["queries"]

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def collect(self) -> tuple[list[dict], dict]:
        """Collecte toutes les sources et retourne les offres + statistiques.

        Returns:
            (jobs, stats) où jobs est la liste dédupliquée des offres normalisées.
        """
        stats = {
            "adzuna":      {"fetched": 0, "kept": 0},
            "serpapi":     {"fetched": 0, "kept": 0, "requests": 0},
            "indeed_rss":  {"fetched": 0, "kept": 0},
            "total_raw":   0,
            "total_dedup": 0,
        }

        all_jobs = []

        if USE_ADZUNA and ADZUNA_APP_ID:
            jobs = self._collect_adzuna()
            stats["adzuna"]["fetched"] = len(jobs)
            jobs = self._filter_by_date(jobs)
            stats["adzuna"]["kept"] = len(jobs)
            all_jobs.extend(jobs)
            logger.info(f"Adzuna : {stats['adzuna']['fetched']} fetched, {stats['adzuna']['kept']} kept")
        else:
            logger.info("Adzuna : désactivé ou clé manquante")

        if USE_SERPAPI and SERPAPI_KEY:
            jobs, n_requests = self._collect_serpapi()
            stats["serpapi"]["fetched"] = len(jobs)
            stats["serpapi"]["requests"] = n_requests
            jobs = self._filter_by_date(jobs)
            stats["serpapi"]["kept"] = len(jobs)
            all_jobs.extend(jobs)
            logger.info(f"SerpApi : {stats['serpapi']['fetched']} fetched, {stats['serpapi']['kept']} kept ({n_requests} requêtes)")
        else:
            logger.info("SerpApi : désactivé ou clé manquante")

        if USE_INDEED_RSS:
            jobs = self._collect_indeed_rss()
            stats["indeed_rss"]["fetched"] = len(jobs)
            jobs = self._filter_by_date(jobs)
            stats["indeed_rss"]["kept"] = len(jobs)
            all_jobs.extend(jobs)
            logger.info(f"Indeed RSS : {stats['indeed_rss']['fetched']} fetched, {stats['indeed_rss']['kept']} kept")

        stats["total_raw"] = len(all_jobs)

        # Déduplication par ID
        seen_ids = set()
        unique_jobs = []
        for job in all_jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                unique_jobs.append(job)

        stats["total_dedup"] = len(unique_jobs)
        logger.info(f"Total : {stats['total_raw']} brut → {stats['total_dedup']} après déduplication interne")

        return unique_jobs, stats

    # ------------------------------------------------------------------
    # Adzuna
    # ------------------------------------------------------------------

    def _collect_adzuna(self) -> list[dict]:
        """Collecte les offres depuis l'API Adzuna (Suisse)."""
        jobs = []
        base_url = "https://api.adzuna.com/v1/api/jobs/ch/search/1"

        for query in self.queries:
            params = {
                "app_id":   ADZUNA_APP_ID,
                "app_key":  ADZUNA_APP_KEY,
                "what":     query["keywords"],
                "where":    query["location"],
                "max_days_old": MAX_DAYS_OLD,
                "results_per_page": 20,
                "content-type": "application/json",
            }
            try:
                resp = requests.get(base_url, params=params, timeout=API_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("results", []):
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
            except requests.RequestException as e:
                logger.warning(f"Adzuna erreur ({query['keywords']}/{query['location']}): {e}")

        return jobs

    # ------------------------------------------------------------------
    # SerpApi Google Jobs
    # ------------------------------------------------------------------

    def _collect_serpapi(self) -> tuple[list[dict], int]:
        """Collecte les offres via SerpApi Google Jobs.

        Paramètres identiques à ceux validés en étape 1 (collect_serpapi.py) :
        - location séparé de q (évite les erreurs "no results")
        - gl=ch pour cibler la Suisse
        - chips=date_posted:3days (moins restrictif que :today mais garde les offres récentes)
        """
        jobs      = []
        n_requests = 0
        base_url  = "https://serpapi.com/search"

        for query in self.queries:
            # Normalise la location pour SerpApi (ex: "Lausanne" → "Lausanne, Switzerland")
            location = query["location"]
            if "switzerland" not in location.lower() and "suisse" not in location.lower():
                location = f"{location}, Switzerland"

            params = {
                "engine":   "google_jobs",
                "q":        query["keywords"],
                "location": location,
                "gl":       "ch",   # Pays : Suisse
                "hl":       "fr",   # Langue d'interface
                "chips":    "date_posted:3days",  # Offres des 3 derniers jours
                "api_key":  SERPAPI_KEY,
            }
            try:
                resp = requests.get(base_url, params=params, timeout=API_TIMEOUT)
                resp.raise_for_status()
                n_requests += 1
                data = resp.json()
                for item in data.get("jobs_results", []):
                    # Extraction date SerpApi (detect_extensions.posted_at peut être "1 day ago" etc.)
                    date_posted = ""
                    extensions = item.get("detected_extensions", {})
                    posted_at  = extensions.get("posted_at", "")
                    # On ne filtre pas ici sur la date car SerpApi "today" filtre déjà
                    # share_link absent si l'offre n'a pas de lien direct → fallback apply_options
                    url = item.get("share_link", "")
                    if not url:
                        apply_opts = item.get("apply_options", [])
                        url = apply_opts[0].get("link", "") if apply_opts else ""
                    jobs.append({
                        "id":          _job_id(item.get("job_id", "") or url, item.get("title", "")),
                        "title":       item.get("title", ""),
                        "company":     item.get("company_name", ""),
                        "location":    item.get("location", ""),
                        "description": item.get("description", ""),
                        "url":         url,
                        "date_posted": date.today().isoformat(),  # SerpApi "today" = aujourd'hui
                        "source":      "serpapi",
                    })
                time.sleep(0.5)  # Politesse envers l'API
            except requests.RequestException as e:
                logger.warning(f"SerpApi erreur ({query['keywords']}): {e}")

        return jobs, n_requests

    # ------------------------------------------------------------------
    # Indeed RSS
    # ------------------------------------------------------------------

    def _collect_indeed_rss(self) -> list[dict]:
        """Collecte les offres depuis les flux RSS Indeed Suisse."""
        jobs = []

        for query in self.queries:
            keywords = query["keywords"].replace(" ", "+")
            location = query["location"].replace(" ", "+")
            rss_url  = (
                f"https://www.indeed.com/rss?q={keywords}"
                f"&l={location}&fromage=1&sort=date"
            )
            try:
                feed = feedparser.parse(rss_url)
                for entry in feed.entries:
                    # Date depuis published_parsed (struct_time)
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        dt = datetime(*entry.published_parsed[:6])
                        date_posted = dt.strftime("%Y-%m-%d")
                    else:
                        date_posted = date.today().isoformat()

                    # feedparser : entry.source est un FeedParserDict, pas un dict Python standard
                    company = ""
                    if hasattr(entry, "source") and hasattr(entry.source, "get"):
                        company = entry.source.get("value", "")
                    jobs.append({
                        "id":          _job_id(entry.get("link", ""), entry.get("title", "")),
                        "title":       entry.get("title", ""),
                        "company":     company,
                        "location":    query["location"],
                        "description": entry.get("summary", ""),
                        "url":         entry.get("link", ""),
                        "date_posted": date_posted,
                        "source":      "indeed_rss",
                    })
            except Exception as e:
                logger.warning(f"Indeed RSS erreur ({query['keywords']}): {e}")

        return jobs

    # ------------------------------------------------------------------
    # Filtre par date
    # ------------------------------------------------------------------

    def _filter_by_date(self, jobs: list[dict]) -> list[dict]:
        """Garde uniquement les offres publiées depuis MAX_DAYS_OLD jours."""
        kept = []
        for job in jobs:
            dp = job.get("date_posted", "")
            if not dp:
                kept.append(job)  # Date inconnue → garder par prudence
                continue
            try:
                if datetime.strptime(dp, "%Y-%m-%d").date() >= self.cutoff:
                    kept.append(job)
            except ValueError:
                kept.append(job)  # Format inconnu → garder par prudence
        return kept
