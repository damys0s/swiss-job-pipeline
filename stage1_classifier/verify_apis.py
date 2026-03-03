"""
Phase 0 — Vérification des API
Teste chaque API avec un appel minimal pour confirmer que les clés fonctionnent.
Usage : python verify_apis.py
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def test_openai():
    """Test OpenAI API avec un appel minimal."""
    print("\n--- Test OpenAI API ---")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-..."):
        print(f"{FAIL} OPENAI_API_KEY non configurée dans .env")
        return False

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Réponds juste: OK"}],
                "max_tokens": 5,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
            print(f"{PASS} OpenAI fonctionne — réponse: '{reply}'")
            # Vérifier les crédits restants (approximatif)
            print(f"   Modèle utilisé: {data['model']}")
            print(f"   Tokens utilisés: {data['usage']['total_tokens']}")
            return True
        elif resp.status_code == 401:
            print(f"{FAIL} Clé API invalide (401)")
        elif resp.status_code == 429:
            print(f"{FAIL} Rate limit ou crédits épuisés (429)")
        else:
            print(f"{FAIL} Erreur {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"{FAIL} Erreur de connexion: {e}")
        return False


def test_adzuna():
    """Test Adzuna API avec une recherche minimale."""
    print("\n--- Test Adzuna API ---")
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or app_id == "your_app_id":
        print(f"{FAIL} ADZUNA_APP_ID non configurée dans .env")
        return False
    if not app_key or app_key == "your_app_key":
        print(f"{FAIL} ADZUNA_APP_KEY non configurée dans .env")
        return False

    try:
        resp = requests.get(
            "https://api.adzuna.com/v1/api/jobs/ch/search/1",
            params={
                "app_id": app_id,
                "app_key": app_key,
                "what": "data engineer",
                "where": "Lausanne",
                "results_per_page": 1,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = data.get("count", 0)
            print(f"{PASS} Adzuna fonctionne — {count} offres trouvées pour 'data engineer' à Lausanne")
            if data.get("results"):
                first = data["results"][0]
                print(f"   Exemple: {first.get('title', 'N/A')} @ {first.get('company', {}).get('display_name', 'N/A')}")
            return True
        elif resp.status_code == 401:
            print(f"{FAIL} Clé API invalide (401)")
        else:
            print(f"{FAIL} Erreur {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"{FAIL} Erreur de connexion: {e}")
        return False


def test_serpapi():
    """Test SerpApi avec une recherche Google Jobs minimale."""
    print("\n--- Test SerpApi ---")
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key or api_key == "your_serpapi_key":
        print(f"{FAIL} SERPAPI_KEY non configurée dans .env")
        return False

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_jobs",
                "q": "data engineer Lausanne",
                "hl": "fr",
                "gl": "ch",
                "api_key": api_key,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs_results", [])
            print(f"{PASS} SerpApi fonctionne — {len(jobs)} offres trouvées")
            if jobs:
                first = jobs[0]
                print(f"   Exemple: {first.get('title', 'N/A')} @ {first.get('company_name', 'N/A')}")
            # Vérifier crédits restants
            account = data.get("search_metadata", {})
            print(f"   Search ID: {account.get('id', 'N/A')}")
            return True
        elif resp.status_code == 401:
            print(f"{FAIL} Clé API invalide (401)")
        elif resp.status_code == 429:
            print(f"{FAIL} Quota épuisé (429)")
        else:
            print(f"{FAIL} Erreur {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"{FAIL} Erreur de connexion: {e}")
        return False


def test_indeed_rss():
    """Test Indeed RSS — vérification si le flux est encore actif."""
    print("\n--- Test Indeed RSS ---")
    try:
        import feedparser
        url = "https://emplois.indeed.com/rss?q=data+engineer&l=Lausanne"
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            print(f"{WARN} Indeed RSS semble inactif ou bloqué pour la Suisse")
            print(f"   Erreur: {feed.bozo_exception}")
            print(f"   → Ce n'est pas bloquant. On compensera avec Adzuna + SerpApi.")
            return False

        if feed.entries:
            print(f"{PASS} Indeed RSS fonctionne — {len(feed.entries)} offres trouvées")
            first = feed.entries[0]
            print(f"   Exemple: {first.get('title', 'N/A')}")
            return True
        else:
            print(f"{WARN} Indeed RSS répond mais 0 résultats")
            print(f"   → Probablement désactivé pour cette région. Non bloquant.")
            return False

    except ImportError:
        print(f"{FAIL} feedparser non installé — lance: pip install feedparser")
        return False
    except Exception as e:
        print(f"{FAIL} Erreur: {e}")
        return False


def main():
    print("=" * 50)
    print("VÉRIFICATION DES API — Phase 0")
    print("=" * 50)

    results = {
        "OpenAI": test_openai(),
        "Adzuna": test_adzuna(),
        "SerpApi": test_serpapi(),
        "Indeed RSS": test_indeed_rss(),
    }

    print("\n" + "=" * 50)
    print("RÉSUMÉ")
    print("=" * 50)
    all_critical_ok = True
    for name, ok in results.items():
        status = PASS if ok else (WARN if name == "Indeed RSS" else FAIL)
        print(f"  {status} {name}")
        if not ok and name != "Indeed RSS":
            all_critical_ok = False

    if all_critical_ok:
        print(f"\n{PASS} Toutes les API critiques fonctionnent. Tu peux passer à la Phase 1.")
    else:
        print(f"\n{FAIL} Corrige les erreurs ci-dessus avant de continuer.")
        sys.exit(1)


if __name__ == "__main__":
    main()
