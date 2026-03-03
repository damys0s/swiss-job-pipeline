# Job RAG Assistant — Recherche augmentée sur offres d'emploi IT

Système RAG (Retrieval-Augmented Generation) pour interroger en langage naturel
un corpus d'offres d'emploi IT en Suisse romande.

**Projet portfolio** — Étape 2/3 d'un pipeline de veille emploi :
1. ~~Fine-tuning~~ ✅ → Classificateur d'offres (`job-classifier-finetuning/`)
2. **RAG** (ce projet) → Assistant de recherche sur les offres classifiées
3. Agent (étape 3) → Pipeline automatisé quotidien

---

## 2.1 Nature et volume des sources

| Paramètre | Valeur |
|-----------|--------|
| **Type** | Offres d'emploi IT (JSON/CSV) |
| **Source** | Adzuna API (Suisse romande) |
| **Volume total collecté** | 351 offres |
| **Volume indexé** | 180 offres pertinentes |
| **Langues** | Français + Anglais (corpus mixte) |
| **Date de collecte** | Mars 2026 |
| **Taille moyenne** | ~200 mots par offre |

**Catégories indexées** (issues du classificateur fine-tuné — étape 1) :
- `DATA_ENGINEERING` : 117 offres
- `BI_ANALYTICS` : 58 offres
- `DBA_INFRA` : 5 offres
- `NOT_RELEVANT` : exclu de l'index (171 offres)

---

## 2.2 Préparation des données

### Filtrage
Les offres `NOT_RELEVANT` sont exclues de l'index. Le label provient du
classificateur fine-tuné GPT-4o-mini de l'étape 1 (`src/classify.py`).

### Granularité du chunking
**1 offre = 1 document** — les offres font en moyenne 200 mots après
pré-traitement, bien en dessous du seuil où le chunking apporterait de la valeur.

### Format de document
Chaque offre est sérialisée en une chaîne structurée :
```
Titre: <title> | Entreprise: <company> | Lieu: <location> | Catégorie: <label> | Description: <desc_400_mots>
```
Les champs structurés permettent à l'embedding de capter les requêtes
du type "data engineer à Genève" même si ces termes n'apparaissent pas
dans la description.

### Représentation vectorielle
- **Modèle** : `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers)
- **Dimension** : 384
- **Normalisation** : L2 (cosine similarity via produit scalaire)
- **Justification** : supporte 50+ langues dont FR et EN ; inférence CPU ;
  meilleur que `all-MiniLM-L6-v2` pour le français ; plus léger que `multilingual-e5-base`

### Vector store
- **FAISS `IndexFlatIP`** — exact search, persisté localement
- **Persistance** : `data/vectorstore/jobs.index` + `jobs_meta.json`
- **Métadonnées stockées** : `id, title, company, location, label, source, url, date_posted`

---

## 2.3 Recherche et restitution

### Pipeline complet
```
Question utilisateur
        ↓
Embedding (paraphrase-multilingual-MiniLM-L12-v2, 384 dims)
        ↓
FAISS IndexFlatIP — top-20 candidats (similarité cosine)
        ↓
Re-ranking : score = sim × recency_boost × category_weight
        ↓
Filtrage : score >= 0.30 → top-5
        ↓
Prompt template (system + 5 offres + question)
        ↓
Claude API (claude-sonnet-4-6, max_tokens=1024)
        ↓
Réponse avec citations [Titre - Entreprise]
```

### Re-ranking
| Facteur | Formule | Effet |
|---------|---------|-------|
| `similarity` | cosine brut FAISS | Pertinence sémantique |
| `recency_boost` | `0.5 + 0.5 × 0.5^(age/30j)` | Favorise les offres récentes |
| `category_weight` | 1.0 (DE/BI), 0.9 (DBA) | Légère priorité aux catégories cibles |

### Filtrage par métadonnées
`JobRetriever.search()` accepte un paramètre `filters` :
```python
retriever.search("data analyst", filters={"label": "BI_ANALYTICS", "location_contains": "Genève"})
```

### Prompt template
```
Tu es un assistant spécialisé dans la recherche d'offres d'emploi IT en Suisse romande.
Réponds à la question de l'utilisateur en te basant UNIQUEMENT sur les offres fournies.
Pour chaque information, cite l'offre source entre crochets [Titre - Entreprise].

--- OFFRES PERTINENTES ---
[1] Titre: ... | Entreprise: ... | ...
...

--- QUESTION ---
{question}
```

---

## 2.4 Évaluation

### Protocole

**Retrieval** (automatique, 25 questions) :
- Hit Rate : ≥ 1 keyword attendu présent dans les top-5 documents
- MRR : rang moyen du premier document pertinent

**Answer quality** (manuelle, 10 questions) :
- Faithfulness 1-5 : fidélité de la réponse aux sources
- Relevance 1-5 : pertinence par rapport à la question
- Citations correctes : True/False

Workflow d'annotation :
```bash
# Génère eval/results/answer_eval_YYYYMMDD_HHMMSS.json
python -m src.evaluate --answer --provider anthropic

