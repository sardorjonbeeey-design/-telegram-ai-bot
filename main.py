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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
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
    "Sening isming Qadam AI. Sen foydalanuvchi uchun samimiy va ishonchli AI do'st/yordamcisan. "
    "Siyosiy mavzularda betaraf va xolis qol. O'zbekiston qonunchiligi va milliy qadriyatlarga hurmat bilan yondash."
    "Sen Claude stilidan foydalanasan, doim rost va tog'ri gapirasan, bilsang javob berasan, bilmasang to'g'risini aytasan."
)

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
    await message.reply("Assalomu alaykum! Men Qadam — sizning AI yordamchingizman.")

@dp.message(Command("clear"))
async def handle_clear(message: types.Message):
    await history_col.delete_many({"user_id": message.chat.id})
    await message.reply("✅ Suhbat tarixi tozalandi.")

# --- FEATURES ---
@dp.message(F.text.startswith(("/voice", "/ovoz")))
async def handle_voice(message: types.Message):
    user_id = message.chat.id
    text = message.text.replace("/voice", "").replace("/ovoz", "").strip() or (await get_history_context(user_id))[-1]["content"]
    
    # Language detection
    voice = "en-US-EmmaNeural" if any(ord(char) < 128 for char in text) else "uz-UZ-MadinaNeural"
    
    await bot.send_chat_action(chat_id=user_id, action="record_voice")
    path = f"voice_{user_id}.mp3"
    await edge_tts.Communicate(text, voice).save(path)
    await message.reply_voice(voice=types.FSInputFile(path))
    os.remove(path)

@dp.message(F.text.startswith("/image"))
async def handle_image(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    status_msg = await message.reply("🎨 Rasm yaratilmoqda...")
    
    try:
        img = await asyncio.get_event_loop().run_in_executor(None, lambda: hf_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell"))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        await message.reply_photo(photo=types.BufferedInputFile(buf.getvalue(), filename="img.png"))
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text("❌ Rasm yaratishda xatolik yuz berdi.")

@dp.message(F.text)
async def process_chat(message: types.Message):
    if message.text.startswith("/"): return
    if not await check_and_update_limit(message.chat.id, message.from_user.first_name): 
        return await message.reply("Limit tugadi.")
    
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}] + await get_history_context(message.chat.id)
    msgs.append({"role": "user", "content": message.text})
    
    try:
        res = await asyncio.get_event_loop().run_in_executor(None, lambda: hf_client.chat.completions.create(model="meta-llama/Llama-3.3-70B-Instruct", messages=msgs))
        reply = res.choices[0].message.content
        await save_to_memory(message.chat.id, "User", message.text)
        await save_to_memory(message.chat.id, "AI", reply)
        await message.reply(reply)
    except Exception as e:
        await message.reply("Kechirasiz, javob olishda xatolik yuz berdi.")

# --- RUNNER ---
async def start_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(runner.setup())
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    loop.run_until_complete(site.start())
    loop.run_until_complete(start_bot())
# --- RUNNER ---
async def main():
    # 1. Forcefully tell Telegram to forget ANY previous connections/webhooks
    # This is the "kill switch" for the 409 Conflict error
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 2. Start polling and tell it to drop any messages that were waiting
    # while the bot was offline.
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    # Start the web server first
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(runner.setup())
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    loop.run_until_complete(site.start())
    
    # Now start the bot with drop_pending_updates=True
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
