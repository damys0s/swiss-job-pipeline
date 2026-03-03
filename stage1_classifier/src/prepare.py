"""
Phase 3 — Préparation des données pour le fine-tuning OpenAI
Remap 5 classes → 3 classes, génération JSONL, split train/val stratifié.
Usage : python -m src.prepare
"""

import csv
import json
import random
from pathlib import Path
from collections import Counter
from datetime import datetime

# ─── Config ───────────────────────────────────────────────────────────────────
# Résolution du chemin racine depuis l'emplacement de ce fichier.
# Permet d'appeler le script depuis n'importe quel répertoire de travail.
BASE_DIR = Path(__file__).resolve().parent.parent

LABELED_FILE = BASE_DIR / "data" / "labeled" / "labeled_jobs.csv"
OUTPUT_DIR = BASE_DIR / "data" / "training"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_FILE = BASE_DIR / "data" / "training" / "train.jsonl"
VAL_FILE = BASE_DIR / "data" / "training" / "val.jsonl"

VALID_CLASSES = {"DATA_ENGINEERING", "BI_ANALYTICS", "NOT_RELEVANT"}

# Remapping 5 → 3 classes.
# Décision de conception : DBA_INFRA et APP_SUPPORT sont fusionnés dans NOT_RELEVANT
# car ces profils ne correspondent pas à notre objectif (Data Engineer / BI Developer).
# Les conserver comme classes distinctes nécessiterait ~100 exemples supplémentaires
# par classe pour un fine-tuning fiable, ce qui dépasse le scope du projet.
REMAP = {
    "DATA_ENGINEERING": "DATA_ENGINEERING",
    "BI_ANALYTICS": "BI_ANALYTICS",
    "NOT_RELEVANT": "NOT_RELEVANT",
    "DBA_INFRA": "NOT_RELEVANT",
    "APP_SUPPORT": "NOT_RELEVANT",
}

# CRITIQUE : ce prompt système doit être IDENTIQUE à celui de classify.py.
# Toute divergence entre le prompt d'entraînement et le prompt d'inférence
# dégrade significativement les performances du modèle fine-tuné.
SYSTEM_PROMPT = (
    "Tu es un classificateur d'offres d'emploi IT en Suisse romande. "
    "Classe chaque offre dans exactement une catégorie parmi : "
    "DATA_ENGINEERING, BI_ANALYTICS, NOT_RELEVANT. "
    "Réponds uniquement avec le nom de la catégorie, rien d'autre."
)

TRAIN_RATIO = 0.8  # 80% train, 20% validation
SEED = 42          # Graine fixe pour la reproductibilité du split


# ─── Step 1: Load and remap ──────────────────────────────────────────────────

def load_and_remap(filepath: Path) -> list[dict]:
    """Charge le CSV étiqueté, applique le remapping et retourne une liste de dicts.

    Les entrées avec un label non reconnu sont ignorées avec un avertissement,
    ce qui permet de traiter les CSV partiellement corrompus sans crash.
    """
    examples = []
    remapped_count = 0

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            original_label = row.get("label", "").strip()
            if original_label not in REMAP:
                print(f"  ⚠️  Label inconnu ignoré: '{original_label}' (offre {row.get('id', '?')})")
                continue

            new_label = REMAP[original_label]
            if new_label != original_label:
                remapped_count += 1

            examples.append({
                "id": row["id"],
                "title": row.get("title", ""),
                "company": row.get("company", ""),
                "location": row.get("location", ""),
                "description": row.get("description", ""),
                "label": new_label,
                "original_label": original_label,
            })

    print(f"  Chargé: {len(examples)} offres")
    print(f"  Remappé: {remapped_count} offres (DBA_INFRA/APP_SUPPORT → NOT_RELEVANT)")
    return examples


# ─── Step 2: Format to JSONL messages ────────────────────────────────────────

def format_user_content(example: dict) -> str:
    """Construit le message utilisateur à partir des champs de l'offre.

    La description est tronquée à 200 mots pour deux raisons :
      1. Réduire le coût de fine-tuning (tokens = coût).
      2. Forcer le modèle à apprendre à classifier sur des inputs courts,
         ce qui correspond au cas d'usage réel (l'agent n'a souvent que
         le titre + un résumé de quelques lignes).

    Le format (Titre / Entreprise / Localisation / Description) doit être
    identique à celui utilisé dans classify.py._format_input().
    """
    parts = [f"Titre: {example['title']}"]
    if example["company"]:
        parts.append(f"Entreprise: {example['company']}")
    if example["location"]:
        parts.append(f"Localisation: {example['location']}")
    if example["description"]:
        words = example["description"].split()
        desc = " ".join(words[:200])
        parts.append(f"Description: {desc}")
    return "\n".join(parts)


