"""
retriever.py — Recherche sémantique sur l'index FAISS
======================================================
Implémente la classe JobRetriever réutilisable par l'étape 3 (agent).

Pipeline de recherche :
    1. Encode la requête en vecteur (même modèle que l'indexation)
    2. Recherche les top-K candidats par similarité cosine (FAISS)
    3. Re-rank par score combiné : similarité × recency_boost × category_match
    4. Retourne les top-5 avec métadonnées et scores

Usage :
    from src.retriever import JobRetriever
    retriever = JobRetriever()
    results = retriever.search("data engineer dbt Genève", top_k=5)
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from config import (
    EMBEDDING_MODEL,
    FAISS_INDEX_PATH,
    FAISS_META_PATH,
    MIN_SIMILARITY,
    RECENCY_DECAY_DAYS,
    TOP_K_FINAL,
    TOP_K_RETRIEVAL,
)


class JobRetriever:
    """Moteur de recherche sémantique sur les offres d'emploi indexées.

    Conçu pour être réutilisé par l'agent de l'étape 3 :
    - search() : recherche sémantique avec re-ranking
    - add_documents() : mise à jour incrémentale de l'index
    - similarity_score() : scoring d'une offre vs un profil (étape 3)
    """

    def __init__(
        self,
        vectorstore_path: Path = None,
        embedding_model: str = EMBEDDING_MODEL,
    ):
        """
        Args:
            vectorstore_path: Répertoire contenant jobs.index et jobs_meta.json.
                              Si None, utilise config.VECTORSTORE_DIR.
            embedding_model:  Identifiant sentence-transformers du modèle d'embedding.
        """
        if vectorstore_path is None:
            from config import VECTORSTORE_DIR
            vectorstore_path = VECTORSTORE_DIR

        index_path = Path(vectorstore_path) / "jobs.index"
        meta_path  = Path(vectorstore_path) / "jobs_meta.json"

        if not index_path.exists():
            raise FileNotFoundError(
                f"Index FAISS introuvable : {index_path}\n"
                "Lance d'abord : python -m src.indexer"
            )

        self.model = SentenceTransformer(embedding_model)
        self.index = faiss.read_index(str(index_path))
        self.metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        self._index_path = index_path
        self._meta_path  = meta_path

    # -----------------------------------------------------------------------
    # Recherche principale
    # -----------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = TOP_K_FINAL,
        filters: dict = None,
    ) -> list[dict]:
        """Recherche sémantique avec re-ranking et filtrage optionnel.

        Args:
            query:   Question en langage naturel (FR ou EN).
            top_k:   Nombre de résultats finaux à retourner.
            filters: Filtres sur les métadonnées, ex:
                     {"label": "DATA_ENGINEERING"}
                     {"location_contains": "Genève"}
                     {"min_date": "2026-01-01"}

        Returns:
            Liste de dicts triés par score décroissant :
            {
                "title", "company", "location", "label",
                "source", "url", "date_posted",
                "similarity": float,   # Score cosine brut (0-1)
                "score": float,        # Score re-ranké
                "rank": int,
            }
        """
        # 1. Encode la requête
        q_vec = self.model.encode(
            [query], normalize_embeddings=True
        ).astype(np.float32)

        # 2. Recherche FAISS top-K_RETRIEVAL candidats
        n_candidates = min(TOP_K_RETRIEVAL, self.index.ntotal)
        scores, indices = self.index.search(q_vec, n_candidates)

        # 3. Construction des résultats candidats
        candidates = []
        for idx, sim in zip(indices[0], scores[0]):
            if idx < 0:  # FAISS retourne -1 si index.ntotal < k
                continue
            meta = self.metadata[idx].copy()
            meta["similarity"] = float(sim)
            candidates.append(meta)

        # 4. Filtrage par métadonnées
        if filters:
            candidates = self._apply_filters(candidates, filters)

        # 5. Re-ranking
        candidates = self._rerank(candidates)

        # 6. Seuil de similarité minimale
        candidates = [c for c in candidates if c["similarity"] >= MIN_SIMILARITY]

        # 7. Top-k final avec rang
        results = candidates[:top_k]
        for i, r in enumerate(results, 1):
            r["rank"] = i

        return results

    # -----------------------------------------------------------------------
    # Filtrage par métadonnées
    # -----------------------------------------------------------------------

    def _apply_filters(self, candidates: list[dict], filters: dict) -> list[dict]:
        """Applique des filtres sur les métadonnées des candidats.

        Filtres supportés :
            label            : correspondance exacte de catégorie
            location_contains: sous-chaîne dans le champ location (insensible casse)
            min_date         : date_posted >= min_date (format YYYY-MM-DD)
        """
        out = []
        for c in candidates:
            if "label" in filters:
                if c.get("label") != filters["label"]:
                    continue
            if "location_contains" in filters:
                loc = c.get("location", "").lower()
                if filters["location_contains"].lower() not in loc:
                    continue
            if "min_date" in filters:
                posted = c.get("date_posted", "")
                if posted and posted < filters["min_date"]:
                    continue
            out.append(c)
        return out

    # -----------------------------------------------------------------------
    # Re-ranking
    # -----------------------------------------------------------------------

    def _rerank(self, candidates: list[dict]) -> list[dict]:
        """Re-rank les candidats par score combiné.

        score = similarity × recency_boost × category_weight

        - recency_boost : favorise les offres récentes avec décroissance
          exponentielle (demi-vie = RECENCY_DECAY_DAYS jours). Les offres
          sans date connue reçoivent un boost neutre de 0.8.

        - category_weight : DATA_ENGINEERING et BI_ANALYTICS sont les
          catégories cibles principales → boost 1.0. DBA_INFRA → 0.9
          (légèrement moins central pour le profil visé).
        """
        now = datetime.now().date()

        category_weights = {
            "DATA_ENGINEERING": 1.0,
            "BI_ANALYTICS": 1.0,
            "DBA_INFRA": 0.9,
        }

        for c in candidates:
            sim = c["similarity"]

            # Recency boost
            date_str = c.get("date_posted", "")
            if date_str:
                try:
                    posted = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
                    age_days = max(0, (now - posted).days)
                    # Décroissance exponentielle : score = 0.5^(age/demi-vie)
                    recency = 0.5 ** (age_days / RECENCY_DECAY_DAYS)
                    # Normalise entre 0.5 et 1.0 pour ne pas trop pénaliser les vieilles offres
                    recency = 0.5 + 0.5 * recency
                except ValueError:
                    recency = 0.8
            else:
                recency = 0.8

            cat_w = category_weights.get(c.get("label", ""), 0.9)

            c["recency_boost"]    = round(recency, 4)
            c["category_weight"]  = cat_w
            c["score"]            = round(sim * recency * cat_w, 4)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    # -----------------------------------------------------------------------
    # Méthodes pour l'étape 3 (agent)
    # -----------------------------------------------------------------------

    def add_documents(self, jobs: list[dict]):
        """Ajoute de nouvelles offres au vector store (mise à jour quotidienne).

        Args:
            jobs: Liste de dicts avec clés : title, company, location,
                  label, description, source, url, date_posted, id.

        Note: Les nouvelles offres sont ajoutées en fin d'index.
              L'index et les métadonnées sont re-persistés sur disque.
        """
        from src.indexer import format_document

        texts = [format_document(j) for j in jobs]
        embeddings = self.model.encode(
            texts, normalize_embeddings=True
        ).astype(np.float32)

        self.index.add(embeddings)
        self.metadata.extend(jobs)

        # Re-persistance
        faiss.write_index(self.index, str(self._index_path))
        self._meta_path.write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def similarity_score(self, job: dict, profile_embedding: np.ndarray) -> float:
        """Score de similarité entre une offre et un profil utilisateur.

        Utilisé par l'agent (étape 3) pour scorer les nouvelles offres
        par rapport au profil du candidat.

        Args:
            job:               Dict avec champs title, company, location, description.
            profile_embedding: Vecteur numpy normalisé représentant le profil.

        Returns:
            Score cosine entre 0 et 1.
        """
        from src.indexer import format_document

        text = format_document(job)
        job_vec = self.model.encode([text], normalize_embeddings=True).astype(np.float32)
        # Produit scalaire de vecteurs normalisés = cosine similarity
        return float(np.dot(job_vec[0], profile_embedding))


# ---------------------------------------------------------------------------
# CLI — test rapide
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "data engineer dbt Genève"
    print(f'Requête : "{query}"')
    print()

    retriever = JobRetriever()
    results = retriever.search(query)

    for r in results:
        print(f"[{r['rank']}] score={r['score']:.4f} sim={r['similarity']:.4f} recency={r['recency_boost']:.4f}")
        print(f"    {r['title']} — {r['company']} | {r['location']}")
        print(f"    Catégorie : {r['label']} | Date : {r['date_posted']}")
        print(f"    URL : {r['url'][:80]}")
        print()
