import os, sqlite3

db = os.getenv("DB_PATH", "assistant.db")
print("Using DB:", db)

con = sqlite3.connect(db)
cur = con.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS google_tokens (
    sender TEXT PRIMARY KEY,
    access_token TEXT,
    refresh_token TEXT,
    token_uri TEXT,
    client_id TEXT,
    client_secret TEXT,
    scopes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

con.commit()
print("google_tokens table created")
