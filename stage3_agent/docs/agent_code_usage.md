# Documentation — Usage des agents de code (Question 4)

> **4) Agents de code** *Requis*
> Estimez la quantité de code produite par agent de code (codex, claude code, cursor, opencode, ...). Décrivez brièvement votre utilisation.

---

## 1. Outil utilisé

**Claude Code** — CLI officiel d'Anthropic, modèle `claude-sonnet-4-6`

Utilisé en mode interactif dans VS Code via l'extension Claude Code (pas l'interface web claude.ai — le CLI permet de lire/éditer les fichiers directement dans le repo, ce qui évite les copier-coller et donne à Claude le contexte complet du projet).

Toutes les phases de ce projet (étapes 1, 2 et 3) ont été construites avec Claude Code comme agent principal. Les sessions sont conversationnelles : je décris l'objectif, Claude génère le code et l'écrit dans les fichiers, je teste, on itère.

---

## 2. Estimation du code produit par l'agent

| Projet | Lignes totales | % écrit par Claude | % écrit par moi | % modifié après génération |
|--------|---------------|--------------------|-----------------|---------------------------|
| Étape 1 — Fine-tuning | ~2630 lignes | ~80% | ~5% | ~15% |
| Étape 2 — RAG | ~1290 lignes | ~90% | ~2% | ~8% |
| Étape 3 — Agent | ~2100 lignes | ~88% | ~3% | ~9% |
| **Total** | **~6020 lignes** | **~85%** | **~4%** | **~11%** |

*Méthode de comptage : `wc -l` sur tous les fichiers `.py` + `.yml` de chaque projet (hors `__pycache__`, données, notebooks).*

*Les % "modifié après génération" incluent : ajustement des noms de champs API réels, chemins d'import cross-projet, SYSTEM_PROMPT identique au training set.*

---

## 3. Workflow d'utilisation

### Structure des prompts

Mes prompts suivaient systématiquement ce format :
1. **Contexte** — rappel du projet, de ce qui est déjà fait, de la stack
2. **Objectif** — ce que le module doit faire (interfaces, contrats)
3. **Contraintes** — pas de X, réutiliser Y, compatible Python 3.14
4. **Format attendu** — code complet, commentaires en français, tests

Exemple de prompt pour `src/collector.py` :
> *"Crée src/collector.py qui collecte les offres emploi des 24 dernières heures depuis Adzuna API, SerpApi Google Jobs et Indeed RSS. Normalise au format {id, title, company, location, description, url, date_posted, source}. Les requêtes sont dans config/search_queries.json. Gestion d'erreurs : si une API est down, skip et log. Retourne (jobs, stats) avec stats = compteurs par source."*

### Itération typique

