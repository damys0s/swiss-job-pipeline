# Swiss Job Pipeline

Système de veille emploi automatisé pour le marché IT en Suisse romande.

Pipeline en 3 étapes : fine-tuning d'un classificateur → RAG sémantique → agent de notification quotidienne.

---

## Architecture

```
swiss-job-pipeline/
│
├── stage1_classifier/     Fine-tuning GPT-4o-mini pour classifier les offres
├── stage2_rag/            Système RAG (FAISS + Claude) pour requêtes en langage naturel
├── stage3_agent/          Agent de veille : collecte → classifie → score → email
│
├── shared/                Modules partagés entre les stages
│   ├── classifier.py      JobClassifier (réutilisé par stages 2 et 3)
│   └── retriever.py       JobRetriever (réutilisé par stage 3)
│
└── data/                  Données centralisées
    ├── labeled/           Corpus annoté manuellement (351 offres)
    ├── training/          Données d'entraînement JSONL (train/val split)
    └── vectorstore/       Index FAISS pré-construit (180 offres, 384 dims)
```

### Flux de données

```
┌─────────────────────────┐
│   stage1_classifier/    │  Collecte → Annotation manuelle → Fine-tuning GPT-4o-mini
│                         │  → Modèle : ft:gpt-4o-mini-...:job-classifier:DF1fQqSZ
│  data/labeled/          │  → Corpus : 351 offres annotées (3 classes)
└───────────┬─────────────┘
            │ corpus + classificateur
            ▼
┌─────────────────────────┐
│   stage2_rag/           │  Embedding FAISS + pipeline RAG Claude
│                         │  → Index : 180 offres (NOT_RELEVANT exclues)
│  data/vectorstore/      │  → Hit Rate 68%, MRR 0.62
└───────────┬─────────────┘
            │ vectorstore + retriever
            ▼
┌─────────────────────────┐
│   stage3_agent/         │  Pipeline quotidien (GitHub Actions, 6h UTC)
│                         │  Collecte → Déduplique → Classifie → Score → Email top-10
│  ⚙️ GitHub Actions cron │
└─────────────────────────┘
```

---

## Stage 1 — Classificateur GPT-4o-mini fine-tuné

**Objectif :** Classer automatiquement les offres d'emploi en 3 catégories.

| Catégorie | Description | Nombre |
|-----------|-------------|--------|
| `DATA_ENGINEERING` | Pipelines, ETL, dbt, Spark, Airflow | 117 |
| `BI_ANALYTICS` | Power BI, Tableau, SQL analytics | 58 |
| `NOT_RELEVANT` | Développement, DevOps, management... | 171 |

**Résultat :** Précision 100% sur le set de validation (vs 87.5% zero-shot).

```bash
cd stage1_classifier
pip install -r requirements.txt
python verify_apis.py               # Vérifier les clés API
python -m src.collect               # Collecter ~800 offres
python -m src.label                 # Annoter manuellement
python -m src.prepare               # Générer train.jsonl / val.jsonl
python -m src.finetuning run        # Lancer le fine-tuning (~10 min)
python -m src.evaluation            # Évaluer zero-shot vs fine-tuné
```

Le fine-tuning est **optionnel** — le modèle déployé (`ft:gpt-4o-mini-2024-07-18:personal:job-classifier:DF1fQqSZ`) est déjà disponible dans `stage1_classifier/results/training_logs/finetune_state.json`.

---

## Stage 2 — RAG FAISS + Claude

**Objectif :** Répondre en langage naturel à des questions sur les offres indexées.

**Stack :** `sentence-transformers` + FAISS IndexFlatIP + Claude claude-sonnet-4-6

```bash
cd stage2_rag
pip install -r requirements.txt
# Lancer les notebooks dans l'ordre :
jupyter notebook notebooks/01_indexing.ipynb      # Construire l'index FAISS
jupyter notebook notebooks/02_retrieval.ipynb     # Tester la recherche sémantique
jupyter notebook notebooks/03_rag_pipeline.ipynb  # Pipeline RAG complet
jupyter notebook notebooks/04_evaluation.ipynb    # Évaluer Hit Rate + MRR
```

L'index est **pré-construit** dans `data/vectorstore/` — pas besoin de relancer `01_indexing.ipynb` pour utiliser le RAG.

**Décisions techniques :**
- ChromaDB écarté (incompatible Python 3.14 — bug `pydantic.v1`)
- RAGAS écarté (`scikit-network` sans wheel Python 3.14)
- FAISS retenu : exact search, ~271 KB, 30ms d'inférence sur CPU

---

## Stage 3 — Agent de veille quotidienne

**Objectif :** Recevoir chaque matin un email avec les 10 meilleures offres du jour.

