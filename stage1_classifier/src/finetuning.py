"""
Phase 4 - Fine-tuning GPT-4o-mini pour classification d'offres d'emploi
Usage:
    python -m src.finetuning validate|upload|start|status|results|run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

# --- Config ---------------------------------------------------------------
BASE_MODEL = "gpt-4o-mini-2024-07-18"  # Version datée pour la reproductibilité
N_EPOCHS = 3
SUFFIX = "job-classifier"  # Suffixe identifiable dans l'ID du modèle fine-tuné

# Résolution du chemin racine depuis l'emplacement de ce fichier.
# Permet d'appeler le script depuis n'importe quel répertoire de travail.
BASE_DIR = Path(__file__).resolve().parent.parent

TRAIN_FILE = BASE_DIR / "data" / "training" / "train.jsonl"
VAL_FILE = BASE_DIR / "data" / "training" / "val.jsonl"
LOG_DIR = BASE_DIR / "results" / "training_logs"

# Fichier d'état machine : persiste les IDs (fichier, job, modèle) entre les
# commandes pour permettre une exécution en plusieurs étapes sans perdre le contexte.
STATE_FILE = LOG_DIR / "finetune_state.json"


def get_client():
    """Crée et retourne un client OpenAI après vérification de la clé API."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERREUR: OPENAI_API_KEY non définie.")
        sys.exit(1)
    return OpenAI(api_key=api_key)


def load_state():
    """Charge l'état de la machine depuis le fichier JSON. Retourne {} si absent."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    """Persiste l'état courant sur disque.

    Appelé après chaque étape critique (upload, start, succeeded) pour permettre
    une reprise sans reprendre depuis le début en cas d'interruption.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def validate_jsonl(path):
    """Valide un fichier JSONL et retourne les statistiques + erreurs trouvées.

    Vérifie : format JSON valide, présence de 3 messages, rôles corrects.
    L'estimation de tokens utilise la règle des 4 caractères par token.
    """
    errors, count, labels, tokens = [], 0, {}, 0
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"Ligne {i}: JSON invalide")
                continue
            count += 1
            msgs = obj.get("messages", [])
            if len(msgs) < 3:
                errors.append(f"Ligne {i}: moins de 3 messages")
                continue
            roles = [m["role"] for m in msgs]
            if roles != ["system", "user", "assistant"]:
                errors.append(f"Ligne {i}: rôles incorrects {roles}")
                continue
            label = msgs[2]["content"].strip()
            labels[label] = labels.get(label, 0) + 1
            tokens += sum(len(m["content"]) for m in msgs) // 4
    return {"count": count, "labels": labels, "errors": errors, "tokens_approx": tokens}


# --- validate -------------------------------------------------------------
def cmd_validate():
    """Valide les fichiers JSONL et estime le coût avant tout upload."""
    print("=" * 60)
    print("VALIDATION DES FICHIERS JSONL")
    print("=" * 60)
    for path in [TRAIN_FILE, VAL_FILE]:
        if not path.exists():
            print(f"\nERREUR: {path} introuvable.")
            sys.exit(1)
        stats = validate_jsonl(path)
        name = "TRAIN" if "train" in path.name else "VALIDATION"
        print(f"\n--- {name}: {path} ---")
        print(f"  Exemples: {stats['count']}")
        print(f"  Tokens (approx): {stats['tokens_approx']:,}")
        print("  Distribution:")
        for label, cnt in sorted(stats["labels"].items()):
            print(f"    {label}: {cnt} ({cnt/stats['count']*100:.1f}%)")
        if stats["errors"]:
            for err in stats["errors"][:10]:
                print(f"    ERREUR: {err}")
            sys.exit(1)
        print("  Format OK")

    # Estimation du coût total avant engagement financier
    train_stats = validate_jsonl(TRAIN_FILE)
    total_tokens = train_stats["tokens_approx"] * N_EPOCHS
    cost = (total_tokens / 1_000_000) * 3.00
    print(f"\n--- ESTIMATION COÛT ---")
    print(f"  Tokens training ({N_EPOCHS} epochs): {total_tokens:,}")
    print(f"  Coût estimé: ${cost:.4f}")


