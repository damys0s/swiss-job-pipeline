# Job Alert Agent — Pipeline de veille emploi automatisé

> **Étape 3** du projet de veille emploi en Suisse romande.
> Envoie chaque matin un email avec les meilleures offres Data/BI du jour, classifiées et scorées automatiquement.

---

## Fonctionnement

```
Adzuna API ──┐
SerpApi      ├──► Collecte ──► Déduplication ──► Classification ──► Scoring ──► Email
Indeed RSS ──┘    (24h)        (SQLite)          (GPT-4o-mini      (FAISS +     (Gmail
                                                  fine-tuné)        cosine sim)  SMTP)
```

**Pipeline quotidien (automatique via GitHub Actions à 7h CET) :**

1. **Collecte** — interroge Adzuna API et SerpApi Google Jobs avec 8 requêtes configurables
2. **Déduplication** — filtre les offres déjà vues (base SQLite `seen_jobs.db`)
3. **Classification** — étiquette chaque offre : `DATA_ENGINEERING`, `BI_ANALYTICS` ou `NOT_RELEVANT` via un modèle GPT-4o-mini fine-tuné sur 351 offres suisses
4. **Scoring** — calcule la similarité cosine entre chaque offre et le profil candidat (embeddings `paraphrase-multilingual-MiniLM-L12-v2`)
5. **Email** — envoie un email HTML avec le top 10 des offres, badges colorés par catégorie et barres de score
6. **Historique** — marque les offres vues dans `seen_jobs.db` pour éviter les doublons le lendemain

---

## Architecture

### Composants réutilisés des étapes précédentes

| Module | Origine | Rôle dans ce projet |
|--------|---------|---------------------|
| `src/classify.py` | Étape 1 — Fine-tuning | Classifier GPT-4o-mini fine-tuné |
| `src/retriever.py` | Étape 2 — RAG | Scoring cosine FAISS |
| `data/vectorstore/` | Étape 2 — RAG | Index FAISS 180 offres (référence profil) |

### Modules propres à ce projet

| Fichier | Rôle |
|---------|------|
| `src/collector.py` | Collecte multi-sources (Adzuna, SerpApi, Indeed RSS) |
| `src/deduplicator.py` | Déduplication inter-runs via SQLite |
| `src/scorer.py` | Orchestration classify → filter → score |
| `src/emailer.py` | Génération HTML + envoi SMTP Gmail |
| `src/pipeline.py` | Orchestration complète + log JSON |
| `config/settings.py` | Configuration centralisée (clés API, seuils, chemins) |
| `config/profile.json` | Profil candidat (compétences, localisations préférées) |
| `config/search_queries.json` | Requêtes de recherche paramétrables |

### Sources de données

