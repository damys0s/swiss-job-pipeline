"""
test_pipeline.py — Tests unitaires du pipeline
===============================================
Tests minimaux pour valider les contrats d'interface de chaque module.
Exécution : pytest tests/

Note : les tests API (Adzuna, SerpApi) ne sont pas inclus ici pour
ne pas consommer de quota à chaque CI run.
"""

import sys
from pathlib import Path

import pytest

# Ajout de la racine au path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_job():
    return {
        "id":          "abc123",
        "title":       "Data Engineer",
        "company":     "Nestlé",
        "location":    "Lausanne",
        "description": "Développement de pipelines ETL avec dbt et Airflow.",
        "url":         "https://example.com/job/123",
        "date_posted": "2026-03-03",
        "source":      "adzuna",
    }

@pytest.fixture
def sample_jobs(sample_job):
    return [
        sample_job,
        {
            "id":          "def456",
            "title":       "BI Developer Power BI",
            "company":     "UBS",
            "location":    "Geneva",
            "description": "Développement de tableaux de bord Power BI et SQL Server.",
            "url":         "https://example.com/job/456",
            "date_posted": "2026-03-03",
            "source":      "serpapi",
        },
    ]


# ---------------------------------------------------------------------------
# Tests — Deduplicator
# ---------------------------------------------------------------------------

def test_deduplicator_marks_as_seen(tmp_path, sample_job):
    from src.deduplicator import Deduplicator

    dedup = Deduplicator(db_path=tmp_path / "test.db")

    # Avant marquage : nouvelle
    assert dedup.is_new(sample_job) is True

    # Marquage
    dedup.mark_seen([sample_job])

    # Après marquage : vue
    assert dedup.is_new(sample_job) is False


def test_deduplicator_filter_new(tmp_path, sample_jobs):
    from src.deduplicator import Deduplicator

    dedup = Deduplicator(db_path=tmp_path / "test.db")
    dedup.mark_seen([sample_jobs[0]])  # Marque la première

    new_jobs = dedup.filter_new(sample_jobs)
    assert len(new_jobs) == 1
    assert new_jobs[0]["id"] == "def456"


def test_deduplicator_count(tmp_path, sample_jobs):
    from src.deduplicator import Deduplicator

    dedup = Deduplicator(db_path=tmp_path / "test.db")
    assert dedup.count() == 0
    dedup.mark_seen(sample_jobs)
    assert dedup.count() == 2


# ---------------------------------------------------------------------------
# Tests — Emailer (génération HTML, pas d'envoi)
# ---------------------------------------------------------------------------

def test_emailer_html_valid(sample_jobs):
    from src.emailer import _build_html

    stats = {
        "total_raw": 10,
        "n_relevant": 4,
        "adzuna":     {"kept": 5},
        "serpapi":    {"requests": 3},
        "indeed_rss": {"kept": 2},
        "duration_seconds": 12.5,
    }

    # Ajoute label et score aux offres
    jobs_with_scores = []
    for job in sample_jobs:
        j = {**job, "label": "DATA_ENGINEERING", "score": 0.75}
        jobs_with_scores.append(j)

    html = _build_html(jobs_with_scores, stats, "03/03/2026")

    # Vérifications basiques du HTML
    assert "<!DOCTYPE html>" in html
    assert "Data Engineer" in html
    assert "Nestlé" in html
    assert "75%" in html  # Score barre


def test_emailer_html_empty_jobs():
    from src.emailer import _build_html

    html = _build_html([], {"total_raw": 0, "n_relevant": 0, "adzuna": {"kept": 0}, "serpapi": {"requests": 0}, "indeed_rss": {"kept": 0}, "duration_seconds": 1.0}, "03/03/2026")
    assert "Aucune nouvelle offre" in html


# ---------------------------------------------------------------------------
# Tests — Classifier (catégorie valide, sans appel OpenAI réel)
# ---------------------------------------------------------------------------

def test_classifier_returns_valid_label(monkeypatch):
    """Le classifier doit retourner une catégorie parmi les labels valides."""
    from src.classify import JobClassifier, VALID_LABELS

    # Mock du client OpenAI — pas d'appel réseau
    class MockChoice:
        class message:
            content = "DATA_ENGINEERING"

    class MockCompletion:
        choices = [MockChoice()]

    class MockClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return MockCompletion()

    monkeypatch.setattr("src.classify.OpenAI", lambda **kwargs: MockClient())

    clf = JobClassifier(model_id="ft:test-model", api_key="test-key")
    label = clf.classify(title="Data Engineer", company="Nestlé", location="Lausanne",
                         description="Pipelines ETL avec dbt et Airflow.")

    assert label in VALID_LABELS


