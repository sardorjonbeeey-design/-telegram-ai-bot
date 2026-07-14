import aiosqlite
from datetime import datetime


DB_NAME = "bot.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            created_at TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            product TEXT,
            description TEXT,
            location TEXT,
            photos TEXT,
            created_at TEXT
        )
        """)

        await db.commit()


async def add_user(telegram_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            """
            INSERT OR IGNORE INTO users
            (telegram_id, created_at)
            VALUES (?, ?)
            """,
            (
                telegram_id,
                datetime.now().isoformat()
            )
        )

        await db.commit()


async def save_listing(
    telegram_id: int,
    product: str,
    description: str,
    location: str,
    photos: str
):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            """
            INSERT INTO listings
            (
                telegram_id,
                product,
                description,
                location,
                photos,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                product,
                description,
                location,
                photos,
                datetime.now().isoformat()
            )
        )

        await db.commit()