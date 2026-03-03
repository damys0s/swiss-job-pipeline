# Job Classifier — Fine-tuning GPT-4o-mini

Classificateur d'offres d'emploi IT en Suisse romande, basé sur un fine-tuning supervisé de GPT-4o-mini.

> Projet personnel de montée en compétence sur le fine-tuning de LLMs, intégré dans un pipeline de veille emploi automatisé.

---

## Résultats

| Métrique   | Zero-shot | Fine-tuné  | Delta   |
|------------|-----------|------------|---------|
| Accuracy   | 87.5%     | **100%**   | +12.5%  |
| F1 macro   | 85.3%     | **100%**   | +14.7%  |

Évalué sur 72 exemples de validation (jamais vus à l'entraînement, split stratifié 80/20).
Les 9 erreurs du modèle zero-shot sont intégralement corrigées par le fine-tuning.

---

## Objectif

Trier automatiquement des offres d'emploi IT en Suisse romande selon leur pertinence pour un profil Data Engineer / BI Developer.

**3 catégories :**
- `DATA_ENGINEERING` — pipelines, ETL, dbt, Spark, Airflow
- `BI_ANALYTICS` — reporting, dashboards, Power BI, Tableau
- `NOT_RELEVANT` — offres hors périmètre

Ce classificateur est réutilisé dans deux projets liés :
- **RAG** — assistant de recherche d'emploi sur les offres classifiées
- **Agent** — pipeline automatisé de veille emploi quotidienne

**Pourquoi le fine-tuning plutôt qu'une approche zero-shot ?**

Le modèle de base (GPT-4o-mini sans fine-tuning) atteint 87.5% d'accuracy. Les erreurs portent sur des offres ambiguës (ex : "Data Scientist Pre-Sales", "Online Data Analyst"). Le fine-tuning permet au modèle d'apprendre les frontières spécifiques à ce domaine et à ce profil, sans écrire de règles manuelles fragiles.

---

## Pipeline

```
Phase 1 — Collecte      python -m src.collect
Phase 2 — Étiquetage    python -m src.label
Phase 3 — Préparation   python -m src.prepare
Phase 4 — Fine-tuning   python -m src.finetuning run
Phase 5 — Évaluation    python -m src.evaluation
```

---

## Données

| Paramètre        | Valeur                                           |
|------------------|--------------------------------------------------|
| Sources          | Adzuna API, SerpApi (Google Jobs), Indeed RSS    |
| Volume brut      | ~800 offres collectées                           |
| Volume étiqueté  | 351 offres (279 train / 72 validation)           |
| Format           | JSONL (chat format OpenAI)                       |
| Split            | 80/20 stratifié par classe                       |

**Distribution :**

| Classe            | Train | Val | %   |
|-------------------|-------|-----|-----|
| DATA_ENGINEERING  | 93    | 24  | 33% |
| BI_ANALYTICS      | 46    | 12  | 17% |
| NOT_RELEVANT      | 140   | 36  | 50% |

**Préparation :**
- Collecte multi-source avec déduplication par (titre, entreprise) normalisés
- Pré-étiquetage automatique par règles (keywords dans le titre) + validation manuelle
- Remapping 5 classes → 3 classes (DBA_INFRA et APP_SUPPORT fusionnés dans NOT_RELEVANT)
- Troncature des descriptions à 200 mots
- Prompt système fixe inclus dans chaque exemple d'entraînement

---

## Entraînement

| Paramètre         | Valeur                      |
|-------------------|-----------------------------|
| Modèle de base    | `gpt-4o-mini-2024-07-18`    |
| Méthode           | Supervised fine-tuning      |
| Epochs            | 3                           |
| Batch size        | auto                        |
| Learning rate     | auto                        |
| Tokens entraînés  | 163 578                     |
| Coût              | $0.49                       |
| Durée             | ~10 minutes                 |
| Ressources        | API OpenAI (sans GPU local) |

Les logs d'entraînement (loss par step, événements) sont dans `results/training_logs/`.
Les matrices de confusion sont dans `results/evaluation/confusion_matrices.png`.

---

## Utilisation

### Module Python

```python
from src.classify import JobClassifier

clf = JobClassifier()

# Classification unitaire
label = clf.classify(
    title="Data Engineer",
    company="UBS",
    location="Genève",
    description="Build ETL pipelines with Spark and dbt"
)
# → "DATA_ENGINEERING"

# Classification batch
results = clf.classify_batch(jobs)
relevant = [r for r in results if r["is_relevant"]]

# Filtre rapide
if clf.is_relevant(job):
    pass  # indexer dans le RAG, envoyer une notification, etc.
```

### CLI

```bash
python -m src.classify "Data Engineer" "UBS" "Genève" "Build ETL pipelines"
```

---

## Reproduction

```bash
# 1. Clone
git clone https://github.com/damys0s/job-classifier-finetuning.git
cd job-classifier-finetuning

# 2. Environnement
python -m venv venv-finetuning
# Windows :
venv-finetuning\Scripts\activate
# Linux/Mac :
source venv-finetuning/bin/activate

pip install -r requirements.txt

# 3. Clés API
cp .env.example .env   # puis renseigner OPENAI_API_KEY, ADZUNA_*, SERPAPI_KEY
python verify_apis.py  # vérification des accès

# 4. Fine-tuning (depuis le corpus étiqueté inclus dans le repo)
python -m src.finetuning validate
python -m src.finetuning upload
python -m src.finetuning start
python -m src.finetuning status   # polling jusqu'à succeeded (~10 min)
python -m src.finetuning results

# ou en une seule commande :
python -m src.finetuning run

# 5. Évaluation comparative
python -m src.evaluation
```

Les données étiquetées (`data/labeled/`) et les fichiers JSONL (`data/training/`) sont inclus dans le repo — les étapes 4 et 5 peuvent être relancées directement sans recollecte ni ré-étiquetage.

---

## Erreurs corrigées par le fine-tuning

| Offre                                        | Vrai label        | Zero-shot         | Fine-tuné         |
|----------------------------------------------|-------------------|-------------------|-------------------|
| Online Data Analyst - German (CH)            | BI_ANALYTICS      | NOT_RELEVANT      | BI_ANALYTICS      |
| Senior Azure Platform Engineer AI & Data     | DATA_ENGINEERING  | NOT_RELEVANT      | DATA_ENGINEERING  |
| Big Data & Platform Engineer (AMAG)          | NOT_RELEVANT      | DATA_ENGINEERING  | NOT_RELEVANT      |

---

## Structure du repo

```
job-classifier-finetuning/
├── verify_apis.py               # Phase 0 — vérification des clés API
├── requirements.txt
├── .env.example                 # Template de configuration
├── src/
│   ├── utils.py                 # Helpers partagés (make_job_id, normalize_text, retry_request)
│   ├── collect.py               # Phase 1a — collecte Adzuna + SerpApi
│   ├── collect_dba.py           # Phase 1b — collecte ciblée DBA/infra
│   ├── collect_serpapi.py       # Phase 1c — collecte SerpApi (localisations)
│   ├── label.py                 # Phase 2 — étiquetage interactif
│   ├── prepare.py               # Phase 3 — génération JSONL + split stratifié
│   ├── classify.py              # Module réutilisable (JobClassifier)
│   ├── finetuning.py            # Phase 4 — orchestration du fine-tuning OpenAI
│   └── evaluation.py            # Phase 5 — évaluation comparative zero-shot vs fine-tuné
├── data/
│   ├── labeled/labeled_jobs.csv # Corpus étiqueté à la main (351 offres)
│   └── training/                # train.jsonl (279 ex.) + val.jsonl (72 ex.)
└── results/
    ├── training_logs/           # État du job, métriques de loss, résumé
    └── evaluation/              # Matrices de confusion, métriques comparatives
```
