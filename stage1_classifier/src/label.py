"""
Phase 2 — Étiquetage des offres d'emploi
Pré-étiquetage automatique par règles + validation manuelle dans le terminal.
Usage : python -m src.label
"""

import os
import re
import json
import csv
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
LABELED_DIR = BASE_DIR / "data" / "labeled"
LABELED_DIR.mkdir(parents=True, exist_ok=True)

LABELED_FILE = LABELED_DIR / "labeled_jobs.csv"
STATS_FILE = LABELED_DIR / "labeling_stats.json"

# 5 classes utilisées pour l'étiquetage manuel.
# DBA_INFRA et APP_SUPPORT seront fusionnées dans NOT_RELEVANT en Phase 3,
# mais sont conservées séparément ici pour permettre une analyse plus fine
# de la composition du dataset si nécessaire.
CATEGORIES = ["DATA_ENGINEERING", "BI_ANALYTICS", "DBA_INFRA", "APP_SUPPORT", "NOT_RELEVANT"]

# ============================================================
# Règles de pré-étiquetage (appliquées sur titre et description en minuscules)
# ============================================================
# L'ordre des règles est intentionnel et critique :
#   1. DATA_ENGINEERING en premier, car il partage des termes avec BI_ANALYTICS
#      (ex: "data analyst" pourrait faussement matcher "data" dans DE).
#   2. BI_ANALYTICS en deuxième.
#   3. Les classes moins ambiguës (DBA, APP_SUPPORT, NOT_RELEVANT) en dernier.
#
# Les patterns sur le titre ont la priorité sur ceux de la description,
# car le titre est le signal le plus fort (intentionnel, concis, sans bruit).

RULES = [
    # DATA_ENGINEERING — vérifié avant BI car les deux contiennent "data"
    {
        "label": "DATA_ENGINEERING",
        "title_patterns": [
            r"\bdata\s*engineer", r"\betl\b", r"\bpipeline", r"\bdbt\b",
            r"\bairflow\b", r"\bspark\b", r"\bdata\s*platform",
            r"\bingénieur\s*donn", r"\bdata\s*integration",
        ],
        "desc_patterns": [
            r"\betl\b.*\bpipeline", r"\bdata\s*warehouse", r"\bdbt\b",
            r"\bairflow\b", r"\bspark\b", r"\bdata\s*lake",
        ],
    },
    # BI_ANALYTICS
    {
        "label": "BI_ANALYTICS",
        "title_patterns": [
            r"\bbi\b", r"\bbusiness\s*intelligence", r"\bpower\s*bi\b",
            r"\btableau\b", r"\bdata\s*analyst", r"\breporting",
            r"\banalyste\s*donn", r"\banalytics\b",
        ],
        "desc_patterns": [
            r"\bpower\s*bi\b.*\bdashboard", r"\btableau\b.*\breport",
            r"\bkpi\b.*\breporting",
        ],
    },
    # DBA_INFRA
    {
        "label": "DBA_INFRA",
        "title_patterns": [
            r"\bdba\b", r"\bdatabase\s*admin", r"\boracle\s*dba",
            r"\bsql\s*server\s*admin", r"\bpostgresql\s*admin",
            r"\bdatabase\s*engineer", r"\badmin.*base\s*de\s*donn",
            r"\badministrateur.*base", r"\badministrateur.*système",
            r"\bsysadmin\b",
        ],
        "desc_patterns": [
            r"\boracle\b.*\badmin", r"\bsql\s*server\b.*\badmin",
            r"\bbackup.*\brestore", r"\btuning.*\bdatabase",
        ],
    },
    # APP_SUPPORT
    {
        "label": "APP_SUPPORT",
        "title_patterns": [
            r"\bsupport\s*applicatif", r"\bapplication\s*support",
            r"\bhelpdesk\b", r"\bl2\b", r"\bl3\b",
            r"\bqa\s*fonctionnel", r"\bsupport\s*informatique",
            r"\btechnicien\s*support", r"\bsupport\s*technique",
        ],
        "desc_patterns": [
            r"\bticket.*\bincident", r"\bjira\b.*\bsupport",
            r"\bescalation\b", r"\btroubleshooting\b",
        ],
    },
    # NOT_RELEVANT — vérification explicite pour accélérer l'étiquetage des
    # offres manifestement hors scope sans attendre un match par défaut.
    # Le pattern `\bmanager\b(?!.*data)(?!.*bi)(?!.*engineer)` exclut
    # les "Data Manager" et "BI Manager" qui pourraient être pertinents.
    {
        "label": "NOT_RELEVANT",
        "title_patterns": [
            r"\bfrontend\b", r"\bfront[\s-]*end\b", r"\bux\s*design",
            r"\bui\s*design", r"\bmarketing\b", r"\bcommercial\b",
            r"\bressources\s*humaines\b", r"\brh\b", r"\bcomptable\b",
            r"\bvendeur", r"\bassistant.*administratif",
            r"\bchef\s*de\s*projet\b", r"\bproject\s*manager\b",
            r"\bcuisinier", r"\bserveur\b", r"\bréceptionnist",
            r"\bmanager\b(?!.*data)(?!.*bi)(?!.*engineer)",
        ],
        "desc_patterns": [],
    },
]


