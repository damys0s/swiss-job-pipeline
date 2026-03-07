"""
track_apply.py — Suivi des candidatures
========================================
Marque les offres reçues par email comme "candidatées" et consulte l'historique.

Usage :
    python scripts/track_apply.py apply <url>     Enregistrer une candidature
    python scripts/track_apply.py list             Lister toutes les candidatures
    python scripts/track_apply.py stats            Statistiques globales de la DB
"""

import sys
from pathlib import Path

# Accès aux modules du projet depuis scripts/
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.deduplicator import Deduplicator


def cmd_apply(url: str):
    dedup = Deduplicator()
    found = dedup.mark_applied(url)
    if found:
        print(f"Candidature enregistrée : {url}")
    else:
        print(f"Offre non trouvée dans la DB : {url}")
        print("Vérifie l'URL ou lance d'abord un run du pipeline.")


def cmd_list():
    dedup = Deduplicator()
    jobs = dedup.get_applied()
    if not jobs:
        print("Aucune candidature enregistrée.")
        return

    print(f"\n{'Date':12}  {'Score':6}  {'Titre':38}  Entreprise")
    print("-" * 85)
    for j in jobs:
        score = f"{j['score']:.2f}" if j.get("score") else "  —  "
        title = (j.get("title") or "")[:38]
        company = (j.get("company") or "")[:30]
        print(f"{j['applied_at']:12}  {score:6}  {title:38}  {company}")


def cmd_stats():
    dedup = Deduplicator()
    s = dedup.get_stats()
    print(f"\nStatistiques seen_jobs.db")
    print(f"  Total offres vues         : {s['total']}")
    print(f"  Envoyées dans un email    : {s['in_email']}")
    print(f"  Candidatures enregistrées : {s['applied']}")
    if s["in_email"]:
        print(f"  Taux de candidature       : {s['applied'] / s['in_email'] * 100:.1f}%")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "apply":
        if len(sys.argv) < 3:
            print("Usage : python scripts/track_apply.py apply <url>")
            sys.exit(1)
        cmd_apply(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    elif cmd == "stats":
        cmd_stats()
    else:
        print(f"Commande inconnue : {cmd}")
        print(__doc__)
        sys.exit(1)