def to_jsonl_message(example: dict) -> dict:
    """Convertit un exemple au format JSONL attendu par l'API de fine-tuning OpenAI."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_user_content(example)},
            {"role": "assistant", "content": example["label"]},
        ]
    }


# ─── Step 3: Stratified split ────────────────────────────────────────────────

def stratified_split(examples: list[dict], train_ratio: float, seed: int) -> tuple[list, list]:
    """Divise les exemples en train/val de façon stratifiée par label.

    La stratification garantit que chaque split conserve les mêmes proportions
    de classes que le dataset complet, ce qui est essentiel pour une évaluation
    représentative (surtout avec des classes déséquilibrées comme BI_ANALYTICS).

    Utilise random.Random(seed) (instance locale) plutôt que random.seed() global
    pour éviter d'affecter d'autres parties du code qui pourraient utiliser le
    générateur global.
    """
    by_class = {}
    for ex in examples:
        by_class.setdefault(ex["label"], []).append(ex)

    rng = random.Random(seed)
    train, val = [], []

    for label, items in by_class.items():
        rng.shuffle(items)
        split_idx = int(len(items) * train_ratio)
        train.extend(items[:split_idx])
        val.extend(items[split_idx:])

    # Mélange final pour éviter que le modèle apprenne un ordre par classe
    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


# ─── Step 4: Validate JSONL ──────────────────────────────────────────────────

def validate_messages(messages: list[dict]) -> list[str]:
    """Valide une entrée JSONL avant l'upload. Retourne la liste des erreurs trouvées.

    L'API OpenAI rejette silencieusement les entrées mal formées, ce qui peut
    fausser le fine-tuning. Cette validation anticipée évite des coûts inutiles.
    """
    errors = []
    if not isinstance(messages, list) or len(messages) != 3:
        errors.append("messages doit contenir exactement 3 éléments")
        return errors

    roles = [m.get("role") for m in messages]
    if roles != ["system", "user", "assistant"]:
        errors.append(f"Rôles incorrects: {roles}")

    for m in messages:
        if not m.get("content", "").strip():
            errors.append(f"Contenu vide pour le rôle {m.get('role', '?')}")

    assistant_content = messages[2].get("content", "").strip()
    if assistant_content not in VALID_CLASSES:
        errors.append(f"Label assistant invalide: '{assistant_content}'")

    return errors


# ─── Step 5: Write and estimate cost ─────────────────────────────────────────

def write_jsonl(examples: list[dict], filepath: Path):
    """Écrit les exemples au format JSONL (une entrée JSON par ligne)."""
    with open(filepath, "w", encoding="utf-8") as f:
        for ex in examples:
            msg = to_jsonl_message(ex)
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def estimate_tokens(examples: list[dict]) -> int:
    """Estimation grossière du nombre de tokens : ~1 token par 4 caractères.

    Cette heuristique (règle des 4 caractères) est une approximation standard
    pour les modèles GPT en anglais/français. L'estimation réelle de l'API
    tiktoken peut différer de ±15%, mais suffit pour la planification budgétaire.
    """
    total_chars = 0
    for ex in examples:
        msg = to_jsonl_message(ex)
        for m in msg["messages"]:
            total_chars += len(m["content"])
    return total_chars // 4


def estimate_cost(n_tokens: int, n_epochs: int = 3) -> float:
    """Estime le coût de fine-tuning pour gpt-4o-mini.

    Tarif : $3.00 / 1M tokens de training (au 2025-Q1).
    Le coût total = tokens_train × n_epochs × tarif_par_million.
    """
    return (n_tokens * n_epochs / 1_000_000) * 3.00


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  PHASE 3 — Préparation fine-tuning (3 classes)")
    print("=" * 60)

    # Step 1: Load & remap
    print("\n[1/5] Chargement et remapping...")
    if not LABELED_FILE.exists():
        print(f"  ❌ Fichier non trouvé: {LABELED_FILE}")
        return
    examples = load_and_remap(LABELED_FILE)

    # Step 2: Stats après remap
    print("\n[2/5] Distribution après remapping:")
    counts = Counter(ex["label"] for ex in examples)
    for label in sorted(VALID_CLASSES):
        c = counts.get(label, 0)
        pct = c / len(examples) * 100
        bar = "█" * (c // 2)
        print(f"  {label:<20} {c:>4} ({pct:5.1f}%) {bar}")

    # Step 3: Split
    print(f"\n[3/5] Split stratifié ({int(TRAIN_RATIO*100)}/{int((1-TRAIN_RATIO)*100)})...")
    train, val = stratified_split(examples, TRAIN_RATIO, SEED)

    print(f"  Train: {len(train)} exemples")
    train_counts = Counter(ex["label"] for ex in train)
    for label in sorted(VALID_CLASSES):
        print(f"    {label:<20} {train_counts.get(label, 0):>4}")

    print(f"  Val:   {len(val)} exemples")
    val_counts = Counter(ex["label"] for ex in val)
    for label in sorted(VALID_CLASSES):
        print(f"    {label:<20} {val_counts.get(label, 0):>4}")

    # Step 4: Validate — arrêt si des erreurs sont détectées pour éviter
    # un upload coûteux d'un fichier invalide
    print("\n[4/5] Validation du format JSONL...")
    error_count = 0
    for ex in examples:
        msg = to_jsonl_message(ex)
        errs = validate_messages(msg["messages"])
        if errs:
            error_count += 1
            print(f"  ❌ Offre {ex['id']}: {'; '.join(errs)}")
    if error_count == 0:
        print("  ✅ Toutes les entrées sont valides.")
    else:
        print(f"  ❌ {error_count} entrées invalides.")
        return

    # Step 5: Write + cost
    print("\n[5/5] Écriture des fichiers...")
    write_jsonl(train, TRAIN_FILE)
    write_jsonl(val, VAL_FILE)
    print(f"  ✅ {TRAIN_FILE}")
    print(f"  ✅ {VAL_FILE}")

    train_tokens = estimate_tokens(train)
    total_tokens = estimate_tokens(examples)
    cost_3ep = estimate_cost(train_tokens, n_epochs=3)

    print(f"\n{'=' * 60}")
    print(f"  RÉSUMÉ")
    print(f"{'=' * 60}")
    print(f"  Total exemples:    {len(examples)}")
    print(f"  Train:             {len(train)}")
    print(f"  Validation:        {len(val)}")
    print(f"  Tokens estimés:    ~{total_tokens:,} (total), ~{train_tokens:,} (train)")
    print(f"  Coût estimé:       ~${cost_3ep:.2f} (3 epochs, gpt-4o-mini)")
    print(f"  Fichiers:          {TRAIN_FILE}, {VAL_FILE}")
    print()


if __name__ == "__main__":
    main()
