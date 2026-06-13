import json
import os
import re
import smtplib
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from jose import jwt
from passlib.context import CryptContext

# ─── Auth ─────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("JWT_SECRET", "inventory-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES =600
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
auth_scheme = HTTPBearer(auto_error=False)

USERS_DB = Path(__file__).parent / "users.db"

def get_users_db():
    conn = sqlite3.connect(str(USERS_DB))
    conn.row_factory = sqlite3.Row
    return conn

def init_users_db():
    conn = get_users_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'editor',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    # Add role column if missing (migration)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'editor'")
    except sqlite3.OperationalError:
        pass
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_pass = os.environ.get("ADMIN_PASS", "admin123")
    existing = conn.execute("SELECT id FROM users WHERE username=?", (admin_user,)).fetchone()
    if not existing:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, 'admin')",
                     (admin_user, pwd_ctx.hash(admin_pass)))
    else:
        conn.execute("UPDATE users SET role='admin' WHERE username=?", (admin_user,))
    conn.commit()
    conn.close()

def create_access_token(username: str, role: str = "editor"):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": username, "role": role, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"username": payload["sub"], "role": payload.get("role", "editor")}
    except Exception:
        return None

def get_current_user(auth: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    user = verify_token(auth.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user

# ─── WebSocket Chat Manager ───────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, dict[str, WebSocket]] = {}

    def add(self, branch: str, username: str, ws: WebSocket):
        if branch not in self.connections:
            self.connections[branch] = {}
        self.connections[branch][username] = ws

    def remove(self, branch: str, username: str):
        if branch in self.connections and username in self.connections[branch]:
            del self.connections[branch][username]
            if not self.connections[branch]:
                del self.connections[branch]

    def get_users(self, branch: str) -> list[dict]:
        return [{"username": u} for u in self.connections.get(branch, {}).keys()]

    async def broadcast(self, branch: str, message: dict, exclude: str = None):
        for username, ws in list(self.connections.get(branch, {}).items()):
            if username != exclude:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def send_to(self, branch: str, username: str, message: dict):
        ws = self.connections.get(branch, {}).get(username)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                pass

chat_manager = ConnectionManager()

# ─── Active Sessions Tracking ──────────────────────────────────────
active_sessions: dict = {}

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    host, port = request.client.host, request.client.port
    return host or "0.0.0.0"

def is_private_ip(ip: str) -> bool:
    ip = ip.strip()
    if ip.startswith("127.") or ip == "localhost" or ip == "::1":
        return True
    if ip.startswith("192.168."):
        return True
    if ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (IndexError, ValueError):
            pass
    return False

def record_active_session(username: str, request: Request):
    ip = get_client_ip(request)
    network = "داخلى" if is_private_ip(ip) else "خارجى"
    active_sessions[username] = {
        "username": username,
        "ip": ip,
        "network": network,
        "last_seen": datetime.utcnow().isoformat(),
    }

def cleanup_stale_sessions(max_minutes: int = 5):
    cutoff = datetime.utcnow() - timedelta(minutes=max_minutes)
    stale = [u for u, s in active_sessions.items()
             if datetime.fromisoformat(s["last_seen"]) < cutoff]
    for u in stale:
        del active_sessions[u]

# ─── .env ─────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

TEMPLATES_DIR = Path(__file__).parent / "templates"

SMTP_SERVER = os.environ.get("SMTP_SERVER", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_EMAILS = os.environ.get("ALERT_EMAILS", "").split(",") if os.environ.get("ALERT_EMAILS") else []
WEBHOOK_URLS = [u.strip() for u in os.environ.get("WEBHOOK_URL", "").split(",") if u.strip()]

def send_email(subject: str, body: str, html_body: str = "", to_emails: list = None) -> str:
    recipients = to_emails if to_emails is not None else ALERT_EMAILS
    if not SMTP_PASS:
        return "SMTP password not configured (set SMTP_PASS)"
    if not recipients:
        return "No recipients configured"
    try:
        msg = MIMEText(html_body, "html") if html_body else MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(recipients)
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
        return ""
    except Exception as e:
        return str(e)

def send_webhook(subject: str, body: str) -> list:
    errors = []
    for url in WEBHOOK_URLS:
        try:
            import urllib.request, json
            payload = json.dumps({
                "text": f"{subject}\n{body}",
                "content": f"{subject}\n{body}",
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            errors.append(f"{url[:40]}: {e}")
    return errors

def build_alert_html(items: list, threshold_items: list = None):
    threshold_items = threshold_items or items
    rows = ""
    for i in items:
        status = i["current_stock"] <= i["warning_threshold"]
        en_badge = '<span style="background:#eab308;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold">LOW</span>' if status else '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:4px;">OK</span>'
        ar_badge = '<span style="background:#eab308;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold">منخفض</span>' if status else '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:4px;">جيد</span>'
        rows += f"<tr><td style='padding:6px 10px;border-bottom:1px solid #ddd'>{i['name']}</td><td style='padding:6px 10px;border-bottom:1px solid #ddd;text-align:right'>{i['current_stock']}</td><td style='padding:6px 10px;border-bottom:1px solid #ddd;text-align:right'>{i['warning_threshold']}</td><td style='padding:6px 10px;border-bottom:1px solid #ddd;text-align:center'>{en_badge} {ar_badge}</td></tr>"
    svg = _svg_chart(items)
    return f"""<!DOCTYPE html>
<html dir="ltr"><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;padding:20px;margin:0">
<div style="max-width:700px;margin:auto">
  <div style="background:linear-gradient(135deg,#0369a1,#0284c7);color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="margin:0;font-size:20px">📊 Inventory Report / تقرير المخزون</h1>
  </div>
  {svg}
  <div style="background:white;border-radius:0 0 12px 12px;overflow:hidden;border:1px solid #e2e8f0">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#0369a1;color:white">
        <th style="padding:10px;text-align:left">Item / الصنف</th>
        <th style="padding:10px;text-align:right">Stock / المخزون</th>
        <th style="padding:10px;text-align:right">Threshold / الحد الادنى</th>
        <th style="padding:10px;text-align:center">Status / الحالة</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <p style="color:#94a3b8;font-size:11px;margin-top:12px;text-align:center">Inventory System — نظام إدارة المخزون</p>
</div></body></html>"""

def _svg_chart(items):
    if not items:
        return ""
    values = [i["current_stock"] for i in items]
    thresh = [i["warning_threshold"] for i in items]
    max_v = max(max(values), max(thresh), 1)
    n = len(items)
    w = max(500, n * 90)
    h = 260
    bar_w = max(28, min(55, (w - 60) // n - 12))
    bars = ""
    labels = ""
    for idx, (i, v, t) in enumerate(zip(items, values, thresh)):
        bx = 35 + idx * (bar_w + 14)
        bh = (v / max_v) * 180
        th = (t / max_v) * 180
        color = "#eab308" if v <= t else "#38bdf8"
        bars += f'<rect x="{bx}" y="{205 - bh}" width="{bar_w}" height="{bh}" fill="{color}" rx="4" />'
        bars += f'<line x1="{bx}" y1="{205 - th}" x2="{bx + bar_w}" y2="{205 - th}" stroke="#ef4444" stroke-width="2" stroke-dasharray="4,3" />'
        labels += f'<text x="{bx + bar_w / 2}" y="226" text-anchor="middle" font-size="10" fill="#475569">{i["name"]}</text>'
        labels += f'<text x="{bx + bar_w / 2}" y="238" text-anchor="middle" font-size="9" fill="#94a3b8">{v} / {t}</text>'
    return f'''<div style="background:white;padding:16px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0">
  <svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;margin:auto">
    <line x1="25" y1="205" x2="{w - 15}" y2="205" stroke="#cbd5e1" stroke-width="1"/>
    <line x1="25" y1="25" x2="25" y2="205" stroke="#cbd5e1" stroke-width="1"/>
    {bars} {labels}
    <text x="{w - 10}" y="16" text-anchor="end" font-size="9" fill="#94a3b8">Stock Level / مستوى المخزون</text>
  </svg></div>'''

# ─── Branch factory ───────────────────────────────────────────────
class InventoryBranch:
    def __init__(self, name: str, db_path: str, template: str, port: int):
        self.name = name
        self.db_path = Path(db_path)
        self.template = template
        self.port = port
        self.templates = Jinja2Templates(directory=TEMPLATES_DIR)
        self.router = FastAPI(docs_url=None, redoc_url=None)

        @self.router.middleware("http")
        async def auth_middleware(request: Request, call_next):
            path = request.url.path
            # Only protect API routes, let HTML pages through
            if "/api/" in path:
                token = None
                auth_header = request.headers.get("authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                user_info = verify_token(token) if token else None
                if not user_info:
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
                record_active_session(user_info["username"], request)
            return await call_next(request)

        self.router.add_api_route("/", self.serve_html, response_class=HTMLResponse, methods=["GET"])
        self.router.add_api_route("/api/inventory/items", self.list_items, methods=["GET"])
        self.router.add_api_route("/api/inventory/items/{item_id}", self.get_item, methods=["GET"])
        self.router.add_api_route("/api/inventory/items", self.create_item, methods=["POST"])
        self.router.add_api_route("/api/inventory/items/{item_id}", self.update_item, methods=["PUT"])
        self.router.add_api_route("/api/inventory/items/{item_id}", self.delete_item, methods=["DELETE"])
        self.router.add_api_route("/api/inventory/items/{item_id}/restore", self.restore_item, methods=["POST"])
        self.router.add_api_route("/api/inventory/items/archived", self.list_archived_items, methods=["GET"])
        self.router.add_api_route("/api/inventory/items/{item_id}/add", self.add_stock, methods=["POST"])
        self.router.add_api_route("/api/inventory/items/{item_id}/transactions", self.get_transactions, methods=["GET"])
        self.router.add_api_route("/api/inventory/items/{item_id}/distribute", self.distribute, methods=["POST"])
        self.router.add_api_route("/api/inventory/items/{item_id}/distributions", self.get_item_distributions, methods=["GET"])
        self.router.add_api_route("/api/inventory/distributions", self.list_all_distributions, methods=["GET"])
        self.router.add_api_route("/api/inventory/distributions/{dist_id}/complete", self.complete_distribution, methods=["PUT"])
        self.router.add_api_route("/api/inventory/report", self.get_report, methods=["GET"])
        self.router.add_api_route("/api/inventory/history", self.get_global_history, methods=["GET"])
        self.router.add_api_route("/api/inventory/send-report", self.send_report_manual, methods=["POST"])
        self.router.add_api_route("/api/inventory/stations/search", self.search_stations, methods=["GET"])
        self.router.add_api_route("/api/inventory/distributions/stats", self.distribution_stats, methods=["GET"])
        self.router.add_api_route("/api/inventory/station_accounts", self.get_station_accounts, methods=["GET"])
        self.router.add_api_route("/api/inventory/alerts", self.get_alerts, methods=["GET"])
        self.router.add_api_route("/api/inventory/recipients", self.list_recipients, methods=["GET"])
        self.router.add_api_route("/api/inventory/recipients", self.add_recipient, methods=["POST"])
        self.router.add_api_route("/api/inventory/recipients/{recipient_id}", self.delete_recipient, methods=["DELETE"])
        self.router.add_api_route("/api/inventory/orders", self.list_orders, methods=["GET"])
        self.router.add_api_route("/api/inventory/orders", self.create_order, methods=["POST"])
        self.router.add_api_route("/api/inventory/orders/aggregate", self.aggregate_orders, methods=["GET"])
        self.router.add_api_route("/api/inventory/orders/{order_id}", self.update_order_status, methods=["PUT"])
        self.router.add_api_route("/api/inventory/cement-news", self.get_cement_news, methods=["GET"])
        self.router.add_api_route("/api/inventory/contacts", self.list_contacts, methods=["GET"])
        self.router.add_api_route("/api/inventory/contacts", self.create_contact, methods=["POST"])
        self.router.add_api_route("/api/inventory/contacts/{contact_id}", self.get_contact, methods=["GET"])
        self.router.add_api_route("/api/inventory/contacts/{contact_id}", self.update_contact, methods=["PUT"])
        self.router.add_api_route("/api/inventory/contacts/{contact_id}", self.delete_contact, methods=["DELETE"])
        self.router.add_api_route("/api/inventory/vehicles", self.list_vehicles, methods=["GET"])
        self.router.add_api_route("/api/inventory/vehicles", self.create_vehicle, methods=["POST"])
        self.router.add_api_route("/api/inventory/vehicles/{vehicle_id}/location", self.update_vehicle_location, methods=["PUT"])
        self.router.add_api_route("/api/inventory/vehicles/{vehicle_id}", self.delete_vehicle, methods=["DELETE"])
        self.router.add_api_route("/api/inventory/ostool-operations", self.get_ostool_operations, methods=["GET"])
        self.router.add_api_route("/api/inventory/ostool-operations/email", self.send_ostool_email, methods=["POST"])
        self.router.add_api_route("/api/inventory/distances-search", self.search_distances_files, methods=["GET"])
        self.router.add_api_route("/api/active-users", self.get_active_users, methods=["GET"])

    def db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        conn = self.db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                current_stock REAL NOT NULL DEFAULT 0,
                beginning_stock REAL NOT NULL DEFAULT 0,
                warning_threshold REAL NOT NULL DEFAULT 10,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('addition','distribution')),
                quantity REAL NOT NULL,
                previous_stock REAL NOT NULL DEFAULT 0,
                new_stock REAL NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                distribution_id INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (item_id) REFERENCES items(id)
            );
            CREATE TABLE IF NOT EXISTS distributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                station TEXT NOT NULL,
                weight REAL NOT NULL,
                transportation_company TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (item_id) REFERENCES items(id)
            );
            CREATE TABLE IF NOT EXISTS email_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS customer_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL DEFAULT 'ton',
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','refused')),
                refusal_reason TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS customer_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                has_whatsapp INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','pending')),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                plate_number TEXT NOT NULL DEFAULT '',
                current_location TEXT NOT NULL DEFAULT 'Inside the factory',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        # Add archived column if upgrading existing DB
        try:
            conn.execute("ALTER TABLE items ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE distributions ADD COLUMN transportation_company TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE distributions ADD COLUMN driver_name TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE distributions ADD COLUMN truck_number TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE distributions ADD COLUMN remarks TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        conn.close()

    # ─── HTML ───
    async def serve_html(self, request: Request):
        return self.templates.TemplateResponse(request, self.template, {"api_prefix": f"/{self.name}"})

    # ─── Items ───
    async def list_items(self):
        conn = self.db()
        rows = conn.execute("SELECT * FROM items WHERE archived=0 ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def get_item(self, item_id: int):
        conn = self.db()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(404)
        return dict(row)

    async def create_item(self, item: dict):
        name = item.get("name", "").strip()
        if not name:
            raise HTTPException(400, "Name required")
        desc = item.get("description", "")
        bs = float(item.get("beginning_stock", 0))
        wt = float(item.get("warning_threshold", 10))
        now = datetime.utcnow().isoformat()
        conn = self.db()
        try:
            conn.execute(
                "INSERT INTO items (name, description, current_stock, beginning_stock, warning_threshold, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (name, desc, bs, bs, wt, now, now),
            )
            conn.commit()
            if bs > 0:
                conn.execute(
                    "INSERT INTO transactions (item_id, type, quantity, previous_stock, new_stock, note, created_at) VALUES (?, 'addition', ?, 0, ?, ?, ?)",
                    (conn.execute("SELECT last_insert_rowid()").fetchone()[0], bs, bs, "Beginning inventory", now),
                )
                conn.commit()
            row = conn.execute("SELECT * FROM items WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
            conn.close()
            return dict(row)
        except sqlite3.IntegrityError:
            conn.close()
            raise HTTPException(400, "Item name already exists")

    async def update_item(self, item_id: int, item: dict):
        conn = self.db()
        now = datetime.utcnow().isoformat()
        if "warning_threshold" in item:
            conn.execute("UPDATE items SET warning_threshold=?, updated_at=? WHERE id=?", (float(item["warning_threshold"]), now, item_id))
        if "name" in item:
            conn.execute("UPDATE items SET name=?, updated_at=? WHERE id=?", (item["name"].strip(), now, item_id))
        if "description" in item:
            conn.execute("UPDATE items SET description=?, updated_at=? WHERE id=?", (item["description"], now, item_id))
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(404)
        return dict(row)

    async def delete_item(self, item_id: int):
        conn = self.db()
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.close()
            raise HTTPException(404)
        conn.execute("UPDATE items SET archived=1, updated_at=? WHERE id=?", (datetime.utcnow().isoformat(), item_id))
        conn.commit()
        conn.close()
        return {"ok": True, "archived": True}

    async def restore_item(self, item_id: int):
        conn = self.db()
        item = conn.execute("SELECT * FROM items WHERE id=? AND archived=1", (item_id,)).fetchone()
        if not item:
            conn.close()
            raise HTTPException(404)
        conn.execute("UPDATE items SET archived=0, updated_at=? WHERE id=?", (datetime.utcnow().isoformat(), item_id))
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.close()
        return dict(row)

    async def list_archived_items(self):
        conn = self.db()
        rows = conn.execute("SELECT * FROM items WHERE archived=1 ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ─── Add Stock ───
    async def add_stock(self, item_id: int, body: dict):
        qty = float(body.get("quantity", 0))
        if qty <= 0:
            raise HTTPException(400, "Quantity must be positive")
        note = body.get("note", "Manual addition")
        conn = self.db()
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.close()
            raise HTTPException(404)
        prev = item["current_stock"]
        new = prev + qty
        now = datetime.utcnow().isoformat()
        conn.execute("UPDATE items SET current_stock=?, updated_at=? WHERE id=?", (new, now, item_id))
        conn.execute("INSERT INTO transactions (item_id, type, quantity, previous_stock, new_stock, note, created_at) VALUES (?, 'addition', ?, ?, ?, ?, ?)", (item_id, qty, prev, new, note, now))
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.close()
        return dict(row)

    # ─── Distribute ───
    async def distribute(self, item_id: int, body: dict):
        station = body.get("station", "").strip()
        if not station:
            raise HTTPException(400, "Station name required")
        weight = float(body.get("weight", 0))
        if weight <= 0:
            raise HTTPException(400, "Weight must be positive")
        transportation_company = body.get("transportation_company", "").strip()
        driver_name = body.get("driver_name", "").strip()
        truck_number = body.get("truck_number", "").strip()
        remarks = body.get("remarks", "").strip()
        conn = self.db()
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.close()
            raise HTTPException(404)
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO distributions (item_id, station, weight, transportation_company, driver_name, truck_number, remarks, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (item_id, station, weight, transportation_company, driver_name, truck_number, remarks, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM distributions WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
        conn.close()
        return dict(row)

    # ─── Transactions ───
    async def get_transactions(self, item_id: int, limit: int = 50, offset: int = 0):
        conn = self.db()
        rows = conn.execute(
            """SELECT t.*,
                      CASE WHEN t.distribution_id IS NOT NULL
                        THEN (SELECT COUNT(*) FROM distributions d
                              WHERE d.item_id = t.item_id AND d.id <= t.distribution_id
                              AND d.status = 'completed')
                        ELSE NULL END as dist_num
               FROM transactions t
               WHERE t.item_id=? ORDER BY t.created_at DESC LIMIT ? OFFSET ?""",
            (item_id, limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE item_id=?", (item_id,)).fetchone()["c"]
        conn.close()
        return {"transactions": [dict(r) for r in rows], "total": total}

    async def get_item_distributions(self, item_id: int, limit: int = 50):
        conn = self.db()
        rows = conn.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM distributions WHERE item_id=d.item_id AND id <= d.id) as dist_num
               FROM distributions d WHERE d.item_id=? ORDER BY d.created_at DESC LIMIT ?""",
            (item_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def search_stations(self, q: str = ""):
        conn = self.db()
        if q:
            rows = conn.execute(
                """SELECT d.*, i.name as item_name, i.current_stock as item_stock,
                          (SELECT COUNT(*) FROM distributions WHERE item_id=d.item_id AND id <= d.id) as dist_num
                   FROM distributions d
                   JOIN items i ON i.id = d.item_id
                   WHERE d.station LIKE ? ORDER BY d.created_at DESC LIMIT 50""",
                (f"%{q}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.station, SUM(d.weight) as total_weight, COUNT(*) as dist_count,
                          MAX(d.created_at) as last_dist
                   FROM distributions d GROUP BY d.station ORDER BY last_dist DESC LIMIT 50"""
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def distribution_stats(self):
        conn = self.db()
        total = conn.execute("SELECT COUNT(*) as c FROM distributions WHERE status='completed'").fetchone()["c"]
        weight = conn.execute("SELECT COALESCE(SUM(weight),0) as s FROM distributions WHERE status='completed'").fetchone()["s"]
        stations = conn.execute("SELECT COUNT(DISTINCT station) as c FROM distributions WHERE status='completed'").fetchone()["c"]
        conn.close()
        return {"completed_distributions": total, "total_weight": weight, "unique_stations": stations}

    async def list_orders(self):
        conn = self.db()
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        rows = conn.execute(
            "SELECT customer_name, item_name, SUM(quantity) as total_qty, unit, COUNT(*) as order_count, (SELECT phone FROM customer_orders WHERE customer_name=co.customer_name AND status='accepted' AND created_at >= ? ORDER BY id DESC LIMIT 1) as phone FROM customer_orders co WHERE status='accepted' AND created_at >= ? GROUP BY customer_name, item_name ORDER BY customer_name",
            (cutoff, cutoff)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def create_order(self, body: dict):
        name = body.get("customer_name", "").strip()
        item = body.get("item_name", "").strip()
        qty = float(body.get("quantity", 0))
        if not name or not item or qty <= 0:
            raise HTTPException(400, "customer_name, item_name, and positive quantity required")
        now = datetime.utcnow().isoformat()
        conn = self.db()
        conn.execute(
            "INSERT INTO customer_orders (customer_name, phone, item_name, quantity, unit, status, notes, created_at, updated_at) VALUES (?,?,?,?,?, 'pending',?,?,?)",
            (name, body.get("phone", ""), item, qty, body.get("unit", "ton"), body.get("notes", ""), now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM customer_orders WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
        conn.close()
        return dict(row)

    async def aggregate_orders(self):
        conn = self.db()
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        rows = conn.execute(
            "SELECT customer_name, item_name, SUM(quantity) as total_qty, unit, COUNT(*) as order_count, (SELECT phone FROM customer_orders WHERE customer_name=co.customer_name AND status='accepted' AND created_at >= ? ORDER BY id DESC LIMIT 1) as phone FROM customer_orders co WHERE status='accepted' AND created_at >= ? GROUP BY customer_name, item_name ORDER BY customer_name",
            (cutoff, cutoff)
        ).fetchall()
        total = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) as s FROM customer_orders WHERE status='accepted' AND created_at >= ?",
            (cutoff,)
        ).fetchone()["s"]
        conn.close()
        return {"items": [dict(r) for r in rows], "grand_total": total}

    async def update_order_status(self, order_id: int, body: dict):
        status = body.get("status", "")
        if status not in ("accepted", "refused"):
            raise HTTPException(400, "Status must be 'accepted' or 'refused'")
        reason = body.get("refusal_reason", "")
        now = datetime.utcnow().isoformat()
        conn = self.db()
        existing = conn.execute("SELECT * FROM customer_orders WHERE id=?", (order_id,)).fetchone()
        if not existing:
            conn.close()
            raise HTTPException(404, "Order not found")
        if existing["status"] != "pending":
            conn.close()
            raise HTTPException(400, "Order already processed")
        conn.execute("UPDATE customer_orders SET status=?, refusal_reason=?, updated_at=? WHERE id=?", (status, reason, now, order_id))
        conn.commit()
        row = conn.execute("SELECT * FROM customer_orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        return dict(row)

    async def get_station_accounts(self, date: str = ""):
        conn = self.db()
        if date:
            rows = conn.execute(
                """SELECT d.station, SUM(d.weight) as total_weight, COUNT(*) as dist_count,
                          i.name as item_name
                   FROM distributions d
                   JOIN items i ON i.id = d.item_id
                   WHERE d.status='completed' AND DATE(d.created_at) = ?
                   GROUP BY d.station, i.name
                   ORDER BY d.station""",
                (date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.station, SUM(d.weight) as total_weight, COUNT(*) as dist_count,
                          i.name as item_name
                   FROM distributions d
                   JOIN items i ON i.id = d.item_id
                   WHERE d.status='completed' AND DATE(d.created_at) = DATE('now')
                   GROUP BY d.station, i.name
                   ORDER BY d.station"""
            ).fetchall()
        grand_total = conn.execute(
            "SELECT COALESCE(SUM(weight),0) as s FROM distributions WHERE status='completed' AND DATE(created_at) = DATE('now')"
        ).fetchone()["s"]
        conn.close()
        return {"stations": [dict(r) for r in rows], "grand_total_weight": grand_total}

    async def get_active_users(self):
        cleanup_stale_sessions()
        return {"users": list(active_sessions.values())}

    async def list_all_distributions(self, limit: int = 100):
        conn = self.db()
        rows = conn.execute(
            """SELECT d.*, i.name as item_name,
                      (SELECT COUNT(*) FROM distributions WHERE item_id=d.item_id AND id <= d.id) as dist_num
               FROM distributions d
               JOIN items i ON i.id = d.item_id
               ORDER BY d.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def complete_distribution(self, dist_id: int):
        conn = self.db()
        dist = conn.execute("SELECT * FROM distributions WHERE id=?", (dist_id,)).fetchone()
        if not dist:
            conn.close()
            raise HTTPException(404, "Distribution not found")
        if dist["status"] == "completed":
            conn.close()
            return dict(dist)
        item_id = dist["item_id"]
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        weight = dist["weight"]
        prev = item["current_stock"]
        if prev < weight:
            conn.close()
            raise HTTPException(400, f"Insufficient stock. Available: {prev}, required: {weight}")
        new_stock = prev - weight
        now = datetime.utcnow().isoformat()
        conn.execute("UPDATE items SET current_stock=?, updated_at=? WHERE id=?", (new_stock, now, item_id))
        conn.execute(
            "INSERT INTO transactions (item_id, type, quantity, previous_stock, new_stock, note, distribution_id, created_at) VALUES (?, 'distribution', ?, ?, ?, ?, ?, ?)",
            (item_id, weight, prev, new_stock,             dist['station'], dist_id, now),
        )
        conn.execute("UPDATE distributions SET status='completed' WHERE id=?", (dist_id,))
        conn.commit()
        dist = conn.execute("SELECT * FROM distributions WHERE id=?", (dist_id,)).fetchone()
        if new_stock <= item["warning_threshold"]:
            low_items = conn.execute("SELECT * FROM items WHERE archived=0 AND current_stock <= warning_threshold").fetchall()
            html = build_alert_html([dict(r) for r in low_items])
            text = f"Low Stock Alert: {item['name']} - Stock: {new_stock}/{item['warning_threshold']}"
            send_email(f"Low Stock Alert: {item['name']}", text, html)
            send_webhook(f"Low Stock Alert: {item['name']}", text)
        conn.close()
        return dict(dist)

    # ─── Report ───
    async def get_report(self, date: str = ""):
        conn = self.db()
        if not date:
            from datetime import date as dt_date
            date = dt_date.today().isoformat()
        items = conn.execute("SELECT * FROM items WHERE archived=0 ORDER BY name").fetchall()
        items_data = []
        for item in items:
            item_dict = dict(item)
            today_adds = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) as s FROM transactions WHERE item_id=? AND DATE(created_at)=? AND type='add'",
                (item["id"], date)
            ).fetchone()["s"]
            today_dists = conn.execute(
                "SELECT COALESCE(SUM(weight), 0) as s FROM distributions WHERE item_id=? AND DATE(created_at)=?",
                (item["id"], date)
            ).fetchone()["s"]
            item_dict["today_additions"] = today_adds
            item_dict["today_distributions"] = today_dists
            items_data.append(item_dict)
        dist_data = conn.execute(
            """SELECT station, SUM(weight) as total FROM distributions
               WHERE status='completed' AND DATE(created_at)=? GROUP BY station ORDER BY total DESC""",
            (date,)
        ).fetchall()
        recent = conn.execute(
            """SELECT d.*, i.name as item_name,
                      (SELECT COUNT(*) FROM distributions WHERE item_id=d.item_id AND id <= d.id) as dist_num
               FROM distributions d
               JOIN items i ON i.id = d.item_id
               WHERE d.status='completed' AND DATE(d.created_at)=? ORDER BY d.created_at DESC LIMIT 20""",
            (date,)
        ).fetchall()
        conn.close()
        return {"items": items_data, "distributions_by_station": [dict(r) for r in dist_data], "recent": [dict(r) for r in recent], "report_date": date}

    async def get_global_history(self, station: str = "", limit: int = 50, offset: int = 0):
        conn = self.db()
        where = ""
        params = []
        if station:
            where = " WHERE (t.note LIKE ? OR t.note LIKE ?)"
            params = [f"%{station}%", f"%{station}%"]
        rows = conn.execute(
            f"""SELECT t.*, i.name as item_name,
                      CASE WHEN t.distribution_id IS NOT NULL
                        THEN (SELECT COUNT(*) FROM distributions d
                              WHERE d.item_id = t.item_id AND d.id <= t.distribution_id
                              AND d.status = 'completed')
                        ELSE NULL END as dist_num
               FROM transactions t
               JOIN items i ON i.id = t.item_id
               {where}
               ORDER BY t.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        count_params = params[:] if station else []
        total = conn.execute(
            f"SELECT COUNT(*) as c FROM transactions t JOIN items i ON i.id = t.item_id {where}",
            count_params,
        ).fetchone()["c"]
        conn.close()
        return {"transactions": [dict(r) for r in rows], "total": total}

    async def send_report_manual(self, body: dict = {}):
        conn = self.db()
        low_items = conn.execute("SELECT * FROM items WHERE archived=0 AND current_stock <= warning_threshold").fetchall()
        all_items = conn.execute("SELECT * FROM items WHERE archived=0").fetchall()
        selected_ids = body.get("recipient_ids", [])
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            recipients = conn.execute(f"SELECT email FROM email_recipients WHERE id IN ({placeholders})", selected_ids).fetchall()
            to_emails = [r["email"] for r in recipients]
        else:
            all_rec = conn.execute("SELECT email FROM email_recipients").fetchall()
            to_emails = [r["email"] for r in all_rec] if all_rec else ALERT_EMAILS
        conn.close()
        html = build_alert_html([dict(r) for r in all_items], [dict(r) for r in low_items])
        text = "Inventory Report - " + ", ".join(f"{i['name']}: {i['current_stock']}" for i in all_items)
        email_err = send_email("Inventory Report", text, html, to_emails=to_emails)
        webhook_errs = send_webhook("Inventory Report", text)
        errors = []
        if email_err: errors.append("Email: " + email_err)
        for e in webhook_errs: errors.append("Webhook: " + e)
        return {"ok": not errors, "sent_to": to_emails, "webhook_urls": WEBHOOK_URLS, "errors": errors}

    async def list_recipients(self):
        conn = self.db()
        rows = conn.execute("SELECT * FROM email_recipients ORDER BY email").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def add_recipient(self, body: dict):
        email = body.get("email", "").strip()
        if not email or "@" not in email:
            raise HTTPException(400, "Valid email required")
        conn = self.db()
        try:
            conn.execute("INSERT INTO email_recipients (email) VALUES (?)", (email,))
            conn.commit()
            row = conn.execute("SELECT * FROM email_recipients WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
            conn.close()
            return dict(row)
        except sqlite3.IntegrityError:
            conn.close()
            raise HTTPException(400, "Email already exists")

    async def delete_recipient(self, recipient_id: int):
        conn = self.db()
        conn.execute("DELETE FROM email_recipients WHERE id=?", (recipient_id,))
        conn.commit()
        conn.close()
        return {"ok": True}

    _cement_news_cache = None
    _cement_news_time = 0

    def _egypt_now(self):
        try:
            import pytz
            return datetime.now(pytz.timezone("Africa/Cairo"))
        except Exception:
            from datetime import timezone, timedelta
            return datetime.now(timezone(timedelta(hours=2)))

    async def get_cement_news(self):
        now = time.time()
        if self._cement_news_cache and now - self._cement_news_time < 600:
            return self._cement_news_cache
        articles = []
        error = None
        try:
            import requests, xml.etree.ElementTree as ET
            url = "https://news.google.com/rss/search?q=%D8%A7%D8%B3%D8%B9%D8%A7%D8%B1+%D8%A7%D9%84%D8%A7%D8%B3%D9%85%D9%86%D8%AA+%D9%85%D8%B5%D8%B1&hl=ar&gl=EG&ceid=EG:ar"
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                source = item.findtext("source", "")
                if title:
                    articles.append({"title": title, "link": link, "date": pub_date, "source": source})
            if not articles:
                error = "No news articles found"
        except Exception as e:
            error = str(e)
        egypt_dt = self._egypt_now()
        result = {
            "articles": articles,
            "fetched_at": egypt_dt.isoformat(),
            "egypt_time": egypt_dt.strftime("%A %d %B %Y %H:%M"),
        }
        if error:
            result["error"] = error
        self._cement_news_cache = result
        self._cement_news_time = now
        return result

    async def list_contacts(self):
        conn = self.db()
        rows = conn.execute("SELECT * FROM customer_contacts ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def get_contact(self, contact_id: int):
        conn = self.db()
        row = conn.execute("SELECT * FROM customer_contacts WHERE id=?", (contact_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(404, "Contact not found")
        return dict(row)

    async def create_contact(self, body: dict):
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(400, "Name required")
        phone = body.get("phone", "").strip()
        has_whatsapp = 1 if body.get("has_whatsapp") else 0
        status = body.get("status", "available")
        notes = body.get("notes", "").strip()
        conn = self.db()
        conn.execute(
            "INSERT INTO customer_contacts (name, phone, has_whatsapp, status, notes) VALUES (?,?,?,?,?)",
            (name, phone, has_whatsapp, status, notes),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM customer_contacts WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
        conn.close()
        return dict(row)

    async def update_contact(self, contact_id: int, body: dict):
        conn = self.db()
        existing = conn.execute("SELECT * FROM customer_contacts WHERE id=?", (contact_id,)).fetchone()
        if not existing:
            conn.close()
            raise HTTPException(404, "Contact not found")
        name = body.get("name", existing["name"]).strip()
        phone = body.get("phone", existing["phone"]).strip()
        has_whatsapp = 1 if body.get("has_whatsapp", existing["has_whatsapp"]) else 0
        status = body.get("status", existing["status"])
        notes = body.get("notes", existing["notes"]).strip()
        conn.execute(
            "UPDATE customer_contacts SET name=?, phone=?, has_whatsapp=?, status=?, notes=?, updated_at=datetime('now') WHERE id=?",
            (name, phone, has_whatsapp, status, notes, contact_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM customer_contacts WHERE id=?", (contact_id,)).fetchone()
        conn.close()
        return dict(row)

    async def delete_contact(self, contact_id: int):
        conn = self.db()
        conn.execute("DELETE FROM customer_contacts WHERE id=?", (contact_id,))
        conn.commit()
        conn.close()
        return {"ok": True}

    # ─── Vehicles ───
    async def list_vehicles(self):
        conn = self.db()
        rows = conn.execute("SELECT * FROM vehicles ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    async def create_vehicle(self, body: dict):
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(400, "Name required")
        plate = body.get("plate_number", "").strip()
        location = body.get("current_location", "Inside the factory")
        allowed = ["Inside the factory","Outside the factory","On the way there","On the way back","At the location"]
        if location not in allowed:
            raise HTTPException(400, "Invalid location")
        conn = self.db()
        conn.execute("INSERT INTO vehicles (name, plate_number, current_location) VALUES (?,?,?)", (name, plate, location))
        conn.commit()
        row = conn.execute("SELECT * FROM vehicles WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
        conn.close()
        return dict(row)

    async def update_vehicle_location(self, vehicle_id: int, body: dict):
        location = body.get("current_location", "").strip()
        allowed = ["Inside the factory","Outside the factory","On the way there","On the way back","At the location"]
        if location not in allowed:
            raise HTTPException(400, "Invalid location")
        conn = self.db()
        existing = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not existing:
            conn.close()
            raise HTTPException(404, "Vehicle not found")
        conn.execute("UPDATE vehicles SET current_location=?, updated_at=datetime('now') WHERE id=?", (location, vehicle_id))
        conn.commit()
        row = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        conn.close()
        return dict(row)

    async def delete_vehicle(self, vehicle_id: int):
        conn = self.db()
        conn.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
        conn.commit()
        conn.close()
        return {"ok": True}

    # ─── Ostool Operations ───
    def _ostool_where(self):
        return "(d.transportation_company LIKE '%ostool%' OR d.transportation_company LIKE '%Ostool%' OR d.transportation_company LIKE '%اسطول%')"

    async def get_ostool_operations(self, station: str = "", date: str = ""):
        conn = self.db()
        where_ostool = self._ostool_where()
        params = []
        if not date:
            from datetime import date as dt_date
            date = dt_date.today().isoformat()
        where_date = where_ostool + " AND DATE(d.created_at) = ?"
        date_params = [date]
        where = where_date
        params = list(date_params)
        if station:
            where += " AND d.station = ?"
            params.append(station)
        rows = conn.execute(
            f"""SELECT d.transportation_company,
                       COUNT(*) as operation_count,
                       COALESCE(SUM(d.weight), 0) as total_weight,
                       COUNT(DISTINCT d.station) as station_count,
                       COUNT(CASE WHEN d.status='completed' THEN 1 END) as completed_count,
                       MAX(d.created_at) as last_operation
                FROM distributions d
                WHERE {where}
                GROUP BY d.transportation_company
                ORDER BY last_operation DESC""",
            params,
        ).fetchall()
        grand_total_weight = conn.execute(
            f"SELECT COALESCE(SUM(d.weight), 0) as s FROM distributions d WHERE {where}", params
        ).fetchone()["s"]
        grand_total_ops = conn.execute(
            f"SELECT COUNT(*) as c FROM distributions d WHERE {where}", params
        ).fetchone()["c"]
        stations_map = {}
        st_rows = conn.execute(
            f"""SELECT d.transportation_company, GROUP_CONCAT(DISTINCT d.station) as stations
                FROM distributions d WHERE {where} GROUP BY d.transportation_company""",
            params,
        ).fetchall()
        for r in st_rows:
            stations_map[r["transportation_company"]] = r["stations"]
        drivers_map = {}
        dr_rows = conn.execute(
            f"""SELECT d.transportation_company, GROUP_CONCAT(DISTINCT d.driver_name) as drivers
                FROM distributions d WHERE {where} AND d.driver_name != '' GROUP BY d.transportation_company""",
            params,
        ).fetchall()
        for r in dr_rows:
            drivers_map[r["transportation_company"]] = r["drivers"]
        trucks_map = {}
        tr_rows = conn.execute(
            f"""SELECT d.transportation_company, GROUP_CONCAT(DISTINCT d.truck_number) as trucks
                FROM distributions d WHERE {where} AND d.truck_number != '' GROUP BY d.transportation_company""",
            params,
        ).fetchall()
        for r in tr_rows:
            trucks_map[r["transportation_company"]] = r["trucks"]
        remarks_map = {}
        rm_rows = conn.execute(
            f"""SELECT d.transportation_company, GROUP_CONCAT(DISTINCT d.remarks) as remarks
                FROM distributions d WHERE {where} AND d.remarks != '' GROUP BY d.transportation_company""",
            params,
        ).fetchall()
        for r in rm_rows:
            remarks_map[r["transportation_company"]] = r["remarks"]
        # Return station list filtered by same date (but not station filter)
        stations = conn.execute(
            f"SELECT DISTINCT d.station FROM distributions d WHERE {where_date} ORDER BY d.station",
            date_params,
        ).fetchall()
        conn.close()
        return {
            "companies": [dict(r) for r in rows],
            "grand_total_weight": grand_total_weight,
            "grand_total_ops": grand_total_ops,
            "stations": [r["station"] for r in stations],
            "stations_map": stations_map,
            "drivers_map": drivers_map,
            "trucks_map": trucks_map,
            "remarks_map": remarks_map,
        }

    async def send_ostool_email(self, body: dict = {}):
        station_filter = body.get("station", "").strip()
        general_note = body.get("general_note", "").strip()
        date = body.get("date", "").strip()
        transport_info = body.get("transport_info", "")
        conn = self.db()
        where = self._ostool_where()
        params = []
        if not date:
            from datetime import date as dt_date
            date = dt_date.today().isoformat()
        where += " AND DATE(d.created_at) = ?"
        params.append(date)
        if station_filter:
            where += " AND d.station = ?"
            params.append(station_filter)
        rows = conn.execute(
            f"""SELECT d.*, i.name as item_name
                FROM distributions d
                LEFT JOIN items i ON i.id = d.item_id
                WHERE {where}
                ORDER BY d.created_at DESC""",
            params,
        ).fetchall()
        if not rows:
            conn.close()
            return {"ok": False, "error": "No Ostool operations found"}
        grand_total_weight = conn.execute(
            f"SELECT COALESCE(SUM(d.weight), 0) as s FROM distributions d WHERE {where}", params
        ).fetchone()["s"]
        grand_total_ops = conn.execute(
            f"SELECT COUNT(*) as c FROM distributions d WHERE {where}", params
        ).fetchone()["c"]
        conn.close()
        station_label = f" - {station_filter}" if station_filter else ""
        ops_rows = ""
        for r in rows:
            driver = r["driver_name"] or "—"
            truck = r["truck_number"] or "—"
            remark = r["remarks"] or ""
            ops_rows += f"""<tr>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#92400e">{r['transportation_company']}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee">{driver}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee">{truck}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee">{r['station']}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold">{r['weight']:.2f}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:center;color:{'#059669' if r['status']=='completed' else '#d97706'}">{r['status']}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;font-size:11px;color:#64748b">{remark}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:center;color:#64748b;font-size:11px">{r['created_at'][:10] if r['created_at'] else '—'}</td>
            </tr>"""
        html = f"""<!DOCTYPE html>
<html dir="ltr"><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;padding:20px;margin:0">
<div style="max-width:900px;margin:auto">
  <div style="background:linear-gradient(135deg,#92400e,#d97706);color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="margin:0;font-size:20px">🚛 Ostool Operations Report{station_label} / تقرير تشغيل الاسطول{station_label}</h1>
  </div>
    <div style="background:white;border-radius:0 0 12px 12px;overflow:hidden;border:1px solid #e2e8f0;padding:4px">
        <div style="display:flex;justify-content:space-between;padding:12px 16px;background:#fef3c7;border-bottom:1px solid #fde68a;font-size:13px;font-weight:bold">
          <span>Total Ops / إجمالي العمليات: {grand_total_ops}</span>
          <span>Total Weight / إجمالي الوزن: {grand_total_weight:.2f} t</span>
        </div>"""
        general_note_html = f"""<div style="padding:8px 16px;background:#fefce8;border-bottom:1px solid #fde68a;font-size:12px;color:#92400e">
          <strong>ملاحظات:</strong> {general_note}</div>""" if general_note else ""
        transport_html = f"""<div style="padding:8px 16px;background:#f0fdf4;border-bottom:1px solid #bbf7d0;font-size:12px;color:#166534">
          <strong>🚚 النقل مشمول:</strong> {transport_info}</div>""" if transport_info else ""
        html += f"""{general_note_html}{transport_html}
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="background:#92400e;color:white">
        <th style="padding:8px;text-align:left">الشركة الناقلة</th>
        <th style="padding:8px;text-align:left">اسم السائق</th>
        <th style="padding:8px;text-align:left">رقم السيارة</th>
        <th style="padding:8px;text-align:left">المحطة</th>
        <th style="padding:8px;text-align:right">الوزن (طن)</th>
        <th style="padding:8px;text-align:center">الحالة</th>
        <th style="padding:8px;text-align:left">ملاحظات</th>
        <th style="padding:8px;text-align:center">التاريخ</th>
      </tr></thead>
      <tbody>{ops_rows}</tbody>
    </table>
  </div>
  <p style="color:#94a3b8;font-size:11px;margin-top:12px;text-align:center;border-top:1px solid #e2e8f0;padding-top:12px;color:#1e293b;font-weight:bold;font-size:13px">created by @KARIM — نظام إدارة المخزون</p>
</div></body></html>"""
        subject = f"🚛 Ostool Operations Report{station_label} / تقرير تشغيل الاسطول{station_label}"
        custom_emails = body.get("to_emails", "")
        if custom_emails:
            to_emails = [e.strip() for e in custom_emails.split(",") if e.strip()]
        else:
            to_emails = ALERT_EMAILS if ALERT_EMAILS else ["dtitan@ostool-eg.com"]
        err = send_email(subject, "Ostool Operations Report", html, to_emails=to_emails)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "sent_to": to_emails}

    async def search_distances_files(self, q: str = ""):
        distances_dir = Path(__file__).parent.parent / "distances"
        if not distances_dir.exists():
            return {"results": [], "error": "distances directory not found"}
        results = []
        ql = q.lower().strip() if q else ""
        for fpath in sorted(distances_dir.iterdir()):
            if fpath.suffix not in (".csv", ".json"):
                continue
            fname = fpath.name.lower()
            file_matches = not ql or ql in fname
            try:
                if fpath.suffix == ".csv":
                    with open(fpath, encoding="utf-8") as f:
                        lines = f.readlines()
                    if lines:
                        headers = [h.strip().lower() for h in lines[0].split(",")]
                        for line in lines[1:]:
                            line = line.strip()
                            if not line:
                                continue
                            vals = [v.strip() for v in line.split(",")]
                            row = dict(zip(headers, vals))
                            if not ql:
                                results.append({"file": fpath.name, "row": row})
                            elif file_matches:
                                results.append({"file": fpath.name, "row": row})
                            elif any(ql in v.lower() for v in vals):
                                results.append({"file": fpath.name, "row": row})
                elif fpath.suffix == ".json":
                    data = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for item in data:
                            if not ql:
                                results.append({"file": fpath.name, "row": item})
                            elif file_matches:
                                results.append({"file": fpath.name, "row": item})
                            elif any(ql in str(v).lower() for v in item.values()):
                                results.append({"file": fpath.name, "row": item})
            except Exception:
                pass
        return {"results": results}

    async def get_alerts(self):
        conn = self.db()
        rows = conn.execute("SELECT * FROM items WHERE archived=0 AND current_stock <= warning_threshold ORDER BY current_stock ASC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

# ─── Create branches ──────────────────────────────────────────────
bani = InventoryBranch("bani", "bani.db", "bani.html", 8002)
alex = InventoryBranch("alex", "alex.db", "alex.html", 8003)

# ─── Main app ─────────────────────────────────────────────────────
app = FastAPI(title="Inventory Management V2")
STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
from fastapi.staticfiles import StaticFiles
from fastapi import UploadFile, File
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

@app.websocket("/ws/chat/{branch}")
async def chat_websocket(websocket: WebSocket, branch: str):
    token = websocket.query_params.get("token")
    user_info = verify_token(token) if token else None
    if not user_info:
        await websocket.close(code=4001)
        return
    username = user_info["username"]
    await websocket.accept()
    chat_manager.add(branch, username, websocket)
    await chat_manager.broadcast(branch, {"type": "user-list", "users": chat_manager.get_users(branch)})
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "chat":
                to = data.get("to")
                text = data.get("text", "").strip()
                if to and text:
                    await chat_manager.send_to(branch, to, {
                        "type": "chat", "from": username, "text": text,
                        "timestamp": datetime.utcnow().isoformat()
                    })
            elif msg_type in ("offer", "answer", "ice-candidate"):
                to = data.get("to")
                if to:
                    data["from"] = username
                    await chat_manager.send_to(branch, to, data)
            elif msg_type in ("call-request", "call-accept", "call-decline", "call-end"):
                to = data.get("to")
                if to:
                    data["from"] = username
                    data["from_name"] = username
                    await chat_manager.send_to(branch, to, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        chat_manager.remove(branch, username)
        await chat_manager.broadcast(branch, {"type": "user-list", "users": chat_manager.get_users(branch)})

@app.post("/api/login")
async def login(body: dict, request: Request):
    username = body.get("username", "").strip()
    password = body.get("password", "")
    conn = get_users_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not user or not pwd_ctx.verify(password, user["password"]):
        raise HTTPException(401, "Invalid username or password")
    role = user["role"] if "role" in dict(user) and user["role"] else "editor"
    token = create_access_token(username, role)
    record_active_session(username, request)
    return {"token": token, "username": username, "role": role}

@app.get("/api/test")
async def test_api():
    return {"ok": True}

@app.get("/api/active-users")
async def get_active_users():
    cleanup_stale_sessions()
    return {"users": list(active_sessions.values())}

@app.get("/api/users")
async def list_users(auth: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    if not auth:
        raise HTTPException(401, "Not authenticated")
    user = verify_token(auth.credentials)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    conn = get_users_db()
    rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/users")
async def create_user(body: dict, auth: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    if not auth:
        raise HTTPException(401, "Not authenticated")
    user = verify_token(auth.credentials)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    role = body.get("role", "editor").strip()
    if not username or not password:
        raise HTTPException(400, "Username and password required")
    if role not in ("admin", "editor", "viewer"):
        raise HTTPException(400, "Invalid role")
    conn = get_users_db()
    try:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                     (username, pwd_ctx.hash(password), role))
        conn.commit()
        row = conn.execute("SELECT id, username, role, created_at FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        return dict(row)
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, "Username already exists")

@app.put("/api/users/{user_id}")
async def update_user(user_id: int, body: dict, auth: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    if not auth:
        raise HTTPException(401, "Not authenticated")
    user = verify_token(auth.credentials)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    conn = get_users_db()
    role = body.get("role", "").strip()
    if role:
        if role not in ("admin", "editor", "viewer"):
            conn.close()
            raise HTTPException(400, "Invalid role")
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    password = body.get("password", "").strip()
    if password:
        conn.execute("UPDATE users SET password=? WHERE id=?", (pwd_ctx.hash(password), user_id))
    conn.commit()
    row = conn.execute("SELECT id, username, role, created_at FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, auth: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    if not auth:
        raise HTTPException(401, "Not authenticated")
    user = verify_token(auth.credentials)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    conn = get_users_db()
    target = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "User not found")
    if target["username"] == user["username"]:
        conn.close()
        raise HTTPException(400, "Cannot delete yourself")
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/branches/"):
    return templates.TemplateResponse(request, "login.html", {"next": next})

@app.get("/control-room", response_class=HTMLResponse)
async def control_room(request: Request):
    return templates.TemplateResponse(request, "control-room.html", {})

@app.get("/api/dashboard-summary")
async def dashboard_summary():
    async def fetch_branch_data(branch):
        try:
            db_path = Path(branch + ".db")
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM items WHERE archived=0 ORDER BY name").fetchall()
            low = [dict(r) for r in rows if r["current_stock"] <= r["warning_threshold"]]
            total = sum(r["current_stock"] for r in rows)
            vehicles = conn.execute("SELECT * FROM vehicles ORDER BY name").fetchall()
            conn.close()
            return {
                "branch": branch,
                "items": [dict(r) for r in rows],
                "alerts": low,
                "total_stock": total,
                "vehicles": [dict(r) for r in vehicles],
            }
        except:
            return {"branch": branch, "items": [], "alerts": [], "total_stock": 0, "vehicles": []}
    bani_data = await fetch_branch_data("bani")
    alex_data = await fetch_branch_data("alex")
    return {
        "bani": bani_data,
        "alex": alex_data,
        "active_users": list(active_sessions.values()),
    }

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    import uuid
    ext = Path(file.filename).suffix if file.filename else ""
    name = uuid.uuid4().hex + ext
    path = UPLOAD_DIR / name
    content = await file.read()
    path.write_bytes(content)
    return {"url": f"/uploads/{name}", "name": file.filename or name}

@app.get("/branches", response_class=HTMLResponse)
@app.get("/branches/", response_class=HTMLResponse)
async def branch_select(request: Request):
    return templates.TemplateResponse(request, "branch_select.html", {})

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_users_db()
    bani.init_db()
    alex.init_db()
    yield

app.router.lifespan_context = lifespan

app.mount("/bani", bani.router)
app.mount("/alex", alex.router)

@app.get("/")
async def root():
    return RedirectResponse(url="/login")

def start():
    import uvicorn
    port = int(os.environ.get("PORT", 8100))
    uvicorn.run("inventory_app:app", host="0.0.0.0", port=port)

if __name__ == "__main__":
    start()

