"""
job_pipeline_dag.py — DAG Airflow pour la veille emploi quotidienne
====================================================================
Remplace le cron GitHub Actions (.github/workflows/daily_pipeline.yml).

Planification : tous les jours à 8h00 (Europe/Zurich)
UI Airflow    : http://localhost:8080

Lancement manuel :
    docker exec sjp-airflow airflow dags trigger swiss_job_pipeline
    docker exec sjp-airflow airflow dags trigger swiss_job_pipeline --conf '{"dry_run": true}'
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, ShortCircuitOperator

log = logging.getLogger(__name__)

# Chemin du projet dans le conteneur (volume monté dans docker-compose.yml)
PIPELINE_ROOT = Path("/opt/pipeline")
STAGE3_ROOT   = PIPELINE_ROOT / "stage3_agent"

# ---------------------------------------------------------------------------
# Fonctions des tasks
# ---------------------------------------------------------------------------

def _check_env(**context):
    """Vérifie que les variables d'environnement critiques sont présentes."""
    import os
    missing = [k for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "SERPAPI_KEY", "OPENAI_API_KEY")
               if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Variables manquantes : {', '.join(missing)}")
    log.info("Toutes les variables d'environnement sont présentes.")
    return True


def _run_pipeline(**context):
    """Appelle run_daily_pipeline() depuis le code du repo monté."""
    # Injecter les paths pour que les imports pipeline fonctionnent
    for p in (str(STAGE3_ROOT), str(PIPELINE_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Lire dry_run depuis dag_run.conf si déclenché manuellement
    dag_run_conf = context.get("dag_run").conf or {}
    dry_run = dag_run_conf.get("dry_run", False)

    from src.pipeline import run_daily_pipeline
    result = run_daily_pipeline(dry_run=dry_run)

    if not result.get("success"):
        raise RuntimeError(f"Pipeline échoué : {result.get('error')}")

    log.info("Pipeline terminé avec succès : %s", json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------

with DAG(
    dag_id="swiss_job_pipeline",
    description="Veille emploi quotidienne — collecte, scoring, email",
    schedule="0 8 * * *",          # 8h00 Europe/Zurich (timezone défini dans Airflow config)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["jobs", "pipeline", "stage3"],
    doc_md=__doc__,
) as dag:

    check_env = ShortCircuitOperator(
        task_id="check_environment",
        python_callable=_check_env,
    )

    run_pipeline = PythonOperator(
        task_id="run_daily_pipeline",
        python_callable=_run_pipeline,
    )

    check_env >> run_pipeline
