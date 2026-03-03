"""
src — Package de classification d'offres d'emploi IT en Suisse romande
=======================================================================
Modules :
    utils            Utilitaires partagés (make_job_id, normalize_text, retry_request)
    collect          Phase 1a — collecte Adzuna + SerpApi
    collect_dba      Phase 1b — collecte ciblée DBA_INFRA
    collect_serpapi  Phase 1c — collecte SerpApi (localisations corrigées)
    label            Phase 2  — étiquetage interactif
    prepare          Phase 3  — préparation JSONL (train/val split)
    classify         Module réutilisable de classification (JobClassifier)
    finetuning       Phase 4  — orchestration du fine-tuning OpenAI
    evaluation       Phase 5  — évaluation comparative zero-shot vs fine-tuné

Exécution des scripts depuis la racine du projet :
    python -m src.collect
    python -m src.label
    python -m src.prepare
    python -m src.finetuning validate
    python -m src.evaluation
"""
