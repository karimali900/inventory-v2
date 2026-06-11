# Inventory v2 — Agent Memory

## Overview
FastAPI inventory management system for two branches (Alexandria / Bani Suwayf), each with its own SQLite DB. Served with uvicorn at port 8100.

## Run Commands
```bash
uvicorn inventory_app:app --port 8100
# or
python run.py
```

## Project Structure
```
/home/karim/Documents/inventory_v2/
├── inventory_app/
│   ├── __init__.py          # FastAPI app, auth, routes, DB init
│   ├── run.py               # Entry point
│   └── templates/
│       ├── bani.html        # Bani Suwayf branch
│       ├── alex.html        # Alexandria branch
│       ├── login.html       # Login page
│       └── branch_select.html
├── alex.db                  # Alexandria SQLite DB
├── bani.db                  # Bani Suwayf SQLite DB
├── .env                     # Gmail SMTP app password
├── AGENTS.md                # This file
└── requirements.txt
```

## Key Config
- **Port**: 8100
- **Auth**: JWT, default `admin / admin123`
- **DBs**: `alex.db` (Alexandria), `bani.db` (Bani Suwayf) — same schema, separate per branch
- **.env**: `/home/karim/Documents/inventory_v2/.env` — Gmail SMTP app password
- **Tailwind**: CDN, no build step

## Tab Order (both branches)
1. 📦 Inventory
2. 📜 History
3. 📋 Orders
4. 💰 Station Accounts
5. 📊 Reports
6. 📞 Contacts
7. 🛰️ GPS
8. 🏭 Cement News
9. 🚛 Ostool Operation

## Distribution Tab
- **Station** + **Weight (tons)** + **Transportation Company** fields
- Transportation company field: `id="dist-transport"`, i18n key `transportCompany` / `الشركة الناقلة`
- Displayed in detail modal distribution rows as small amber badge

## Ostool Operation Tab (`🚛 Ostool Operation` / `🚛 تشغيل اسطول`)
- Aggregates distributions where `transportation_company` contains "ostool", "Ostool", or "اسطول"
- Shows per-company stats: operations count, total weight, stations, completed count, last operation date
- Grand total row at bottom
- **📧 Email to Ostool** button → calls `POST /api/inventory/ostool-operations/email` → sends HTML email to `ALERT_EMAILS`
- **🖨️ Print** button → prints via `printOstool()` function
- `@media print` CSS: shows `#tab-ostool-content`, hides other tabs
- API: `GET /api/inventory/ostool-operations` → `{ companies: [...], grand_total_weight, grand_total_ops }`

## i18n
- Client-side dictionary in each template (`ar` / `en`)
- `t(key)` function returns translated string
- `data-i18n="key"` on elements, `applyTranslation()` updates DOM
- Language stored in `localStorage`

## Template Editing Rules
- **Both `bani.html` and `alex.html` must be edited in parallel** for any UI change
- When adding a tab, update in order:
  1. Tab button in `<div class="flex gap-2 border-b ...">`
  2. Content div with `id="tab-<name>-content"`
  3. `switchMainTab()` — add to `const` arrays + `if/else if` blocks
  4. i18n keys in both `ar` and `en` dicts
  5. `@media print` CSS if needed
- Icons in tab buttons: `📦 📜 📋 💰 📊 📞 🛰️ 🏭`
- Signature at bottom: `@CREATED BY Karim`

## Backend Editing Rules
- Check for duplicate method definitions with `grep "def "` — last definition wins
- `__init__.py` contains all routes, DB operations, and scraping logic
- DB tables: `items`, `transactions`, `distributions`, `users`, `customer_orders`, `customer_contacts`, `email_recipients`, `station_accounts`

## Cement News
- Scrapes `https://cementegypt.com/prices/` server-side with 10-min cache
- Site is a Vue.js SPA — actual content is JS-rendered, not scrapeable
- Alternative API: `https://api.cementegypt.com` (Laravel, needs auth token)
- Falls back to empty results if scraping fails

## External APIs (blocked / fallback)
- **AIS vessel tracking**: needs `AISSTREAM_API_KEY` from aisstream.io
- **Planet API**: needs paid Planet API key
- **ACLED conflict data**: needs `ACLED_API_KEY` + `ACLED_EMAIL` env vars
- **NDVI/Crop health**: needs `AGRO_API_KEY`
- **Cement prices**: cementegypt.com is a Vue SPA (no server-side rendering)

## Inventory Tab Buttons
- Green button (`bg-emerald-700`): `quickCreate('أسمنت عادي')` — i18n key `quickOrdinary` → "Normal Cement" / "أسمنت عادي"
- Orange button (`bg-amber-600`): `openCreateModal()` — i18n key `newItem` → "Add New Item" / "اضافة صنف جديد"
- Both buttons clear/reset on open/close

## Contact
- Created by Karim — signature: `@CREATED BY Karim`
