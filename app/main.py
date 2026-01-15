import os
import time
import json
import sqlite3
from typing import Optional, Tuple

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

# =====================
# Config
# =====================
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./assistant.db")
ALLOWED_SENDERS = {
    s.strip() for s in os.getenv("ALLOWED_SENDERS", "").split(",") if s.strip()
}
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))

# =====================
# App
# =====================
app = FastAPI(title="WhatsApp Task Assistant")

# =====================
# Database
# =====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    now = int(time.time())

    cur.execute("""
    CREATE TABLE IF NOT EXISTS lists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS preferences (
        sender TEXT PRIMARY KEY,
        active_list_id INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        FOREIGN KEY(active_list_id) REFERENCES lists(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        list_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('open','done')),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        FOREIGN KEY(list_id) REFERENCES lists(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        sender TEXT,
        event_type TEXT NOT NULL,
        detail TEXT NOT NULL
    );
    """)

    # ensure default list exists
    cur.execute(
        "INSERT OR IGNORE INTO lists (name, created_at) VALUES (?,?)",
        ("todo", now),
    )

    conn.commit()
    conn.close()

def audit(sender: Optional[str], event_type: str, detail: dict):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (ts, sender, event_type, detail) VALUES (?,?,?,?)",
        (int(time.time()), sender, event_type, json.dumps(detail)),
    )
    conn.commit()
    conn.close()

# =====================
# Security
# =====================
_rate_bucket = {}

def rate_limit_ok(sender: str) -> bool:
    now = int(time.time())
    window = now // 60
    w, c = _rate_bucket.get(sender, (window, 0))
    if w != window:
        w, c = window, 0
    c += 1
    _rate_bucket[sender] = (w, c)
    return c <= RATE_LIMIT_PER_MIN

def sender_allowed(sender: str) -> bool:
    return sender in ALLOWED_SENDERS

# =====================
# Parsing
# =====================
ALLOWED_COMMANDS = {
    "/lists",
    "/newlist",
    "/use",
    "/todo",
    "/list",
    "/done",
}

def parse_command(body: str) -> Tuple[Optional[str], Optional[str]]:
    body = (body or "").strip()
    if not body.startswith("/"):
        return None, None

    parts = body.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) == 2 else None

    if cmd not in ALLOWED_COMMANDS:
        return "UNKNOWN", cmd

    return cmd, arg

def parse_int(arg: Optional[str]) -> int:
    if not arg or not arg.isdigit():
        raise ValueError("Expected a numeric ID.")
    return int(arg)

# =====================
# Core logic
# =====================
def get_or_create_prefs(sender: str) -> dict:
    conn = get_db()
    now = int(time.time())

    row = conn.execute(
        "SELECT * FROM preferences WHERE sender=?", (sender,)
    ).fetchone()

    if row:
        conn.close()
        return dict(row)

    todo = conn.execute(
        "SELECT id FROM lists WHERE name='todo'"
    ).fetchone()

    conn.execute(
        "INSERT INTO preferences (sender, active_list_id, created_at, updated_at) VALUES (?,?,?,?)",
        (sender, todo["id"], now, now),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM preferences WHERE sender=?", (sender,)
    ).fetchone()
    conn.close()
    return dict(row)

