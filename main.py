import os
import asyncio
import logging
import itertools
import google.generativeai as genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.ratelimiter import RateLimiterMiddleware
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from duckduckgo_search import DDGS

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")
# Store your 7 keys in Render Environment Variables as GEMINI_KEYS="key1,key2,..."
GEMINI_KEYS = os.environ.get("GEMINI_KEYS", "").split(",")

bot = Bot(token=TOKEN)
dp = Dispatcher()
# Apply 20-second rate limiting per user
dp.message.middleware(RateLimiterMiddleware(limit=1, key_builder=lambda m: m.chat.id))

db = AsyncIOMotorClient(MONGODB_URI)["qadam_db"]
history_col = db["history"]
voice_usage = {}
VOICE_LIMIT = 20 # Limit set to 20 per user session

# --- GEMINI ROTATOR ---
class GeminiManager:
    def __init__(self, keys):
        self.keys = itertools.cycle(keys)
        self.current_key = next(self.keys)
        self._configure_model()

    def _configure_model(self):
        genai.configure(api_key=self.current_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

    def rotate(self):
        self.current_key = next(self.keys)
        self._configure_model()
        logging.info("Rotated to next Gemini API Key")

gemini = GeminiManager(GEMINI_KEYS)

SYSTEM_INSTRUCTION = (
    "Sen o'zbek tilida mukammal so'zlashadigan do'stsan. "
    "Muloqot uslubing: 1. Insoniy, samimiy va hazilkash bo'l. "
    "2. Grammatikani to'g'ri qo'lla (ayniqsa, -ni, -ning, -dan, -ga qo'shimchalari). "
    "3. O'zbekcha tabiiy so'zlashuvdan (Qalay, nima gap?) foydalan. "
    "4. Javoblaring qisqa, o'tkir va insoniy bo'lsin. "
)

# --- HANDLERS ---
@dp.message(Command("voice", "ovoz"))
async def handle_voice(msg: types.Message):
    user_id = msg.chat.id
    usage = voice_usage.get(user_id, 0)
    
    if usage >= VOICE_LIMIT:
        await msg.reply("Do'stim, ovozli xabarlar uchun limitim to'ldi. Hozircha faqat matn yozib tura olamiz! 😊")
        return

    hist = await get_history_context(msg.chat.id)
    text = hist[-1]["content"] if hist else "Assalomu alaykum."
    
    voice_usage[user_id] = usage + 1
    await bot.send_chat_action(msg.chat.id, "record_voice")
    path = f"voice_{user_id}.mp3"
    await edge_tts.Communicate(text, "uz-UZ-MadinaNeural").save(path)
    await msg.reply_voice(voice=types.FSInputFile(path))
    if os.path.exists(path): os.remove(path)

@dp.message(F.text)
async def chat(msg: types.Message):
    await bot.send_chat_action(msg.chat.id, "typing")
    try:
        # Generate with rotation fallback logic
        chat_session = gemini.model.start_chat(history=[])
        res = chat_session.send_message(f"{SYSTEM_INSTRUCTION}\n\nFoydalanuvchi: {msg.text}")
        reply = res.text
        await msg.reply(reply)
    except Exception as e:
        if "429" in str(e):
            gemini.rotate()
            await chat(msg) # Retry once with new key
        else:
            await msg.reply("Xatolik yuz berdi, do'stim.")

# --- RUNNER ---
async def main():
    # Keep your existing web app runner here...
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