def test_classifier_fallback_on_invalid_response(monkeypatch):
    """Si le modèle retourne une réponse inattendue, doit fallback sur NOT_RELEVANT."""
    from src.classify import JobClassifier

    class MockChoice:
        class message:
            content = "Je ne sais pas"

    class MockCompletion:
        choices = [MockChoice()]

    class MockClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return MockCompletion()

    monkeypatch.setattr("src.classify.OpenAI", lambda **kwargs: MockClient())

    clf = JobClassifier(model_id="ft:test-model", api_key="test-key")
    label = clf.classify(title="Inconnu")

    assert label == "NOT_RELEVANT"


# ---------------------------------------------------------------------------
# Tests — Scorer (score float entre 0 et 1, sans appel OpenAI réel)
# ---------------------------------------------------------------------------

def test_scorer_returns_float_between_0_and_1(monkeypatch, sample_job):
    """Le scorer doit retourner un score float compris entre 0 et 1."""
    from src.scorer import JobScorer

    # Mock du classifier
    class MockClassifier:
        def classify_batch(self, jobs):
            return [{**j, "label": "DATA_ENGINEERING", "is_relevant": True} for j in jobs]

    # Mock du retriever
    import numpy as np

    class MockRetriever:
        def encode_profile(self, text):
            return np.ones(384, dtype=np.float32) / np.sqrt(384)

        def similarity_score(self, job, profile_embedding):
            return 0.72  # Valeur fixe pour le test

    monkeypatch.setattr("src.scorer.JobClassifier", lambda **kwargs: MockClassifier())
    monkeypatch.setattr("src.scorer.JobRetriever", lambda **kwargs: MockRetriever())

    scorer = JobScorer()
    results = scorer.score_and_rank([sample_job], top_n=5)

    assert len(results) == 1
    score = results[0]["score"]
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Tests — Collector (format de sortie, sans appel API réel)
# ---------------------------------------------------------------------------

def test_collector_normalize_date():
    from src.collector import _normalize_date

    assert _normalize_date("2026-03-03T14:30:00") == "2026-03-03"
    assert _normalize_date("2026-03-03") == "2026-03-03"
    assert _normalize_date("") == ""
    assert _normalize_date("invalid") == ""


def test_collector_job_id_from_url():
    from src.collector import _job_id

    id1 = _job_id("https://example.com/job/1")
    id2 = _job_id("https://example.com/job/2")
    id3 = _job_id("https://example.com/job/1")

    assert id1 != id2
    assert id1 == id3  # Même URL → même ID
    assert len(id1) == 16


# ---------------------------------------------------------------------------
# Test end-to-end — Pipeline dry-run (sans API, sans email)
# ---------------------------------------------------------------------------

def test_pipeline_dry_run_no_crash(monkeypatch, tmp_path, sample_jobs):
    """Vérifie que le pipeline ne crashe pas en dry-run.
    Mocke le collector pour éviter les appels API réels.
    """
    from src.deduplicator import Deduplicator
    from src import pipeline as p

    # Mock : collector retourne sample_jobs sans appel API
    class MockCollector:
        def collect(self):
            return sample_jobs, {"total_raw": 2, "total_dedup": 2,
                                  "adzuna": {"fetched": 2, "kept": 2},
                                  "serpapi": {"fetched": 0, "kept": 0, "requests": 0},
                                  "indeed_rss": {"fetched": 0, "kept": 0}}

    # Mock : scorer retourne les offres avec scores sans appel OpenAI
    class MockScorer:
        def score_and_rank(self, jobs, top_n=10):
            return [{**j, "label": "DATA_ENGINEERING", "score": 0.8} for j in jobs[:top_n]]

    monkeypatch.setattr(p, "JobCollector", MockCollector)
    monkeypatch.setattr(p, "JobScorer", MockScorer)
    monkeypatch.setattr(p, "Deduplicator", lambda: Deduplicator(db_path=tmp_path / "test.db"))
    monkeypatch.setattr(p, "LOGS_DIR", tmp_path / "logs")

    result = p.run_daily_pipeline(dry_run=True)

    assert result["success"] is True
    assert result["dry_run"] is True
    assert "collect" in result["steps"]
    assert "score" in result["steps"]
