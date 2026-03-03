"""
deduplicator.py — Gestion des offres déjà vues (SQLite)
========================================================
Maintient une base SQLite des offres déjà envoyées par email.
Permet de ne présenter que les nouvelles offres à chaque run.

Table : seen_jobs
  - id TEXT PRIMARY KEY   (identifiant unique de l'offre : hash ou url)
  - title TEXT
  - company TEXT
  - url TEXT
  - first_seen TEXT       (date ISO de la première collecte)
  - sent_date TEXT        (date ISO d'envoi dans l'email)
"""

import hashlib
import sqlite3
from datetime import date
from pathlib import Path

from config.settings import DB_PATH


def _get_job_id(job: dict) -> str:
    """Génère un identifiant unique pour une offre.

    Priorité : url (le plus stable) → hash(title+company+location).
    """
    url = job.get("url", "").strip()
    if url:
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    key = f"{job.get('title','')}|{job.get('company','')}|{job.get('location','')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class Deduplicator:
    """Gère la déduplication des offres vues via SQLite."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_jobs (
                    id         TEXT PRIMARY KEY,
                    title      TEXT,
                    company    TEXT,
                    url        TEXT,
                    first_seen TEXT,
                    sent_date  TEXT
                )
            """)
            conn.commit()

    def is_new(self, job: dict) -> bool:
        """Retourne True si l'offre n'a pas encore été vue."""
        job_id = _get_job_id(job)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM seen_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return row is None

    def filter_new(self, jobs: list[dict]) -> list[dict]:
        """Retourne uniquement les offres non encore vues."""
        return [j for j in jobs if self.is_new(j)]

    def mark_seen(self, jobs: list[dict], sent_date: str = None):
        """Marque les offres comme vues (et envoyées si sent_date fourni)."""
        today = sent_date or date.today().isoformat()
        rows = []
        for job in jobs:
            job_id = _get_job_id(job)
            rows.append((
                job_id,
                job.get("title", ""),
                job.get("company", ""),
                job.get("url", ""),
                today,
                today,
            ))
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_jobs "
                "(id, title, company, url, first_seen, sent_date) VALUES (?,?,?,?,?,?)",
                rows,
            )
            conn.commit()

    def count(self) -> int:
        """Nombre total d'offres vues dans l'historique."""
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
