import sqlite3
import datetime

def init_db():
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS clock_times (
                 user_id INTEGER,
                 date TEXT,
                 clock_in TEXT,
                 clock_out TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS timestamps (
                 id INTEGER PRIMARY KEY,
                 base_timestamp TEXT)''')
    c.execute("""
        CREATE TABLE IF NOT EXISTS clock_times_sas (
            user_id INTEGER,
            date TEXT,
            clock_in TEXT,
            clock_out TEXT
        )
    """)
    try:
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.execute("PRAGMA wal_autocheckpoint=1000;")      # checkpoint roughly every ~1k pages
        c.execute("PRAGMA journal_size_limit=10485760;")   # cap WAL to ~10 MB
        c.execute("PRAGMA temp_store=MEMORY;")             # avoid /tmp writes
        c.execute("PRAGMA auto_vacuum=INCREMENTAL;")
    except Exception:
        pass
    conn.commit()
    conn.close()


def checkpoint_and_vacuum() -> None:
    conn = sqlite3.connect('clock_times.db')
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("VACUUM;")
    finally:
        conn.close()

def db_stats() -> str:
    try:
        conn = sqlite3.connect('clock_times.db')
        cur = conn.cursor()
        cur.execute("PRAGMA page_count"); pages = cur.fetchone()[0]
        cur.execute("PRAGMA page_size"); psize = cur.fetchone()[0]
        cur.execute("PRAGMA freelist_count"); freep = cur.fetchone()[0]
        conn.close()
        size = pages * psize
        free = freep * psize
        return f"size={size}B free={free}B page_size={psize}"
    except Exception as e:
        return f"stats_error:{e}"

def add_clock_in(user_id, date, clock_in):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    c.execute("INSERT INTO clock_times (user_id, date, clock_in, clock_out) VALUES (?, ?, ?, ?)",
              (user_id, date, clock_in, None))
    conn.commit()
    conn.close()

def update_clock_out(user_id, date, clock_out, start_time: str | None = None):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    if start_time:
        # Close only the session that started at start_time
        c.execute(
            "UPDATE clock_times SET clock_out = ? WHERE user_id = ? AND date = ? AND clock_in = ?",
            (clock_out, user_id, date, start_time)
        )
    else:
        # Legacy: close the most recent open session
        c.execute(
            "UPDATE clock_times SET clock_out = ? WHERE user_id = ? AND date = ? AND clock_out IS NULL",
            (clock_out, user_id, date)
        )
    conn.commit()
    conn.close()

def get_clock_times(user_id, date):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    c.execute("SELECT clock_in, clock_out FROM clock_times WHERE user_id = ? AND date = ? ORDER BY clock_in", (user_id, date))
    rows = c.fetchall()
    conn.close()
    return rows

def get_ongoing_sessions(user_id=None):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    if user_id:
        c.execute("SELECT user_id, date, clock_in FROM clock_times WHERE user_id = ? AND clock_out IS NULL ORDER BY clock_in", (user_id,))
    else:
        c.execute("SELECT user_id, date, clock_in FROM clock_times WHERE clock_out IS NULL ORDER BY clock_in")
    rows = c.fetchall()
    conn.close()
    return rows

def remove_session(user_id, date, clock_in):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    c.execute("DELETE FROM clock_times WHERE user_id = ? AND date = ? AND clock_in = ?", (user_id, date, clock_in))
    conn.commit()
    conn.close()

def get_punish_count(user_id):
    conn = sqlite3.connect('punishments.db')
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS punishments (user_id INTEGER PRIMARY KEY, count INTEGER)")
    cursor.execute("SELECT count FROM punishments WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def reset_punish_count(user_id):
    conn = sqlite3.connect('punishments.db')
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS punishments (user_id INTEGER PRIMARY KEY, count INTEGER)")
    cursor.execute("UPDATE punishments SET count = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def increment_punish_count(user_id):
    conn = sqlite3.connect('punishments.db')
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS punishments (user_id INTEGER PRIMARY KEY, count INTEGER)")
    current_count = get_punish_count(user_id)
    new_count = current_count + 1
    cursor.execute("INSERT OR REPLACE INTO punishments (user_id, count) VALUES (?, ?)", (user_id, new_count))
    conn.commit()
    conn.close()
    return new_count

# ---------- SAS clock functions ----------
def add_clock_in_sas(user_id: int, date: str, clock_in: str):
    conn = sqlite3.connect('clock_times.db'); cur = conn.cursor()
    cur.execute(
        "INSERT INTO clock_times_sas (user_id, date, clock_in, clock_out) VALUES (?, ?, ?, NULL)",
        (user_id, date, clock_in)
    )
    conn.commit(); conn.close()

def update_clock_out_sas(user_id: int, date: str, clock_out: str, start_time: str | None = None):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    if start_time:
        # Close only the session that started at start_time
        c.execute(
            "UPDATE clock_times_sas SET clock_out=? WHERE user_id=? AND date=? AND clock_in=?",
            (clock_out, user_id, date, start_time)
        )
    else:
        # Fallback: close the most recent open session
        c.execute(
            "UPDATE clock_times_sas SET clock_out=? WHERE user_id=? AND date=? AND clock_out IS NULL",
            (clock_out, user_id, date)
        )
    conn.commit()
    conn.close()

def get_clock_times_sas(user_id: int, date: str):
    conn = sqlite3.connect('clock_times.db'); cur = conn.cursor()
    cur.execute(
        "SELECT clock_in, clock_out FROM clock_times_sas WHERE user_id=? AND date=? ORDER BY clock_in",
        (user_id, date)
    )
    rows = cur.fetchall(); conn.close()
    return rows

def get_ongoing_sessions_sas():
    conn = sqlite3.connect('clock_times.db'); cur = conn.cursor()
    cur.execute("SELECT user_id, date, clock_in FROM clock_times_sas WHERE clock_out IS NULL ORDER BY clock_in")
    rows = cur.fetchall(); conn.close()
    return rows

def remove_session_sas(user_id, date, clock_in):
    conn = sqlite3.connect('clock_times.db')
    c = conn.cursor()
    c.execute("DELETE FROM clock_times_sas WHERE user_id = ? AND date = ? AND clock_in = ?", (user_id, date, clock_in))
    conn.commit()
    conn.close()
