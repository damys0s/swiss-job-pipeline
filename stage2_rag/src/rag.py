"""
rag.py — Pipeline RAG complet (Retrieval-Augmented Generation)
==============================================================
Connecte JobRetriever à l'API Claude pour produire des réponses
en langage naturel avec citations des offres sources.

Usage :
    from src.rag import JobRAG
    from src.retriever import JobRetriever

    retriever = JobRetriever()
    rag = JobRAG(retriever)
    result = rag.ask("Quelles offres demandent dbt à Genève ?")
    print(result["answer"])
    print(result["sources"])
"""

import os
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from config import (
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    LLM_PROVIDER, MIN_SIMILARITY,
    OPENAI_API_KEY, OPENAI_MODEL,
)


# ---------------------------------------------------------------------------
# Prompt système
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es un assistant spécialisé dans la recherche d'offres d'emploi IT en Suisse romande.
Réponds à la question de l'utilisateur en te basant UNIQUEMENT sur les offres fournies ci-dessous.
Pour chaque information, cite l'offre source entre crochets [Titre - Entreprise].
Si aucune offre ne correspond à la question, dis-le clairement sans inventer.
Réponds en français. Sois concis et précis."""


# ---------------------------------------------------------------------------
# Formatage du contexte
# ---------------------------------------------------------------------------

def format_context(results: list[dict]) -> str:
    """Formate les offres récupérées en contexte pour le prompt LLM.

    Chaque offre est numérotée pour permettre les citations.
    Les champs clés sont présentés de manière structurée.
    """
    if not results:
        return "Aucune offre pertinente trouvée."

    parts = []
    for r in results:
        desc = r.get("description_short") or r.get("description", "")
        if not desc:
            desc = "(description non disponible)"

        block = (
            f"[{r['rank']}] Titre: {r['title']}\n"
            f"    Entreprise: {r['company']}\n"
            f"    Lieu: {r['location']}\n"
            f"    Catégorie: {r['label']}\n"
            f"    Date: {r['date_posted']}\n"
            f"    Description: {desc}"
        )
        parts.append(block)

    return "\n\n".join(parts)


def build_prompt(question: str, context: str) -> str:
    """Construit le prompt utilisateur avec les offres en contexte."""
    return (
        "--- OFFRES PERTINENTES ---\n"
        f"{context}\n\n"
        "--- QUESTION ---\n"
        f"{question}"
    )


# ---------------------------------------------------------------------------
# Classe principale
# ---------------------------------------------------------------------------

class JobRAG:
    """Pipeline RAG complet pour la recherche d'offres d'emploi.

    Supporte deux backends LLM : Anthropic (Claude) et OpenAI (gpt-4o-mini).
    Le backend est sélectionné via config.LLM_PROVIDER ou le paramètre `provider`.
    Réutilisable par l'agent de l'étape 3 via la méthode ask().
    """

    def __init__(
        self,
        retriever,
        provider: str = None,
        llm_model: str = None,
        api_key: str = None,
    ):
        """
        Args:
            retriever:  Instance de JobRetriever (index déjà chargé).
            provider:   "anthropic" ou "openai". Si None, lit config.LLM_PROVIDER.
            llm_model:  Modèle à utiliser. Si None, déduit du provider.
            api_key:    Clé API. Si None, lit depuis .env / variables d'env.
        """
        self.retriever = retriever
        self.provider  = (provider or LLM_PROVIDER).lower()

        if self.provider == "anthropic":
            self.llm_model = llm_model or ANTHROPIC_MODEL
            key = api_key or ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")
            if not key:
                raise ValueError(
                    "Clé API Anthropic manquante — définis ANTHROPIC_API_KEY dans .env\n"
                    "Ou utilise : JobRAG(retriever, provider='openai')"
                )
            import anthropic as _anthropic
            self.client = _anthropic.Anthropic(api_key=key)

        elif self.provider == "openai":
            self.llm_model = llm_model or OPENAI_MODEL
            key = api_key or OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
            if not key:
                raise ValueError(
                    "Clé API OpenAI manquante — définis OPENAI_API_KEY dans .env"
                )
            from openai import OpenAI as _OpenAI
            self.client = _OpenAI(api_key=key)

        else:
            raise ValueError(f"Provider inconnu : {self.provider!r}. Utilise 'anthropic' ou 'openai'.")

    def ask(
        self,
        question: str,
        top_k: int = 5,
        filters: dict = None,
        verbose: bool = False,
    ) -> dict:
        """Répond à une question en langage naturel sur le corpus d'offres.

        Pipeline :
            1. Retrieval sémantique (JobRetriever.search)
            2. Construction du prompt avec contexte
            3. Génération via Claude API
            4. Retour structuré avec sources et scores

        Args:
            question: Question en langage naturel (FR ou EN).
            top_k:    Nombre d'offres à inclure dans le contexte.
            filters:  Filtres optionnels sur les métadonnées (cf. retriever.search).
            verbose:  Si True, affiche le prompt et la réponse dans le terminal.

        Returns:
            {
                "answer":  str,        # Réponse générée par le LLM
                "sources": list[dict], # Offres utilisées comme contexte
                "scores":  list[float],# Scores de pertinence des sources
                "no_relevant_docs": bool, # True si aucune offre au seuil
            }
        """
        # 1. Retrieval
        results = self.retriever.search(question, top_k=top_k, filters=filters)

        # Cas : aucun document au-dessus du seuil de similarité
        if not results:
            return {
                "answer": (
                    "Je n'ai trouvé aucune offre dans le corpus qui corresponde "
                    "suffisamment à votre question. Essayez avec des termes différents "
                    "ou reformulez votre recherche."
                ),
                "sources": [],
                "scores": [],
                "no_relevant_docs": True,
            }

        # 2. Construction du contexte et du prompt
        context = format_context(results)
        user_message = build_prompt(question, context)

        if verbose:
            print("=== CONTEXTE ENVOYÉ AU LLM ===")
            print(context)
            print()
            print("=== QUESTION ===")
            print(question)
            print()

        # 3. Appel LLM (Anthropic ou OpenAI)
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.llm_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            answer = response.content[0].text

        else:  # openai
            response = self.client.chat.completions.create(
                model=self.llm_model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                temperature=0.3,
            )
            answer = response.choices[0].message.content

        if verbose:
            print("=== RÉPONSE ===")
            print(answer)

        # 4. Retour structuré
        return {
            "answer": answer,
            "sources": results,
            "scores": [r["score"] for r in results],
            "no_relevant_docs": False,
        }

    def ask_pretty(self, question: str, **kwargs) -> None:
        """Affiche la réponse formatée dans le terminal (usage interactif)."""
        print(f'\nQuestion : "{question}"')
        print("-" * 60)

        result = self.ask(question, **kwargs)

        print("Réponse :")
        print(result["answer"])

        if result["sources"]:
            print()
            print("Sources utilisées :")
            for r in result["sources"]:
                print(
                    f"  [{r['rank']}] score={r['score']:.3f} | "
                    f"{r['title'][:50]} — {r['company'][:25]}"
                )
        print("-" * 60)


# ---------------------------------------------------------------------------
# CLI interactif — python -m src.rag
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, warnings, logging
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    from dotenv import load_dotenv
    load_dotenv()

    from src.retriever import JobRetriever

    parser = argparse.ArgumentParser(
        prog="python -m src.rag",
        description="JobRAG — Assistant interactif pour les offres IT en Suisse romande",
    )
    parser.add_argument(
        "--provider", "-p",
        default=LLM_PROVIDER,
        choices=["anthropic", "openai"],
        help=f"Backend LLM (défaut : {LLM_PROVIDER})",
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int, default=5, metavar="N",
        help="Nombre d'offres dans le contexte (défaut : 5)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Afficher le contexte envoyé au LLM",
    )
    args = parser.parse_args()

    # Chargement de l'index FAISS et du modèle d'embedding depuis data/vectorstore/.
    # L'opération est silencieuse côté sentence-transformers (warnings filtrés).
    print("Chargement de l'index vectoriel...", end=" ", flush=True)
    retriever = JobRetriever()
    print("OK")

    rag = JobRAG(retriever, provider=args.provider)
    top_k   = args.top_k
    verbose = args.verbose  # modifiable à chaud via la commande "verbose"

    print(f"\nJobRAG — Offres IT Suisse romande")
    print(f"Provider : {rag.provider}  |  Modele : {rag.llm_model}  |  Top-K : {top_k}")
    print("Commandes : exit  verbose  top N  help")
    print("-" * 60)

    # Boucle REPL principale.
    # KeyboardInterrupt (Ctrl-C) et EOFError (stdin fermé en mode pipe/test)
    # déclenchent une sortie propre sans stack trace.
    while True:
        try:
            question = input("\nQuestion > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAu revoir !")
            break

        if not question:
            continue

        cmd = question.lower()

        # --- Commandes de contrôle du REPL ---

        if cmd in ("exit", "quit", "q", "bye"):
            print("Au revoir !")
            break

        if cmd in ("help", "aide", "?", "h"):
            print(
                "\nCommandes disponibles :\n"
                "  exit        — Quitter\n"
                "  verbose     — Activer / desactiver le contexte LLM\n"
                "  top N       — Changer le nombre de resultats (ex: top 3)\n"
                "  help        — Afficher cette aide\n"
            )
            continue

        if cmd == "verbose":
            # Bascule l'affichage du contexte envoyé au LLM sans relancer le pipeline.
            verbose = not verbose
            print(f"Mode verbose : {'active' if verbose else 'desactive'}.")
            continue

        if cmd.startswith("top "):
            # Permet de comparer la qualité des réponses selon la taille du contexte
            # sans quitter la session (ex : top 3 vs top 10).
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                top_k = int(parts[1])
                print(f"Top-K regle a {top_k}.")
            else:
                print("Usage : top N  (ex: top 3)")
            continue

        # --- Pipeline RAG (retrieval + génération) ---
        rag.ask_pretty(question, top_k=top_k, verbose=verbose)