# --- upload ---------------------------------------------------------------
def cmd_upload():
    """Upload les fichiers JSONL vers l'API OpenAI (idempotent).

    Vérifie d'abord si les fichiers ont déjà été uploadés (présence de
    train_file_id / val_file_id dans le state) pour éviter les uploads en double.
    """
    cmd_validate()
    client = get_client()
    state = load_state()
    print("\n" + "=" * 60)
    print("UPLOAD DES FICHIERS")
    print("=" * 60)
    for key, path in [("train_file_id", TRAIN_FILE), ("val_file_id", VAL_FILE)]:
        label = "train" if "train" in key else "validation"
        if key in state:
            print(f"  {label}: déjà uploadé → {state[key]}")
            continue
        print(f"  Upload {label}: {path}...")
        with open(path, "rb") as f:
            resp = client.files.create(file=f, purpose="fine-tune")
        state[key] = resp.id
        save_state(state)  # Sauvegarde immédiate après chaque upload
        print(f"  {label} → {resp.id}")
    print(f"\nState → {STATE_FILE}")


# --- start ----------------------------------------------------------------
def cmd_start():
    """Lance le job de fine-tuning (protégé contre les doubles soumissions).

    La présence de job_id dans le state empêche de créer un second job
    accidentellement, ce qui coûterait de l'argent sans bénéfice.
    """
    client = get_client()
    state = load_state()
    if "train_file_id" not in state or "val_file_id" not in state:
        print("ERREUR: fichiers pas uploadés. Lance: python -m src.finetuning upload")
        sys.exit(1)
    if "job_id" in state:
        print(f"Job existant: {state['job_id']} (status: {state.get('last_status')})")
        print("Pour relancer, supprime results/training_logs/finetune_state.json")
        sys.exit(1)

    print("=" * 60)
    print("LANCEMENT DU FINE-TUNING")
    print("=" * 60)
    print(f"  Modèle: {BASE_MODEL} | Epochs: {N_EPOCHS} | Suffix: {SUFFIX}")

    # La structure method.supervised est requise par l'API OpenAI v2 pour
    # distinguer le supervised fine-tuning des autres méthodes (RLHF, DPO...).
    job = client.fine_tuning.jobs.create(
        training_file=state["train_file_id"],
        validation_file=state["val_file_id"],
        model=BASE_MODEL,
        suffix=SUFFIX,
        method={
            "type": "supervised",
            "supervised": {"hyperparameters": {"n_epochs": N_EPOCHS}},
        },
    )
    state["job_id"] = job.id
    state["last_status"] = job.status
    state["created_at"] = datetime.now().isoformat()
    save_state(state)
    print(f"  Job créé: {job.id} | Status: {job.status}")
    print(f"\nSuivi: python -m src.finetuning status")


# --- status ---------------------------------------------------------------
def cmd_status():
    """Surveille le job de fine-tuning en mode polling (intervalle: 30s).

    Polling toutes les 30s car OpenAI ne propose pas de webhooks pour les jobs
    de fine-tuning. Ctrl+C interrompt le suivi sans annuler le job (il continue
    en arrière-plan sur les serveurs OpenAI).
    """
    client = get_client()
    state = load_state()
    if "job_id" not in state:
        print("ERREUR: aucun job. Lance: python -m src.finetuning start")
        sys.exit(1)
    job_id = state["job_id"]
    print(f"Suivi: {job_id} (Ctrl+C pour arrêter)\n")
    try:
        while True:
            job = client.fine_tuning.jobs.retrieve(job_id)
            status = job.status
            state["last_status"] = status
            save_state(state)
            parts = [f"[{datetime.now().strftime('%H:%M:%S')}] {status}"]
            if getattr(job, "trained_tokens", None):
                parts.append(f"tokens: {job.trained_tokens:,}")
            if getattr(job, "estimated_finish", None):
                parts.append(f"fin: {job.estimated_finish}")
            print(" | ".join(parts))

            if status == "succeeded":
                # Récupération et persistance du model_id immédiatement
                state["model_id"] = job.fine_tuned_model
                state["finished_at"] = datetime.now().isoformat()
                state["trained_tokens"] = job.trained_tokens
                save_state(state)
                print(f"\nTERMINÉ → {job.fine_tuned_model}")
                print(f"Tokens entraînés: {job.trained_tokens:,}")
                print(f"\nSuite: python -m src.finetuning results")
                return
            elif status in ("failed", "cancelled"):
                print(f"\nJob {status}: {getattr(job, 'error', 'N/A')}")
                sys.exit(1)
            time.sleep(30)
    except KeyboardInterrupt:
        print(f"\nArrêté. Dernier status: {status}")


