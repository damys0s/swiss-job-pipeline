"""
Phase 5 - Évaluation comparative: fine-tuné vs zero-shot
=========================================================
Usage:
    python -m src.evaluation

Pré-requis:
    - OPENAI_API_KEY en variable d'environnement
    - data/training/val.jsonl
    - results/training_logs/finetune_state.json (contient le model ID)
    - pip install openai scikit-learn seaborn matplotlib pandas
"""

import json
import os
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from openai import OpenAI
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)

# --- Config ---------------------------------------------------------------
# Résolution du chemin racine depuis l'emplacement de ce fichier.
# Permet d'appeler le script depuis n'importe quel répertoire de travail.
BASE_DIR = Path(__file__).resolve().parent.parent

BASE_MODEL = "gpt-4o-mini-2024-07-18"
VAL_FILE = BASE_DIR / "data" / "training" / "val.jsonl"
STATE_FILE = BASE_DIR / "results" / "training_logs" / "finetune_state.json"
EVAL_DIR = BASE_DIR / "results" / "evaluation"
# Ordre des labels pour les matrices de confusion et les rapports sklearn
LABELS = ["DATA_ENGINEERING", "BI_ANALYTICS", "NOT_RELEVANT"]


def get_client():
    """Crée et retourne un client OpenAI après vérification de la clé API."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERREUR: OPENAI_API_KEY non définie.")
        sys.exit(1)
    return OpenAI(api_key=api_key)


def load_val_data():
    """Charge le fichier de validation JSONL et retourne une liste d'exemples.

    Le system prompt est extrait du fichier (plutôt que de le hardcoder) pour
    garantir que l'évaluation utilise exactement le même prompt que l'entraînement.
    """
    examples = []
    with open(VAL_FILE, "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            msgs = obj["messages"]
            examples.append({
                "system": msgs[0]["content"],
                "user": msgs[1]["content"],
                "label": msgs[2]["content"].strip(),
            })
    return examples


def classify_batch(client, model, examples, model_label):
    """Classifie tous les exemples du dataset de validation avec un modèle donné.

    temperature=0 et max_tokens=20 pour la cohérence avec classify.py.
    Les prédictions invalides (hors LABELS) sont remplacées par NOT_RELEVANT
    et comptées comme erreurs pour ne pas gonfler artificiellement les métriques.

    Le rate limiting (0.2s entre requêtes) est fixe ici car on évalue deux
    modèles sur le même set — la cohérence temporelle est moins importante
    que lors d'une classification en production.
    """
    predictions = []
    total = len(examples)
    errors = 0

    for i, ex in enumerate(examples, 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": ex["system"]},
                    {"role": "user", "content": ex["user"]},
                ],
                temperature=0,
                max_tokens=20,
            )
            pred = response.choices[0].message.content.strip()

            # Nettoyage défensif identique à classify.py._format_input()
            pred = pred.split("\n")[0].strip().upper()

            if pred not in LABELS:
                print(f"  [{model_label}] #{i} label inattendu: '{pred}' → NOT_RELEVANT")
                pred = "NOT_RELEVANT"
                errors += 1

            predictions.append(pred)

        except Exception as e:
            print(f"  [{model_label}] #{i} erreur API: {e}")
            # Fallback conservateur en cas d'erreur API
            predictions.append("NOT_RELEVANT")
            errors += 1

        if i % 10 == 0 or i == total:
            print(f"  [{model_label}] {i}/{total}", end="\r")

        # Rate limiting : 0.2s ≈ 5 req/s, en dessous du seuil de throttling OpenAI
        time.sleep(0.2)

    print(f"  [{model_label}] {total}/{total} terminé ({errors} erreurs)")
    return predictions


def compute_metrics(y_true, y_pred, model_label):
    """Calcule les métriques de classification et retourne un dict structuré.

    F1 macro (moyenne simple par classe) est la métrique principale car elle
    pénalise les mauvaises performances sur les classes minoritaires (BI_ANALYTICS).
    F1 weighted est fourni en supplément pour information.
    """
    acc = accuracy_score(y_true, y_pred)
    f1_macro = f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)
    f1_weighted = f1_score(y_true, y_pred, labels=LABELS, average="weighted", zero_division=0)
    report = classification_report(
        y_true, y_pred, labels=LABELS, output_dict=True, zero_division=0
    )
    cm = confusion_matrix(y_true, y_pred, labels=LABELS)

    return {
        "model": model_label,
        "accuracy": round(acc, 4),
        "f1_macro": round(f1_macro, 4),
        "f1_weighted": round(f1_weighted, 4),
        "report": report,
        "confusion_matrix": cm.tolist(),
    }


def plot_confusion_matrices(metrics_base, metrics_ft):
    """Génère les heatmaps côte à côte (zero-shot vs fine-tuné).

    Sauvegardé en PNG (dpi=150) pour un bon compromis qualité/taille.
    plt.close() libère la mémoire après sauvegarde — important pour éviter
    les fuites mémoire si cette fonction est appelée en boucle.
    """
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, metrics, title in [
        (axes[0], metrics_base, f"Zero-shot ({BASE_MODEL})"),
        (axes[1], metrics_ft, "Fine-tuné"),
    ]:
        cm = metrics["confusion_matrix"]
        sns.heatmap(
            cm,
            annot=True,
            fmt="d",
            cmap="Blues",
            xticklabels=LABELS,
            yticklabels=LABELS,
            ax=ax,
        )
        ax.set_title(f"{title}\nAccuracy: {metrics['accuracy']:.1%} | F1 macro: {metrics['f1_macro']:.1%}")
        ax.set_ylabel("Vrai label")
        ax.set_xlabel("Prédit")

    plt.tight_layout()
    path = EVAL_DIR / "confusion_matrices.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Confusion matrices → {path}")


def find_errors(examples, preds_base, preds_ft):
    """Identifie les cas d'intérêt pour l'analyse qualitative.

    Deux catégories de cas :
      - "fine-tuné incorrect"    : regressions introduites par le fine-tuning
      - "corrigé par fine-tuning": améliorations par rapport au zero-shot

    La première catégorie est prioritaire — des régressions sur un dataset
    aussi petit (72 exemples) seraient un signal d'alarme (overfitting possible).
    """
    errors = []
    for i, ex in enumerate(examples):
        true = ex["label"]
        base = preds_base[i]
        ft = preds_ft[i]

        if ft != true:
            errors.append({
                "index": i,
                "type": "fine-tuné incorrect",
                "true": true,
                "base_pred": base,
                "ft_pred": ft,
                "input_preview": ex["user"][:200],
            })
        elif base != true and ft == true:
            errors.append({
                "index": i,
                "type": "corrigé par fine-tuning",
                "true": true,
                "base_pred": base,
                "ft_pred": ft,
                "input_preview": ex["user"][:200],
            })

    return errors


def main():
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    # Chargement du model_id depuis l'état du fine-tuning
    if not STATE_FILE.exists():
        print("ERREUR: finetune_state.json introuvable. Lance d'abord la Phase 4.")
        sys.exit(1)
    state = json.loads(STATE_FILE.read_text())
    ft_model = state.get("model_id")
    if not ft_model:
        print("ERREUR: model_id absent du state. Le fine-tuning est-il terminé ?")
        sys.exit(1)

    client = get_client()
    examples = load_val_data()

    print("=" * 60)
    print(f"ÉVALUATION COMPARATIVE — {len(examples)} exemples")
    print("=" * 60)
    print(f"  Base:    {BASE_MODEL}")
    print(f"  Fine-tuné: {ft_model}")

    y_true = [ex["label"] for ex in examples]

    # --- Baseline (zero-shot) ---
    # Évaluation du modèle de base sans fine-tuning pour établir la référence.
    # La mesure du temps inclut les délais de rate limiting (0.2s par requête),
    # ce qui reflète le temps réel d'inférence en production.
    print(f"\n--- BASELINE (zero-shot) ---")
    t0 = time.time()
    preds_base = classify_batch(client, BASE_MODEL, examples, "base")
    time_base = time.time() - t0

    # --- Fine-tuné ---
    print(f"\n--- FINE-TUNÉ ---")
    t0 = time.time()
    preds_ft = classify_batch(client, ft_model, examples, "ft")
    time_ft = time.time() - t0

    # --- Métriques ---
    print(f"\n{'=' * 60}")
    print("MÉTRIQUES")
    print("=" * 60)

    m_base = compute_metrics(y_true, preds_base, "zero-shot")
    m_ft = compute_metrics(y_true, preds_ft, "fine-tuné")

    # Tableau comparatif
    comparison = {
        "Métrique": ["Accuracy", "F1 macro", "F1 weighted", "Temps (s)", "Temps/requête (s)"],
        "Zero-shot": [
            f"{m_base['accuracy']:.1%}",
            f"{m_base['f1_macro']:.1%}",
            f"{m_base['f1_weighted']:.1%}",
            f"{time_base:.1f}",
            f"{time_base/len(examples):.2f}",
        ],
        "Fine-tuné": [
            f"{m_ft['accuracy']:.1%}",
            f"{m_ft['f1_macro']:.1%}",
            f"{m_ft['f1_weighted']:.1%}",
            f"{time_ft:.1f}",
            f"{time_ft/len(examples):.2f}",
        ],
    }
    df_comp = pd.DataFrame(comparison)
    print(df_comp.to_string(index=False))

    # Classification report détaillé
    print(f"\n--- Détail par classe (fine-tuné) ---")
    print(classification_report(y_true, preds_ft, labels=LABELS, zero_division=0))

    print(f"--- Détail par classe (zero-shot) ---")
    print(classification_report(y_true, preds_base, labels=LABELS, zero_division=0))

    # --- Confusion matrices ---
    plot_confusion_matrices(m_base, m_ft)

    # --- Analyse d'erreurs ---
    errors = find_errors(examples, preds_base, preds_ft)
    print(f"\n--- ANALYSE D'ERREURS ---")
    print(f"  Erreurs fine-tuné: {sum(1 for e in errors if e['type'] == 'fine-tuné incorrect')}")
    print(f"  Corrigés par fine-tuning: {sum(1 for e in errors if e['type'] == 'corrigé par fine-tuning')}")

    # Afficher quelques exemples
    for e in errors[:10]:
        print(f"\n  [{e['type']}] index={e['index']}")
        print(f"    Vrai: {e['true']} | Base: {e['base_pred']} | FT: {e['ft_pred']}")
        print(f"    Aperçu: {e['input_preview'][:120]}...")

    # --- Export ---
    # CSV des prédictions individuelles — utile pour l'analyse manuelle des erreurs
    # et pour créer des visualisations personnalisées hors script.
    preds_df = pd.DataFrame({
        "true_label": y_true,
        "pred_baseline": preds_base,
        "pred_finetuned": preds_ft,
        "baseline_correct": [t == p for t, p in zip(y_true, preds_base)],
        "finetuned_correct": [t == p for t, p in zip(y_true, preds_ft)],
    })
    preds_df.to_csv(EVAL_DIR / "predictions.csv", index=False, encoding="utf-8-sig")

    # Métriques JSON
    eval_results = {
        "n_examples": len(examples),
        "labels": LABELS,
        "baseline": m_base,
        "finetuned": m_ft,
        "time_baseline_s": round(time_base, 1),
        "time_finetuned_s": round(time_ft, 1),
        "errors_analysis": errors[:10],
    }
    (EVAL_DIR / "evaluation_results.json").write_text(
        json.dumps(eval_results, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Résumé
    print(f"\n{'=' * 60}")
    print("FICHIERS EXPORTÉS")
    print("=" * 60)
    print(f"  results/evaluation/predictions.csv")
    print(f"  results/evaluation/evaluation_results.json")
    print(f"  results/evaluation/confusion_matrices.png")

    # Verdict final : interprétation de l'amélioration apportée par le fine-tuning.
    # Seuil de 5% de gain F1 macro considéré comme "significatif" (arbitraire mais
    # cohérent avec la littérature sur les datasets < 1000 exemples).
    delta_acc = m_ft["accuracy"] - m_base["accuracy"]
    delta_f1 = m_ft["f1_macro"] - m_base["f1_macro"]
    print(f"\n{'=' * 60}")
    print("VERDICT")
    print("=" * 60)
    print(f"  Accuracy: {m_base['accuracy']:.1%} → {m_ft['accuracy']:.1%} ({delta_acc:+.1%})")
    print(f"  F1 macro: {m_base['f1_macro']:.1%} → {m_ft['f1_macro']:.1%} ({delta_f1:+.1%})")

    if delta_f1 > 0.05:
        print("  → Amélioration significative par le fine-tuning.")
    elif delta_f1 > 0:
        print("  → Amélioration légère. Enrichir les données pourrait aider.")
    else:
        print("  → Pas d'amélioration. Revoir les données ou les hyperparamètres.")


if __name__ == "__main__":
    main()
