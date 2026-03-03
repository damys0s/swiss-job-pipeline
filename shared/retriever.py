"""
retriever.py — Recherche sémantique sur l'index FAISS (module partagé)
======================================================================
Source canonique : stage2_rag/src/retriever.py

Ce module expose uniquement les méthodes utilisées par stage3_agent :
  - similarity_score() : score cosine d'une offre vs profil candidat
  - encode_profile()   : encode le profil en vecteur normalisé
  - encode_job()       : encode une offre en vecteur normalisé
  - add_documents()    : mise à jour incrémentale de l'index

Usage en import :
    from shared.retriever import JobRetriever
    retriever = JobRetriever(vectorstore_path=Path("data/vectorstore"))
    score = retriever.similarity_score(job_dict, profile_embedding)
"""

import json
from datetime import datetime
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# Modèle d'embedding par défaut (identique à stage2_rag)
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Seuils de re-ranking (identiques à stage2_rag)
TOP_K_RETRIEVAL    = 20
TOP_K_FINAL        = 5
RECENCY_DECAY_DAYS = 30


def format_document(row: dict) -> str:
    """Construit le texte indexé pour une offre d'emploi (identique à stage2_rag)."""
    title    = row.get("title", "").strip()
    company  = row.get("company", "").strip()
    location = row.get("location", "").strip()
    category = row.get("label", "").strip()
    desc     = row.get("description", "").strip()

    words = desc.split()
    if len(words) > 400:
        desc = " ".join(words[:400])

    parts = [f"Titre: {title}"]
    if company:
        parts.append(f"Entreprise: {company}")
    if location:
        parts.append(f"Lieu: {location}")
    if category:
        parts.append(f"Catégorie: {category}")
    if desc:
        parts.append(f"Description: {desc}")

    return " | ".join(parts)


class JobRetriever:
    """Moteur de recherche sémantique sur les offres d'emploi indexées.

    Utilisé par stage3_agent pour :
    - similarity_score() : score cosine d'une offre vs profil candidat
    - add_documents()    : mise à jour incrémentale de l'index avec les nouvelles offres
    """

    def __init__(
        self,
        vectorstore_path: Path = None,
        embedding_model: str = EMBEDDING_MODEL,
    ):
        """
        Args:
            vectorstore_path: Chemin vers le dossier contenant jobs.index et jobs_meta.json.
                              Par défaut pointe vers data/vectorstore/ à la racine du repo.
            embedding_model:  Nom du modèle sentence-transformers à utiliser.
        """
        if vectorstore_path is None:
            # Fallback : data/vectorstore/ à la racine du repo (shared/ → repo root → data/)
            vectorstore_path = Path(__file__).resolve().parent.parent / "data" / "vectorstore"

        index_path = Path(vectorstore_path) / "jobs.index"
        meta_path  = Path(vectorstore_path) / "jobs_meta.json"

        if not index_path.exists():
            raise FileNotFoundError(
                f"Index FAISS introuvable : {index_path}\n"
                "Lance d'abord stage2_rag/src/indexer.py pour construire l'index."
            )

        self.model    = SentenceTransformer(embedding_model)
        self.index    = faiss.read_index(str(index_path))
        self.metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        self._index_path = index_path
        self._meta_path  = meta_path

    def similarity_score(self, job: dict, profile_embedding: np.ndarray) -> float:
        """Score cosine entre une offre et un profil candidat.

        Args:
            job:               Dict avec champs title, company, location, description.
            profile_embedding: Vecteur numpy normalisé (produit de encode_profile).

        Returns:
            Score cosine entre 0 et 1.
        """
        text    = format_document(job)
        job_vec = self.model.encode([text], normalize_embeddings=True).astype(np.float32)
        return float(np.dot(job_vec[0], profile_embedding))

    def encode_profile(self, profile_text: str) -> np.ndarray:
        """Encode le texte du profil candidat en vecteur normalisé.

        À appeler une seule fois et stocker le résultat pour éviter de
        re-encoder à chaque offre.
        """
        return self.model.encode([profile_text], normalize_embeddings=True).astype(np.float32)[0]

    def encode_job(self, job: dict) -> np.ndarray:
        """Encode une offre en vecteur normalisé."""
        text = format_document(job)
        return self.model.encode([text], normalize_embeddings=True).astype(np.float32)[0]

    def add_documents(self, jobs: list[dict]):
        """Ajoute de nouvelles offres au vector store (mise à jour quotidienne).

        Args:
            jobs: Liste de dicts avec clés : title, company, location,
                  label, description, source, url, date_posted, id.
        """
        texts      = [format_document(j) for j in jobs]
        embeddings = self.model.encode(
            texts, normalize_embeddings=True
        ).astype(np.float32)

        self.index.add(embeddings)
        self.metadata.extend(jobs)

        faiss.write_index(self.index, str(self._index_path))
        self._meta_path.write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
