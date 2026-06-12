#!/bin/bash
# Lance le backend. Copie .env.example en .env et remplis les valeurs d'abord.
cd "$(dirname "$0")"
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
