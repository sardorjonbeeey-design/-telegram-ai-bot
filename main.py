import os
import io
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from huggingface_hub import InferenceClient
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from langdetect import detect
from duckduckgo_search import DDGS

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")

bot = Bot(token=TOKEN)
dp = Dispatcher()
hf_client = InferenceClient(api_key=HF_TOKEN)
db = AsyncIOMotorClient(MONGODB_URI)["qadam_db"]
history_col = db["history"]

SYSTEM_INSTRUCTION = (
    "Sening isming Qadam AI. Sen professional, qisqa va aniq javob beradigan yordamchisan. "
    "Claude uslubida javob ber: ortiqcha gaplardan qoch, javobing har doim lo'nda va foydali bo'lsin."
)

# --- HANDLERS ---
@dp.message(F.text.startswith(("/voice", "/ovoz")))
async def handle_voice(msg: types.Message):
    text = msg.text.replace("/voice", "").replace("/ovoz", "").strip()
    try:
        lang = detect(text)
        voice = "en-US-EmmaNeural" if lang == 'en' else "uz-UZ-MadinaNeural"
    except:
        voice = "uz-UZ-MadinaNeural"
    
    await bot.send_chat_action(msg.chat.id, "record_voice")
    path = f"voice_{msg.chat.id}.mp3"
    await edge_tts.Communicate(text, voice).save(path)
    await msg.reply_voice(voice=types.FSInputFile(path))
    os.remove(path)

@dp.message(F.text)
async def chat(msg: types.Message):
    if msg.text.startswith("/"): return
    await bot.send_chat_action(msg.chat.id, "typing")
    
    # Search
    search_data = ""
    if "qidir" in msg.text.lower():
        with DDGS() as ddgs:
            results = list(ddgs.text(msg.text, max_results=2))
            search_data = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
    
    prompt = f"Ma'lumot: {search_data}\n\nSavol: {msg.text}" if search_data else msg.text
    
    try:
        msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}, {"role": "user", "content": prompt}]
        res = hf_client.chat.completions.create(model="meta-llama/Llama-3.3-70B-Instruct", messages=msgs)
        await msg.reply(res.choices[0].message.content)
    except:
        await msg.reply("Xatolik.")

# --- MINIMAL RUNNER ---
async def start_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()

async def main():
    await start_server()
    # The order here is vital: delete webhook first, then poll
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
