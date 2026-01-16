import sqlite3

conn = sqlite3.connect("assistant.db")
cur = conn.cursor()

print("Tables:")
print(cur.execute(
    "select name from sqlite_master where type='table' order by name"
).fetchall())

print("\nmessage_dedup exists:")
print(cur.execute(
    "select name from sqlite_master where type='table' and name='message_dedup'"
).fetchall())

print("\nmessage_dedup count:")
try:
    print(cur.execute("select count(*) from message_dedup").fetchone())
except Exception as e:
    print("ERROR:", e)

print("\nLast 10 audit events:")
print(cur.execute(
    "select event_type, detail from audit_log order by id desc limit 10"
).fetchall())

conn.close()
