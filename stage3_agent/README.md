# Job Alert Agent — Pipeline de veille emploi automatisé

> **Étape 3** du projet de veille emploi en Suisse romande.
> Envoie chaque matin un email avec les meilleures offres Data/BI du jour, classifiées et scorées automatiquement.

---

## Fonctionnement

```
Adzuna API ──┐
SerpApi      ├──► Collecte ──► Déduplication ──► Classification ──► Scoring ──► Email
JobUp.ch ────┘    (24h)        (SQLite)          (GPT-4o-mini      (FAISS +     (Gmail
                                                  fine-tuné)        cosine sim)  SMTP)
```

**Pipeline quotidien (automatique via GitHub Actions à 7h CET) :**

1. **Collecte** — Adzuna API, SerpApi Google Jobs et JobUp.ch (scraping HTML, sans clé API)
2. **Déduplication** — filtre les offres déjà vues (base SQLite `seen_jobs.db`)
3. **Classification** — étiquette chaque offre : `DATA_ENGINEERING`, `BI_ANALYTICS` ou `NOT_RELEVANT` via un modèle GPT-4o-mini fine-tuné sur 351 offres suisses
4. **Scoring** — calcule la similarité cosine entre chaque offre et le profil candidat (embeddings `paraphrase-multilingual-MiniLM-L12-v2`)
5. **Email** — envoie un email HTML avec le top 10 des offres, badges colorés par catégorie et barres de score
6. **Historique** — marque les offres vues dans `seen_jobs.db` pour éviter les doublons le lendemain
7. **Monitoring** — en cas d'exception non récupérée, un email `🚨 PIPELINE FAILURE` est envoyé automatiquement avec le traceback complet

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
| `src/collector.py` | Collecte multi-sources (Adzuna, SerpApi, JobUp.ch) |
| `src/deduplicator.py` | Déduplication inter-runs via SQLite |
| `src/scorer.py` | Orchestration classify → filter → score |
| `src/emailer.py` | Génération HTML + envoi SMTP Gmail (alertes emploi + erreurs pipeline) |
| `src/pipeline.py` | Orchestration complète + log JSON + notification d'erreur |
| `src/tracker.py` | Suivi manuel des candidatures (SQLite `tracker.db`) + historique d'état |
| `dashboard.py` | Dashboard Streamlit local (offres pipeline + suivi candidatures) |
| `config/settings.py` | Configuration centralisée (clés API, seuils, chemins) |
| `config/profile.json` | Profil candidat (compétences, localisations préférées) |
| `config/search_queries.json` | Requêtes de recherche paramétrables |

### Sources de données

| Source | Type | Offres/run | Statut |
|--------|------|-----------|--------|
| [Adzuna API](https://developer.adzuna.com) | REST API | ~5-10 | ✅ Actif |
| [SerpApi Google Jobs](https://serpapi.com) | REST API | ~40 | ✅ Actif |
| [JobUp.ch](https://www.jobup.ch) | Scraping HTML (BeautifulSoup) | ~10-20 | ✅ Actif |
| Indeed RSS | RSS | 0 | ❌ Désactivé (Indeed bloque depuis 2024) |

---

## Installation et configuration

### Prérequis

- Python 3.11+ (recommandé) ou 3.14
- Comptes API : Adzuna (gratuit), SerpApi (100 req/mois gratuit), OpenAI
- Compte Gmail avec validation 2 étapes (pour App Password)

### Installation locale

```bash
git clone https://github.com/<username>/swiss-job-pipeline.git
cd swiss-job-pipeline/stage3_agent
pip install -r requirements.txt
```

### Configuration locale (`.env`)

Créer un fichier `.env` à la racine du repo :

```env
ADZUNA_APP_ID=xxxx
ADZUNA_APP_KEY=xxxx
SERPAPI_KEY=xxxx
OPENAI_API_KEY=sk-xxxx
EMAIL_ADDRESS=toncompte@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # App Password Gmail (16 chars)
EMAIL_TO=destinataire@gmail.com

# Optionnel : backup cloud du tracker de candidatures
BACKUP_CLOUD_PATH=C:/Users/Toi/OneDrive/Documents/job_backups
```

### Configuration GitHub Actions (production)

Dans **Settings → Secrets and variables → Actions**, créer les 7 secrets ci-dessus (sans `BACKUP_CLOUD_PATH` qui est local uniquement).

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
# Tester chaque source séparément
python scripts/test_collect.py --source adzuna
python scripts/test_collect.py --source serpapi
python scripts/test_collect.py --source jobup     # Test scraping JobUp.ch
python scripts/test_collect.py --source all --save

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

En cas d'échec, un email `🚨 [Job Alert] PIPELINE FAILURE` est envoyé automatiquement avec l'erreur et le traceback complet.

### Dashboard local

```bash
cd stage3_agent
python -m streamlit run dashboard.py
```

**Onglet "Offres pipeline"** — offres reçues par email, marquage candidaté en un clic.

**Onglet "Mes candidatures"** — suivi manuel complet :
- Ajout avec champ **Catégorie** (DATA / BI / SUPPORT / LOGISTIQUE / AI)
- **Édition inline complète** — entreprise, poste, lieu, URL, catégorie, état, commentaire modifiables directement dans la table
- **Historique d'état** — chaque changement est horodaté (`application_history`) ; consultable en bas de l'onglet
- **Filtres** : état, catégorie, entreprise, recherche texte, **plage de dates** (Du / Au)
- Suppression avec confirmation
- **Backup cloud** — copie quotidienne vers OneDrive/Dropbox si `BACKUP_CLOUD_PATH` défini

**Onglet "Statistiques"** — graphiques par état, par catégorie, activité hebdomadaire, top entreprises.

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

**Activer/désactiver les sources** → `config/settings.py` :
```python
USE_ADZUNA     = True
USE_SERPAPI    = True
USE_JOBUP      = True   # Scraping JobUp.ch (pas de clé API requise)
USE_INDEED_RSS = False  # Indeed bloque depuis 2024
TOP_N_RESULTS  = 10     # Nombre d'offres dans l'email
MIN_SCORE      = 0.30   # Seuil minimal de score cosine
MAX_DAYS_OLD   = 1      # Fenêtre de collecte (jours)
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

### Logs d'exécution

Les logs JSON sont générés dans `logs/runs/YYYY-MM-DD.json` à chaque run :
```json
{
  "date": "2026-03-03",
  "steps": {
    "collect":   {"adzuna": {"kept": 8}, "serpapi": {"kept": 28}, "jobup": {"kept": 12}, "total_dedup": 39},
    "dedup":     {"n_new": 39, "n_in_history": 0},
    "score":     {"n_top": 10},
    "email":     {"sent": true}
  },
  "success": true,
  "duration_seconds": 70.0
}
```

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Classification | GPT-4o-mini fine-tuné (OpenAI) |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers) |
| Vector search | FAISS IndexFlatIP |
| Collecte | Adzuna API + SerpApi + JobUp.ch (BeautifulSoup) |
| Déduplication | SQLite (stdlib) |
| Email | SMTP Gmail + App Password |
| Dashboard | Streamlit |
| Automatisation | GitHub Actions (cron quotidien) |
| Python | 3.11 (CI) / 3.14 (dev local) |
