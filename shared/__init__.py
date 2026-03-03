"""
shared/ — Modules partagés entre les trois stages du pipeline.

- classifier.py : JobClassifier (GPT-4o-mini fine-tuné)
- retriever.py  : JobRetriever (recherche sémantique FAISS)

Ces modules sont utilisés par :
  - stage1_classifier/ : classify.py (source canonique du classifier)
  - stage2_rag/        : retriever.py (source canonique du retriever)
  - stage3_agent/      : importe depuis shared/ via scorer.py
"""
