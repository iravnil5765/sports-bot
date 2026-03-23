import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'picks.db')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS picks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT NOT NULL,
            sport         TEXT NOT NULL,
            home_team     TEXT NOT NULL,
            away_team     TEXT NOT NULL,
            bet_type      TEXT NOT NULL,
            bet_on        TEXT NOT NULL,
            bet_label     TEXT NOT NULL,
            odds          REAL NOT NULL,
            units         REAL NOT NULL,
            ev            REAL NOT NULL,
            result        TEXT DEFAULT 'PENDING',
            discord_msg_id TEXT,
            game_id       TEXT,
            commence_time TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS bankroll (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            amount     REAL NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Seed bankroll if first run
    c.execute('SELECT COUNT(*) FROM bankroll')
    if c.fetchone()[0] == 0:
        import config
        c.execute('INSERT INTO bankroll (amount) VALUES (?)', (config.STARTING_BANKROLL,))
    conn.commit()
    conn.close()


def add_pick(date, sport, home_team, away_team, bet_type, bet_on, bet_label,
             odds, units, ev, game_id, commence_time):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO picks
            (date, sport, home_team, away_team, bet_type, bet_on, bet_label,
             odds, units, ev, game_id, commence_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (date, sport, home_team, away_team, bet_type, bet_on, bet_label,
          odds, units, ev, game_id, commence_time))
    pick_id = c.lastrowid
    conn.commit()
    conn.close()
    return pick_id


def set_message_id(pick_id, message_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE picks SET discord_msg_id = ? WHERE id = ?', (str(message_id), pick_id))
    conn.commit()
    conn.close()


def grade_pick(pick_id, result):
    """Grade a pick WIN / LOSS / VOID. Returns (success, new_bankroll)."""
    import config
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT odds, units, result FROM picks WHERE id = ?', (pick_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, None
    odds, units, old_result = row
    if old_result != 'PENDING':
        conn.close()
        return False, None

    c.execute('UPDATE picks SET result = ? WHERE id = ?', (result, pick_id))

    c.execute('SELECT amount FROM bankroll ORDER BY id DESC LIMIT 1')
    bankroll = c.fetchone()[0]
    unit_value = config.STARTING_BANKROLL * 0.01

    if result == 'WIN':
        bankroll += units * unit_value * (odds - 1)
    elif result == 'LOSS':
        bankroll -= units * unit_value
    # VOID — no change

    c.execute('INSERT INTO bankroll (amount) VALUES (?)', (bankroll,))
    conn.commit()
    conn.close()
    return True, bankroll


def get_bankroll():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT amount FROM bankroll ORDER BY id DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    return row[0] if row else 500.0


def get_record():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT
            COUNT(CASE WHEN result = 'WIN'     THEN 1 END),
            COUNT(CASE WHEN result = 'LOSS'    THEN 1 END),
            COUNT(CASE WHEN result = 'VOID'    THEN 1 END),
            COUNT(CASE WHEN result = 'PENDING' THEN 1 END)
        FROM picks
    ''')
    w, l, v, p = c.fetchone()
    conn.close()
    return {'wins': w, 'losses': l, 'voids': v, 'pending': p}


def get_recent_picks(limit=10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM picks ORDER BY created_at DESC LIMIT ?', (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_pending_picks():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM picks WHERE result = "PENDING" ORDER BY commence_time ASC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_pick(pick_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM picks WHERE id = ?', (pick_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_today_picks():
    today = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM picks WHERE date = ? ORDER BY id ASC', (today,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
