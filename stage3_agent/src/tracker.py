"""
tracker.py — Suivi manuel des candidatures (SQLite)
====================================================
Gère la table `applications` dans seen_jobs.db.
Indépendant du pipeline — données saisies manuellement via le dashboard.

Table : applications
  - id INTEGER PRIMARY KEY AUTOINCREMENT
  - entreprise TEXT
  - poste TEXT
  - url TEXT
  - lieu TEXT
  - etat TEXT
  - date_envoi TEXT    (ISO date)
  - contact TEXT
  - commentaire TEXT
  - description TEXT
  - created_at TEXT   (ISO date de création de l'entrée)
"""

import sqlite3
from datetime import date
from pathlib import Path

from config.settings import TRACKER_DB_PATH


class ApplicationTracker:
    """Gère les candidatures saisies manuellement."""

    ETATS = [
        "Je vais postuler",
        "J'ai postulé",
        "J'ai relancé",
        "J'ai un entretien",
        "Je n'ai pas reçu de réponse",
        "J'ai reçu une réponse négative",
    ]

    ETAT_COLORS = {
        "Je vais postuler":               "#F59E0B",
        "J'ai postulé":                   "#3B82F6",
        "J'ai relancé":                   "#F97316",
        "J'ai un entretien":              "#10B981",
        "Je n'ai pas reçu de réponse":    "#9CA3AF",
        "J'ai reçu une réponse négative": "#EF4444",
    }

    def __init__(self, db_path: Path = TRACKER_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_table()

    def _init_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    entreprise  TEXT NOT NULL,
                    poste       TEXT NOT NULL,
                    url         TEXT DEFAULT '',
                    lieu        TEXT DEFAULT '',
                    etat        TEXT DEFAULT 'Je vais postuler',
                    date_envoi  TEXT,
                    contact     TEXT DEFAULT '',
                    commentaire TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    created_at  TEXT
                )
            """)
            # Migration : ajoute la colonne si elle n'existait pas encore
            cols = [r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
            if "description" not in cols:
                conn.execute("ALTER TABLE applications ADD COLUMN description TEXT DEFAULT ''")
            conn.commit()

    def add(
        self,
        entreprise: str,
        poste: str,
        url: str = "",
        lieu: str = "",
        etat: str = "Je vais postuler",
        date_envoi: str = None,
        contact: str = "",
        commentaire: str = "",
        description: str = "",
    ) -> int:
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO applications
                   (entreprise, poste, url, lieu, etat, date_envoi, contact, commentaire, description, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (entreprise, poste, url, lieu, etat, date_envoi or today, contact, commentaire, description, today),
            )
            conn.commit()
            return cursor.lastrowid

    def delete(self, app_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
            conn.commit()

    def update_etat(self, app_id: int, etat: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE applications SET etat=? WHERE id=?", (etat, app_id))
            conn.commit()

    def update_commentaire(self, app_id: int, commentaire: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE applications SET commentaire=? WHERE id=?", (commentaire, app_id))
            conn.commit()

    def normalize_entreprises(self) -> int:
        """Unifie la casse des noms d'entreprise : casing majoritaire par groupe."""
        from collections import Counter
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT id, entreprise FROM applications").fetchall()
        groups: dict[str, Counter] = {}
        for app_id, name in rows:
            key = name.strip().lower()
            groups.setdefault(key, Counter())[name.strip()] += 1
        updated = 0
        with sqlite3.connect(self.db_path) as conn:
            for key, counter in groups.items():
                canonical = counter.most_common(1)[0][0]
                for app_id, name in rows:
                    if name.strip().lower() == key and name.strip() != canonical:
                        conn.execute("UPDATE applications SET entreprise=? WHERE id=?", (canonical, app_id))
                        updated += 1
            conn.commit()
        return updated

    def auto_close_stale(self, days: int = 42) -> int:
        """Passe en 'J'ai reçu une réponse négative' les candidatures restées
        en 'J'ai postulé' ou 'J'ai relancé' sans retour après `days` jours."""
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """UPDATE applications
                   SET etat = 'J''ai reçu une réponse négative'
                   WHERE etat IN ('J''ai postulé', 'J''ai relancé')
                     AND date_envoi IS NOT NULL
                     AND date_envoi <= ?""",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount

    def get_all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM applications ORDER BY date_envoi DESC, created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            by_etat = dict(
                conn.execute(
                    "SELECT etat, COUNT(*) FROM applications GROUP BY etat"
                ).fetchall()
            )
        return {"total": total, "by_etat": by_etat}
