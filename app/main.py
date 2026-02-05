import os
import time
import json
import sqlite3
import asyncio
from pathlib import Path
from typing import Optional, Tuple
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from datetime import datetime
from app.google_calendar import (
    build_flow,
    creds_from_row,
    ensure_fresh_creds,
    calendar_service,
    list_next_events,
)
from fastapi.responses import PlainTextResponse
from twilio.request_validator import RequestValidator

from app.ollama_client import ollama_suggest


# Load .env (robust)

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"  # project root .env
load_dotenv(dotenv_path=ENV_PATH)


# Config

DB_PATH = os.getenv("DB_PATH", "./assistant.db")

ALLOWED_SENDERS = {
    s.strip() for s in os.getenv("ALLOWED_SENDERS", "").split(",") if s.strip()
}
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))

TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "").strip()

SUGGEST_RATE_LIMIT_PER_MIN = int(os.getenv("SUGGEST_RATE_LIMIT_PER_MIN", "5"))
DEBUG_TWILIO_FORM_KEYS = os.getenv("DEBUG_TWILIO_FORM_KEYS", "0").strip() == "1"


# App

app = FastAPI(title="WhatsApp Task Assistant")


# Database

def get_db():
    # timeout helps avoid immediate "database is locked" under light concurrency
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    now = int(time.time())

    #Tabel
    
    #Make the auth table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS oauth_tokens (
        sender TEXT NOT NULL,
        provider TEXT NOT NULL,     -- 'google'
        access_token TEXT NOT NULL,
        refresh_token TEXT,
        token_uri TEXT NOT NULL,
        client_id TEXT NOT NULL,
        client_secret TEXT NOT NULL,
        scopes TEXT NOT NULL,       -- JSON array
        expiry_ts INTEGER,          -- epoch seconds
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (sender, provider)
    );
    """)
    
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS message_dedup (
        message_sid TEXT PRIMARY KEY,
        first_seen_ts INTEGER NOT NULL
    );
    """)

  #background job queue for slow tasks like /suggest
    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT NOT NULL,
        job_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','done','error')),
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        result TEXT,
        error TEXT
    );
    """)

    # Ensure default list exists
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


# Security

_rate_bucket = {}
_suggest_rate_bucket = {}

def rate_limit_ok(sender: str) -> bool:
    now = int(time.time())
    window = now // 60
    w, c = _rate_bucket.get(sender, (window, 0))
    if w != window:
        w, c = window, 0
    c += 1
    _rate_bucket[sender] = (w, c)
    return c <= RATE_LIMIT_PER_MIN

def suggest_rate_limit_ok(sender: str) -> bool:
    now = int(time.time())
    window = now // 60
    w, c = _suggest_rate_bucket.get(sender, (window, 0))
    if w != window:
        w, c = window, 0
    c += 1
    _suggest_rate_bucket[sender] = (w, c)
    return c <= SUGGEST_RATE_LIMIT_PER_MIN

def sender_allowed(sender: str) -> bool:
    return sender in ALLOWED_SENDERS

def twilio_signature_ok(request_url: str, form: dict, signature: str) -> bool:
    # Dev bypass: only active if Twilio vars are unset
    if not TWILIO_AUTH_TOKEN or not WEBHOOK_PUBLIC_URL:
        return True
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    return validator.validate(request_url, form, signature)

def register_message_sid(message_sid: str) -> bool:
    """
    Returns True if this MessageSid is a replay (already seen).
    Returns False if it's new (and stores it).
    """
    if not message_sid:
        return True

    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM message_dedup WHERE message_sid=?",
        (message_sid,)
    ).fetchone()

    if row:
        conn.close()
        return True

    conn.execute(
        "INSERT INTO message_dedup (message_sid, first_seen_ts) VALUES (?,?)",
        (message_sid, int(time.time()))
    )
    conn.commit()
    conn.close()
    return False

def upsert_google_tokens(sender: str, creds):
    conn = get_db()
    now = int(time.time())
    
    expiry_ts = None
    if getattr(creds, "expiry", None):
        try:
            expiry_ts = int(creds.expiry.timestamp())
        except Exception:
            expiry_ts = None
    conn.execute("""
    INSERT INTO oauth_tokens (
        sender, provider, access_token, refresh_token, token_uri, client_id, client_secret, scopes, expiry_ts, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(sender, provider) DO UPDATE SET
        access_token=excluded.access_token,
        refresh_token=COALESCE(excluded.refresh_token, oauth_tokens.refresh_token),
        token_uri=excluded.token_uri,
        client_id=excluded.client_id,
        client_secret=excluded.client_secret,
        scopes=excluded.scopes,
        expiry_ts=excluded.expiry_ts,
        updated_at=excluded.updated_at  
    """, (
        sender,"google",
        creds.token,
        creds.refresh_token,
        creds.token_uri,
        creds.client_id,
        creds.client_secret,
        json.dumps(list(creds.scopes or [])),
        expiry_ts,
        now,now,
    )) 
    conn.commit()
    conn.close()
def get_google_tokens(sender: str):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE sender=? AND provider='google'",
        (sender,)
    ).fetchone()
    conn.close()
    return row


# Parsing

ALLOWED_COMMANDS = {
    "/lists",
    "/newlist",
    "/use",
    "/todo",
    "/list",           # open only
    "/all",            # open + done
    "/done",
    "/suggest",        # enqueue suggestion job
    "/suggest_result", # fetch last suggestion result
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


# Core logic
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
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
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

def get_open_items_for_sender(sender: str, limit: int = 20):
    prefs = get_or_create_prefs(sender)
    conn = get_db()

    lst = conn.execute(
        "SELECT id,name FROM lists WHERE id=?",
        (prefs["active_list_id"],),
    ).fetchone()

    rows = conn.execute(
        """
        SELECT id, text, created_at
        FROM items
        WHERE list_id=? AND status='open'
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (prefs["active_list_id"], limit),
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


# Jobs: enqueue + fetch

def enqueue_job(sender: str, job_type: str, payload: dict) -> int:
    conn = get_db()
    now = int(time.time())
    conn.execute(
        "INSERT INTO jobs (sender, job_type, payload, status, created_at) VALUES (?,?,?,?,?)",
        (sender, job_type, json.dumps(payload), "queued", now),
    )
    conn.commit()
    job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return int(job_id)

def get_latest_job(sender: str, job_type: str):
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, status, result, error
        FROM jobs
        WHERE sender=? AND job_type=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (sender, job_type),
    ).fetchone()
    conn.close()
    return dict(row) if row else None