def create_list(name: str) -> int:
    name = name.strip().lower()
    if not name or len(name) > 30:
        raise ValueError("List name must be 1–30 characters.")

    for ch in name:
        if not (ch.isalnum() or ch in ("-", "_")):
            raise ValueError("Invalid character in list name.")

    conn = get_db()
    now = int(time.time())

    try:
        conn.execute(
            "INSERT INTO lists (name, created_at) VALUES (?,?)",
            (name, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("List already exists.")

    row = conn.execute(
        "SELECT id FROM lists WHERE name=?", (name,)
    ).fetchone()
    conn.close()
    return row["id"]

def set_active_list(sender: str, list_id: int) -> str:
    conn = get_db()
    row = conn.execute(
        "SELECT id,name FROM lists WHERE id=?", (list_id,)
    ).fetchone()

    if not row:
        conn.close()
        raise ValueError("List not found.")

    now = int(time.time())
    conn.execute(
        "UPDATE preferences SET active_list_id=?, updated_at=? WHERE sender=?",
        (list_id, now, sender),
    )
    conn.commit()
    conn.close()
    return row["name"]

def list_lists() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT id,name FROM lists ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_item(sender: str, text: str) -> int:
    prefs = get_or_create_prefs(sender)
    text = text.strip()
    if not text or len(text) > 300:
        raise ValueError("Task text invalid.")

    now = int(time.time())
    conn = get_db()
    conn.execute(
        "INSERT INTO items (list_id, text, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        (prefs["active_list_id"], text, "open", now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()
    conn.close()
    return row["id"]

def get_items(sender: str):
    prefs = get_or_create_prefs(sender)
    conn = get_db()

    lst = conn.execute(
        "SELECT id,name FROM lists WHERE id=?",
        (prefs["active_list_id"],),
    ).fetchone()

    rows = conn.execute(
        "SELECT id,text,status FROM items WHERE list_id=? ORDER BY id",
        (prefs["active_list_id"],),
    ).fetchall()

    conn.close()
    return dict(lst), [dict(r) for r in rows]

def mark_done(sender: str, item_id: int):
    prefs = get_or_create_prefs(sender)
    conn = get_db()

    row = conn.execute(
        "SELECT id,status FROM items WHERE id=? AND list_id=?",
        (item_id, prefs["active_list_id"]),
    ).fetchone()

    if not row:
        conn.close()
        raise ValueError("Item not found in active list.")

    now = int(time.time())
    conn.execute(
        "UPDATE items SET status='done', updated_at=? WHERE id=?",
        (now, item_id),
    )
    conn.commit()
    conn.close()

# =====================
# Webhook
# =====================
HELP_TEXT = (
    "Commands:\n"
    "/lists\n"
    "/newlist <name>\n"
    "/use <list_id>\n"
    "/todo <text>\n"
    "/list\n"
    "/done <item_id>"
)

@app.on_event("startup")
def startup():
    init_db()

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = dict(await request.form())
    sender = form.get("From", "")
    body = form.get("Body", "")

    audit(sender, "MSG_RECEIVED", {"len": len(body)})

    if not sender_allowed(sender):
        audit(sender, "AUTH_FAIL", {"reason": "sender_not_allowed"})
        raise HTTPException(status_code=403, detail="Forbidden")

    if not rate_limit_ok(sender):
        audit(sender, "RATE_LIMIT", {})
        return PlainTextResponse("Rate limit exceeded.", status_code=429)

    cmd, arg = parse_command(body)

    if cmd is None:
        return PlainTextResponse(HELP_TEXT)

    if cmd == "UNKNOWN":
        audit(sender, "CMD_REJECTED", {"cmd": arg})
        return PlainTextResponse(f"Unknown command: {arg}\n\n{HELP_TEXT}")

    try:
        audit(sender, "CMD_EXECUTED", {"cmd": cmd})

        if cmd == "/lists":
            lists_ = list_lists()
            return PlainTextResponse(
                "Lists:\n" + "\n".join(f"{l['id']}: {l['name']}" for l in lists_)
            )

        if cmd == "/newlist":
            if not arg:
                return PlainTextResponse("Usage: /newlist <name>")
            list_id = create_list(arg)
            audit(sender, "LIST_CREATED", {"list_id": list_id})
            return PlainTextResponse(f"Created list {list_id}.")

        if cmd == "/use":
            list_id = parse_int(arg)
            name = set_active_list(sender, list_id)
            audit(sender, "ACTIVE_LIST_SET", {"list_id": list_id})
            return PlainTextResponse(f"Active list set to {name}.")

        if cmd == "/todo":
            if not arg:
                return PlainTextResponse("Usage: /todo <text>")
            item_id = add_item(sender, arg)
            audit(sender, "ITEM_CREATED", {"item_id": item_id})
            return PlainTextResponse(f"Added item {item_id}.")

        if cmd == "/list":
            lst, items = get_items(sender)
            if not items:
                return PlainTextResponse(f"List {lst['name']} is empty.")
            lines = [f"List {lst['id']}: {lst['name']}"]
            for it in items:
                prefix = "✅" if it["status"] == "done" else "•"
                lines.append(f"{prefix} {it['id']}: {it['text']}")
            return PlainTextResponse("\n".join(lines))

        if cmd == "/done":
            item_id = parse_int(arg)
            mark_done(sender, item_id)
            audit(sender, "ITEM_DONE", {"item_id": item_id})
            return PlainTextResponse(f"Marked {item_id} done.")

        return PlainTextResponse(HELP_TEXT)

    except ValueError as e:
        audit(sender, "CMD_ERROR", {"error": str(e)})
        return PlainTextResponse(f"Error: {e}", status_code=400)
