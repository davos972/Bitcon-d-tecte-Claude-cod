# Déploiement permanent (Render)

Hébergement 24/7 du backend (FastAPI, always-on + disque persistant) et du
frontend (Expo web statique) via le blueprint [`render.yaml`](render.yaml).

> Les étapes qui demandent un compte ou des identifiants sont à faire par toi —
> je ne peux pas créer de compte ni me connecter à ta place.

## 1. Compte Render + accès au dépôt
1. Crée un compte sur https://render.com (connexion via GitHub recommandée).
2. Autorise Render à accéder au dépôt `davos972/Bitcon-d-tecte-Claude-cod`.

## 2. Déployer le blueprint
1. Dashboard Render → **New → Blueprint**.
2. Sélectionne le dépôt et la branche **`claude/bitcoin-detector-refactor-07zsx4`**.
3. Render lit `render.yaml` et propose deux services :
   `btc-markov-backend` (web) et `btc-markov-frontend` (static).
4. Il demande les variables marquées `sync: false` :
   - **`CRYPTOCOMPARE_API_KEY`** → copie la valeur depuis `backend/.env` (local).
   - **`EXPO_PUBLIC_BACKEND_URL`** → laisse vide pour l'instant (étape 4).
   - **`TRACKER_API_KEY`** (backend) **et** **`EXPO_PUBLIC_TRACKER_KEY`** (frontend)
     → **la MÊME valeur** dans les deux (invente une longue chaîne aléatoire).
     Protège l'écriture du tracker (POST/DELETE) contre les abus publics. Laissées
     vides, l'écriture reste ouverte à tous — à éviter en prod. Changer la clé =
     reconstruire le frontend (elle est intégrée au build).
5. Lance la création. Le backend obtient un disque persistant monté sur `/data`
   (le tracker y est déjà dirigé via `TRACKER_DATA_DIR=/data`).

## 3. Récupérer l'URL du backend
Une fois `btc-markov-backend` déployé (statut *Live*), note son URL, du type
`https://btc-markov-backend.onrender.com`. Vérifie `…/health` → `{"status":"ok"}`.

## 4. Brancher le frontend sur le backend
1. Service `btc-markov-frontend` → **Environment** → mets
   `EXPO_PUBLIC_BACKEND_URL` = l'URL du backend (étape 3, **sans** slash final).
2. **Manual Deploy → Clear build cache & deploy** (l'URL est intégrée au build).
3. Ouvre l'URL du frontend (`https://btc-markov-frontend.onrender.com`) sur ton
   téléphone — c'est ton lien **permanent**.

## 5. (Optionnel) Migrer l'historique existant
Le store de prod démarre vide. Tes appareils repousseront leur cache local au
premier lancement. Pour transférer d'un coup les ~228 prédictions déjà sur le PC,
demande-moi : je POST le contenu de `backend/tracker_store.json` (local) vers
`…/api/tracker` de la prod (la fusion gère les doublons).

## Notes
- **Sécurité** : `CRYPTOCOMPARE_API_KEY` n'est jamais commitée (dans `.env`,
  gitignoré) — elle se renseigne uniquement dans le dashboard Render.
- **Changement d'URL backend** → reconstruire le frontend (l'URL est figée au
  build). Sinon, plus besoin de tunnels Cloudflare ni de garder le PC allumé.
- **Prix indicatif** : backend Starter ~7 $/mo + disque 1 Go ~0,25 $/mo ;
  frontend statique gratuit.
