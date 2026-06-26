import os
import io
import asyncio
import logging
import re
import aiohttp
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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
HF_TOKEN = os.environ.get("HF_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
MONGODB_URI = os.environ.get("MONGODB_URI")

# --- MONGODB CONNECTION ---
try:
    clean_uri = MONGODB_URI.replace("mongodb+srv://", "").replace("mongodb://", "")
    user_pass, rest = clean_uri.split("@", 1)
    user, password = user_pass.split(":", 1)
    escaped_uri = f"mongodb+srv://{urllib.parse.quote_plus(user)}:{urllib.parse.quote_plus(password)}@{rest}"
    client = AsyncIOMotorClient(escaped_uri)
except Exception:
    client = AsyncIOMotorClient(MONGODB_URI)

db = client["qadam_db"]
history_col = db["history"]
users_col = db["users"]

hf_client = InferenceClient(api_key=HF_TOKEN)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

SYSTEM_INSTRUCTION = (
    "Sizning ismingiz Qadam. Siz foydalanuvchi uchun samimiy va ishonchli AI do'st/yordamcisiz. "
    "Siyosiy mavzularda betaraf va xolis qoling. O'zbekiston qonunchiligi va milliy qadriyatlarga hurmat bilan yondashing."
)
# Ensure this is at the very top of your start-up block
async def main():
    # This clears the conflict with old sessions
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
# --- DATABASE HELPERS ---
async def save_to_memory(user_id, role, content):
    await history_col.insert_one({"user_id": user_id, "role": role, "content": content})
    if await history_col.count_documents({"user_id": user_id}) > 10:
        cursor = history_col.find({"user_id": user_id}).sort("_id", 1).limit(1)
        async for doc in cursor: await history_col.delete_one({"_id": doc["_id"]})

async def get_history_context(user_id):
    rows = await history_col.find({"user_id": user_id}).sort("_id", 1).to_list(length=10)
    return [{"role": "user" if r["role"] == "User" else "assistant", "content": r["content"]} for r in rows]

async def check_and_update_limit(user_id, first_name):
    today = date.today().isoformat()
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        await users_col.insert_one({"user_id": user_id, "first_name": first_name, "usage_date": today, "request_count": 1})
        return True
    if user["usage_date"] != today:
        await users_col.update_one({"user_id": user_id}, {"$set": {"usage_date": today, "request_count": 1, "first_name": first_name}})
        return True
    if user["request_count"] >= user.get("custom_limit", 50): return False
    await users_col.update_one({"user_id": user_id}, {"$inc": {"request_count": 1}, "$set": {"first_name": first_name}})
    return True

# --- COMMANDS ---
@dp.message(Command("start"))
async def handle_start(message: types.Message):
    await message.reply("Assalomu alaykum! Men Qadam — sizning AI yordamchingizman. /help orqali yordam oling.")

@dp.message(Command("help"))
async def handle_help(message: types.Message):
    await message.reply("🤖 **Qadam AI - Qo'llanma**\n\n"
                        "💬 Suhbat: shunchaki yozing.\n"
                        "🎙 Ovozli: yuborsangiz matnga o'giraman.\n"
                        "🗣 Ovozli javob: /voice [matn].\n"
                        "🎨 Rasm: /image [matn].\n"
                        "👁 Rasm tahlili: rasm yuboring.\n"
                        "🧹 /clear: tarixni tozalash.", parse_mode="Markdown")

@dp.message(Command("clear"))
async def handle_clear(message: types.Message):
    await history_col.delete_many({"user_id": message.chat.id})
    await message.reply("✅ Suhbat tarixi tozalandi.")

@dp.message(F.text.startswith("/admin"))
async def handle_admin(message: types.Message):
    if message.chat.id != ADMIN_ID: return
    cmd = message.text.split()
    if cmd[0] == "/admin":
        count = await users_col.count_documents({})
        await message.reply(f"📊 Total Users: {count}")
    elif cmd[0] == "/admin_chat":
        res = "📜 History:\n"
        async for row in history_col.find({"user_id": int(cmd[1])}).sort("_id", 1):
            res += f"{row['role']}: {row['content']}\n"
        await message.reply(res[:4000])

# --- FEATURES ---
@dp.message(F.text.startswith(("/voice", "/ovoz")))
async def handle_voice(message: types.Message):
    user_id = message.chat.id
    text = message.text.replace("/voice", "").replace("/ovoz", "").strip() or (await get_history_context(user_id))[-1]["content"]
    path = f"voice_{user_id}.mp3"
    voice = "uz-UZ-MadinaNeural"
    await edge_tts.Communicate(text, voice).save(path)
    await message.reply_voice(voice=types.FSInputFile(path))
    os.remove(path)

@dp.message(F.text.startswith("/image"))
async def handle_image(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    img = await asyncio.get_event_loop().run_in_executor(None, lambda: hf_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell"))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    await message.reply_photo(photo=types.BufferedInputFile(buf.read(), filename="img.png"))

@dp.message(F.voice)
async def handle_voice_note(message: types.Message):
    buf = io.BytesIO()
    await bot.download_file((await bot.get_file(message.voice.file_id)).file_path, destination=buf)
    trans = await asyncio.get_event_loop().run_in_executor(None, lambda: hf_client.automatic_speech_recognition(buf, model="openai/whisper-large-v3-turbo"))
    await process_chat(message, trans.text)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    buf = io.BytesIO()
    await bot.download_file((await bot.get_file(message.photo[-1].file_id)).file_path, destination=buf)
    res = await asyncio.get_event_loop().run_in_executor(None, lambda: hf_client.chat.completions.create(model="Qwen/Qwen2.5-VL-7B-Instruct", messages=[{"role": "user", "content": [{"type": "image", "image": buf.getvalue()}]}]))
    await message.reply(res.choices[0].message.content)

@dp.message(F.text)
async def process_chat(message: types.Message, query=None):
    query = query or message.text
    logging.info(f"Received message from {message.chat.id}: {query}") # New Debug Line
    
    if query.startswith("/"): 
        logging.info("Message was a command, skipping process_chat.")
        return
        
    if not await check_and_update_limit(message.chat.id, message.from_user.first_name): 
        return await message.reply("Limit tugadi.")
        
    msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}] + await get_history_context(message.chat.id)
    msgs.append({"role": "user", "content": query})
    
    try:
        res = await asyncio.get_event_loop().run_in_executor(None, lambda: hf_client.chat.completions.create(model="meta-llama/Llama-3.3-70B-Instruct", messages=msgs))
        reply = res.choices[0].message.content
        await save_to_memory(message.chat.id, "User", query)
        await save_to_memory(message.chat.id, "AI", reply)
        await message.reply(reply)
    except Exception as e:
        logging.error(f"Error during AI processing: {e}")
        await message.reply("Kechirasiz, javob olishda xatolik yuz berdi.")

# --- RUNNER ---
async def keep_alive():
    async with aiohttp.ClientSession() as session:
        while True:
            try: await session.get(RENDER_EXTERNAL_URL)
            except: pass
            await asyncio.sleep(300)

async def run_dummy_server():
    app = web.Application()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Dummy web server running on port {port}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_dummy_server())
    loop.create_task(keep_alive())
    print("Bot is starting...")
    loop.run_until_complete(dp.start_polling(bot))