| Source | Type | Offres/run | Statut |
|--------|------|-----------|--------|
| [Adzuna API](https://developer.adzuna.com) | REST API | ~5-10 | ✅ Actif |
| [SerpApi Google Jobs](https://serpapi.com) | REST API | ~40 | ✅ Actif |
| Indeed RSS | RSS | 0 | ❌ Désactivé (Indeed bloque depuis 2024) |

---

## Installation et configuration

### Prérequis

- Python 3.11+ (recommandé) ou 3.14
- Comptes API : Adzuna (gratuit), SerpApi (100 req/mois gratuit), OpenAI
- Compte Gmail avec validation 2 étapes (pour App Password)

### Installation locale

```bash
git clone https://github.com/<username>/job-alert-agent.git
cd job-alert-agent
pip install -r requirements.txt
```

### Configuration locale (`.env`)

```bash
cp .env.example .env  # ou créer .env manuellement
```

Contenu du `.env` :

```env
ADZUNA_APP_ID=xxxx
ADZUNA_APP_KEY=xxxx
SERPAPI_KEY=xxxx
OPENAI_API_KEY=sk-xxxx
EMAIL_ADDRESS=toncompte@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # App Password Gmail (16 chars)
EMAIL_TO=destinataire@gmail.com
```

### Configuration GitHub Actions (production)

Dans **Settings → Secrets and variables → Actions**, créer les 7 secrets ci-dessus.

---

## Usage

### Exécution manuelle locale

```bash
# Run complet (collecte + email)
python src/pipeline.py

# Dry-run (pas d'email, pas de mise à jour DB)
python src/pipeline.py --dry-run
```

### Scripts de test unitaires

```bash
# Tester la collecte seule (affiche les offres brutes)
python scripts/test_collect.py

# Tester le scoring seul
python scripts/test_score.py

# Aperçu de l'email HTML (génère data/email_preview.html)
python scripts/test_email.py
```

### Tests automatisés

```bash
pytest tests/ -v
# → 11 tests, ~5s, sans appel API réel (mocks)
```

### Exécution automatique (GitHub Actions)

Le workflow `.github/workflows/daily_alert.yml` s'exécute :
- **Automatiquement** : tous les jours à 6h UTC (7h CET / 8h CEST)
- **Manuellement** : Actions → Daily Job Alert → Run workflow

### Personnalisation

**Modifier les requêtes de recherche** → `config/search_queries.json` :
```json
{"queries": [
  {"keywords": "data engineer", "location": "Lausanne"},
  {"keywords": "BI developer",  "location": "Geneva"}
]}
```

**Modifier le profil candidat** → `config/profile.json` :
```json
{
  "title": "Data Engineer / BI Developer",
  "skills": ["SQL", "Python", "dbt", "Power BI"],
  "locations_preferred": ["Lausanne", "Geneva", "Nyon"]
}
```

**Ajuster les seuils** → `config/settings.py` :
```python
TOP_N_RESULTS = 10    # Nombre d'offres dans l'email
MIN_SCORE     = 0.30  # Seuil minimal de score cosine
MAX_DAYS_OLD  = 1     # Fenêtre de collecte (jours)
```

---

## Résultats

### Run du 2026-03-03

| Métrique | Valeur |
|----------|--------|
| Offres collectées | 42 brutes → 39 après dédup interne |
| Nouvelles offres (vs historique) | 39 |
| Offres pertinentes (après classification) | 31 |
| Top offres envoyées | 10 |
| Durée totale | 70s |
| Email envoyé | ✅ |

### Email reçu

> *[Ajouter screenshot ici après le premier vrai run automatique]*

### Logs d'exécution

Les logs JSON sont générés dans `logs/runs/YYYY-MM-DD.json` à chaque run :
```json
{
  "date": "2026-03-03",
  "steps": {
    "collect":   {"total_dedup": 39, "n_relevant": 31},
    "dedup":     {"n_new": 39, "n_in_history": 0},
    "score":     {"n_top": 10},
    "email":     {"sent": true}
  },
  "success": true,
  "duration_seconds": 70.0
}
```

---

## Lien avec le pipeline global

Ce projet est l'**étape 3** d'un pipeline de veille emploi en 3 étapes :

```
scraper_offres_suisse/
├── job-classifier-finetuning/   ← Étape 1 : Fine-tuning GPT-4o-mini
│   └── Corpus 351 offres → modèle ft:gpt-4o-mini (87% accuracy)
│
├── job-rag-assistant/           ← Étape 2 : Système RAG
│   └── FAISS + Claude → questions/réponses sur les offres
│       Hit rate 68%, MRR 0.62
│
└── job-alert-agent/             ← Étape 3 : Agent de veille (ce projet)
    └── Réutilise le classificateur (étape 1) + les embeddings FAISS (étape 2)
        pour envoyer automatiquement les meilleures offres chaque matin
```

**Flux de réutilisation :**
- Le **modèle fine-tuné** (étape 1) est appelé via `src/classify.py` pour filtrer les offres non pertinentes
- L'**index FAISS** (étape 2) sert de référence pour scorer les offres par similarité avec le profil candidat

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Classification | GPT-4o-mini fine-tuné (OpenAI) |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers) |
| Vector search | FAISS IndexFlatIP |
| Déduplication | SQLite (stdlib) |
| Email | SMTP Gmail + App Password |
| Automatisation | GitHub Actions (cron quotidien) |
| Python | 3.11 (CI) / 3.14 (dev local) |
