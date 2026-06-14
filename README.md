# BTC Up/Down — Prédicteur Markov pour Polymarket

Une app qui prédit si la **prochaine bougie BTC** (5M / 15M / 1H) clôturera
**UP ou DOWN**, en mesurant les **fréquences historiques réelles** (chaîne de
Markov), pour aider à trader les marchés *"BTC Up or Down"* de Polymarket — avec
un tracker de performance scoré sur la **résolution officielle Polymarket**.

> ⚠️ Cette app ne garantit aucun gain. C'est un **instrument de mesure honnête** :
> elle affiche "NO EDGE" quand la probabilité est ~50%, et son tracker prouvera
> (ou non) l'existence d'un edge. **Ne rien conclure avant 100+ prédictions sur
> le 15M.** Voir [`CLAUDE.md`](CLAUDE.md) pour le concept complet et les leçons.

## Architecture

```
Frontend (Expo / React Native)  ──EXPO_PUBLIC_BACKEND_URL──▶  Backend (FastAPI)
  usePrice  (WS RTDS + fallback)                                /api/price
  useMarkov (3 timeframes)                                      /api/markov
  useTracker(réconciliation)                                    /api/poly_resolution
                                                                     │
        CryptoCompare ──▶ Coinbase (secours) ──▶ Gamma API Polymarket
```

## Prérequis

- **Python ≥ 3.11** (`py` sur Windows)
- **Node ≥ 18** + npm
- Une **clé API CryptoCompare** (gratuite) — *désormais nécessaire* : les
  endpoints d'historique renvoient `401 API key required` sans clé. Sans elle,
  l'app retombe sur Coinbase (historique plus court, stats plus faibles).

## Démarrage — Windows / PowerShell

### 1. Backend

```powershell
cd backend
Copy-Item .env.example .env        # puis remplis CRYPTOCOMPARE_API_KEY
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Ou simplement : `./start.ps1`

Vérifie : <http://localhost:8000/health> doit renvoyer `{"status":"ok"}`.

### 2. Frontend

```powershell
cd frontend
npm install
# Pointe le frontend vers le backend (defaut http://localhost:8000)
$env:EXPO_PUBLIC_BACKEND_URL = "http://localhost:8000"
npx expo start --web
```

> En production : **ne jamais** laisser `localhost` dans
> `EXPO_PUBLIC_BACKEND_URL` — utilise l'URL du backend déployé.

## Tests

```powershell
# Backend
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q

# Frontend
cd frontend
npm test          # tests unitaires de utils/cycle.ts
npm run typecheck # tsc --noEmit
```

## Endpoints backend

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness |
| `GET /api/price` | Prix BTC live (CryptoCompare → Coinbase) |
| `GET /api/markov?mode=15M&window=<utc_ts>` | Historique + calcul Markov (bougie en cours exclue) |
| `GET /api/poly_resolution?slug=btc-updown-15m-<ts>` | Verdict officiel Polymarket (lecture seule) |

`mode` ∈ `5M` / `15M` / `1H`. `window` = `floor(now / cycle) * cycle`.

## Variables d'environnement

| Variable | Où | Rôle |
|---|---|---|
| `CRYPTOCOMPARE_API_KEY` | backend | **Nécessaire** pour l'historique riche (sinon fallback Coinbase) |
| `EXPO_PUBLIC_BACKEND_URL` | frontend | URL du backend (jamais `localhost` en prod) |
| `MONGO_URL`, `DB_NAME` | backend | Optionnel — persistance serveur (non utilisé par défaut) |
