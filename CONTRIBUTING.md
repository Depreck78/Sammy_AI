# Contributing

Thanks for helping improve Sammy.

## Local Setup

```bash
./setup.sh
sammy
```

For frontend-only work:

```bash
cd frontend
npm install
npm run dev
```

For backend work:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 3131
```

## Checks

Run these before opening a pull request:

```bash
cd frontend && npm run build
cd ..
PYTHONPATH=backend .venv/bin/python -m compileall backend/app backend/tools
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests
```

## Secrets

Do not commit local credentials, OAuth tokens, SQLite databases, generated encryption keys, uploads, logs, `.env` files, `.venv`, `node_modules`, or frontend build output.

Sammy stores runtime data in `~/.sammy` by default so it can stay outside the repository.
