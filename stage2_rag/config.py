"""
config.py — Paramètres centralisés du système RAG
==================================================
Tous les chemins, modèles et seuils sont définis ici.
Modifier ce fichier suffit pour adapter le système à un autre corpus.
"""

from pathlib import Path
import os

# --- Racine du projet -------------------------------------------------------
# BASE_DIR  = stage2_rag/ (répertoire du stage)
# REPO_ROOT = swiss-job-pipeline/ (racine du dépôt)
BASE_DIR  = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

# --- Chemins des données ----------------------------------------------------
# Les données partagées sont centralisées à la racine du dépôt.
DATA_DIR        = REPO_ROOT / "data"
CORPUS_PATH     = DATA_DIR / "labeled" / "labeled_jobs.csv"
VECTORSTORE_DIR = DATA_DIR / "vectorstore"

# --- Stage 1 (classificateur) ----------------------------------------------
STEP1_DIR = REPO_ROOT / "stage1_classifier"

# --- Modèle d'embedding -----------------------------------------------------
# paraphrase-multilingual-MiniLM-L12-v2 :
#   - Supporte 50+ langues dont FR et EN
#   - 12 couches, 384 dimensions — bon compromis vitesse/qualité
#   - ~420MB téléchargé une seule fois, inférence CPU rapide
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Nom de la collection ChromaDB
FAISS_INDEX_PATH  = VECTORSTORE_DIR / "jobs.index"
FAISS_META_PATH   = VECTORSTORE_DIR / "jobs_meta.json"

# --- Catégories pertinentes à indexer --------------------------------------
# NOT_RELEVANT est exclu de l'index RAG (le système ne doit répondre que
# sur les offres IT pertinentes). DBA_INFRA est inclus malgré son faible
# nombre — il reste informatif pour les requêtes sur l'infrastructure.
RELEVANT_CATEGORIES = {"DATA_ENGINEERING", "BI_ANALYTICS", "DBA_INFRA"}

# --- Paramètres de retrieval ------------------------------------------------
TOP_K_RETRIEVAL = 20   # Candidats récupérés avant re-ranking
TOP_K_FINAL     = 5    # Documents retournés après re-ranking

# Seuil minimal de similarité cosine pour considérer un document pertinent.
# En dessous de ce seuil, le système répond "aucune offre pertinente trouvée".
MIN_SIMILARITY  = 0.30

# Poids du re-ranking : score_final = sim * recency_boost * category_match
RECENCY_DECAY_DAYS = 30   # Demi-vie de la fraîcheur en jours

# --- LLM (génération) -------------------------------------------------------
LLM_PROVIDER = "openai"             # "anthropic" ou "openai"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
OPENAI_MODEL    = "gpt-4o-mini"

# Clés API — chargement depuis .env (python-dotenv) ou variables d'environnement.
# Charger le .env ici garantit que les clés sont disponibles même sans load_dotenv()
# dans le script appelant.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(REPO_ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")

# --- Évaluation -------------------------------------------------------------
EVAL_DIR          = BASE_DIR / "eval"
TEST_QUESTIONS_PATH = EVAL_DIR / "test_questions.json"
EVAL_RESULTS_DIR  = EVAL_DIR / "results"
