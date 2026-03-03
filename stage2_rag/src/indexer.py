"""
indexer.py — Préparation et indexation des offres d'emploi
===========================================================
Charge le corpus CSV (étape 1), filtre les offres pertinentes,
génère les embeddings avec sentence-transformers, et persiste
l'index FAISS + les métadonnées JSON sur disque.

Usage standalone :
    python -m src.indexer

Usage en import :
    from src.indexer import build_index
    stats = build_index()
"""

import json
import sys
import time
from pathlib import Path

import faiss
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

# Résolution du chemin racine depuis l'emplacement de ce fichier
_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from config import (
    CORPUS_PATH,
    EMBEDDING_MODEL,
    FAISS_INDEX_PATH,
    FAISS_META_PATH,
    RELEVANT_CATEGORIES,
    VECTORSTORE_DIR,
)


# ---------------------------------------------------------------------------
# Formatage du texte de chaque offre
# ---------------------------------------------------------------------------

def format_document(row: dict) -> str:
    """Construit le texte indexé pour une offre d'emploi.

    Format choisi : champs structurés en tête + description complète.
    Les champs structurés permettent à l'embedding de capter les requêtes
    du type "data engineer à Genève" même si ces mots n'apparaissent pas
    dans la description.

    La description est tronquée à 400 mots — les offres font en moyenne
    200 mots après le pré-traitement de l'étape 1, donc ce plafond ne
    tronquera presque rien en pratique.
    """
    title    = row.get("title", "").strip()
    company  = row.get("company", "").strip()
    location = row.get("location", "").strip()
    category = row.get("label", "").strip()
    desc     = row.get("description", "").strip()

    # Tronque la description à 400 mots
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


# ---------------------------------------------------------------------------
# Chargement du corpus
# ---------------------------------------------------------------------------

def load_corpus(corpus_path: Path = CORPUS_PATH) -> pd.DataFrame:
    """Charge le corpus CSV et retourne uniquement les offres pertinentes.

    Décision de design : on exclut NOT_RELEVANT de l'index.
    Raison : le RAG doit répondre sur les offres IT pertinentes.
    Inclure NOT_RELEVANT polluerait les résultats et produirait des réponses
    hors-sujet (ex : une offre de comptable remonterait pour "data engineer").

    DBA_INFRA est conservé malgré son faible nombre (5 offres) :
    il reste informatif pour les requêtes sur l'infrastructure de données.
    """
    df = pd.read_csv(corpus_path, encoding="utf-8")

    # Nettoyage des valeurs manquantes
    for col in ["title", "company", "location", "description", "label", "url", "date_posted", "source"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    # Filtrage : garder uniquement les catégories pertinentes
    mask = df["label"].isin(RELEVANT_CATEGORIES)
    df_relevant = df[mask].copy().reset_index(drop=True)

    return df_relevant


# ---------------------------------------------------------------------------
# Construction de l'index
# ---------------------------------------------------------------------------

def build_index(
    corpus_path: Path = CORPUS_PATH,
    embedding_model: str = EMBEDDING_MODEL,
    vectorstore_dir: Path = VECTORSTORE_DIR,
    verbose: bool = True,
) -> dict:
    """Charge le corpus, génère les embeddings et persiste l'index FAISS.

    Returns:
        dict avec les statistiques d'indexation.
    """
    t0 = time.time()

    # 1. Chargement et filtrage
    if verbose:
        print("1/4 Chargement du corpus...")
    df = load_corpus(corpus_path)
    if verbose:
        print(f"    {len(df)} offres pertinentes chargées")
        print(f"    Distribution : {df['label'].value_counts().to_dict()}")

    # 2. Préparation des textes
    if verbose:
        print("2/4 Préparation des documents...")
    documents = [format_document(row) for row in df.to_dict("records")]

    # 3. Génération des embeddings
    if verbose:
        print(f"3/4 Génération des embeddings ({embedding_model})...")
        print("    (premier lancement : téléchargement du modèle ~420MB)")
    model = SentenceTransformer(embedding_model)
    embeddings = model.encode(
        documents,
        batch_size=32,
        show_progress_bar=verbose,
        convert_to_numpy=True,
        normalize_embeddings=True,  # Normalisation L2 pour cosine similarity via produit scalaire
    )

    # 4. Construction de l'index FAISS
    # IndexFlatIP : produit scalaire exact (= cosine similarity après normalisation L2)
    # Choix "flat" : exact search justifié pour 180 vecteurs — ANN serait inutilement complexe.
    if verbose:
        print("4/4 Construction et persistance de l'index FAISS...")
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings.astype(np.float32))

    # Persistance de l'index FAISS
    vectorstore_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(FAISS_INDEX_PATH))

    # Persistance des métadonnées (une entrée par vecteur, dans le même ordre)
    # Description tronquée à 300 mots : suffit pour que le LLM voie les technos
    # mentionnées sans dépasser la fenêtre de contexte du prompt.
    df["description_short"] = df["description"].apply(
        lambda d: " ".join(str(d).split()[:300]) if pd.notna(d) else ""
    )
    metadata = df[[
        "id", "title", "company", "location", "label",
        "source", "url", "date_posted", "date_collected", "description_short"
    ]].to_dict("records")
    FAISS_META_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - t0

    stats = {
        "n_documents": len(df),
        "embedding_model": embedding_model,
        "embedding_dim": dim,
        "categories": df["label"].value_counts().to_dict(),
        "index_path": str(FAISS_INDEX_PATH),
        "meta_path": str(FAISS_META_PATH),
        "elapsed_seconds": round(elapsed, 1),
    }

    if verbose:
        print()
        print("=== Indexation terminée ===")
        print(f"  Documents indexés : {stats['n_documents']}")
        print(f"  Dimension         : {stats['embedding_dim']}")
        print(f"  Durée             : {stats['elapsed_seconds']}s")
        print(f"  Index sauvegardé  : {stats['index_path']}")
        print(f"  Métadonnées       : {stats['meta_path']}")

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    build_index(verbose=True)
