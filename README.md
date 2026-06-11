# Inventory Management System

Bilingual (English/Arabic) cement inventory tracking with distribution management, low-stock alerts, charts, and user authentication.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure (optional)
cp .env.example .env
# Edit .env with your SMTP settings and admin credentials

# 3. Run
python run.py
# or
uvicorn inventory_app:app --host 0.0.0.0 --port 8100
# or
python -m inventory_app
```

Open http://localhost:8100 — login with `admin` / `admin123`.

## Install as Package

```bash
pip install -e .
inventory
```

## Configuration

Create a `.env` file in the project root:

```
ADMIN_USER=admin
ADMIN_PASS=yourpassword
JWT_SECRET=random-secret-string
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
ALERT_EMAILS=alert@example.com
WEBHOOK_URL=https://hooks.example.com/alert
```

## Default Login

- Username: `admin`
- Password: `admin123`

Change via `ADMIN_USER` / `ADMIN_PASS` in `.env`.

## Branches

- `/bani/` — Bani Suwayf branch
- `/alex/` — Alexandria branch
