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
  - score REAL            (score cosine si l'offre a été scorée, NULL sinon)
  - label TEXT            (classe ML : DATA_ENGINEERING | BI_ANALYTICS, NULL sinon)
  - in_email INTEGER      (1 si l'offre a été envoyée dans un email, 0 sinon)
  - applied_at TEXT       (date ISO de candidature, NULL si pas encore candidaté)
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
        self._migrate_db()

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

    def _migrate_db(self):
        """Ajoute les colonnes de feedback si elles n'existent pas (migration non-destructive)."""
        columns_to_add = [
            ("score",      "REAL"),
            ("label",      "TEXT"),
            ("in_email",   "INTEGER DEFAULT 0"),
            ("applied_at", "TEXT"),
        ]
        with sqlite3.connect(self.db_path) as conn:
            for col_name, col_type in columns_to_add:
                try:
                    conn.execute(f"ALTER TABLE seen_jobs ADD COLUMN {col_name} {col_type}")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # Colonne déjà présente

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

    def mark_sent_details(self, jobs: list[dict]):
        """Met à jour score, label et in_email=1 pour les offres envoyées dans l'email."""
        with sqlite3.connect(self.db_path) as conn:
            for job in jobs:
                job_id = _get_job_id(job)
                conn.execute(
                    "UPDATE seen_jobs SET score=?, label=?, in_email=1 WHERE id=?",
                    (job.get("score"), job.get("label"), job_id),
                )
            conn.commit()

    def mark_applied(self, url: str, applied_date: str = None) -> bool:
        """Marque une offre comme candidatée par URL. Retourne True si trouvée."""
        applied_date = applied_date or date.today().isoformat()
        job_id = _get_job_id({"url": url})
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE seen_jobs SET applied_at=? WHERE id=? OR url=?",
                (applied_date, job_id, url),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_applied(self) -> list[dict]:
        """Retourne toutes les offres marquées comme candidatées, triées par date."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM seen_jobs WHERE applied_at IS NOT NULL ORDER BY applied_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def unmark_applied(self, url: str):
        """Annule le marquage candidature d'une offre."""
        job_id = _get_job_id({"url": url})
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE seen_jobs SET applied_at=NULL WHERE id=? OR url=?",
                (job_id, url),
            )
            conn.commit()

    def get_stats(self) -> dict:
        """Retourne les statistiques globales de la base."""
        with sqlite3.connect(self.db_path) as conn:
            total    = conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
            in_email = conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE in_email=1").fetchone()[0]
            applied  = conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE applied_at IS NOT NULL").fetchone()[0]
        return {"total": total, "in_email": in_email, "applied": applied}
