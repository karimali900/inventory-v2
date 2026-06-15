# Inventory Management System

Bilingual (English/Arabic) cement inventory tracking with distribution management, fleet tracking, real-time monitoring, and user authentication.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your credentials (ADMIN_PASS, JWT_SECRET are required)

# 3. Run
python run.py
```

Open http://localhost:8100

> **IMPORTANT**: You must set `JWT_SECRET` and `ADMIN_PASS` in `.env` before running.

## Installation

### Option A: Direct Python

```bash
pip install -r requirements.txt
python run.py
```

### Option B: Install as Package

```bash
pip install -e .
inventory
```

### Option C: Docker

```bash
docker build -t inventory-system .
docker run -p 8100:8100 inventory-system
```

### Option D: Heroku

```bash
git push heroku main
```

Procfile and runtime.txt are pre-configured.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET` | Yes | Secret key for JWT tokens |
| `ADMIN_USER` | No | Admin username (default: admin) |
| `ADMIN_PASS` | Yes | Admin password |
| `SMTP_SERVER` | No | SMTP server (default: smtp.gmail.com) |
| `SMTP_PORT` | No | SMTP port (default: 465) |
| `SMTP_USER` | No | SMTP email address |
| `SMTP_PASS` | No | SMTP app password |
| `ALERT_EMAILS` | No | Comma-separated alert recipients |
| `WEBHOOK_URL` | No | Webhook URL(s) for alerts |
| `CHECK_EMAIL_PASS` | No | Fallback password for inbox checking |

## Default Login

Username: `admin`  
Password: Set via `ADMIN_PASS` in `.env`

## Branches

- `/bani/` — Bani Suwayf branch
- `/alex/` — Alexandria branch
- `/control-room` — Central monitoring dashboard

## Documentation

Full documentation: [DOCUMENTATION.pdf](./DOCUMENTATION.pdf)