**Pipeline :**
1. **Collecte** — Adzuna API + SerpApi Google Jobs (RSS Indeed désactivé depuis 2024)
2. **Déduplication** — SQLite (`stage3_agent/data/seen_jobs.db`) — évite les doublons inter-runs
3. **Classification** — GPT-4o-mini fine-tuné — filtre `NOT_RELEVANT`
4. **Scoring** — Similarité cosine offre × profil candidat (`stage3_agent/config/profile.json`)
5. **Email** — HTML formaté avec badges couleur, barres de score, liens directs

```bash
cd stage3_agent
pip install -r requirements.txt
cp ../.env.example ../.env && nano ../.env    # Remplir les clés API
python src/pipeline.py --dry-run              # Test sans envoi d'email
python src/pipeline.py                        # Run complet
```

**GitHub Actions (cron) :**

Le pipeline tourne automatiquement chaque jour à 06h00 UTC via `.github/workflows/daily_alert.yml`. Pour l'activer sur votre fork :

1. `Settings → Secrets and variables → Actions`
2. Ajouter les 7 secrets : `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `SERPAPI_KEY`, `OPENAI_API_KEY`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `EMAIL_TO`
3. `Actions → Daily Job Alert → Enable workflow`

**Résultats dernière exécution (2026-03-03) :**

| Métrique | Valeur |
|----------|--------|
| Offres collectées | 42 |
| Nouvelles (après dédup) | 39 |
| Pertinentes (après classification) | 31 |
| Envoyées dans l'email | 10 |
| Durée totale | 70s |

---

## Installation rapide

### Via Docker (recommandé)

```bash
git clone https://github.com/VOTRE_USERNAME/swiss-job-pipeline.git
cd swiss-job-pipeline
cp .env.example .env
# Éditer .env avec vos clés API

docker compose build              # Premier build (~10-15 min, modèle d'embedding inclus)
docker compose up dry-run         # Test sans envoi d'email
docker compose up agent           # Run complet
```

> **Note :** Le build est long la première fois (PyTorch CPU + modèle `paraphrase-multilingual-MiniLM-L12-v2` bake dans l'image). Les runs suivants démarrent en quelques secondes.

### Via Python directement

```bash
git clone https://github.com/VOTRE_USERNAME/swiss-job-pipeline.git
cd swiss-job-pipeline
cp .env.example .env
# Éditer .env avec vos clés API

pip install -r stage3_agent/requirements.txt
python stage3_agent/src/pipeline.py --dry-run
```

### Prérequis

- Docker Desktop (recommandé) **ou** Python 3.11+
- Clé API OpenAI (classificateur GPT-4o-mini fine-tuné)
- Compte Gmail avec App Password (stage 3 uniquement)
- Clés Adzuna et SerpApi (optionnel — pour recollecte)

> **Note Python 3.14 :** ChromaDB et RAGAS sont incompatibles avec Python 3.14. Ce projet utilise FAISS (pur C++) qui fonctionne sur toutes les versions.

---

## Personnalisation

### Adapter le profil candidat

Modifier `stage3_agent/config/profile.json` :

```json
{
  "title": "Data Engineer / BI Developer",
  "skills": ["SQL", "Python", "dbt", "Power BI", "Azure"],
  "experience_years": 5,
  "languages": ["French", "English"],
  "locations_preferred": ["Lausanne", "Geneva", "Nyon"]
}
```

### Modifier les recherches

Éditer `stage3_agent/config/search_queries.json` pour ajouter des mots-clés ou zones géographiques.

---

## Structure des modules partagés

```python
# stage3_agent/src/scorer.py
from shared.classifier import JobClassifier   # GPT-4o-mini fine-tuné
from shared.retriever import JobRetriever     # FAISS similarity search
```

`shared/` contient les modules réutilisés par plusieurs stages sans duplication de code.

---

## Dashboard de suivi

Un dashboard Streamlit local permet de suivre les candidatures sans quitter le navigateur.

```bash
cd stage3_agent
python -m streamlit run dashboard.py
```

**Onglet "Offres pipeline"** — offres reçues par email, marquage candidaté en un clic (checkbox).

**Onglet "Mes candidatures"** — suivi manuel complet :
- Ajout d'une candidature (formulaire intégré)
- Édition de l'état directement dans la table (menu déroulant)
- Suppression avec confirmation (mode suppression activable)
- Filtres par état et recherche texte libre
- Stats par état en temps réel

Toutes les données sont stockées dans `stage3_agent/data/seen_jobs.db` (SQLite).

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Classificateur | GPT-4o-mini (fine-tuné OpenAI) |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, FR+EN) |
| Vector store | FAISS IndexFlatIP (exact search) |
| LLM RAG | Claude claude-sonnet-4-6 (Anthropic) |
| Collecte | Adzuna API + SerpApi Google Jobs |
| Déduplication / Suivi | SQLite |
| Email | SMTP Gmail + HTML templating |
| Dashboard | Streamlit |
| CI/CD | GitHub Actions (cron quotidien) |
| Conteneurisation | Docker (python:3.11-slim, user non-root, modèle bake) |
| Python | 3.11 (Docker/CI) / 3.14 (local) |
