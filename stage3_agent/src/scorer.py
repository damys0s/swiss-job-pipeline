"""
scorer.py — Classification et scoring des offres d'emploi
==========================================================
Étape 2 du pipeline :
  1. Classe chaque offre via JobClassifier (GPT-4o-mini fine-tuné)
  2. Exclut NOT_RELEVANT
  3. Score les offres restantes par similarité cosine avec le profil candidat
  4. Retourne les top-N offres triées par score décroissant

Usage:
    from src.scorer import JobScorer
    scorer = JobScorer()
    top_jobs = scorer.score_and_rank(jobs, top_n=10)
"""

import json
import logging
from pathlib import Path

import numpy as np

from config.settings import (
    CLASSIFIER_MODEL_ID,
    EMBEDDING_MODEL,
    MIN_SCORE,
    OPENAI_API_KEY,
    PROFILE_PATH,
    TOP_N_RESULTS,
    VECTORSTORE_DIR,
)
from shared.classifier import JobClassifier
from shared.retriever import JobRetriever

logger = logging.getLogger(__name__)

RELEVANT_LABELS = {"DATA_ENGINEERING", "BI_ANALYTICS"}

# Mots-clés acceptés même hors des villes préférées (remote, toute la Suisse romande...)
_LOCATION_WILDCARDS = {"remote", "télétravail", "home office", "switzerland", "suisse", "vaud", "romandie", "hybrid", "hybride"}


def _is_location_ok(job_location: str, preferred: list[str]) -> bool:
    """Retourne True si la localisation est acceptable selon le profil."""
    if not job_location:
        return True  # localisation inconnue → ne pas filtrer
    loc = job_location.lower()
    if any(w in loc for w in _LOCATION_WILDCARDS):
        return True
    return any(city.lower() in loc for city in preferred)


def _is_title_excluded(job_title: str, excluded_keywords: list[str]) -> bool:
    """Retourne True si le titre contient un mot-clé exclu (ex: Senior, Lead...).

    Exception : "Junior Senior" est toléré (rare mais existe).
    """
    if not job_title or not excluded_keywords:
        return False
    title_lower = job_title.lower()
    for kw in excluded_keywords:
        kw_lower = kw.lower()
        if kw_lower in title_lower:
            # Exception : "Senior" toléré si "junior" aussi présent
            if kw_lower == "senior" and "junior" in title_lower:
                continue
            return True
    return False


def _has_negative_keywords(job_text: str, negative_keywords: list[str]) -> bool:
    """Retourne True si l'offre contient au moins un mot-clé rédhibitoire."""
    if not job_text or not negative_keywords:
        return False
    text_lower = job_text.lower()
    return any(kw.lower() in text_lower for kw in negative_keywords)


def _count_positive_keywords(job_text: str, positive_keywords: list[str]) -> int:
    """Compte combien de mots-clés positifs sont présents dans le texte."""
    if not job_text or not positive_keywords:
        return 0
    text_lower = job_text.lower()
    return sum(1 for kw in positive_keywords if kw.lower() in text_lower)


def _build_profile_text(profile: dict) -> str:
    """Construit le texte du profil pour l'embedding (identique à la spec)."""
    title    = profile.get("title", "")
    years    = profile.get("experience_years", "")
    skills   = ", ".join(profile.get("skills", []))
    langs    = ", ".join(profile.get("languages", []))
    locs     = ", ".join(profile.get("locations_preferred", []))
    return (
        f"{title} with {years} years experience. "
        f"Skills: {skills}. "
        f"Languages: {langs}. "
        f"Preferred locations: {locs}."
    )


class JobScorer:
    """Classifie et score les offres d'emploi par pertinence candidat."""

    def __init__(self):
        self.classifier = JobClassifier(
            model_id=CLASSIFIER_MODEL_ID,
            api_key=OPENAI_API_KEY,
        )
        self.retriever = JobRetriever(
            vectorstore_path=VECTORSTORE_DIR,
            embedding_model=EMBEDDING_MODEL,
        )
        self._profile_embedding = self._load_profile_embedding()

    def _load_profile_embedding(self) -> np.ndarray:
        """Encode le profil candidat une seule fois."""
        self._profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        profile_text = _build_profile_text(self._profile)
        logger.info(f"Profil encodé : {profile_text[:80]}...")
        return self.retriever.encode_profile(profile_text)

    def score_and_rank(self, jobs: list[dict], top_n: int = TOP_N_RESULTS) -> list[dict]:
        """Classifie, filtre et score les offres.

        Args:
            jobs:  Liste d'offres au format standard (voir collector.py).
            top_n: Nombre d'offres à retourner.

        Returns:
            Liste de dicts enrichis avec les champs :
              - label        : catégorie (DATA_ENGINEERING | BI_ANALYTICS)
              - score        : float cosine similarity vs profil (0-1)
            Triés par score décroissant, longueur max top_n.
        """
        if not jobs:
            return []

        logger.info(f"Classification de {len(jobs)} offres...")
        classified = self.classifier.classify_batch(jobs)

        # Filtre NOT_RELEVANT
        relevant = [j for j in classified if j.get("label") in RELEVANT_LABELS]
        logger.info(f"  {len(classified)} → {len(relevant)} après filtrage NOT_RELEVANT")

        # Filtre titres exclus (Senior, Lead, Manager...)
        excluded_title_kws = self._profile.get("excluded_title_keywords", [])
        relevant = [j for j in relevant if not _is_title_excluded(j.get("title", ""), excluded_title_kws)]
        logger.info(f"  → {len(relevant)} après filtrage titres exclus")

        # Filtre mots-clés négatifs (red flags dans titre + description)
        negative_kws = self._profile.get("negative_keywords", [])
        relevant = [
            j for j in relevant
            if not _has_negative_keywords(f"{j.get('title', '')} {j.get('description', '')}", negative_kws)
        ]
        logger.info(f"  → {len(relevant)} après filtrage mots-clés négatifs")

        # Filtre mots-clés positifs (min 2 requis)
        positive_kws = self._profile.get("positive_keywords", [])
        min_pos = self._profile.get("min_positive_keywords", 2)
        relevant = [
            j for j in relevant
            if _count_positive_keywords(f"{j.get('title', '')} {j.get('description', '')}", positive_kws) >= min_pos
        ]
        logger.info(f"  → {len(relevant)} après filtrage mots-clés positifs (min {min_pos})")

        # Filtre géographique
        preferred_locs = self._profile.get("locations_preferred", [])
        relevant = [j for j in relevant if _is_location_ok(j.get("location", ""), preferred_locs)]
        logger.info(f"  → {len(relevant)} après filtrage géographique ({', '.join(preferred_locs)})")

        if not relevant:
            return []

        # Scoring cosine vs profil
        logger.info("Scoring par similarité cosine...")
        for job in relevant:
            score = self.retriever.similarity_score(job, self._profile_embedding)
            job["score"] = round(score, 4)

        # Filtre par seuil minimal
        scored = [j for j in relevant if j["score"] >= MIN_SCORE]
        logger.info(f"  {len(relevant)} → {len(scored)} après seuil minimal {MIN_SCORE}")

        # Tri décroissant
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Expose les stats pour le pipeline
        self.last_n_classified = len(classified)
        self.last_n_relevant   = len(relevant)

        return scored[:top_n]
