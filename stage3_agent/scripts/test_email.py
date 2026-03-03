"""
test_email.py — Script de test interactif pour l'emailer (Phase 3)
===================================================================
Charge les offres scorées (Phase 2) et génère/envoie l'email d'alerte.

Usage (depuis la racine de job-alert-agent/) :
    python scripts/test_email.py --preview        # Génère le HTML, ouvre dans le navigateur
    python scripts/test_email.py --send           # Envoie l'email réel via SMTP
    python scripts/test_email.py --preview --send # Les deux
"""

import argparse
import json
import sys
import webbrowser
from datetime import date
from pathlib import Path

# Force UTF-8 sur Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(ROOT / "data" / "test_score_output.json"))
    parser.add_argument("--preview", action="store_true", help="Sauvegarde le HTML et ouvre dans le navigateur")
    parser.add_argument("--send", action="store_true", help="Envoie l'email via SMTP Gmail")
    args = parser.parse_args()

    if not args.preview and not args.send:
        print("Spécifie --preview et/ou --send")
        print("  --preview : génère le HTML localement")
        print("  --send    : envoie l'email via Gmail SMTP")
        sys.exit(1)

    # Chargement des offres scorées
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERREUR] Fichier introuvable : {input_path}")
        print("  Lance d'abord : python scripts/test_score.py --save")
        sys.exit(1)

    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"\n[TEST] Emailer — Phase 3")
    print(f"   Input  : {input_path.name} ({len(jobs)} offres)")

    # Stats simulées (normalement viennent du pipeline complet)
    stats = {
        "total_raw":   12,
        "n_relevant":  10,
        "adzuna":      {"kept": 2},
        "serpapi":     {"requests": 1},
        "indeed_rss":  {"kept": 0},
        "duration_seconds": 45.0,
    }

    from src.emailer import _build_html, JobEmailer

    run_date = date.today().strftime("%d/%m/%Y")
    html     = _build_html(jobs, stats, run_date)

    # ----------------------------------------------------------------
    # Mode --preview
    # ----------------------------------------------------------------
    if args.preview:
        preview_path = ROOT / "data" / "email_preview.html"
        preview_path.write_text(html, encoding="utf-8")
        print(f"\n[PREVIEW] HTML sauvegardé : {preview_path}")

        # Ouvre automatiquement dans le navigateur par défaut
        try:
            webbrowser.open(preview_path.as_uri())
            print("           Ouverture dans le navigateur...")
        except Exception:
            print("           Ouvre manuellement le fichier dans un navigateur.")

    # ----------------------------------------------------------------
    # Mode --send
    # ----------------------------------------------------------------
    if args.send:
        from config.settings import EMAIL_ADDRESS, EMAIL_TO, EMAIL_PASSWORD

        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            print("\n[ERREUR] EMAIL_ADDRESS ou EMAIL_PASSWORD manquant dans .env")
            print("  Remplis ton .env avec tes identifiants Gmail + App Password")
            sys.exit(1)

        print(f"\n[ENVOI] De  : {EMAIL_ADDRESS}")
        print(f"        Pour : {EMAIL_TO}")
        print(f"        Objet: [Job Alert] {len(jobs)} nouvelles offres — {run_date}")

        emailer = JobEmailer()
        success = emailer.send(jobs, stats)

        if success:
            print("[OK] Email envoyé avec succes !")
        else:
            print("[ERREUR] Echec de l'envoi — vérifie les logs ci-dessus")
            sys.exit(1)


if __name__ == "__main__":
    main()