# --- results --------------------------------------------------------------
def cmd_results():
    """Récupère les artefacts du fine-tuning et effectue un test rapide de sanité.

    Exporte :
      - training_summary.json : métadonnées du job
      - training_events.json  : log des événements (progress, metrics)
      - result_*.csv          : courbes de loss par step
    """
    client = get_client()
    state = load_state()
    if "model_id" not in state:
        print("ERREUR: pas terminé. Lance: python -m src.finetuning status")
        sys.exit(1)

    job = client.fine_tuning.jobs.retrieve(state["job_id"])
    print("=" * 60)
    print("RÉSULTATS")
    print("=" * 60)

    # Résumé des métadonnées du job
    summary = {
        "job_id": job.id,
        "model_base": BASE_MODEL,
        "model_finetuned": job.fine_tuned_model,
        "status": job.status,
        "created_at": state.get("created_at"),
        "finished_at": state.get("finished_at"),
        "trained_tokens": job.trained_tokens,
        "hyperparameters": {"n_epochs": N_EPOCHS, "batch_size": "auto", "lr_multiplier": "auto"},
    }
    (LOG_DIR / "training_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"  Résumé → results/training_logs/training_summary.json")

    # Événements du job (utiles pour analyser la convergence de la loss)
    events = []
    for e in client.fine_tuning.jobs.list_events(fine_tuning_job_id=job.id, limit=100).data:
        events.append({"id": e.id, "created_at": e.created_at, "level": e.level, "message": e.message})
    (LOG_DIR / "training_events.json").write_text(json.dumps(events, indent=2))
    print(f"  Events ({len(events)}) → results/training_logs/training_events.json")

    # Fichiers de résultats CSV (loss par step, pour tracer les courbes)
    if job.result_files:
        for rf_id in job.result_files:
            content = client.files.content(rf_id)
            path = LOG_DIR / f"result_{rf_id}.csv"
            path.write_bytes(content.read())
            print(f"  Result CSV → {path}")

    if job.trained_tokens:
        cost = (job.trained_tokens / 1_000_000) * 3.00
        print(f"\n  Modèle: {job.fine_tuned_model}")
        print(f"  Tokens: {job.trained_tokens:,} | Coût: ${cost:.4f}")

    # Test rapide de sanité avec 3 cas représentatifs.
    # IMPORTANT : utilise le prompt 3 classes (DATA_ENGINEERING, BI_ANALYTICS, NOT_RELEVANT)
    # identique à classify.py — le modèle a été entraîné avec ce prompt.
    print(f"\n--- TEST RAPIDE ---")
    sys_msg = {"role": "system", "content": (
        "Tu es un classificateur d'offres d'emploi IT en Suisse romande. "
        "Classe chaque offre dans exactement une catégorie parmi : "
        "DATA_ENGINEERING, BI_ANALYTICS, NOT_RELEVANT. "
        "Réponds uniquement avec le nom de la catégorie, rien d'autre."
    )}
    tests = [
        ("Senior Data Engineer @ UBS Genève — Spark, Airflow, dbt", "DATA_ENGINEERING"),
        ("BI Developer @ Nestlé Vevey — Power BI, reporting financier", "BI_ANALYTICS"),
        ("Marketing Manager @ Logitech Lausanne — digital campaigns", "NOT_RELEVANT"),
    ]
    for desc, expected in tests:
        r = client.chat.completions.create(
            model=job.fine_tuned_model,
            messages=[sys_msg, {"role": "user", "content": desc}],
            temperature=0, max_tokens=20,
        )
        got = r.choices[0].message.content.strip()
        ok = "OK" if got == expected else "MISMATCH"
        print(f"  [{ok}] attendu={expected} → obtenu={got}")

    print(f"\n  Model ID pour Phase 5: {job.fine_tuned_model}")


def cmd_run():
    """Exécute le pipeline complet en une seule commande : upload → start → status → results."""
    cmd_upload()
    cmd_start()
    cmd_status()
    cmd_results()


def main():
    p = argparse.ArgumentParser(description="Fine-tuning job classifier")
    p.add_argument("command", choices=["validate", "upload", "start", "status", "results", "run"])
    args = p.parse_args()
    {"validate": cmd_validate, "upload": cmd_upload, "start": cmd_start,
     "status": cmd_status, "results": cmd_results, "run": cmd_run}[args.command]()


if __name__ == "__main__":
    main()
