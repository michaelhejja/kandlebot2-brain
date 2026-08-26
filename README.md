# kandlebot2-brain

Python "brain" for the kandlebot trading system. The Vue.js frontend displays
data ingested by a Node.js server, which computes indicators every minute and
detects candidate trade signals. When a signal fires, the Node server calls
this service's `/analyze` endpoint, which runs further analysis (a trained
classifier, or a heuristic fallback until a model is trained) and returns an
accept/reject decision to help filter out bad signals.

## Architecture

```
Vue.js frontend  <-->  Node.js server (ingestion + indicators + signal detection)
                              |
                              | POST /analyze
                              v
                     Python brain (this repo, Flask)
```

## Project layout

- `brain_app/` — Flask application (routes, config, feature extraction, model wrapper)
- `training/train_model.py` — trains the classifier from a labeled historical CSV
- `models/` — trained model artifacts (`model.joblib`), gitignored by default
- `tests/` — pytest suite for the API
- `wsgi.py` — entry point used locally and by gunicorn on Heroku

## API contract

### `GET /health`
```json
{ "status": "ok", "model_loaded": true }
```

### `POST /analyze`
Request body (sent by the Node.js server when a signal fires):
```json
{
  "symbol": "ETHUSD",
  "signal_type": "long",
  "indicators": {
    "rsi": 62.5,
    "ema_9": 3400.1,
    "ema_21": 3390.5,
    "macd": 1.2,
    "macd_signal": 0.9,
    "atr": 5.4,
    "volume": 1234.0
  }
}
```
Response:
```json
{
  "symbol": "ETHUSD",
  "signal_type": "long",
  "decision": "accept",
  "confidence": 0.82,
  "model_used": "trained"
}
```

Update `FEATURE_COLUMNS` in [brain_app/features.py](brain_app/features.py) to match
whatever indicator keys your Node.js server actually sends. Optional auth: set
`BRAIN_API_KEY` and have the Node server send it as the `X-API-Key` header.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
python wsgi.py   # runs on http://localhost:5000
```

## Training the classifier

Once you have a labeled historical CSV (one row per past signal, one column
per `FEATURE_COLUMNS` entry, plus a `label` column: `1` = good signal, `0` =
bad signal):

```bash
python -m training.train_model --csv data/labeled_signals.csv --out models/model.joblib
```

Restart the app afterward (or redeploy) so it picks up the new model file.

## Deploying to Heroku

```bash
heroku create your-app-name
heroku config:set BRAIN_API_KEY=some-long-random-secret
git push heroku main
heroku ps:scale web=1
heroku logs --tail
```

The `Procfile` runs `gunicorn wsgi:app`. `.python-version` pins the Python
runtime. If you commit a trained `models/model.joblib`, either remove it from
`.gitignore` or track it with Git LFS (`*.joblib` is already configured in
`.gitattributes`) so Heroku's build includes it.

