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
from duckduckgo_search import DDGS

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

# --- RATE LIMITER MIDDLEWARE ---
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 2.0):
        self.limit = limit
        self.cache = {}

    async def __call__(self, handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]], event: Message, data: Dict[str, Any]) -> Any:
        user_id = event.from_user.id
        now = time.time()
        if now - self.cache.get(user_id, 0) < self.limit:
            return # Block spam
        self.cache[user_id] = now
        return await handler(event, data)

dp.message.middleware(ThrottlingMiddleware(limit=2.0))

# --- GEMINI ROTATOR ---
class GeminiManager:
    def __init__(self, keys):
        self.keys = itertools.cycle(keys)
        self.rotate()

    def rotate(self):
        self.current_key = next(self.keys)
        genai.configure(api_key=self.current_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

gemini = GeminiManager(GEMINI_KEYS)

SYSTEM_INSTRUCTION = (
    "Sen o'zbek tilida mukammal so'zlashadigan do'stsan. "
    "Muloqot uslubing: 1. Insoniy, samimiy va hazilkash bo'l. "
    "2. Grammatikani to'g'ri qo'lla. "
    "3. O'zbekcha tabiiy so'zlashuvdan (Qalay, nima gap?) foydalan. "
    "4. Javoblaring qisqa, o'tkir va insoniy bo'lsin."
)

# --- DATABASE & HANDLERS ---
async def get_history_context(user_id):
    rows = await history_col.find({"user_id": user_id}).sort("_id", -1).limit(5).to_list(length=5)
    return [{"role": "user" if r["role"] == "User" else "model", "parts": [r["content"]]} for r in reversed(rows)]

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.reply("Assalomu alaykum! Men Qadamman.")

@dp.message(Command("voice", "ovoz"))
async def handle_voice(msg: Message):
    user_id = msg.chat.id
    if voice_usage.get(user_id, 0) >= VOICE_LIMIT:
        await msg.reply("Ovozli xabar limiti tugadi. Hozircha faqat matn!")
        return
    
    hist = await get_history_context(user_id)
    text = hist[-1]["parts"][0] if hist else "Assalomu alaykum."
    voice_usage[user_id] = voice_usage.get(user_id, 0) + 1
    
    path = f"voice_{user_id}.mp3"
    await edge_tts.Communicate(text, "uz-UZ-MadinaNeural").save(path)
    await msg.reply_voice(voice=types.FSInputFile(path))
    if os.path.exists(path): os.remove(path)

@dp.message(F.text)
async def chat(msg: Message):
    await bot.send_chat_action(msg.chat.id, "typing")
    try:
        chat_session = gemini.model.start_chat(history=await get_history_context(msg.chat.id))
        res = chat_session.send_message(f"{SYSTEM_INSTRUCTION}\n\nFoydalanuvchi: {msg.text}")
        await history_col.insert_one({"user_id": msg.chat.id, "role": "User", "content": msg.text})
        await history_col.insert_one({"user_id": msg.chat.id, "role": "AI", "content": res.text})
        await msg.reply(res.text)
    except Exception as e:
        if "429" in str(e):
            gemini.rotate()
            await chat(msg)
        else:
            await msg.reply("Texnik muammo yuz berdi.")

async def main():
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
