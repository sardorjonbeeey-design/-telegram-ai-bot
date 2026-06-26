import os
import io
import asyncio
import logging
import urllib.parse
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from huggingface_hub import InferenceClient
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")

# --- MONGODB CONNECTION ---
try:
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client["qadam_db"]
    history_col = db["history"]
    users_col = db["users"]
except Exception as e:
    logging.error(f"DB Connection Error: {e}")

hf_client = InferenceClient(api_key=HF_TOKEN)
bot = Bot(token=TOKEN)
dp = Dispatcher()

SYSTEM_INSTRUCTION = "Sening isming Qadam AI. Sen foydalanuvchi uchun samimiy va ishonchli AI do'st/yordamcisan."

# --- DATABASE HELPERS ---
async def save_to_memory(user_id, role, content):
    await history_col.insert_one({"user_id": user_id, "role": role, "content": content})
    if await history_col.count_documents({"user_id": user_id}) > 10:
        cursor = history_col.find({"user_id": user_id}).sort("_id", 1).limit(1)
        async for doc in cursor: await history_col.delete_one({"_id": doc["_id"]})

async def get_history_context(user_id):
    rows = await history_col.find({"user_id": user_id}).sort("_id", 1).to_list(length=10)
    return [{"role": "user" if r["role"] == "User" else "assistant", "content": r["content"]} for r in rows]

# --- FEATURES ---
@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply("Assalomu alaykum! Qadam AI ishga tushdi.")

@dp.message(F.text.startswith(("/voice", "/ovoz")))
async def handle_voice(msg: types.Message):
    text = msg.text.replace("/voice", "").replace("/ovoz", "").strip()
    if not text:
        hist = await get_history_context(msg.chat.id)
        text = hist[-1]["content"] if hist else "Salom"
    
    eng_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    voice = "en-US-EmmaNeural" if eng_chars / len(text) > 0.3 else "uz-UZ-MadinaNeural"
    
    await bot.send_chat_action(msg.chat.id, "record_voice")
    path = f"voice_{msg.chat.id}.mp3"
    await edge_tts.Communicate(text, voice).save(path)
    await msg.reply_voice(voice=types.FSInputFile(path))
    os.remove(path)

@dp.message(F.text.startswith("/image"))
async def handle_image(msg: types.Message):
    prompt = msg.text.replace("/image", "").strip()
    await bot.send_chat_action(msg.chat.id, "upload_photo")
    status = await msg.reply("🎨 Rasm yaratilmoqda...")
    try:
        img = hf_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        await msg.reply_photo(photo=types.BufferedInputFile(buf.getvalue(), "img.png"))
        await status.delete()
    except:
        await status.edit_text("❌ Rasm yaratishda xatolik.")

@dp.message(F.text)
async def chat(msg: types.Message):
    if msg.text.startswith("/"): return
    await bot.send_chat_action(msg.chat.id, "typing")
    try:
        msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}] + await get_history_context(msg.chat.id)
        msgs.append({"role": "user", "content": msg.text})
        res = hf_client.chat.completions.create(model="meta-llama/Llama-3.3-70B-Instruct", messages=msgs)
        reply = res.choices[0].message.content
        await save_to_memory(msg.chat.id, "User", msg.text)
        await save_to_memory(msg.chat.id, "AI", reply)
        await msg.reply(reply)
    except:
        await msg.reply("Xatolik yuz berdi.")

# --- RUNNER ---
async def main():
    # Web Server
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()
    
    # Polling
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
