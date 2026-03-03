"""
evaluate.py — Évaluation de la qualité du système RAG
======================================================
Mesure deux dimensions :
  1. Retrieval quality : Hit Rate et MRR sur les 25 questions de test
  2. Answer quality   : Faithfulness et Relevance (évaluation manuelle 1-5)

Usage :
    from src.evaluate import RagEvaluator
    evaluator = RagEvaluator(retriever, rag)
    report = evaluator.run_retrieval_eval()
    evaluator.save_results(report)
"""

import json
import sys
from datetime import datetime
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from config import EVAL_RESULTS_DIR, TEST_QUESTIONS_PATH


class RagEvaluator:
    """Évaluateur du système RAG.

    Protocole d'évaluation :
    - Retrieval : automatique (vérifie si keywords attendus dans les top-k résultats)
    - Answer    : manuelle — l'évaluateur note chaque réponse 1-5 sur deux critères
    """

    def __init__(self, retriever, rag=None):
        """
        Args:
            retriever: Instance de JobRetriever.
            rag:       Instance de JobRAG (optionnel — pour l'évaluation des réponses).
        """
        self.retriever = retriever
        self.rag = rag
        self.questions = self._load_questions()

    def _load_questions(self) -> list[dict]:
        data = json.loads(TEST_QUESTIONS_PATH.read_text(encoding="utf-8"))
        return data["questions"]

    # -----------------------------------------------------------------------
    # Évaluation retrieval (automatique)
    # -----------------------------------------------------------------------

    def _check_hit(self, results: list[dict], expected_keywords: list[str]) -> bool:
        """Retourne True si au moins un keyword attendu apparaît dans les top-k résultats.

        Vérifie dans les champs title + description (insensible à la casse).
        """
        if not results:
            return False

        for r in results:
            text = (r.get("title", "") + " " + r.get("description_short", "") + " " + r.get("description", "")).lower()
            for kw in expected_keywords:
                if kw.lower() in text:
                    return True
        return False

    def _check_category_hit(self, results: list[dict], expected_category: str) -> bool:
        """Retourne True si au moins un résultat appartient à la catégorie attendue."""
        if not expected_category:
            return True  # Pas de contrainte de catégorie
        return any(r.get("label") == expected_category for r in results)

    def _mrr_score(self, results: list[dict], expected_keywords: list[str]) -> float:
        """Mean Reciprocal Rank : 1/rang du premier résultat pertinent."""
        for r in results:
            text = (r.get("title", "") + " " + r.get("description_short", "") + " " + r.get("description", "")).lower()
            for kw in expected_keywords:
                if kw.lower() in text:
                    return 1.0 / r["rank"]
        return 0.0

    def run_retrieval_eval(self, top_k: int = 5, verbose: bool = True) -> dict:
        """Évalue la qualité du retrieval sur les 25 questions de test.

        Métriques :
        - Hit rate (top-k) : % de questions où ≥ 1 document pertinent est dans les top-k
        - MRR : rang moyen du premier document pertinent
        - Category hit rate : % de questions où la bonne catégorie est dans les top-k

        Returns:
            Dict avec métriques globales + résultats par question.
        """
        if verbose:
            print(f"Évaluation retrieval sur {len(self.questions)} questions (top-{top_k})")
            print("-" * 60)

        results_per_q = []
        hits = 0
        mrr_scores = []
        cat_hits = 0
        n_with_cat = 0

        for q in self.questions:
            search_results = self.retriever.search(q["question"], top_k=top_k)

            hit = self._check_hit(search_results, q["expected_keywords"])
            mrr = self._mrr_score(search_results, q["expected_keywords"])
            cat_hit = self._check_category_hit(search_results, q.get("expected_category"))

            if hit:
                hits += 1
            mrr_scores.append(mrr)
            if q.get("expected_category"):
                n_with_cat += 1
                if cat_hit:
                    cat_hits += 1

            result = {
                "id": q["id"],
                "question": q["question"],
                "difficulty": q["difficulty"],
                "hit": hit,
                "mrr": round(mrr, 4),
                "category_hit": cat_hit,
                "expected_keywords": q["expected_keywords"],
                "expected_category": q.get("expected_category"),
                "n_results": len(search_results),
                "top1_title": search_results[0]["title"] if search_results else "",
                "top1_score": search_results[0]["score"] if search_results else 0.0,
            }
            results_per_q.append(result)

            if verbose:
                status = "OK" if hit else "--"
                print(
                    f"[{status}] {q['id']} ({q['difficulty']:<6}) "
                    f"MRR={mrr:.3f} | {q['question'][:55]}"
                )

        # Métriques globales
        n = len(self.questions)
        hit_rate = hits / n
        mean_mrr = sum(mrr_scores) / n
        cat_hit_rate = cat_hits / n_with_cat if n_with_cat else None

        # Par difficulté
        by_difficulty = {}
        for diff in ["easy", "medium", "hard"]:
            subset = [r for r in results_per_q if r["difficulty"] == diff]
            if subset:
                by_difficulty[diff] = {
                    "hit_rate": sum(r["hit"] for r in subset) / len(subset),
                    "mean_mrr": sum(r["mrr"] for r in subset) / len(subset),
                    "n": len(subset),
                }

        report = {
            "timestamp": datetime.now().isoformat(),
            "n_questions": n,
            "top_k": top_k,
            "global": {
                "hit_rate": round(hit_rate, 4),
                "mean_mrr": round(mean_mrr, 4),
                "category_hit_rate": round(cat_hit_rate, 4) if cat_hit_rate else None,
            },
            "by_difficulty": by_difficulty,
            "per_question": results_per_q,
        }

        if verbose:
            print()
            print("=== Résultats globaux ===")
            print(f"  Hit rate (top-{top_k})   : {hit_rate:.1%}")
            print(f"  MRR                   : {mean_mrr:.4f}")
            if cat_hit_rate:
                print(f"  Category hit rate     : {cat_hit_rate:.1%}")
            print()
            print("Par difficulté :")
            for diff, m in by_difficulty.items():
                print(f"  {diff:<7} : hit={m['hit_rate']:.1%}  mrr={m['mean_mrr']:.3f}  (n={m['n']})")

        return report

    # -----------------------------------------------------------------------
    # Évaluation manuelle des réponses (answer quality)
    # -----------------------------------------------------------------------

    def run_answer_eval_sample(
        self, n_questions: int = 10, verbose: bool = True
    ) -> dict:
        """Génère les réponses pour un échantillon de questions.

        Le résultat est sauvegardé en JSON pour annotation manuelle.
        Charge un sous-ensemble représentatif (easy/medium/hard équilibré).

        Returns:
            Dict avec les réponses générées, prêt pour annotation.
        """
        if not self.rag:
            raise ValueError("JobRAG requis pour l'évaluation des réponses.")

        # Sélection équilibrée par difficulté
        easy   = [q for q in self.questions if q["difficulty"] == "easy"][:3]
        medium = [q for q in self.questions if q["difficulty"] == "medium"][:4]
        hard   = [q for q in self.questions if q["difficulty"] == "hard"][:3]
        sample = (easy + medium + hard)[:n_questions]

        results = []
        for i, q in enumerate(sample, 1):
            if verbose:
                print(f"[{i}/{len(sample)}] {q['question'][:60]}")

            rag_result = self.rag.ask(q["question"])

            results.append({
                "id": q["id"],
                "difficulty": q["difficulty"],
                "question": q["question"],
                "answer": rag_result["answer"],
                "sources_used": [
                    {
                        "rank": r["rank"],
                        "title": r["title"],
                        "company": r["company"],
                        "score": r["score"],
                    }
                    for r in rag_result["sources"]
                ],
                "no_relevant_docs": rag_result["no_relevant_docs"],
                # Champs à remplir manuellement :
                "manual_scores": {
                    "faithfulness": None,   # 1-5 : réponse fidèle aux sources ?
                    "relevance": None,      # 1-5 : réponse pertinente à la question ?
                    "citations_correct": None,  # True/False
                    "notes": "",
                }
            })

        return {
            "timestamp": datetime.now().isoformat(),
            "n_evaluated": len(results),
            "instruction": (
                "Remplis manual_scores pour chaque question.\n"
                "faithfulness: 1=hallucination, 5=parfaitement fidèle aux sources\n"
                "relevance: 1=hors sujet, 5=répond exactement à la question\n"
                "citations_correct: True si les citations [Titre - Entreprise] sont exactes"
            ),
            "results": results,
        }

    # -----------------------------------------------------------------------
    # Agrégation des scores manuels
    # -----------------------------------------------------------------------

    def compute_answer_metrics(self, annotated_path: Path) -> dict:
        """Calcule les métriques à partir d'un fichier d'évaluation annoté.

        Args:
            annotated_path: Chemin vers le JSON complété manuellement.
        """
        data = json.loads(annotated_path.read_text(encoding="utf-8"))
        results = data["results"]

        # Filtre les questions réellement annotées
        annotated = [
            r for r in results
            if r["manual_scores"]["faithfulness"] is not None
        ]

        if not annotated:
            return {"error": "Aucune annotation manuelle trouvée."}

        faithfulness = [r["manual_scores"]["faithfulness"] for r in annotated]
        relevance    = [r["manual_scores"]["relevance"]    for r in annotated]
        citations    = [r["manual_scores"]["citations_correct"] for r in annotated if r["manual_scores"]["citations_correct"] is not None]

        metrics = {
            "n_annotated": len(annotated),
            "faithfulness_mean": round(sum(faithfulness) / len(faithfulness), 2),
            "relevance_mean":    round(sum(relevance) / len(relevance), 2),
            "citations_correct_pct": round(sum(citations) / len(citations) * 100, 1) if citations else None,
        }
        return metrics

    # -----------------------------------------------------------------------
    # Persistance des résultats
    # -----------------------------------------------------------------------

    def save_results(self, report: dict, filename: str = None) -> Path:
        """Sauvegarde le rapport d'évaluation dans eval/results/."""
        EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"eval_retrieval_{ts}.json"

        path = EVAL_RESULTS_DIR / filename
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Résultats sauvegardés : {path}")
        return path


