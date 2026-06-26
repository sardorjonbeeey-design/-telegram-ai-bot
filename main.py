import os
import io
import asyncio
import logging
import itertools
import time
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from typing import Callable, Dict, Any, Awaitable
import google.generativeai as genai
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from huggingface_hub import InferenceClient

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")
GEMINI_KEYS = os.environ.get("GEMINI_KEYS", "").split(",")
HF_TOKEN = os.environ.get("HF_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = AsyncIOMotorClient(MONGODB_URI)["qadam_db"]
history_col = db["history"]
hf_client = InferenceClient(api_key=HF_TOKEN)
voice_usage = {}
VOICE_LIMIT = 20 

# --- GEMINI MANAGER ---
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
    "Sen Qadamsan, foydalanuvchining eng yaqin, samimiy do'stisan. "
    "Muloqot uslubing: 1. Robotcha ohangni yig'ishtir. 'Siz' emas, 'sen' deb gaplash. "
    "2. O'zbek tilidagi jonli so'zlashuv uslubini qo'lla. "
    "3. Javoblaring doimo qisqa, tushunarli va insoniy bo'lsin."
)

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.reply("Assalomu alaykum! Men Qadamman.")

@dp.message(F.text.startswith("/image"))
async def handle_image(msg: Message):
    prompt = msg.text.replace("/image", "").strip()
    if not prompt:
        await msg.reply("Iltimos, rasm uchun so'rovni kiriting.")
        return
    await bot.send_chat_action(msg.chat.id, "upload_photo")
    status = await msg.reply("🎨 Rasm yaratilmoqda...")
    try:
        img = hf_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        await msg.reply_photo(photo=BufferedInputFile(buf.getvalue(), filename="img.png"))
        await status.delete()
    except Exception as e:
        logging.error(f"Image gen error: {e}")
        await status.edit_text("❌ Rasm yaratishda xatolik yuz berdi.")

@dp.message(Command("voice", "ovoz"))
async def handle_voice(msg: Message):
    user_id = msg.chat.id
    if voice_usage.get(user_id, 0) >= VOICE_LIMIT:
        await msg.reply("Ovozli xabar limiti tugadi.")
        return
    hist = await history_col.find({"user_id": user_id}).sort("_id", -1).limit(1).to_list(length=1)
    text = hist[0]["content"] if hist else "Assalomu alaykum."
    voice_usage[user_id] = voice_usage.get(user_id, 0) + 1
    await bot.send_chat_action(msg.chat.id, "record_voice")
    path = f"voice_{user_id}.mp3"
    await edge_tts.Communicate(text, "uz-UZ-MadinaNeural").save(path)
    await msg.reply_voice(voice=types.FSInputFile(path))
    if os.path.exists(path): os.remove(path)

@dp.message(F.text)
async def chat(msg: Message):
    # Try to send typing action, but don't crash if it fails
    try:
        await bot.send_chat_action(msg.chat.id, "typing")
    except:
        pass
    
    # 5-second delay before processing
    await asyncio.sleep(5)
    
    try:
        chat_session = gemini.model.start_chat(history=[])
        res = chat_session.send_message(f"{SYSTEM_INSTRUCTION}\n\nFoydalanuvchi: {msg.text}")
        
        await history_col.insert_one({"user_id": msg.chat.id, "role": "AI", "content": res.text})
        await msg.reply(res.text)
    except Exception as e:
        logging.error(f"Chat error: {e}")
        # Add your key rotation logic back here if you still want it!
        if "429" in str(e):
            gemini.rotate()
            await msg.reply("Limitga yetildi, qaytadan urinib ko'ring.")
        else:
            await msg.reply("Texnik muammo yuz berdi.")

# --- WEB SERVER & MAIN ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000)))
    await site.start()

async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
