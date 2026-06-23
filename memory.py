import sqlite3


def save_message(user_id, role, content):
    conn = sqlite3.connect("chat_memory.db")
    c = conn.cursor()

    c.execute(
        "INSERT INTO messages(user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )

    conn.commit()
    conn.close()


def get_recent_messages(user_id):
    conn = sqlite3.connect("chat_memory.db")
    c = conn.cursor()

    c.execute("""
    SELECT role, content 
    FROM messages
    WHERE user_id=?
    ORDER BY id DESC
    LIMIT 200
    """, (user_id,))

    data = c.fetchall()
    conn.close()

    return list(reversed(data))