def pre_label(title: str, description: str) -> str:
    """Applique les règles de pré-étiquetage et retourne le label suggéré.

    Logique : première règle dont un pattern titre correspond → label retourné.
    Si aucun pattern titre ne correspond, on tente les patterns description.
    Si aucune règle ne correspond → "TO_REVIEW" (étiquetage manuel requis).

    Seuls les 500 premiers caractères de la description sont analysés pour
    éviter les faux positifs sur des textes techniques longs qui mentionnent
    incidemment des technologies hors scope.
    """
    title_lower = title.lower()
    desc_lower = description[:500].lower() if description else ""

    for rule in RULES:
        # Le titre est le signal prioritaire — plus précis et moins bruité
        for pattern in rule["title_patterns"]:
            if re.search(pattern, title_lower):
                return rule["label"]

        # La description est un signal secondaire — utilisée seulement si le
        # titre n'a pas permis de trancher
        for pattern in rule.get("desc_patterns", []):
            if re.search(pattern, desc_lower):
                return rule["label"]

    return "TO_REVIEW"


def load_existing_labels() -> dict:
    """Charge les offres déjà étiquetées depuis le CSV. Retourne {job_id: row_dict}.

    Appelé en lecture fraîche à chaque affichage de statistiques pour refléter
    toutes les modifications (y compris les sessions parallèles éventuelles).
    """
    labeled = {}
    if LABELED_FILE.exists():
        with open(LABELED_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                labeled[row["id"]] = row
    return labeled


def save_label(job: dict, label: str, pre_label_val: str):
    """Ajoute une entrée étiquetée au CSV en mode append.

    L'écriture ligne par ligne (mode append) est intentionnelle : elle permet
    de reprendre l'étiquetage là où il s'est arrêté sans perdre de données
    en cas d'interruption (Ctrl+C, crash, etc.).

    La description est tronquée à 500 caractères pour limiter la taille du CSV
    tout en conservant suffisamment de contexte pour la relecture.
    """
    file_exists = LABELED_FILE.exists()
    fieldnames = ["id", "source", "title", "company", "location", "description",
                  "url", "date_collected", "date_posted", "pre_label", "label", "labeled_at"]

    with open(LABELED_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        row = {
            "id": job["id"],
            "source": job["source"],
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "description": job.get("description", "")[:500],
            "url": job.get("url", ""),
            "date_collected": job.get("date_collected", ""),
            "date_posted": job.get("date_posted", ""),
            "pre_label": pre_label_val,
            "label": label,
            "labeled_at": datetime.now().isoformat(),
        }
        writer.writerow(row)


def print_stats(labeled: dict):
    """Affiche les statistiques d'étiquetage avec une barre de progression visuelle.

    Seuils d'alerte :
      - ✅ ≥ 50 exemples : suffisant pour le fine-tuning
      - ⚠️ ≥ 25 exemples : insuffisant mais utilisable
      - ❌ < 25 exemples : classe sous-représentée, risque de mauvaises performances

    Le taux d'accord (pré-label == label final) mesure la qualité des règles.
    Un taux > 80% indique que les règles couvrent bien les cas fréquents.
    """
    if not labeled:
        print("\n  Aucune offre étiquetée.\n")
        return

    counts = {}
    agreements = 0
    total = len(labeled)

    for row in labeled.values():
        lbl = row.get("label", "UNKNOWN")
        counts[lbl] = counts.get(lbl, 0) + 1
        if row.get("pre_label") == row.get("label"):
            agreements += 1

    print("\n" + "=" * 50)
    print(f"  STATISTIQUES ({total} étiquetées)")
    print("=" * 50)
    for cat in CATEGORIES:
        count = counts.get(cat, 0)
        bar = "█" * (count // 2)
        pct = (count / total * 100) if total else 0
        status = "✅" if count >= 50 else "⚠️" if count >= 25 else "❌"
        print(f"  {status} {cat:<20} {count:>4} ({pct:5.1f}%) {bar}")

    agreement_rate = (agreements / total * 100) if total else 0
    print(f"\n  Taux accord pré-label/label: {agreement_rate:.1f}%")

    low_classes = [cat for cat in CATEGORIES if counts.get(cat, 0) < 50]
    if low_classes:
        print(f"  ⚠️  Classes sous 50 exemples: {', '.join(low_classes)}")
    print()


def main():
    # Chargement du fichier raw le plus récent (tri lexicographique sur la date ISO)
    raw_files = sorted(RAW_DIR.glob("jobs_raw_*.json"), reverse=True)
    if not raw_files:
        print("❌ Aucun fichier dans data/raw/. Lance d'abord la collecte.")
        return

    with open(raw_files[0], "r", encoding="utf-8") as f:
        all_jobs = json.load(f)

    print(f"Fichier chargé: {raw_files[0].name} ({len(all_jobs)} offres)")

    # Chargement des labels existants pour permettre la reprise de session
    existing = load_existing_labels()
    labeled_ids = set(existing.keys())
    print(f"Déjà étiquetées: {len(labeled_ids)}")

    to_label = [j for j in all_jobs if j["id"] not in labeled_ids]
    print(f"Restantes: {len(to_label)}")

    if not to_label:
        print("Toutes les offres sont étiquetées.")
        print_stats(existing)
        return

    print_stats(existing)

    print("─" * 50)
    print("COMMANDES:")
    print("  1-5  → Assigner la catégorie")
    print("  Enter → Accepter le pré-label")
    print("  s    → Voir les statistiques")
    print("  q    → Quitter (sauvegarde automatique)")
    print("─" * 50)

    labeled_this_session = 0

    for i, job in enumerate(to_label):
        title = job.get("title", "N/A")
        company = job.get("company", "N/A")
        location = job.get("location", "N/A")
        # 2000 chars affichés à l'écran — plus que les 500 sauvegardés en CSV,
        # pour donner le contexte complet à l'annotateur humain
        desc = job.get("description", "")[:2000]

        suggested = pre_label(title, desc)

        print(f"\n[{len(labeled_ids) + labeled_this_session + 1}/{len(all_jobs)}] ── Restant: {len(to_label) - i}")
        print(f"  Titre:       {title}")
        print(f"  Entreprise:  {company}")
        print(f"  Lieu:        {location}")
        print(f"  Description: {desc}...")
        print()
        print(f"  Suggestion: → {suggested}")
        print()
        for idx, cat in enumerate(CATEGORIES, 1):
            marker = " ◄" if cat == suggested else ""
            print(f"    {idx}. {cat}{marker}")

        while True:
            choice = input(f"\n  Choix [Enter={suggested}] > ").strip().lower()

            if choice == "":
                label = suggested
                break
            elif choice == "q":
                print(f"\nSession terminée. {labeled_this_session} offres étiquetées.")
                print_stats(load_existing_labels())
                return
            elif choice == "s":
                # Rechargement depuis disque pour voir les stats à jour
                print_stats(load_existing_labels())
                continue
            elif choice in ("1", "2", "3", "4", "5"):
                label = CATEGORIES[int(choice) - 1]
                break
            else:
                print("  ❌ Choix invalide. 1-5, Enter, s, ou q.")

        save_label(job, label, suggested)
        labeled_this_session += 1

        # Affichage de statistiques intermédiaires toutes les 25 offres
        # pour aider l'annotateur à cibler les classes sous-représentées
        if labeled_this_session % 25 == 0:
            print_stats(load_existing_labels())

    print(f"\n✅ Tout étiqueté ! {labeled_this_session} offres cette session.")
    print_stats(load_existing_labels())


if __name__ == "__main__":
    main()