# Ouvrir le JSON → remplir faithfulness, relevance, citations_correct
# Puis agréger :
python -m src.evaluate  # (relit le dernier fichier annoté via compute_answer_metrics)
```

### Résultats retrieval (exécuté le 2026-03-03)

| Métrique | Score |
|----------|-------|
| Hit rate (top-5) — global | **68.0%** |
| Hit rate — easy (5 questions) | **100%** |
| Hit rate — medium (10 questions) | **60%** |
| Hit rate — hard (10 questions) | **60%** |
| MRR — global | **0.62** |
| Category hit rate | **86.7%** |
| Faithfulness (moyenne /5) | *voir `eval/results/answer_eval_*.json`* |
| Relevance (moyenne /5) | *voir `eval/results/answer_eval_*.json`* |

**Analyse des misses** : les 8 questions sans hit portent sur des technologies
rares dans le corpus (Kafka, Airflow, Tableau, télétravail) ou sur DBA_INFRA
(5 offres seulement). Voir section 2.5.

---

## 2.5 Retour d'expérience

### Problème 1 — ChromaDB incompatible Python 3.14

**Contexte** : ChromaDB 1.5.2 (vector store prévu initialement) utilise
`pydantic.v1.BaseSettings` qui lève une `ConfigError` au chargement sur Python 3.14.

**Hypothèses testées** :
1. Downgrade ChromaDB 0.6.x → `chroma-hnswlib` sans wheel Python 3.14, échec
2. Venv Python 3.11 → non disponible sur la machine
3. Switch FAISS → **succès**

**Solution** : `faiss.IndexFlatIP` (exact search) + JSON pour les métadonnées.
La persistance via `faiss.write_index()` est aussi simple que ChromaDB.

**Impact mesuré** : aucune dégradation. FAISS est même ~30% plus rapide
au chargement pour 180 vecteurs (pas d'overhead serveur ChromaDB).

**Leçon** : pour un petit corpus (<10k docs), FAISS custom > ChromaDB.
ChromaDB apporte de la valeur pour les cas distribués ou multi-tenants.

---

### Problème 2 — Hit rate de 60% sur questions medium/hard

**Constat** : les questions portant sur Kafka, Airflow, Tableau, télétravail
ou les postes DBA retournent des résultats non pertinents.

**Analyse** : le corpus de 180 offres ne couvre pas uniformément toutes les
technologies. Kafka est mentionné dans < 5 offres, rendant la similarité
cosine insuffisante pour les distinguer.

**Solutions testées** :
- `TOP_K_RETRIEVAL` 20 → 40 : +1 hit (marginal)
- `MIN_SIMILARITY` 0.30 → 0.20 : +2 hits, mais bruit accru

**Vrai fix** : enrichir le corpus (collecte ciblée Kafka, Airflow, DBA).
Un RAG est aussi bon que son corpus — c'est la limite fondamentale.

**Impact** : documenté dans `eval/results/eval_retrieval_*.json`.

---

## Architecture du projet

```
job-rag-assistant/
├── config.py               # Paramètres centralisés
├── requirements.txt
├── data/
│   ├── corpus/             # labeled_jobs.csv (depuis étape 1)
│   └── vectorstore/        # jobs.index + jobs_meta.json (FAISS)
├── notebooks/
│   ├── 01_indexing.ipynb   # Préparation + indexation (2.1, 2.2)
│   ├── 02_retrieval.ipynb  # Recherche sémantique + re-ranking (2.3)
│   ├── 03_rag_pipeline.ipynb # Pipeline RAG complet (2.3 suite)
│   └── 04_evaluation.ipynb # Évaluation qualité (2.4, 2.5)
├── src/
│   ├── indexer.py          # Chargement corpus → embeddings → FAISS
│   ├── retriever.py        # JobRetriever (réutilisable étape 3)
│   ├── rag.py              # JobRAG (réutilisable étape 3)
│   └── evaluate.py         # RagEvaluator
└── eval/
    ├── test_questions.json  # 25 questions de test structurées
    └── results/             # Rapports d'évaluation JSON
```

## Lien avec le pipeline global

Ce projet est l'**étape 2** d'un pipeline de 3 étapes :

| Étape | Projet | Réutilisation |
|-------|--------|---------------|
| 1 — Fine-tuning | `job-classifier-finetuning/` | `JobClassifier` classe les nouvelles offres |
| **2 — RAG** | **ce projet** | `JobRetriever` et `JobRAG` exposés pour l'étape 3 |
| 3 — Agent | `job-agent/` (à venir) | Importe `JobRetriever.add_documents()` et `similarity_score()` |

## Reproduire les résultats

```bash
# 1. Installer les dépendances
pip install -r requirements.txt

# 2. Configurer la clé API Claude
cp .env.example .env
# Éditer .env : ANTHROPIC_API_KEY=sk-ant-...

# 3. Indexer le corpus (télécharge le modèle ~420MB au premier lancement)
python -m src.indexer

# 4. Tester le retriever
python -m src.retriever "data engineer dbt Genève"

# 5. Évaluation retrieval automatique (Hit Rate + MRR)
python -m src.evaluate

# 5b. Générer les réponses pour annotation manuelle (answer quality)
python -m src.evaluate --answer --provider anthropic --n 10
# → ouvrir eval/results/answer_eval_*.json et remplir faithfulness/relevance/citations_correct

# 6. Interface interactive (nécessite la clé API)
python -m src.rag                          # provider par défaut (config.py)
python -m src.rag --provider anthropic     # forcer Claude
python -m src.rag --provider openai        # forcer GPT-4o-mini
python -m src.rag --top-k 3 --verbose     # contexte réduit + affichage du prompt
```

**Notebooks** : ouvrir dans Jupyter (`jupyter notebook`) et exécuter dans l'ordre 01 → 04.
