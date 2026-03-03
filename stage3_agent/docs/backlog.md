# Backlog — améliorations à faire après les phases principales

## Filtrage géographique (priorité haute)
- **Problème** : le collector remonte des offres de Bâle, Carouge, Genf (nom allemand) qui ne correspondent pas aux zones souhaitées
- **Solution** : ajouter un filtre post-collecte dans `scorer.py` ou `pipeline.py` :
  - Normaliser les noms de villes (Genf → Geneva, Genève → Geneva)
  - Garder uniquement les offres dont la localisation contient un mot de la liste : `["lausanne", "geneva", "genève", "genf", "nyon", "morges", "vaud", "romand"]`
  - Exclure explicitement : `["bâle", "basel", "zürich", "zurich", "bern", "berne"]`
- **Fichier à modifier** : `src/collector.py` → méthode `_filter_by_location()` à ajouter, ou `config/settings.py` → `LOCATION_WHITELIST` + `LOCATION_BLACKLIST`

## Autres idées (basse priorité)
- Dédoublonner les offres identiques publiées sur Adzuna ET SerpApi (même titre + même entreprise)
- Ajouter un lien "voir l'offre" plus direct dans l'email (les URLs SerpApi pointent vers Google Jobs, pas l'offre directe)
