import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List

DB_PATH = "data.db"


def init_db(path: Optional[str] = None):
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            price REAL NOT NULL,
            start_date TEXT,
            nights INTEGER,
            room TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_scan(price: float, start_date: str, nights: int, room: str, path: Optional[str] = None):
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO scans (ts, price, start_date, nights, room) VALUES (?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), price, start_date, nights, room),
    )
    conn.commit()
    conn.close()


def latest_scan(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    cur.execute("SELECT ts, price, start_date, nights, room FROM scans ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"ts": row[0], "price": row[1], "start_date": row[2], "nights": row[3], "room": row[4]}


def get_history(limit: int = 200, path: Optional[str] = None) -> List[Dict[str, Any]]:
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    cur.execute("SELECT ts, price, start_date, nights, room FROM scans ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    rows.reverse()
    return [
        {"ts": r[0], "price": r[1], "start_date": r[2], "nights": r[3], "room": r[4]}
        for r in rows
    ]
