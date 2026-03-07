"""
settings.py — Configuration centralisée de l'agent de veille emploi
====================================================================
Toutes les clés API et paramètres sont chargés depuis les variables d'environnement
(localement via .env, en CI via GitHub Actions secrets).

NE JAMAIS hardcoder des clés API ici.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# --- Racine du projet -------------------------------------------------------
# BASE_DIR = stage3_agent/ (répertoire du stage)
# REPO_ROOT = swiss-job-pipeline/ (racine du dépôt)
BASE_DIR  = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

# Charge .env si présent (développement local — cherche à la racine du repo)
_env_path = REPO_ROOT / ".env"
if not _env_path.exists():
    _env_path = BASE_DIR / ".env"   # fallback pour run isolé dans stage3_agent/
if _env_path.exists():
    load_dotenv(_env_path)

# --- Clés API ---------------------------------------------------------------
ADZUNA_APP_ID  = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
SERPAPI_KEY    = os.getenv("SERPAPI_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# --- Email ------------------------------------------------------------------
EMAIL_ADDRESS  = os.getenv("EMAIL_ADDRESS", "")   # Ex: moncompte@gmail.com
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")   # App Password Gmail (16 chars)

# Destinataire — si l'adresse contient @ plusieurs fois (typo fréquente), on la corrige
_raw_to = os.getenv("EMAIL_TO", EMAIL_ADDRESS)
if _raw_to.count("@") > 1:
    _parts = _raw_to.split("@")
    _raw_to = _parts[0] + "@" + _parts[-1]
EMAIL_TO = _raw_to
SMTP_HOST      = "smtp.gmail.com"
SMTP_PORT      = 587

# Comportement quand aucune offre nouvelle n'est trouvée
SEND_EMPTY_EMAIL = False   # True = envoie un email "rien de nouveau", False = ne rien envoyer

# --- Chemins des données ----------------------------------------------------
# Les données partagées (vectorstore, corpus) sont à la racine du dépôt.
# La base de déduplication et les logs restent dans stage3_agent/.
DATA_DIR        = REPO_ROOT / "data"
VECTORSTORE_DIR = DATA_DIR / "vectorstore"
DB_PATH         = BASE_DIR / "data" / "seen_jobs.db"   # pipeline dedup (tracké git, CI)
TRACKER_DB_PATH = BASE_DIR / "data" / "tracker.db"    # candidatures perso (gitignored)
LOGS_DIR        = BASE_DIR / "logs" / "runs"

# --- Chemins des configs -----------------------------------------------------
CONFIG_DIR           = BASE_DIR / "config"
PROFILE_PATH         = CONFIG_DIR / "profile.json"
SEARCH_QUERIES_PATH  = CONFIG_DIR / "search_queries.json"

# --- Classificateur (étape 1) -----------------------------------------------
# model_id du fine-tune GPT-4o-mini
CLASSIFIER_MODEL_ID = "ft:gpt-4o-mini-2024-07-18:personal:job-classifier:DF1fQqSZ"

# --- Embedding (étape 2) ----------------------------------------------------
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# --- Paramètres du pipeline -------------------------------------------------
TOP_N_RESULTS   = 10     # Nombre max d'offres présentées dans l'email
MIN_SCORE       = 0.30   # Seuil minimal de score cosine pour garder une offre
MAX_DAYS_OLD    = 1      # Fenêtre de collecte en jours

# Collecte : sources activées
USE_ADZUNA     = True
USE_SERPAPI    = True
USE_INDEED_RSS = False   # Indeed bloque les flux RSS depuis 2024 — désactivé

# Timeout API (secondes)
API_TIMEOUT = 30