async def job_worker_loop():
    """
    Simple in-process worker for laptop dev.
    Later, this becomes a separate worker process in cloud.
    """
    while True:
        await asyncio.sleep(0.25)

        conn = get_db()
        job = conn.execute(
            """
            SELECT id, sender, payload
            FROM jobs
            WHERE status='queued' AND job_type='suggest'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()

        if not job:
            conn.close()
            continue

        job_id = int(job["id"])
        sender = job["sender"]
        payload = json.loads(job["payload"])

        now = int(time.time())
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (now, job_id),
        )
        conn.commit()
        conn.close()

        try:
            suggestion = await ollama_suggest(payload)
            conn = get_db()
            now = int(time.time())
            conn.execute(
                "UPDATE jobs SET status='done', finished_at=?, result=? WHERE id=?",
                (now, suggestion, job_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            conn = get_db()
            now = int(time.time())
            conn.execute(
                "UPDATE jobs SET status='error', finished_at=?, error=? WHERE id=?",
                (now, str(e), job_id),
            )
            conn.commit()
            conn.close()

# Webhook + Health
HELP_TEXT = (
    "Commands:\n"
    "/lists — show lists\n"
    "/newlist <name>\n"
    "/use <list_id>\n"
    "/todo <text>\n"
    "/list — show open items\n"
    "/all — show open + done\n"
    "/done <item_id>\n"
    "/suggest — generate suggestions (async)\n"
    "/suggest_result — fetch latest suggestion\n"
)

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/health/ollama")
async def health_ollama():
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{base}/api/tags")
        r.raise_for_status()
    return {"ok": True}

#For the google oauth start endpoint and the following stuff 
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/oauth/google/callback").strip()

@app.get("/oauth/google/start")
def google_oauth_start(sender: str):
    #Open a browser to this endpoint to start the google oauth flow
    flow = build_flow(redirect_uri=GOOGLE_REDIRECT_URI)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
        state=sender  # for demo purposes; in prod use a secure random state
    )
    
    return RedirectResponse(auth_url)
@app.get("/oauth/google/callback")
def google_oauth_callback(state: str, code: str):
    #Handle the oauth callback from google
    sender = state  # in prod, validate state properly

    flow = build_flow(redirect_uri=GOOGLE_REDIRECT_URI)
    flow.fetch_token(code=code)

    creds = flow.credentials
    upsert_google_tokens(sender, creds)

    return {"status": "success", "message": "Google OAuth successful. You can now use Google Calendar features.", "connected_sender": sender}

#Super quick this reads calendar after connection
@app.get("/calendar/test")
def calendar_test(sender: str):
    row = get_google_token_row(sender)
    if not row:
        raise HTTPException(status_code=401, detail="No Google OAuth tokens found for this sender.")
    creds = ensure_fresh_creds(creds_from_row(row))
    svc = calendar_service(creds)
    
    now = datetime.now(timezone.utc).isoformat()
    events = list_next_events(svc, time_min_iso=now, max_results=5)
    
    simplified = []
    for e in events:
        start = (e.get('start', {}).get('dateTime') or e.get('start', {}).get('date'))
        simplified.append({"summary": e.get('summary'), "start": start})
    return {"okay": True, "events": simplified}
    

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(job_worker_loop())

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = dict(await request.form())
    sender = form.get("From", "")
    body = form.get("Body", "")

    if DEBUG_TWILIO_FORM_KEYS:
        audit(sender, "DEBUG_FORM_KEYS", {"keys": sorted(list(form.keys()))})
        audit(sender, "DEBUG_SID_FIELDS", {
            "MessageSid": form.get("MessageSid"),
            "SmsMessageSid": form.get("SmsMessageSid"),
        })

    # 1) Twilio signature verification
    signature = request.headers.get("X-Twilio-Signature", "")
    request_url = WEBHOOK_PUBLIC_URL or str(request.url)
    if not twilio_signature_ok(request_url, form, signature):
        audit(sender, "AUTH_FAIL", {"reason": "bad_twilio_signature"})
        raise HTTPException(status_code=403, detail="Forbidden")

    # 2) Replay protection
    message_sid = form.get("MessageSid") or form.get("SmsMessageSid") or ""
    if register_message_sid(message_sid):
        audit(sender, "REPLAY_IGNORED", {"message_sid": message_sid})
        return PlainTextResponse("Duplicate ignored.", status_code=200)

    # 3) Audit
    audit(sender, "MSG_RECEIVED", {"len": len(body)})

    # 4) Sender allowlist
    if not sender_allowed(sender):
        audit(sender, "AUTH_FAIL", {"reason": "sender_not_allowed"})
        raise HTTPException(status_code=403, detail="Forbidden")

    # 5) Rate limit
    if not rate_limit_ok(sender):
        audit(sender, "RATE_LIMIT", {"scope": "general"})
        return PlainTextResponse("Rate limit exceeded.", status_code=429)

    # 6) Parse command
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
            return PlainTextResponse("Lists:\n" + "\n".join(f"{l['id']}: {l['name']}" for l in lists_))

        if cmd == "/newlist":
            if not arg:
                return PlainTextResponse("Usage: /newlist <name>\nExample: /newlist school")
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
                return PlainTextResponse("Usage: /todo <text>\nExample: /todo buy eggs")
            item_id = add_item(sender, arg)
            audit(sender, "ITEM_CREATED", {"item_id": item_id})
            return PlainTextResponse(f"Added item {item_id}.")

        if cmd == "/list":
            lst, items = get_items(sender)
            open_items = [it for it in items if it["status"] == "open"]
            if not open_items:
                return PlainTextResponse(f"List {lst['name']} has no open items.")
            lines = [f"List {lst['id']}: {lst['name']} (open)"]
            for it in open_items:
                lines.append(f"• {it['id']}: {it['text']}")
            return PlainTextResponse("\n".join(lines))

        if cmd == "/all":
            lst, items = get_items(sender)
            if not items:
                return PlainTextResponse(f"List {lst['name']} is empty.")
            lines = [f"List {lst['id']}: {lst['name']} (all)"]
            for it in items:
                prefix = "✅" if it["status"] == "done" else "•"
                lines.append(f"{prefix} {it['id']}: {it['text']}")
            return PlainTextResponse("\n".join(lines))

        if cmd == "/done":
            item_id = parse_int(arg)
            mark_done(sender, item_id)
            audit(sender, "ITEM_DONE", {"item_id": item_id})
            return PlainTextResponse(f"Marked {item_id} done.")

        if cmd == "/suggest":
            if not suggest_rate_limit_ok(sender):
                audit(sender, "RATE_LIMIT", {"scope": "suggest"})
                return PlainTextResponse("You’re spamming /suggest. Try again in a bit.", status_code=429)

            lst, open_items = get_open_items_for_sender(sender, limit=20)
            payload = {
                "list": {"id": lst["id"], "name": lst["name"]},
                "open_items": [{"id": x["id"], "text": x["text"]} for x in open_items],
                "supported_commands": sorted(list(ALLOWED_COMMANDS)),
                "advisory_only": True,
            }

            job_id = enqueue_job(sender, "suggest", payload)
            audit(sender, "SUGGEST_ENQUEUED", {"job_id": job_id, "n_open": len(open_items)})

            return PlainTextResponse(f"Generating suggestion (job {job_id}). Reply /suggest_result in ~10 seconds.")

        if cmd == "/suggest_result":
            job = get_latest_job(sender, "suggest")
            if not job:
                return PlainTextResponse("No suggestion yet. Send /suggest first.")

            if job["status"] in ("queued", "running"):
                return PlainTextResponse(f"Still working (job {job['id']}). Try again in a few seconds.")

            if job["status"] == "error":
                return PlainTextResponse(f"Suggestion failed (job {job['id']}): {job.get('error','unknown error')}")

            return PlainTextResponse("Suggestion:\n" + (job.get("result") or "Empty suggestion."))

        return PlainTextResponse(HELP_TEXT)

    except ValueError as e:
        audit(sender, "CMD_ERROR", {"error": str(e)})
        return PlainTextResponse(f"Error: {e}", status_code=400)
