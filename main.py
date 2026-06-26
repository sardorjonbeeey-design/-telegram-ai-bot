import os
import asyncio
import logging
import itertools
import time
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message
from typing import Callable, Dict, Any, Awaitable
import google.generativeai as genai
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")
GEMINI_KEYS = os.environ.get("GEMINI_KEYS", "").split(",")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = AsyncIOMotorClient(MONGODB_URI)["qadam_db"]
history_col = db["history"]
voice_usage = {}
VOICE_LIMIT = 20 

# --- MIDDLEWARE & GEMINI ---
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 2.0):
        self.limit = limit
        self.cache = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        now = time.time()
        if now - self.cache.get(user_id, 0) < self.limit:
            return
        self.cache[user_id] = now
        return await handler(event, data)

dp.message.middleware(ThrottlingMiddleware(limit=2.0))

class GeminiManager:
    def __init__(self, keys):
        self.keys = itertools.cycle(keys)
        self.rotate()

    def rotate(self):
        self.current_key = next(self.keys)
        genai.configure(api_key=self.current_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

gemini = GeminiManager(GEMINI_KEYS)

SYSTEM_INSTRUCTION = "Sen Qadamsan, foydalanuvchining eng yaqin, samimiy do'stisan. Senlashib gaplash, o'zbekcha jonli so'zlashuv uslubini qo'lla, rasmiyatchilikdan qoch, qisqa va insoniy javob ber."

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.reply("Assalomu alaykum! Men Qadamman.")

@dp.message(F.text)
async def chat(msg: Message):
    await bot.send_chat_action(msg.chat.id, "typing")
    try:
        chat_session = gemini.model.start_chat(history=[])
        res = chat_session.send_message(f"{SYSTEM_INSTRUCTION}\n\nFoydalanuvchi: {msg.text}")
        await history_col.insert_one({"user_id": msg.chat.id, "role": "AI", "content": res.text})
        await msg.reply(res.text)
    except Exception as e:
        if "429" in str(e):
            gemini.rotate()
            await chat(msg)
        else:
            await msg.reply("Texnik muammo yuz berdi.")

# --- WEB SERVER (For Render) ---
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- ENTRY POINT ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
