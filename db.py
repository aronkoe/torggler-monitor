import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List

DB_PATH = "data.db"
OPTIONAL_SCAN_COLUMNS = {
    "following_night_price": "REAL",
    "following_night_offer": "TEXT",
    "two_night_total": "REAL",
    "two_night_offer": "TEXT",
    "two_night_start": "TEXT",
    "two_night_room": "TEXT",
    "three_night_total": "REAL",
    "three_night_offer": "TEXT",
    "three_night_start": "TEXT",
    "three_night_room": "TEXT",
}


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
            room TEXT,
            following_night_price REAL,
            following_night_offer TEXT,
            two_night_total REAL,
            two_night_offer TEXT,
            two_night_start TEXT,
            two_night_room TEXT,
            three_night_total REAL,
            three_night_offer TEXT,
            three_night_start TEXT,
            three_night_room TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(scans)")
    existing_columns = {row[1] for row in cur.fetchall()}
    for column, column_type in OPTIONAL_SCAN_COLUMNS.items():
        if column not in existing_columns:
            cur.execute(f"ALTER TABLE scans ADD COLUMN {column} {column_type}")
    conn.commit()
    conn.close()


def save_scan(
    price: float,
    start_date: str,
    nights: int,
    room: str,
    path: Optional[str] = None,
    following_night_price: Optional[float] = None,
    following_night_offer: Optional[str] = None,
    two_night_total: Optional[float] = None,
    two_night_offer: Optional[str] = None,
    two_night_start: Optional[str] = None,
    two_night_room: Optional[str] = None,
    three_night_total: Optional[float] = None,
    three_night_offer: Optional[str] = None,
    three_night_start: Optional[str] = None,
    three_night_room: Optional[str] = None,
):
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    now_iso = datetime.utcnow().isoformat()
    today_prefix = now_iso[:10]

    cur.execute(
        """
        SELECT id FROM scans
        WHERE substr(ts, 1, 10) = ?
          AND price = ?
          AND start_date = ?
          AND nights = ?
          AND room = ?
        ORDER BY id DESC LIMIT 1
        """,
        (today_prefix, price, start_date, nights, room),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE scans
            SET ts = ?, following_night_price = ?, following_night_offer = ?,
                two_night_total = ?, two_night_offer = ?, two_night_start = ?,
                two_night_room = ?, three_night_total = ?, three_night_offer = ?,
                three_night_start = ?, three_night_room = ?
            WHERE id = ?
            """,
            (
                now_iso,
                following_night_price,
                following_night_offer,
                two_night_total,
                two_night_offer,
                two_night_start,
                two_night_room,
                three_night_total,
                three_night_offer,
                three_night_start,
                three_night_room,
                row[0],
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO scans (
                ts, price, start_date, nights, room,
                following_night_price, following_night_offer,
                two_night_total, two_night_offer, two_night_start, two_night_room,
                three_night_total, three_night_offer, three_night_start, three_night_room
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso,
                price,
                start_date,
                nights,
                room,
                following_night_price,
                following_night_offer,
                two_night_total,
                two_night_offer,
                two_night_start,
                two_night_room,
                three_night_total,
                three_night_offer,
                three_night_start,
                three_night_room,
            ),
        )
    conn.commit()
    conn.close()


def latest_scan(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    p = path or DB_PATH
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ts, price, start_date, nights, room,
               following_night_price, following_night_offer,
               two_night_total, two_night_offer, two_night_start, two_night_room,
               three_night_total, three_night_offer, three_night_start, three_night_room
        FROM scans ORDER BY id DESC LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "ts": row[0],
        "price": row[1],
        "start_date": row[2],
        "nights": row[3],
        "room": row[4],
        "following_night_price": row[5],
        "following_night_offer": row[6],
        "two_night_total": row[7],
        "two_night_offer": row[8],
        "two_night_start": row[9],
        "two_night_room": row[10],
        "three_night_total": row[11],
        "three_night_offer": row[12],
        "three_night_start": row[13],
        "three_night_room": row[14],
    }


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
