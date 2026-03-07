"""
pipeline.py — Orchestration du pipeline de veille emploi quotidien
===================================================================
Lance toutes les étapes séquentiellement, gère les erreurs,
log chaque étape avec durée et compteurs.

Usage:
    python src/pipeline.py              # Run complet
    python src/pipeline.py --dry-run    # Sans envoi d'email (tests)
"""

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

# _ROOT = stage3_agent/ → accès à config.settings et src.*
# _REPO_ROOT = swiss-job-pipeline/ → accès à shared.*
_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_REPO_ROOT))

from config.settings import LOGS_DIR, TOP_N_RESULTS
from src.collector import JobCollector
from src.deduplicator import Deduplicator
from src.emailer import JobEmailer
from src.scorer import JobScorer

# --- Logging ----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")


def run_daily_pipeline(dry_run: bool = False) -> dict:
    """
    Pipeline complet :
    1. Collecte les nouvelles offres (24h)
    2. Déduplique contre l'historique seen_jobs.db
    3. Classifie (exclut NOT_RELEVANT) et score par pertinence
    4. Génère et envoie l'email (sauf si dry_run=True)
    5. Met à jour l'historique seen_jobs.db
    6. Sauvegarde un log JSON dans logs/runs/YYYY-MM-DD.json

    Args:
        dry_run: Si True, skip l'envoi d'email et la mise à jour de la DB.

    Returns:
        dict avec les statistiques du run.
    """
    t_start = time.time()
    run_date = date.today().isoformat()
    logger.info(f"=== Pipeline démarré — {run_date} {'[DRY RUN]' if dry_run else ''} ===")

    run_log = {
        "date":       run_date,
        "dry_run":    dry_run,
        "steps":      {},
        "success":    False,
        "error":      None,
    }

    try:
        # ----------------------------------------------------------------
        # Étape 1 — Collecte
        # ----------------------------------------------------------------
        t0 = time.time()
        logger.info("Étape 1/5 — Collecte des offres...")

        collector = JobCollector()
        raw_jobs, collect_stats = collector.collect()

        collect_stats["duration_seconds"] = round(time.time() - t0, 1)
        run_log["steps"]["collect"] = collect_stats
        logger.info(f"  ✓ {collect_stats['total_dedup']} offres collectées ({collect_stats['duration_seconds']}s)")

        # ----------------------------------------------------------------
        # Étape 2 — Déduplication contre l'historique
        # ----------------------------------------------------------------
        t0 = time.time()
        logger.info("Étape 2/5 — Déduplication contre l'historique...")

        dedup = Deduplicator()
        new_jobs = dedup.filter_new(raw_jobs)
        n_seen_before = dedup.count()

        dedup_stats = {
            "n_raw":         len(raw_jobs),
            "n_new":         len(new_jobs),
            "n_in_history":  n_seen_before,
            "duration_seconds": round(time.time() - t0, 1),
        }
        run_log["steps"]["dedup"] = dedup_stats
        logger.info(f"  ✓ {len(raw_jobs)} → {len(new_jobs)} nouvelles ({dedup_stats['duration_seconds']}s)")

        if not new_jobs:
            logger.info("Aucune nouvelle offre. Pipeline terminé.")
            run_log["success"] = True
            _save_log(run_log, LOGS_DIR, run_date)
            return run_log

        # ----------------------------------------------------------------
        # Étape 3 — Classification et scoring
        # ----------------------------------------------------------------
        t0 = time.time()
        logger.info(f"Étape 3/5 — Classification et scoring ({len(new_jobs)} offres)...")

        scorer = JobScorer()
        top_jobs = scorer.score_and_rank(new_jobs, top_n=TOP_N_RESULTS)

        score_stats = {
            "n_input":     len(new_jobs),
            "n_relevant":  getattr(scorer, "last_n_relevant", len(top_jobs)),
            "n_top":       len(top_jobs),
            "duration_seconds": round(time.time() - t0, 1),
        }
        run_log["steps"]["score"] = score_stats
        collect_stats["n_relevant"] = score_stats["n_relevant"]  # Pour l'email
        logger.info(f"  ✓ {score_stats['n_input']} → {score_stats['n_relevant']} pertinentes → top {score_stats['n_top']} ({score_stats['duration_seconds']}s)")

        # ----------------------------------------------------------------
        # Étape 4 — Envoi de l'email
        # ----------------------------------------------------------------
        t0 = time.time()
        logger.info("Étape 4/5 — Envoi de l'email...")

        email_sent = False
        if not dry_run:
            collect_stats["duration_seconds"] = round(time.time() - t_start, 1)
            emailer = JobEmailer()
            email_sent = emailer.send(top_jobs, collect_stats)
        else:
            logger.info("  [DRY RUN] Email non envoyé")

        email_stats = {
            "sent":       email_sent,
            "n_jobs":     len(top_jobs),
            "dry_run":    dry_run,
            "duration_seconds": round(time.time() - t0, 1),
        }
        run_log["steps"]["email"] = email_stats
        logger.info(f"  ✓ Email {'envoyé' if email_sent else 'non envoyé'} ({email_stats['duration_seconds']}s)")

        # ----------------------------------------------------------------
        # Étape 5 — Mise à jour de l'historique
        # ----------------------------------------------------------------
        t0 = time.time()
        logger.info("Étape 5/5 — Mise à jour de l'historique...")

        if not dry_run:
            dedup.mark_seen(new_jobs, sent_date=run_date)
            dedup.mark_sent_details(top_jobs)  # Stocke score/label pour les offres envoyées
            logger.info(f"  ✓ {len(new_jobs)} offres ajoutées à l'historique ({len(top_jobs)} avec score/label)")
        else:
            logger.info("  [DRY RUN] Historique non mis à jour")

        run_log["steps"]["update_db"] = {
            "n_added":  len(new_jobs) if not dry_run else 0,
            "duration_seconds": round(time.time() - t0, 1),
        }

        # ----------------------------------------------------------------
        # Fin du run
        # ----------------------------------------------------------------
        run_log["duration_seconds"] = round(time.time() - t_start, 1)
        run_log["success"] = True
        logger.info(f"=== Pipeline terminé en {run_log['duration_seconds']}s ===")

    except Exception as e:
        run_log["error"]   = str(e)
        run_log["success"] = False
        run_log["duration_seconds"] = round(time.time() - t_start, 1)
        logger.error(f"Pipeline échoué : {e}", exc_info=True)

    _save_log(run_log, LOGS_DIR, run_date)
    return run_log


def _save_log(log: dict, logs_dir: Path, run_date: str):
    """Sauvegarde le log du run en JSON."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_date}.json"
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Log sauvegardé : {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de veille emploi quotidien")
    parser.add_argument("--dry-run", action="store_true", help="Sans envoi d'email ni mise à jour DB")
    args = parser.parse_args()

    result = run_daily_pipeline(dry_run=args.dry_run)
    sys.exit(0 if result.get("success") else 1)
