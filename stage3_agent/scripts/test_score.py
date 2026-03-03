"""
test_score.py — Script de test interactif pour le scorer (Phase 2)
===================================================================
Charge les offres collectées en Phase 1 (data/test_collect_output.json)
et les fait passer par la classification + scoring.

Usage (depuis la racine de job-alert-agent/) :
    python scripts/test_score.py
    python scripts/test_score.py --input data/test_collect_output.json
    python scripts/test_score.py --save   # Sauvegarde le résultat scoré
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Force UTF-8 sur Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_score")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", default=str(ROOT / "data" / "test_collect_output.json"),
        help="Fichier JSON des offres collectées (Phase 1)"
    )
    parser.add_argument("--save", action="store_true", help="Sauvegarde le résultat en JSON")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERREUR] Fichier introuvable : {input_path}")
        print("  Lance d'abord : python scripts/test_collect.py --save")
        sys.exit(1)

    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"\n[TEST] Scorer — Phase 2")
    print(f"   Input  : {input_path.name} ({len(jobs)} offres)")
    print(f"   Profil : {ROOT / 'config' / 'profile.json'}")
    print()

    # ----------------------------------------------------------------
    # Étape 1 — Chargement du scorer (modèle d'embedding + vectorstore)
    # ----------------------------------------------------------------
    print("=== Chargement du scorer ===")
    t0 = time.time()

    from src.scorer import JobScorer, _build_profile_text
    import json as _json
    from config.settings import PROFILE_PATH

    scorer = JobScorer()
    profile = _json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile_text = _build_profile_text(profile)

    print(f"  Profil embedding : {profile_text}")
    print(f"  Chargement : {time.time() - t0:.1f}s")

    # ----------------------------------------------------------------
    # Étape 2 — Classification offre par offre (avec affichage)
    # ----------------------------------------------------------------
    print(f"\n=== Classification de {len(jobs)} offres ===")
    t0 = time.time()

    from src.classify import JobClassifier
    from config.settings import CLASSIFIER_MODEL_ID, OPENAI_API_KEY

    clf = JobClassifier(model_id=CLASSIFIER_MODEL_ID, api_key=OPENAI_API_KEY)

    classified = []
    for i, job in enumerate(jobs):
        label = clf.classify(
            title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
            description=job.get("description", ""),
        )
        job_c = {**job, "label": label}
        classified.append(job_c)
        icon = "[OK]" if label != "NOT_RELEVANT" else "[--]"
        print(f"  {icon} [{label:20s}] {job['title'][:50]} ({job['company']})")

    relevant = [j for j in classified if j["label"] in {"DATA_ENGINEERING", "BI_ANALYTICS"}]
    elapsed_clf = time.time() - t0
    print(f"\n  Résultat : {len(classified)} total → {len(relevant)} pertinentes ({elapsed_clf:.1f}s)")

    if not relevant:
        print("\n  Aucune offre pertinente. Fin du test.")
        return

    # ----------------------------------------------------------------
    # Étape 3 — Scoring cosine vs profil
    # ----------------------------------------------------------------
    print(f"\n=== Scoring cosine ({len(relevant)} offres) ===")
    t0 = time.time()

    for job in relevant:
        score = scorer.retriever.similarity_score(job, scorer._profile_embedding)
        job["score"] = round(score, 4)

    relevant.sort(key=lambda x: x["score"], reverse=True)
    elapsed_score = time.time() - t0
    print(f"  Scoring terminé en {elapsed_score:.2f}s")

    # ----------------------------------------------------------------
    # Affichage des résultats triés
    # ----------------------------------------------------------------
    print(f"\n=== TOP {len(relevant)} offres (triées par score) ===\n")
    for i, job in enumerate(relevant, 1):
        score_pct = int(job["score"] * 100)
        bar = "#" * (score_pct // 5) + "." * (20 - score_pct // 5)
        print(f"  [{i}] score={job['score']:.4f} [{bar}] {score_pct}%")
        print(f"       {job['title']}")
        print(f"       {job['company']} | {job['location']} | {job['label']}")
        print(f"       {job['url'][:80]}")
        print()

    # ----------------------------------------------------------------
    # Sauvegarde
    # ----------------------------------------------------------------
    if args.save:
        out = ROOT / "data" / "test_score_output.json"
        out.write_text(
            json.dumps(relevant, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[OK] Résultat sauvegardé : {out}")


if __name__ == "__main__":
    main()
