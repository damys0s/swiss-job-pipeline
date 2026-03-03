"""
emailer.py — Génération et envoi de l'email d'alerte emploi
============================================================
Génère un email HTML structuré avec les top offres du jour
et l'envoie via SMTP Gmail (App Password).

Usage:
    from src.emailer import JobEmailer
    emailer = JobEmailer()
    success = emailer.send(jobs, stats)
"""

import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config.settings import (
    EMAIL_ADDRESS,
    EMAIL_PASSWORD,
    EMAIL_TO,
    SEND_EMPTY_EMAIL,
    SMTP_HOST,
    SMTP_PORT,
)

logger = logging.getLogger(__name__)

# Couleurs par catégorie (badges dans l'email HTML)
CATEGORY_COLORS = {
    "DATA_ENGINEERING": "#2196F3",   # Bleu
    "BI_ANALYTICS":     "#4CAF50",   # Vert
    "DBA_INFRA":        "#FF9800",   # Orange
}

CATEGORY_LABELS = {
    "DATA_ENGINEERING": "Data Engineering",
    "BI_ANALYTICS":     "BI / Analytics",
    "DBA_INFRA":        "DBA / Infra",
}


def _score_bar(score: float) -> str:
    """Génère une barre de progression HTML pour le score (0-1)."""
    pct = int(score * 100)
    color = "#4CAF50" if pct >= 70 else "#FFC107" if pct >= 50 else "#F44336"
    return (
        f'<div style="background:#eee;border-radius:4px;height:8px;width:200px;display:inline-block;">'
        f'<div style="background:{color};height:8px;border-radius:4px;width:{pct}%;"></div>'
        f'</div> <span style="font-size:12px;color:#666;">{pct}%</span>'
    )


def _truncate(text: str, n_words: int = 100) -> str:
    words = text.split()
    if len(words) <= n_words:
        return text
    return " ".join(words[:n_words]) + "..."


def _job_card(job: dict, rank: int) -> str:
    """Génère le HTML d'une carte d'offre."""
    title    = job.get("title", "Sans titre")
    company  = job.get("company", "")
    location = job.get("location", "")
    url      = job.get("url", "#")
    label    = job.get("label", "")
    score    = job.get("score", 0.0)
    desc     = _truncate(job.get("description", ""), 100)

    badge_color = CATEGORY_COLORS.get(label, "#9E9E9E")
    badge_label = CATEGORY_LABELS.get(label, label)

    company_loc = " | ".join(filter(None, [company, location]))

    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin:12px 0;background:#fff;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <h3 style="margin:0 0 4px 0;font-size:16px;">
          <a href="{url}" style="color:#1a73e8;text-decoration:none;">{rank}. {title}</a>
        </h3>
        <span style="background:{badge_color};color:white;padding:3px 8px;border-radius:12px;font-size:11px;white-space:nowrap;margin-left:8px;">
          {badge_label}
        </span>
      </div>
      <p style="margin:4px 0;color:#555;font-size:13px;">{company_loc}</p>
      <div style="margin:8px 0;">{_score_bar(score)}</div>
      <p style="margin:8px 0 0 0;color:#333;font-size:13px;line-height:1.5;">{desc}</p>
    </div>
    """


def _build_html(jobs: list[dict], stats: dict, run_date: str) -> str:
    """Construit le HTML complet de l'email."""
    n_found   = stats.get("total_raw", 0)
    n_relevant = stats.get("n_relevant", 0)
    n_shown   = len(jobs)

    cards = "".join(_job_card(j, i + 1) for i, j in enumerate(jobs))

    # Stats footer
    adzuna_reqs = stats.get("adzuna", {}).get("kept", 0)
    serpapi_req = stats.get("serpapi", {}).get("requests", 0)
    indeed_kept = stats.get("indeed_rss", {}).get("kept", 0)
    duration    = stats.get("duration_seconds", 0)

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;background:#f5f5f5;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a73e8,#0d47a1);color:white;padding:20px 24px;border-radius:12px 12px 0 0;">
    <h1 style="margin:0;font-size:22px;">🎯 Job Alert — {run_date}</h1>
    <p style="margin:6px 0 0 0;opacity:0.85;font-size:14px;">
      {n_found} offres collectées &nbsp;·&nbsp; {n_relevant} pertinentes &nbsp;·&nbsp; Top {n_shown} présentées
    </p>
  </div>

  <!-- Corps -->
  <div style="background:#f9f9f9;padding:16px 24px;border:1px solid #e0e0e0;border-top:none;">
    {"<p style='color:#666;'>Aucune nouvelle offre pertinente aujourd'hui.</p>" if not jobs else cards}
  </div>

  <!-- Footer stats -->
  <div style="background:#eeeeee;padding:12px 24px;border-radius:0 0 12px 12px;border:1px solid #e0e0e0;border-top:none;">
    <p style="margin:0;color:#888;font-size:11px;">
      📊 Sources : Adzuna ({adzuna_reqs} offres) · SerpApi ({serpapi_req} requêtes) · Indeed RSS ({indeed_kept} offres)
      &nbsp;·&nbsp; Durée : {duration:.1f}s
      &nbsp;·&nbsp; Généré par <strong>job-alert-agent</strong>
    </p>
  </div>

</body>
</html>
"""


class JobEmailer:
    """Génère et envoie l'email d'alerte emploi via SMTP Gmail."""

    def send(self, jobs: list[dict], stats: dict) -> bool:
        """Envoie l'email d'alerte.

        Args:
            jobs:  Liste d'offres scorées et classifiées.
            stats: Statistiques du run (venant de pipeline.py).

        Returns:
            True si l'email a été envoyé avec succès.
        """
        if not jobs and not SEND_EMPTY_EMAIL:
            logger.info("Aucune offre nouvelle — email non envoyé (SEND_EMPTY_EMAIL=False)")
            return False

        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            logger.error("EMAIL_ADDRESS ou EMAIL_PASSWORD manquant — email non envoyé")
            return False

        run_date = date.today().strftime("%d/%m/%Y")
        n_shown  = len(jobs)
        subject  = f"🎯 [Job Alert] {n_shown} nouvelles offres — {run_date}" if jobs else f"[Job Alert] Rien de nouveau — {run_date}"

        html_body = _build_html(jobs, stats, run_date)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ADDRESS, EMAIL_TO, msg.as_string())
            logger.info(f"Email envoyé : {n_shown} offres → {EMAIL_TO}")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("Authentification SMTP échouée. Vérifie l'App Password Gmail.")
            return False
        except Exception as e:
            logger.error(f"Erreur SMTP : {e}")
            return False
