import os
import io
import asyncio
import logging
import itertools
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
import google.generativeai as genai
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
        return await msg.reply("Iltimos, rasm uchun so'rovni kiriting.")
    
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

@dp.message(F.text)
async def chat(msg: Message):
    # Intentional delay as requested
    try:
        await bot.send_chat_action(msg.chat.id, "typing")
    except:
        pass
    
    await asyncio.sleep(5)
    
    try:
        chat_session = gemini.model.start_chat(history=[])
        res = chat_session.send_message(f"{SYSTEM_INSTRUCTION}\n\nFoydalanuvchi: {msg.text}")
        
        await history_col.insert_one({"user_id": msg.chat.id, "role": "AI", "content": res.text})
        await msg.reply(res.text)
        
    except Exception as e:
        error_str = str(e)
        logging.error(f"Chat error: {error_str}")
        
        # If Gemini says quota exceeded, rotate key and tell user
        if "429" in error_str:
            gemini.rotate()
            await msg.reply("Hozirda limit tugadi, bir ozdan so'ng qaytadan urinib ko'ring.")
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