1. Premier jet Claude → code ~80% correct
2. Ajustements : chemins d'import, logique métier spécifique (format exact des API responses)
3. Test manuel → debug en pair avec Claude (copier-coller de l'erreur, correction ciblée)
4. Code final : ~90% Claude, 10% modifications manuelles

---

## 4. Ce que l'agent fait bien

- **Scaffolding** : créer une structure de projet complète en un prompt
- **Boilerplate API** : appels HTTP avec retry, timeout, gestion d'erreurs
- **Formats standards** : parsing RSS (feedparser), SMTP (smtplib), SQLite
- **Documentation inline** : docstrings, commentaires sur les décisions de design
- **Cohérence inter-modules** : respecter les interfaces définies dans d'autres fichiers

---

## 5. Ce que l'agent fait mal / où j'interviens

- **Logique métier spécifique** : les catégories exactes du fine-tune, le format du SYSTEM_PROMPT qui doit être identique au training
- **Chemins de fichiers cross-projet** : les imports entre job-classifier-finetuning/ et job-alert-agent/ nécessitent des ajustements manuels
- **Paramètres API précis** : les noms exacts des champs retournés par Adzuna ou SerpApi (nécessitent de tester contre l'API réelle)
- **Décisions d'architecture** : choisir entre copier les modules vs. sys.path vs. package installable — j'ai décidé moi-même de copier

---

## 6. Réflexion honnête

### Productivité

Sans agent de code, j'estime que ce projet aurait pris 3-4x plus longtemps :
- Scaffolding de 8 fichiers : ~2h manuellement → ~15min avec Claude
- Boilerplate SMTP, SQLite, feedparser : connu mais laborieux → généré en secondes

### Limites rencontrées

- Contexte : Claude ne connaît pas l'état exact des APIs (noms de champs réels) → nécessite des tests
- Hallucinations : parfois génère des paramètres d'API qui n'existent pas → vérification manuelle obligatoire

### Validation du code généré

1. Lecture ligne par ligne pour comprendre la logique
2. Test avec des données réelles (appels API manuels)
3. `--dry-run` pour tester le pipeline sans effet de bord

---

## 7. Tracking par phase

### Phase 0 — Setup (structure du projet)
- Prompt résumé : "Crée la structure job-alert-agent/ avec tous les fichiers de config, settings.py, requirements.txt, workflow GitHub Actions, et les modules src/ squelettes"
- Lignes générées par Claude : ~600
- Lignes modifiées par moi : ~0
- Lignes écrites par moi from scratch : ~0
- Note : Phase entièrement delegée à Claude Code, résultat conforme au plan

### Phase 1 — Collecte
- Prompt résumé : "Crée src/collector.py qui collecte les offres des 24h depuis Adzuna API, SerpApi Google Jobs et Indeed RSS. Format standardisé {id, title, company, location, description, url, date_posted, source}. Si une API est down, skip et log. Retourne (jobs, stats)."
- Lignes générées par Claude : ~280 (collector.py : 306 lignes)
- Lignes modifiées par moi : ~25 (noms de champs Adzuna/SerpApi exacts après test API réel)
- Lignes écrites par moi from scratch : ~0
- Note : Noms de champs API nécessitaient ajustements après test réel (`klass` → `category` pour Adzuna, format date SerpApi différent de la doc)

### Phase 2 — Classification et scoring
- Prompt résumé : "Crée src/scorer.py qui (1) classifie chaque offre via JobClassifier, (2) exclut NOT_RELEVANT, (3) score par cosine similarity vs profil candidat via JobRetriever. Adapte src/retriever.py depuis job-rag-assistant/ pour ne garder que similarity_score()."
- Lignes générées par Claude : ~430 (scorer.py : 118, retriever.py : 135, classify.py : 214 — copie adaptée depuis étape 1)
- Lignes modifiées par moi : ~30 (import paths, SYSTEM_PROMPT identique au training)
- Lignes écrites par moi from scratch : ~0
- Note : SYSTEM_PROMPT critique — doit être identique à prepare.py de l'étape 1. Claude a tendance à reformuler → correction manuelle obligatoire.

### Phase 3 — Email
- Prompt résumé : "Crée src/emailer.py qui génère un email HTML avec les top offres (badge catégorie coloré, barre de score %, extrait description) et l'envoie via SMTP Gmail avec App Password."
- Lignes générées par Claude : ~180 (emailer.py : 187 lignes)
- Lignes modifiées par moi : ~5 (ajustement du format de date FR)
- Lignes écrites par moi from scratch : ~0
- Note : HTML email entièrement généré par Claude, visuellement propre au premier jet. Test via scripts/test_email.py avant intégration.

### Phase 4 — Pipeline + tests
- Prompt résumé : "Crée src/pipeline.py qui orchestre collect → dedup → score → email → update_db avec log JSON par run. Crée src/deduplicator.py avec SQLite pour déduplication inter-runs. Crée tests/test_pipeline.py avec tests unitaires et end-to-end."
- Lignes générées par Claude : ~950 (pipeline.py : 196, deduplicator.py : 96, tests/ : 190, scripts/ : 526)
- Lignes modifiées par moi : ~30 (logique dry_run, export stats pour email)
- Lignes écrites par moi from scratch : ~0
- Note : Pipeline orchestré entièrement par Claude. Déduplicateur SQLite propre dès le premier jet.

### Phase 5 — GitHub Actions
- Prompt résumé : "Crée daily_alert.yml avec cron 6h UTC, workflow_dispatch, secrets, auto-commit seen_jobs.db. Guide-moi sur vector store (committé vs. rebuild) et SQLite."
- Lignes générées par Claude : ~55 (workflow YAML)
- Lignes modifiées par moi : ~0
- Lignes écrites par moi from scratch : ~0
- Note : Workflow existait déjà depuis Phase 0. Correction mineure : suppression de `logs/runs/` du git add (gitignored → dead code silencieux). Ajout `permissions: contents: write` après erreur 403 en production. Guide complet fourni par Claude (secrets, FAISS, SQLite, workflow_dispatch).

### Phase 6 — Documentation agent_code_usage.md
- Prompt résumé : "Complète agent_code_usage.md avec les vrais chiffres de lignes de code (`wc -l`), remplis les phases 1 à 4 du tracking, mets à jour la section outil utilisé."
- Lignes générées par Claude : ~60 (contenu Markdown)
- Lignes modifiées par moi : ~0
- Lignes écrites par moi from scratch : ~0
- Note : Chiffres calculés automatiquement par Claude (`wc -l` sur tous les .py). Percentages estimés à partir de l'expérience réelle par phase.

### Phase 7 — Tests et documentation finale
- Prompt résumé : "Vérifie les tests existants, ajoute tests classifier (catégorie valide, fallback NOT_RELEVANT) et scorer (float 0-1), crée README.md complet."
- Lignes générées par Claude : ~150 (3 nouveaux tests) + ~130 (README.md)
- Lignes modifiées par moi : ~0
- Lignes écrites par moi from scratch : ~0
- Note : 8 tests existaient déjà (tous passaient). 3 tests ajoutés → 11/11 passing. README avec schéma ASCII du pipeline, tableau architecture, section résultats à compléter avec screenshot.