# ---------------------------------------------------------------------------
# CLI — python -m src.evaluate  [--answer]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, warnings, logging
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from src.retriever import JobRetriever

    parser = argparse.ArgumentParser(
        prog="python -m src.evaluate",
        description="Evaluation du systeme RAG (retrieval + answer quality)",
    )
    parser.add_argument(
        "--answer", "-a",
        action="store_true",
        help="Generer les reponses pour annotation manuelle (faithfulness / relevance)",
    )
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help="Backend LLM pour l'answer eval : 'anthropic' ou 'openai'",
    )
    parser.add_argument(
        "--n", "-n",
        type=int, default=10, metavar="N",
        help="Nombre de questions pour l'answer eval (defaut : 10)",
    )
    args = parser.parse_args()

    retriever = JobRetriever()

    if args.answer:
        # Mode answer eval : génère les réponses du LLM sur un échantillon de questions
        # et les sauvegarde pour annotation manuelle (faithfulness / relevance).
        # JobRAG est importé ici pour éviter de charger le client LLM en mode retrieval seul.
        from src.rag import JobRAG
        from config import LLM_PROVIDER

        provider = args.provider or LLM_PROVIDER
        print(f"Generation des reponses (provider={provider}, n={args.n})...")

        rag_instance = JobRAG(retriever, provider=provider)
        evaluator = RagEvaluator(retriever, rag=rag_instance)

        # Sélection équilibrée easy/medium/hard (cf. run_answer_eval_sample).
        sample = evaluator.run_answer_eval_sample(n_questions=args.n, verbose=True)

        # Nom de fichier horodaté pour conserver l'historique des sessions d'annotation.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = EVAL_RESULTS_DIR / f"answer_eval_{ts}.json"
        EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nFichier genere : {out_path}")
        print("Ouvre ce fichier et remplis les champs :")
        print("  faithfulness      : 1 (hallucination) → 5 (fidelement ancre dans les sources)")
        print("  relevance         : 1 (hors sujet)    → 5 (repond exactement a la question)")
        print("  citations_correct : true / false")
        print("\nPuis lance pour agregger les scores :")
        print(f"  python -c \"from src.evaluate import RagEvaluator; from src.retriever import JobRetriever; e=RagEvaluator(JobRetriever()); print(e.compute_answer_metrics(r'{out_path}'))\"")
    else:
        # Mode par défaut : évaluation retrieval automatique (Hit Rate + MRR + Category hit).
        evaluator = RagEvaluator(retriever)
        report = evaluator.run_retrieval_eval(verbose=True)
        evaluator.save_results(report)
