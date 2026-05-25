import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

DB_PATH = "data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Пользователи
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        is_banned INTEGER DEFAULT 0,
        exchange_channel_sub INTEGER DEFAULT 0,
        exchange_group_sub INTEGER DEFAULT 0,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Каналы пользователей (которые они хотят пиарить)
    c.execute('''CREATE TABLE IF NOT EXISTS channels (
        channel_id INTEGER PRIMARY KEY,
        owner_id INTEGER,
        username TEXT,
        title TEXT,
        bot_is_admin INTEGER DEFAULT 0,
        FOREIGN KEY(owner_id) REFERENCES users(user_id)
    )''')
    
    # Накопленные поинты пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS user_points (
        user_id INTEGER PRIMARY KEY,
        total_points INTEGER DEFAULT 0,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # Коины пользователей (для магазина)
    c.execute('''CREATE TABLE IF NOT EXISTS user_coins (
        user_id INTEGER PRIMARY KEY,
        total_coins INTEGER DEFAULT 0,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # Текущий раунд (активный пиар канала)
    c.execute('''CREATE TABLE IF NOT EXISTS current_round (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        channel_id INTEGER,
        start_time TIMESTAMP,
        end_time TIMESTAMP,
        status TEXT DEFAULT 'active',
        waiting_for_admin INTEGER DEFAULT 0,
        start_message_id INTEGER DEFAULT 0
    )''')
    
    # Участники текущего раунда (исполнители)
    c.execute('''CREATE TABLE IF NOT EXISTS round_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        invite_link TEXT,
        invite_link_name TEXT,
        points INTEGER DEFAULT 0,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # История завершённых раундов
    c.execute('''CREATE TABLE IF NOT EXISTS round_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        winner_user_id INTEGER,
        winner_points INTEGER,
        round_start TIMESTAMP,
        round_end TIMESTAMP,
        status TEXT,
        channel_id INTEGER
    )''')
    
    # Ожидание подтверждения победителя
    c.execute('''CREATE TABLE IF NOT EXISTS pending_winner (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        user_id INTEGER,
        round_history_id INTEGER,
        expires_at TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # Жалобы
    c.execute('''CREATE TABLE IF NOT EXISTS complaints (
        complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user_id INTEGER,
        against_user_id INTEGER,
        round_id INTEGER,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        admin_comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP
    )''')
    
    # Чёрный список
    c.execute('''CREATE TABLE IF NOT EXISTS blacklist (
        user_id INTEGER PRIMARY KEY,
        reason TEXT,
        banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()

# ----- Пользователи и поинты -----
async def register_user(user_id: int, username: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    c.execute("INSERT OR IGNORE INTO user_points (user_id, total_points) VALUES (?, 0)", (user_id,))
    c.execute("INSERT OR IGNORE INTO user_coins (user_id, total_coins) VALUES (?, 0)", (user_id,))
    conn.commit()
    conn.close()

async def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

async def update_user_balance(user_id: int, delta: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE user_points SET total_points = total_points + ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

async def get_user_points(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT total_points FROM user_points WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

async def reset_user_points(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE user_points SET total_points = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

async def set_subscription_status(user_id: int, channel_type: str, status: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if channel_type == 'channel':
        c.execute("UPDATE users SET exchange_channel_sub = ? WHERE user_id = ?", (1 if status else 0, user_id))
    else:
        c.execute("UPDATE users SET exchange_group_sub = ? WHERE user_id = ?", (1 if status else 0, user_id))
    conn.commit()
    conn.close()

async def is_user_banned(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

async def ban_user(user_id: int, reason: str = ""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO blacklist (user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()

async def unban_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ----- Каналы -----
async def add_channel(owner_id: int, channel_id: int, username: str, title: str, bot_is_admin: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO channels (channel_id, owner_id, username, title, bot_is_admin) VALUES (?,?,?,?,?)",
              (channel_id, owner_id, username, title, 1 if bot_is_admin else 0))
    conn.commit()
    conn.close()

async def get_user_channel(owner_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM channels WHERE owner_id = ?", (owner_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

async def get_channel_by_id(channel_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

async def check_bot_admin_rights(channel_id: int, bot) -> bool:
    """Проверяет, является ли бот администратором канала и имеет ли право приглашать пользователей."""
    try:
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            return False
        if bot_member.status == 'administrator' and not bot_member.can_invite_users:
            return False
        return True
    except Exception as e:
        print(f"Ошибка при проверке прав бота в канале {channel_id}: {e}")
        return False

# ----- Текущий раунд -----
async def create_round(channel_id: int, start_time: datetime, end_time: datetime, waiting_for_admin: bool = True, start_message_id: int = 0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM current_round WHERE id = 1")
    c.execute("INSERT INTO current_round (id, channel_id, start_time, end_time, status, waiting_for_admin, start_message_id) VALUES (1, ?, ?, ?, 'active', ?, ?)",
              (channel_id, start_time, end_time, 1 if waiting_for_admin else 0, start_message_id))
    conn.commit()
    conn.close()

async def get_current_round() -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM current_round WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None

async def update_round_status(status: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE current_round SET status = ? WHERE id = 1", (status,))
    conn.commit()
    conn.close()

async def set_round_waiting_for_admin(waiting: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE current_round SET waiting_for_admin = ? WHERE id = 1", (1 if waiting else 0,))
    conn.commit()
    conn.close()

async def set_round_start_message_id(message_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE current_round SET start_message_id = ? WHERE id = 1", (message_id,))
    conn.commit()
    conn.close()

async def delete_current_round():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM current_round WHERE id = 1")
    conn.commit()
    conn.close()

# ----- Участники раунда -----
async def add_participant(user_id: int, invite_link: str, invite_link_name: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO round_participants (user_id, invite_link, invite_link_name) VALUES (?, ?, ?)",
              (user_id, invite_link, invite_link_name))
    conn.commit()
    conn.close()

async def get_participants() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute("SELECT * FROM round_participants").fetchall()
    conn.close()
    return [dict(row) for row in rows]

async def get_participant(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM round_participants WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

async def get_participant_by_invite_link(invite_link: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM round_participants WHERE invite_link = ?", (invite_link,)).fetchone()
    conn.close()
    return dict(row) if row else None

async def update_participant_points(user_id: int, points: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE round_participants SET points = ? WHERE user_id = ?", (points, user_id))
    conn.commit()
    conn.close()

async def clear_participants():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM round_participants")
    conn.commit()
    conn.close()

# ----- История раундов -----
async def add_round_history(winner_user_id: int, winner_points: int, start_time: datetime, end_time: datetime, status: str, channel_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO round_history (winner_user_id, winner_points, round_start, round_end, status, channel_id) VALUES (?, ?, ?, ?, ?, ?)",
              (winner_user_id, winner_points, start_time, end_time, status, channel_id))
    history_id = c.lastrowid
    conn.commit()
    conn.close()
    return history_id

async def get_last_round_history() -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM round_history ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None

# ----- Ожидание победителя -----
async def set_pending_winner(user_id: int, round_history_id: int, expires_at: datetime):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM pending_winner WHERE id = 1")
    c.execute("INSERT INTO pending_winner (id, user_id, round_history_id, expires_at) VALUES (1, ?, ?, ?)",
              (user_id, round_history_id, expires_at))
    conn.commit()
    conn.close()

async def get_pending_winner() -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM pending_winner WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None

async def clear_pending_winner():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM pending_winner WHERE id = 1")
    conn.commit()
    conn.close()

# ----- Жалобы -----
async def add_complaint(from_user_id: int, against_user_id: int, round_id: int, reason: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO complaints (from_user_id, against_user_id, round_id, reason) VALUES (?, ?, ?, ?)",
              (from_user_id, against_user_id, round_id, reason))
    conn.commit()
    conn.close()

async def get_pending_complaints() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute("SELECT * FROM complaints WHERE status = 'pending' ORDER BY created_at").fetchall()
    conn.close()
    return [dict(row) for row in rows]

async def resolve_complaint(complaint_id: int, accept: bool, admin_comment: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    status = 'resolved' if accept else 'rejected'
    c.execute("UPDATE complaints SET status = ?, admin_comment = ?, resolved_at = CURRENT_TIMESTAMP WHERE complaint_id = ?",
              (status, admin_comment, complaint_id))
    conn.commit()
    conn.close()

async def get_user_complaints_today(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().date()
    c.execute("SELECT COUNT(*) FROM complaints WHERE from_user_id = ? AND date(created_at) = ?", (user_id, today))
    count = c.fetchone()[0]
    conn.close()
    return count

# ----- Коины -----
async def add_coins(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE user_coins SET total_coins = total_coins + ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?", (amount, user_id))
    if c.rowcount == 0:
        c.execute("INSERT INTO user_coins (user_id, total_coins) VALUES (?, ?)", (user_id, amount))
    conn.commit()
    conn.close()

async def get_user_coins(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT total_coins FROM user_coins WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

async def convert_points_to_coins(user_id: int, points: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT total_points FROM user_points WHERE user_id = ? AND total_points >= ?", (user_id, points))
    if not c.fetchone():
        conn.close()
        return False
    c.execute("UPDATE user_points SET total_points = total_points - ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?", (points, user_id))
    c.execute("UPDATE user_coins SET total_coins = total_coins + ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?", (points, user_id))
    if c.rowcount == 0:
        c.execute("INSERT INTO user_coins (user_id, total_coins) VALUES (?, ?)", (user_id, points))
    conn.commit()
    conn.close()
    return True

# ----- Административные -----
async def get_all_users() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute("SELECT u.user_id, u.username, u.is_banned, up.total_points, u.exchange_channel_sub, u.exchange_group_sub FROM users u LEFT JOIN user_points up ON u.user_id = up.user_id").fetchall()
    conn.close()
    return [dict(row) for row in rows]

async def get_blacklist() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute("SELECT * FROM blacklist ORDER BY banned_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]

async def get_round_history_list(limit: int = 10) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute("SELECT * FROM round_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]