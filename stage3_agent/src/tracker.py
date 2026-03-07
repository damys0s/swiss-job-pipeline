"""
tracker.py — Suivi manuel des candidatures (SQLite)
====================================================
Gère les tables `applications` et `application_history` dans tracker.db.
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
  - categorie TEXT     (DATA | BI | SUPPORT | LOGISTIQUE | AI | "")
  - created_at TEXT   (ISO date de création de l'entrée)

Table : application_history
  - id INTEGER PRIMARY KEY AUTOINCREMENT
  - app_id INTEGER (FK → applications.id)
  - ancien_etat TEXT
  - nouvel_etat TEXT
  - changed_at TEXT   (ISO datetime)
"""

import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from config.settings import BACKUP_CLOUD_PATH, TRACKER_DB_PATH


def backup_tracker_db(db_path: Path = TRACKER_DB_PATH) -> Path | None:
    """Copie tracker.db dans un sous-dossier backups/ horodaté (une fois par jour).
    Copie aussi dans BACKUP_CLOUD_PATH si configuré."""
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    dest = backup_dir / f"tracker_{date.today().isoformat()}.db"
    if dest.exists():
        return None  # déjà sauvegardé aujourd'hui
    shutil.copy2(db_path, dest)
    # Garde les 30 derniers backups locaux
    old = sorted(backup_dir.glob("tracker_*.db"))[:-30]
    for f in old:
        f.unlink()
    # Backup cloud (OneDrive, Dropbox, etc.)
    if BACKUP_CLOUD_PATH:
        cloud_dir = Path(BACKUP_CLOUD_PATH)
        try:
            cloud_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db_path, cloud_dir / dest.name)
        except Exception:
            pass  # ne pas planter si le cloud est inaccessible
    return dest


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

    CATEGORIES = ["DATA", "BI", "SUPPORT", "LOGISTIQUE", "AI"]

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
                    categorie   TEXT DEFAULT '',
                    created_at  TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS application_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id      INTEGER NOT NULL,
                    ancien_etat TEXT,
                    nouvel_etat TEXT NOT NULL,
                    changed_at  TEXT NOT NULL,
                    FOREIGN KEY (app_id) REFERENCES applications(id) ON DELETE CASCADE
                )
            """)
            # Migrations : ajoute les colonnes manquantes sur bases existantes
            cols = [r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
            if "description" not in cols:
                conn.execute("ALTER TABLE applications ADD COLUMN description TEXT DEFAULT ''")
            if "categorie" not in cols:
                conn.execute("ALTER TABLE applications ADD COLUMN categorie TEXT DEFAULT ''")
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
        categorie: str = "",
    ) -> int:
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO applications
                   (entreprise, poste, url, lieu, etat, date_envoi, contact, commentaire, description, categorie, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (entreprise, poste, url, lieu, etat, date_envoi or today, contact, commentaire, description, categorie, today),
            )
            conn.commit()
            return cursor.lastrowid

    def delete(self, app_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
            conn.commit()

    def update_etat(self, app_id: int, etat: str, old_etat: str = None):
        """Met à jour l'état et enregistre le changement dans l'historique."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE applications SET etat=? WHERE id=?", (etat, app_id))
            conn.execute(
                "INSERT INTO application_history (app_id, ancien_etat, nouvel_etat, changed_at) VALUES (?,?,?,?)",
                (app_id, old_etat, etat, now),
            )
            conn.commit()

    def update_commentaire(self, app_id: int, commentaire: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE applications SET commentaire=? WHERE id=?", (commentaire, app_id))
            conn.commit()

    def update_fields(self, app_id: int, **fields):
        """Met à jour un ensemble de champs libres (entreprise, poste, lieu, url, categorie, contact, commentaire).
        N'utilise PAS cette méthode pour etat — passer par update_etat pour l'historique."""
        allowed = {"entreprise", "poste", "lieu", "url", "categorie", "contact", "commentaire"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return
        sets = ", ".join(f"{k}=?" for k in filtered)
        values = list(filtered.values()) + [app_id]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE applications SET {sets} WHERE id=?", values)
            conn.commit()

    def get_history(self, app_id: int = None, limit: int = 50) -> list[dict]:
        """Retourne l'historique des changements d'état.
        Si app_id est fourni, filtre sur cette candidature."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if app_id is not None:
                rows = conn.execute(
                    """SELECT h.id, h.app_id, a.entreprise, a.poste,
                              h.ancien_etat, h.nouvel_etat, h.changed_at
                       FROM application_history h
                       JOIN applications a ON h.app_id = a.id
                       WHERE h.app_id=?
                       ORDER BY h.changed_at DESC""",
                    (app_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT h.id, h.app_id, a.entreprise, a.poste,
                              h.ancien_etat, h.nouvel_etat, h.changed_at
                       FROM application_history h
                       JOIN applications a ON h.app_id = a.id
                       ORDER BY h.changed_at DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

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
        from datetime import timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        now = datetime.now().isoformat(timespec="seconds")
        new_etat = "J'ai reçu une réponse négative"
        with sqlite3.connect(self.db_path) as conn:
            affected = conn.execute(
                """SELECT id, etat FROM applications
                   WHERE etat IN ('J''ai postulé', 'J''ai relancé')
                     AND date_envoi IS NOT NULL
                     AND date_envoi <= ?""",
                (cutoff,),
            ).fetchall()
            if affected:
                conn.execute(
                    """UPDATE applications
                       SET etat = 'J''ai reçu une réponse négative'
                       WHERE etat IN ('J''ai postulé', 'J''ai relancé')
                         AND date_envoi IS NOT NULL
                         AND date_envoi <= ?""",
                    (cutoff,),
                )
                conn.executemany(
                    "INSERT INTO application_history (app_id, ancien_etat, nouvel_etat, changed_at) VALUES (?,?,?,?)",
                    [(row[0], row[1], new_etat, now) for row in affected],
                )
            conn.commit()
        return len(affected)

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
            by_categorie = dict(
                conn.execute(
                    "SELECT categorie, COUNT(*) FROM applications GROUP BY categorie"
                ).fetchall()
            )
        return {"total": total, "by_etat": by_etat, "by_categorie": by_categorie}
