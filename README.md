# Pré-tri locatif

Prototype d'aide au tri des dossiers de candidature à une location.
L'outil vérifie la complétude des dossiers, extrait les chiffres clés des justificatifs,
signale les incohérences et applique les critères financiers du bien.
La décision finale reste au gestionnaire.

Données fictives uniquement. Règles et étapes du projet : voir `CLAUDE.md`.

## Installation (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # puis y mettre la clé API Anthropic
```

## Tests

```
python -m pytest
```
