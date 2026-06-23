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