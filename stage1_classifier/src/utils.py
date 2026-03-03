"""
utils.py — Utilitaires partagés entre les scripts de collecte
=============================================================
Centralise les fonctions dupliquées dans collect.py, collect_dba.py
et collect_serpapi.py pour éviter la divergence silencieuse entre copies.

Import :
    from src.utils import make_job_id, normalize_text, retry_request
"""

import hashlib
import logging
import time

import requests

log = logging.getLogger(__name__)


def make_job_id(source: str, title: str, company: str) -> str:
    """Génère un identifiant déterministe à partir de la source, du titre et de l'entreprise.

    Utilise MD5 (non cryptographique, mais rapide) tronqué à 12 caractères.
    La probabilité de collision sur ~10 000 offres est négligeable (~0.0001%).
    Ce format est préféré à un UUID aléatoire pour garantir la reproductibilité :
    le même job collecté deux fois génère le même ID, facilitant la déduplication.
    """
    raw = f"{source}_{title}_{company}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def normalize_text(text: str) -> str:
    """Normalise un texte pour la déduplication : minuscules + espaces unifiés.

    Ne fait pas de suppression d'accents intentionnellement — deux titres
    avec graphies différentes (ex: "Ingénieur" vs "Ingenieur") sont considérés
    distincts pour éviter les faux positifs de déduplication.
    """
    if not text:
        return ""
    return " ".join(text.lower().split())


def retry_request(func, max_retries: int = 3, base_delay: int = 2):
    """Exécute une fonction de requête avec backoff exponentiel en cas d'échec.

    Stratégie : 3 tentatives avec délais de 2s, 4s, 8s.
    Retourne None si toutes les tentatives échouent, permettant à l'appelant
    de décider de l'action (arrêter la pagination, ignorer, etc.).

    Args:
        func:        Callable sans arguments qui effectue la requête HTTP.
        max_retries: Nombre maximum de tentatives (défaut : 3).
        base_delay:  Délai initial en secondes, doublé à chaque tentative.

    Returns:
        Le résultat de func() en cas de succès, None en cas d'échec total.
    """
    for attempt in range(max_retries):
        try:
            return func()
        except requests.exceptions.RequestException as e:
            wait = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                log.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"All {max_retries} attempts failed: {e}")
                return None
