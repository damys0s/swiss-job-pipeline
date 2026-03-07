# ============================================================
# Dockerfile — swiss-job-pipeline (stage3_agent)
# Cible : pipeline de veille emploi quotidien
# Python 3.11 fixé (résout incompatibilité Python 3.14)
# ============================================================

FROM python:3.11-slim

# --- Utilisateur non-root (sécurité) -------------------------
# -m crée /home/pipeline (requis par HuggingFace cache)
RUN groupadd -r pipeline && useradd -r -g pipeline -m pipeline

# --- Répertoire de travail = racine du repo ------------------
# Reproduit la structure du repo pour que les imports
# shared.* et les chemins Path(__file__) fonctionnent.
WORKDIR /app

# --- Dépendances (layer mis en cache si requirements inchangé)
COPY stage3_agent/requirements.txt ./requirements.txt

# PyTorch CPU-only installé en premier pour éviter que sentence-transformers
# tire automatiquement la variante CUDA (~700 MB inutiles en container CPU)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# --- Pré-téléchargement du modèle d'embedding ----------------
# Bake le modèle dans l'image : pas de DL au runtime, démarrage instantané
# HF_HOME pointe dans /app pour que le chown ci-dessous couvre le cache
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# --- Code source ----------------------------------------------
COPY shared/         ./shared/
COPY stage3_agent/   ./stage3_agent/
COPY data/           ./data/

# --- Répertoires persistants (montés en volume) --------------
# Créés ici pour que chown s'applique avant le passage en non-root
RUN mkdir -p stage3_agent/data stage3_agent/logs/runs \
    && chown -R pipeline:pipeline /app

USER pipeline

# --- Entrypoint ----------------------------------------------
# Override possible via docker-compose (ex: --dry-run)
CMD ["python", "stage3_agent/src/pipeline.py"]
