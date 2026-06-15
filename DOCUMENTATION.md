# Inventory Management System — Complete Documentation

Version 2.0.0

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [User Interface](#5-user-interface)
6. [API Endpoints](#6-api-endpoints)
7. [Database Schema](#7-database-schema)
8. [Features](#8-features)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Overview

The Inventory Management System is a bilingual (English/Arabic) web application designed for cement inventory tracking across two branches (Bani Suwayf and Alexandria). It manages stock levels, distributions to stations, fleet tracking, customer orders, and provides real-time monitoring dashboards.

### Key Capabilities

- **Inventory Management**: Track stock levels, additions, distributions per item
- **Distribution Management**: Distribute items to stations with voucher tracking
- **Weight Difference Tracking**: Record short-weight deliveries and compute financial values
- **Fleet Management**: Track vehicle fleet status (registered, on-road, maintenance)
- **Customer Orders**: Accept/refuse customer orders with aggregation
- **Real-time Monitoring**: Central Control Room dashboard with live updates
- **Email Reports**: Send daily reports and low-stock alerts via email
- **Webhooks**: Send alerts to external systems
- **Chat & Calling**: Real-time chat with WebRTC audio/video calls
- **GPS Tracking**: Vehicle location tracking on OpenStreetMap
- **Cement News**: Scraped industry news from Google News

---

## 2. Architecture

### Tech Stack

- **Backend**: Python 3.12+, FastAPI (async), Uvicorn
- **Database**: SQLite3 (one database per branch + shared users database)
- **Frontend**: Server-rendered HTML with Tailwind CSS (CDN), Chart.js
- **Authentication**: JWT tokens (python-jose), bcrypt password hashing
- **Real-time**: WebSocket for chat and user presence
- **Email**: SMTP (Gmail app passwords)
- **Deployment**: Heroku-ready (Procfile), Docker-ready

### Directory Structure

```
inventory_v2/
├── inventory_app/
│   ├── __init__.py          # Main application (routes, auth, email, DB)
│   ├── __main__.py          # Entry point for `python -m inventory_app`
│   ├── static/              # Static assets (images, manifest, service worker)
│   ├── templates/           # HTML templates (5 files)
│   └── *.db                 # SQLite databases (auto-created)
├── distances/               # Distance/route data files
├── scripts/                 # Deployment scripts
├── uploads/                 # User-uploaded files
├── run.py                   # Simple launcher
├── requirements.txt         # Python dependencies
├── pyproject.toml           # Package configuration
├── Procfile                 # Heroku process definition
└── runtime.txt              # Python version for Heroku
```

### Three-Database Design

| Database | Purpose |
|----------|---------|
| `users.db` | Shared user accounts (auth) |
| `bani.db` | Bani Suwayf branch data |
| `alex.db` | Alexandria branch data |

This design keeps branches isolated while allowing the Control Room to aggregate across both.

---

## 3. Installation

### Prerequisites

- Python 3.12 or later
- pip (Python package manager)
- Git (for cloning)

### Platform-Specific Instructions

#### Windows

```powershell
# 1. Install Python from python.org (3.12+)
# 2. Open PowerShell or Command Prompt

git clone https://github.com/YOUR_USERNAME/inventory-system.git
cd inventory-system

# 3. Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Configure environment
copy .env.example .env
# Edit .env — set JWT_SECRET and ADMIN_PASS at minimum

# 6. Run
python run.py

# 7. Open http://localhost:8100
```

#### macOS / Linux

```bash
# 1. Install Python 3.12+
# macOS: brew install python@3.12
# Linux: sudo apt install python3 python3-pip python3-venv

git clone https://github.com/YOUR_USERNAME/inventory-system.git
cd inventory-system

# 2. Virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
nano .env  # Set JWT_SECRET and ADMIN_PASS

# 5. Run
python run.py
# Or:
uvicorn inventory_app:app --host 0.0.0.0 --port 8100
```

#### Docker

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "inventory_app:app", "--host", "0.0.0.0", "--port", "8100"]
```

```bash
docker build -t inventory-system .
docker run -p 8100:8100 -e JWT_SECRET=your-secret -e ADMIN_PASS=yourpass inventory-system
```

#### Heroku (Cloud)

```bash
heroku create your-app-name
heroku config:set JWT_SECRET=your-secret ADMIN_PASS=yourpass
git push heroku main
```

The included `Procfile` and `runtime.txt` handle deployment.

#### Install as CLI Command

```bash
pip install -e .
inventory  # Runs on port 8100 by default
```

---

## 4. Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET` | **Yes** | — | Cryptographic secret for JWT tokens |
| `ADMIN_PASS` | **Yes** | — | Initial admin user password |
| `ADMIN_USER` | No | `admin` | Admin username |
| `SMTP_SERVER` | No | `smtp.gmail.com` | SMTP server for email |
| `SMTP_PORT` | No | `465` | SMTP port (465=SSL, 587=TLS) |
| `SMTP_USER` | No | — | SMTP login email |
| `SMTP_PASS` | No | — | SMTP app password |
| `ALERT_EMAILS` | No | — | Comma-separated alert recipients |
| `WEBHOOK_URL` | No | — | Webhook URL(s) for alerts |
| `CHECK_EMAIL_PASS` | No | — | Fallback IMAP password for email checking |

### SMTP Setup (Gmail)

1. Enable 2-Factor Authentication on your Google account
2. Generate an App Password: Google Account → Security → App passwords
3. Set `SMTP_USER` to your Gmail address and `SMTP_PASS` to the 16-character app password

### First Run

On first startup, the application:
1. Creates `users.db` with the users table
2. Seeds an admin user (username from `ADMIN_USER`, password from `ADMIN_PASS`)
3. Creates `bani.db` and `alex.db` with all required tables
4. Seeds default fleet status categories (6 categories)

---

## 5. User Interface

### Login Page

Dark-themed login with username/password. Authentication persists via JWT token stored in localStorage (10-hour expiry).

### Branch Selection

Two-card layout to choose Bani Suwayf or Alexandria branch.

### Branch Dashboard (main SPA)

12 tabs organized in a horizontal navigation bar:

#### 📦 Inventory Tab
- Grid of item cards showing name, stock level, threshold
- Color-coded: normal (white), low (yellow), out-of-stock (red)
- Create/edit/delete items
- **Distribute** modal: send items to stations with truck, driver, voucher details
- Add stock modal
- Bar chart of stock levels (optional)

#### 📜 History Tab
- Global transaction history with pagination
- Filter by station name
- Shows additions, distributions with timestamps

#### 📋 Orders Tab
- Customer order management
- Create, accept, or refuse orders
- Aggregated view with grand total weight

#### 💰 Station Accounts Tab
- Per-station daily distribution summary
- Shows total weight delivered per station

#### 📊 Reports Tab
- Daily report with stock levels, additions, distributions
- Bar chart visualization
- Send report via email with rich HTML body
- Print-friendly layout

#### 📞 Contacts Tab
- Customer CRM with phone, WhatsApp integration
- Status tracking (available/pending)

#### 🛰️ GPS Tab
- Vehicle location tracking on OpenStreetMap
- Leaflet.js map with vehicle markers

#### 🚛 Fleet Status Tab
- Vehicle fleet management
- 6 fleet categories with counts
- Add/remove vehicles
- Update fleet status counts for the work day

#### 🏭 Cement News Tab
- Scraped news from Google News RSS (Arabic)
- Links to full articles

#### 🚛 Ostool Operations Tab
- Fleet operations aggregation
- Send fleet report via email
- Check inbox for fleet reports
- Rich text email editor with formatting toolbar

#### ⚖️ Weight Diffs Tab
- Weight difference tracking (short-weight deliveries)
- Voucher types: 60, 65, 70 tons
- Auto-calculated when actual weight < voucher quantity
- Configurable cement price per ton
- Total difference and total value stats
- Period filters: All / Today / Week / Month

#### 👥 Users Tab (admin only)
- Create, edit, delete users
- Assign roles: admin, editor, viewer

### Control Room (Monitoring Dashboard)

Real-time overview of both branches:
- Live clock
- Net stock totals per branch (with tooltip breakdown)
- Items count, alerts count
- Orders: completed ✓ / pending ⏳ (with tooltip per branch + station names)
- Online users
- Weight diffs value and quantity (with tooltip per branch and timestamp)
- Live bar cycling through: net stock items → distribution stations → registered trucks (per branch)
- Alert cards with critical/warning color coding
- Horizontal bar charts for stock levels (per item with unique colors)
- Voice reader for fleet status (text-to-speech)
- Auto-refresh every 15 seconds
- Audio alerts on new low-stock items

### Chat Panel

- Real-time chat across both branches
- WebRTC audio/video calling
- User online status
- Draggable panel with position persistence

---

## 6. API Endpoints

### Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/login` | No | Login, returns JWT token |
| GET | `/api/test` | No | Health check |

### Dashboard

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/dashboard-summary` | No | Combined data for both branches |
| GET | `/api/control-room-report` | No | Detailed report data |

### Inventory (per-branch, mounted at `/bani` and `/alex`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/items` | List all active items |
| POST | `/api/inventory/items` | Create item |
| GET | `/api/inventory/items/{id}` | Get item details |
| PUT | `/api/inventory/items/{id}` | Update item |
| DELETE | `/api/inventory/items/{id}` | Archive item |
| POST | `/api/inventory/items/{id}/restore` | Restore archived item |
| GET | `/api/inventory/items/archived` | List archived items |
| POST | `/api/inventory/items/{id}/add` | Add stock |
| GET | `/api/inventory/items/{id}/transactions` | Item transaction history |
| GET | `/api/inventory/items/{id}/distributions` | Item distributions |

### Distributions

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/inventory/items/{id}/distribute` | Create distribution |
| GET | `/api/inventory/distributions` | List all distributions |
| PUT | `/api/inventory/distributions/{id}/complete` | Complete distribution |
| GET | `/api/inventory/stations/search` | Search stations |
| GET | `/api/inventory/distributions/stats` | Distribution statistics |

### Customer Orders

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/orders` | List orders |
| POST | `/api/inventory/orders` | Create order |
| PUT | `/api/inventory/orders/{id}` | Accept/refuse order |
| GET | `/api/inventory/orders/aggregate` | Aggregated orders |

### Fleet & Vehicles

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/vehicles` | List vehicles |
| POST | `/api/inventory/vehicles` | Add vehicle |
| PUT | `/api/inventory/vehicles/{id}/location` | Update vehicle location |
| DELETE | `/api/inventory/vehicles/{id}` | Delete vehicle |
| GET | `/api/inventory/fleet-status` | Get fleet status |
| PUT | `/api/inventory/fleet-status` | Update fleet status |

### Weight Differences

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/weight-differences?period=` | List weight differences |
| GET | `/api/inventory/weight-differences/stats?period=` | Weight diff stats |

### Settings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/settings/{key}` | Get setting (e.g. cement_price) |
| PUT | `/api/inventory/settings` | Update setting |

### Email

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/inventory/send-report` | Send daily report |
| POST | `/api/inventory/ostool-operations/email` | Send Ostool report |
| POST | `/api/check-email` | Check inbox |

### Other

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/history` | Global history |
| GET | `/api/inventory/alerts` | Low stock alerts |
| GET | `/api/inventory/report` | Daily report data |
| GET | `/api/inventory/cement-news` | Cement industry news |
| GET | `/api/inventory/recipients` | Email recipients |
| POST | `/api/inventory/recipients` | Add recipient |
| DELETE | `/api/inventory/recipients/{id}` | Remove recipient |
| GET | `/api/inventory/contacts` | List contacts |
| POST | `/api/inventory/contacts` | Create contact |
| PUT | `/api/inventory/contacts/{id}` | Update contact |
| DELETE | `/api/inventory/contacts/{id}` | Delete contact |
| GET | `/api/inventory/deposits` | List deposits |
| POST | `/api/inventory/deposits` | Create deposit |
| DELETE | `/api/inventory/deposits/{id}` | Delete deposit |
| GET | `/api/inventory/ostool-operations` | List Ostool operations |
| GET | `/api/inventory/ostool-stations` | List Ostool stations |
| GET | `/api/inventory/ostool-attachments` | List attachments |
| POST | `/api/inventory/ostool-attachments` | Upload attachment |
| DELETE | `/api/inventory/ostool-attachments/{id}` | Delete attachment |
| GET | `/api/inventory/distances-search` | Search distance data |
| POST | `/api/upload` | File upload |
| GET | `/api/active-users` | Active users |
| GET | `/api/activity-log` | Activity log |

---

## 7. Database Schema

### users.db

**users** — Shared authentication
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| username | TEXT | Unique |
| password | TEXT | bcrypt hash |
| role | TEXT | admin / editor / viewer |
| created_at | TEXT | ISO datetime |

### bani.db / alex.db (identical schemas)

**items** — Inventory items
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| name | TEXT | Unique, item name |
| description | TEXT | Optional |
| current_stock | REAL | Current stock level (rounded to 2 decimals) |
| beginning_stock | REAL | Opening stock for the day |
| warning_threshold | REAL | Alert threshold |
| archived | INTEGER | 0=active, 1=archived |
| created_at | TEXT | ISO datetime |
| updated_at | TEXT | ISO datetime |

**transactions** — Stock movements
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| item_id | INTEGER | FK → items.id |
| type | TEXT | addition / distribution |
| quantity | REAL | Amount |
| previous_stock | REAL | Stock before |
| new_stock | REAL | Stock after |
| note | TEXT | Station name for distributions |
| distribution_id | INTEGER | FK → distributions.id (nullable) |
| created_at | TEXT | ISO datetime |

**distributions** — Shipments to stations
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| item_id | INTEGER | FK → items.id |
| station | TEXT | Destination station |
| weight | REAL | Weight distributed |
| transportation_company | TEXT | Carrier |
| driver_name | TEXT | Driver |
| truck_number | TEXT | Vehicle plate |
| remarks | TEXT | Free text |
| status | TEXT | pending / completed |
| voucher_type | INTEGER | 60, 65, or 70 tons |
| created_at | TEXT | ISO datetime |

**email_recipients** — Report email list
| Column | Type |
|--------|------|
| id | INTEGER | Primary key |
| email | TEXT | Unique |
| created_at | TEXT |

**customer_orders** — Orders from customers
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| customer_name | TEXT | |
| phone | TEXT | |
| item_name | TEXT | |
| quantity | REAL | |
| unit | TEXT | ton (default) |
| status | TEXT | pending / accepted / refused |
| refusal_reason | TEXT | |
| notes | TEXT | |
| created_at | TEXT | |
| updated_at | TEXT | |

**customer_contacts** — CRM
| Column | Type |
|--------|------|
| id, name, phone, has_whatsapp, status, notes, created_at, updated_at |

**vehicles** — Fleet vehicles
| Column | Type |
|--------|------|
| id, name, plate_number, current_location, updated_at, created_at |

**station_deposits** — Financial deposits
| Column | Type |
|--------|------|
| id, station_name, amount, method (Swift/Deposit), notes, created_at |

**fleet_status** — Fleet category counts
| Column | Type |
|--------|------|
| id, category (unique), count, notes, updated_at |

Default categories:
1. Registered vehicles
2. Inside the factory
3. Outside the factory
4. On the road - Going
5. On the road - Returning
6. Maintenance / Breakdown

**weight_differences** — Short-weight tracking
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| distribution_id | INTEGER | FK → distributions.id |
| item_id | INTEGER | FK → items.id |
| station | TEXT | |
| voucher_type | INTEGER | 60/65/70 |
| standard_weight | REAL | Voucher weight |
| actual_weight | REAL | Actual delivered |
| difference | REAL | voucher - actual (if positive) |
| price_per_ton | REAL | Cement price at time |
| value | REAL | difference × price |
| branch | TEXT | bani / alex |
| created_at | TEXT | |

**settings** — Key-value store
| Column | Type |
|--------|------|
| key | TEXT | Primary key |
| value | TEXT |

**ostool_attachments** — Uploaded files
| Column | Type |
|--------|------|
| id, filename, original_name, file_size, upload_date, ostool_date |

---

## 8. Features

### Weight Difference System

When a distribution is completed with actual weight less than voucher quantity, the system:
1. Calculates the difference (voucher - actual)
2. Multiplies by the configured cement price per ton
3. Records the weight difference with all details
4. Shows in Weight Diffs tab with period filters (All/Today/Week/Month)
5. Aggregates in Control Room dashboard

### Distribution → Fleet Automation

When a distribution is created (pending), the fleet "On the road - Going" count automatically increments. When completed, it decrements — keeping fleet status in sync with actual operations.

### Email System

- Daily reports with stock levels, distributions, and charts
- Low-stock alerts automatically sent when distribution causes stock to fall below threshold
- Ostool fleet report email with rich text editor
- Inbox checking (IMAP) for incoming fleet reports

### Real-time Monitoring

The Control Room dashboard provides:
- **Alerts**: Items at or below threshold with critical/warning styling
- **Stock Chart**: Horizontal bar chart with per-item colors, branch prefixes
- **Orders**: Today's completed/pending counts
- **Weight Diffs**: Aggregated financial impact
- **Live Bar**: Cycling through stock items, distributions, trucks per branch
- **Voice Reader**: Reads fleet status aloud (supports AR/EN)
- **Audio Alerts**: Chime on new low-stock items

### Chat & Communication

- Multi-branch real-time chat via WebSocket
- WebRTC peer-to-peer audio/video calling
- User presence tracking (online/offline)

---

## 9. Troubleshooting

### Common Issues

**App won't start — "JWT_SECRET environment variable is required"**
→ Set `JWT_SECRET` in `.env` file

**Login fails on first run**
→ Ensure `ADMIN_PASS` is set in `.env`

**Emails not sending**
→ Verify SMTP settings (Gmail requires app password, not regular password)
→ Check that `SMTP_USER` and `SMTP_PASS` are correct

**Low stock alerts not received**
→ Check `ALERT_EMAILS` configuration
→ Check server logs for SMTP errors

**Database errors**
→ Stop the app, delete the `.db` files, restart (they will be recreated)

**Port already in use**
→ Change port: `python run.py --port 8101`

**SQLite "database is locked"**
→ Only one process should access the database at a time
→ Restart the server

**Zero stock on fresh install**
→ Create items and add stock via the Inventory tab

### Security Notes

- JWT tokens expire after 10 hours
- Passwords are hashed with bcrypt
- All database queries use parameterized statements (SQL injection safe)
- Environment variables should never be committed to version control
- Use strong, unique values for `JWT_SECRET` and `ADMIN_PASS`
- For production, deploy behind HTTPS reverse proxy (nginx, Caddy)
