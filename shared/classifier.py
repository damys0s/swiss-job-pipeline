"""
classifier.py — Module de classification d'offres d'emploi (module partagé)
===========================================================================
Source canonique : stage1_classifier/src/classify.py

Utilisé par :
  - stage1_classifier/ : entraînement, évaluation, préparation des données
  - stage2_rag/        : filtrage du corpus avant indexation FAISS
  - stage3_agent/      : classification des nouvelles offres quotidiennes

Usage en import :
    from shared.classifier import JobClassifier
    clf = JobClassifier()
    result = clf.classify(title="BI Developer", company="Nestlé", location="Vevey", description="...")

Usage CLI (depuis la racine du repo) :
    python -m shared.classifier "Senior Data Engineer" "UBS" "Genève" "Pipelines Spark, dbt, Airflow"
"""

import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI

# --- Config ---------------------------------------------------------------

# shared/ est à la racine du repo → parent.parent donne la racine du repo.
_BASE_DIR = Path(__file__).resolve().parent.parent

# Chemin vers le fichier d'état généré par stage1_classifier/src/finetuning.py.
# Ce fichier contient le model_id du modèle fine-tuné.
DEFAULT_STATE = _BASE_DIR / "stage1_classifier" / "results" / "training_logs" / "finetune_state.json"

# CRITIQUE : ce prompt système doit être IDENTIQUE à celui de prepare.py.
# Toute divergence entre le prompt d'entraînement et le prompt d'inférence
# dégrade les performances du modèle fine-tuné.
SYSTEM_PROMPT = (
    "Tu es un classificateur d'offres d'emploi IT en Suisse romande. "
    "Classe chaque offre dans exactement une catégorie parmi : "
    "DATA_ENGINEERING, BI_ANALYTICS, NOT_RELEVANT. "
    "Réponds uniquement avec le nom de la catégorie, rien d'autre."
)

VALID_LABELS = {"DATA_ENGINEERING", "BI_ANALYTICS", "NOT_RELEVANT"}

# Sous-ensemble des labels considérés comme "pertinents" pour les projets aval.
# Utilisé par is_relevant() pour un filtrage binaire rapide.
RELEVANT_LABELS = {"DATA_ENGINEERING", "BI_ANALYTICS"}


class JobClassifier:
    """Classificateur d'offres d'emploi basé sur GPT-4o-mini fine-tuné.

    Conçu pour être réutilisé par plusieurs projets (RAG, agent de monitoring).
    Le model_id est résolu automatiquement depuis finetune_state.json, ou peut
    être passé explicitement pour faciliter les tests avec d'autres modèles.
    """

    def __init__(self, model_id: str = None, api_key: str = None):
        """
        Args:
            model_id: ID du modèle fine-tuné (ft:gpt-4o-mini-...).
                      Si None, lu depuis finetune_state.json.
            api_key:  Clé API OpenAI. Si None, lu depuis OPENAI_API_KEY.
        """
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

        if model_id:
            self.model_id = model_id
        else:
            self.model_id = self._load_model_id()

    def _load_model_id(self) -> str:
        """Charge le model_id depuis finetune_state.json."""
        if not DEFAULT_STATE.exists():
            raise FileNotFoundError(
                f"{DEFAULT_STATE} introuvable. "
                "Passe model_id explicitement ou lance d'abord stage1_classifier."
            )
        state = json.loads(DEFAULT_STATE.read_text())
        model_id = state.get("model_id")
        if not model_id:
            raise ValueError("model_id absent du state. Le fine-tuning est-il terminé ?")
        return model_id

    def classify(self, title: str, company: str = "", location: str = "",
                 description: str = "") -> str:
        """Classifie une offre d'emploi et retourne son label.

        Paramètres temperature=0 et max_tokens=20 :
          - temperature=0 : réponse déterministe pour la reproductibilité.
            Le fine-tuning a rendu le modèle confiant sur ces 3 labels ;
            une temperature > 0 ne ferait qu'introduire du bruit.
          - max_tokens=20 : le label est un seul mot (ex: "DATA_ENGINEERING"),
            limiter les tokens évite les réponses verboses et réduit le coût.

        En cas de réponse inattendue (hors VALID_LABELS), on retourne NOT_RELEVANT
        comme valeur sûre par défaut — mieux vaut manquer une offre pertinente
        que d'inclure une offre non pertinente dans le RAG.
        """
        user_content = self._format_input(title, company, location, description)

        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=20,
        )

        pred = response.choices[0].message.content.strip().upper()
        # Nettoyage défensif : certains modèles ajoutent un saut de ligne ou
        # du texte après le label (ex: "DATA_ENGINEERING\n(explication)")
        pred = pred.split("\n")[0].strip()

        if pred not in VALID_LABELS:
            return "NOT_RELEVANT"

        return pred

    def classify_batch(self, jobs: list[dict], delay: float = 0.2) -> list[dict]:
        """Classifie une liste d'offres séquentiellement avec rate limiting.

        Args:
            jobs: Liste de dicts avec clés: title, company, location, description
            delay: Pause entre requêtes pour respecter le rate limit OpenAI.
                   0.2s correspond à ~5 req/s, bien en dessous des limites tier-1.

        Returns:
            Liste de dicts enrichis avec les clés: label, is_relevant
        """
        results = []
        total = len(jobs)

        for i, job in enumerate(jobs, 1):
            label = self.classify(
                title=job.get("title", ""),
                company=job.get("company", ""),
                location=job.get("location", ""),
                description=job.get("description", ""),
            )

            # Enrichissement du dict original sans le modifier (dict unpacking)
            result = {**job, "label": label, "is_relevant": label in RELEVANT_LABELS}
            results.append(result)

            # Affichage de progression sur une seule ligne (écrasée à chaque update)
            if i % 10 == 0 or i == total:
                print(f"  Classification: {i}/{total}", end="\r")

            # Pas de délai après le dernier élément
            if delay and i < total:
                time.sleep(delay)

        print(f"  Classification: {total}/{total} terminé")
        return results

    def is_relevant(self, job: dict) -> bool:
        """Retourne True si l'offre est pertinente (DATA_ENGINEERING ou BI_ANALYTICS).

        Méthode de convenance pour les cas d'usage binaires (filtrage simple).
        Évite d'avoir à gérer VALID_LABELS dans le code appelant.
        """
        label = self.classify(
            title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
            description=job.get("description", ""),
        )
        return label in RELEVANT_LABELS

    @staticmethod
    def _format_input(title, company, location, description):
        """Formate l'input exactement comme dans le training set (prepare.py).

        La description est tronquée à 200 mots pour être cohérente avec les
        données d'entraînement. Une incohérence de format entre train et inférence
        peut dégrader les performances même si le contenu est identique.
        """
        words = description.split()
        if len(words) > 200:
            description = " ".join(words[:200])

        parts = [f"Titre: {title}"]
        if company:
            parts.append(f"Entreprise: {company}")
        if location:
            parts.append(f"Localisation: {location}")
        if description:
            parts.append(f"Description: {description}")

        return "\n".join(parts)


# --- CLI ------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m shared.classifier <title> [company] [location] [description]")
        sys.exit(1)

    title = sys.argv[1]
    company = sys.argv[2] if len(sys.argv) > 2 else ""
    location = sys.argv[3] if len(sys.argv) > 3 else ""
    description = sys.argv[4] if len(sys.argv) > 4 else ""

    clf = JobClassifier()
    label = clf.classify(title, company, location, description)

    print(f"Titre:    {title}")
    print(f"Label:    {label}")
    print(f"Pertinent: {'oui' if label in RELEVANT_LABELS else 'non'}")
