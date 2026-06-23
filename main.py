import os
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from google import genai
from google.genai import errors
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- DATABASE SETUP (MEMORY LEVEL 1) ---
def init_db():
    conn = sqlite3.connect('chatbot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Run the database initialization right away
init_db()

async def save_message(user_id, role, content):
    def _save():
        conn = sqlite3.connect('chatbot.db')
        cursor = conn.cursor()
        cursor.execute('INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
        conn.commit()
        conn.close()
    await asyncio.to_thread(_save)

async def get_chat_history(user_id, limit=30):
    def _get():
        conn = sqlite3.connect('chatbot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content FROM (
                SELECT role, content, timestamp FROM chat_history 
                WHERE user_id = ? 
                ORDER BY timestamp DESC LIMIT ?
            ) ORDER BY timestamp ASC
        ''', (user_id, limit))
        rows = cursor.fetchall()
        conn.close()
        
        history_text = ""
        for role, content in rows:
            history_text += f"{role}: {content}\n"
        return history_text
    return await asyncio.to_thread(_get)
# ----------------------------------------

SYSTEM_INSTRUCTION = (
    "You are a friendly Telegram chat assistant. Keep all responses brief, direct, "
    "and under 3 sentences long. Avoid formatting long bullet points or essays. "
    "Be friendly but honest. Your response style should be identical to Claude's. "
    "Only claim to remember or know information if it exists explicitly in the Conversation History provided. "
    "If you have the exact info in history, answer 'yes'. If you have no info, be direct and say 'no' or that you don't know. "
    "Do not imagine or fabricate things unless specifically asked to create an image or a story."
)

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Assalomu alaykum! Senga qanday yordam bera olaman?")
        return

    # 1. Save the user's message to history
    await save_message(user_id, "User", user_query)

    # 2. Pull the last 30 messages for this user
    history = await get_chat_history(user_id, limit=30)

    # 3. Format the final context prompt for Gemini
    full_prompt = f"""Conversation History:
{history}

Current User Message: {user_query}"""

    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_prompt,
                config={'system_instruction': SYSTEM_
