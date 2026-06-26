import os
import io
import asyncio
import logging
import re
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
from aiogram.filters import Command
from huggingface_hub import InferenceClient
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

# Setup Logging
logging.basicConfig(level=logging.INFO)

# Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 
HF_TOKEN = os.environ.get("HF_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0")) 
MONGODB_URI = os.environ.get("MONGODB_URI")

# --- MONGODB INITIALIZATION ---
client = AsyncIOMotorClient(MONGODB_URI)
db = client["qadam_db"]
history_col = db["history"]
users_col = db["users"]

# Initialize Hugging Face
hf_client = InferenceClient(api_key=HF_TOKEN)
TEXT_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
VISION_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
IMAGE_GEN_MODEL = "black-forest-labs/FLUX.1-schnell"
WHISPER_MODEL = "openai/whisper-large-v3-turbo"
DAILY_LIMIT = 50 

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

SYSTEM_INSTRUCTION = (
    "Sizning ismingiz Qadam. Siz foydalanuvchi uchun samimiy va ishonchli AI do'st/yordamcisiz. "
    "Siyosiy mavzularda hech qachon biror tomonni yoqlamang yoki o'z fikringizni bildirmang — betaraf va xolis qoling. "
    "O'zbekiston qonunchiligi, davlat siyosati va milliy qadriyatlarga hurmat bilan munosabatda bo'ling. "
    "Javoblaringiz halol, aniq va to'g'ridan-to'g'ri bo'lsin."
)

# --- MONGODB DATABASE FUNCTIONS (Async) ---
async def save_to_memory(user_id, role, content):
    await history_col.insert_one({"user_id": user_id, "role": role, "content": content})
    # Keep history trimmed to last 10
    count = await history_col.count_documents({"user_id": user_id})
    if count > 10:
        cursor = history_col.find({"user_id": user_id}).sort("_id", 1).limit(count - 10)
        async for doc in cursor:
            await history_col.delete_one({"_id": doc["_id"]})

async def get_history_context(user_id):
    cursor = history_col.find({"user_id": user_id}).sort("_id", 1)
    rows = await cursor.to_list(length=10)
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
    limit = user.get("custom_limit", DAILY_LIMIT)
    if user["request_count"] >= limit: return False
    await users_col.update_one({"user_id": user_id}, {"$inc": {"request_count": 1}, "$set": {"first_name": first_name}})
    return True

# --- ADMIN HANDLERS ---
@dp.message(F.text.startswith("/admin"))
async def handle_admin_commands(message: types.Message):
    if message.chat.id != ADMIN_ID: return
    command = message.text.split()
    cmd_name = command[0]

    if cmd_name == "/admin":
        count = await users_col.count_documents({})
        await message.reply(f"📊 **Admin Panel**\nTotal Users: `{count}`")
    elif cmd_name == "/admin_users":
        cursor = users_col.find({})
        report = "👥 **User Activity Logs:**\n\n"
        async for u in cursor:
            report += f"• `{u['user_id']}` | {u.get('first_name')} | Used: **{u.get('request_count')}**\n"
        await message.reply(report, parse_mode="Markdown")
    elif cmd_name == "/admin_chat":
        target_id = int(command[1])
        cursor = history_col.find({"user_id": target_id}).sort("_id", 1)
        chat_log = f"📜 **History `{target_id}`:**\n\n"
        async for row in cursor:
            chat_log += f"**{row['role']}:** {row['content']}\n\n"
        await message.reply(chat_log[:4096])
    elif cmd_name == "/admin_setlimit":
        await users_col.update_one({"user_id": int(command[1])}, {"$set": {"custom_limit": int(command[2])}})
        await message.reply("✅ Limit updated.")

# --- FEATURES ---
async def generate_voice_reply(text: str, user_id: int) -> str:
    voice = "en-US-EmmaNeural" if len(re.findall(r'\b(the|is|are|you)\b', text.lower())) >= 1 else "uz-UZ-MadinaNeural"
    file_path = f"voice_{user_id}.mp3"
    comm = edge_tts.Communicate(text, voice)
    await comm.save(file_path)
    return file_path

@dp.message(F.text.startswith(("/voice", "/ovoz")))
async def handle_voice(message: types.Message):
    user_id = message.chat.id
    if not await check_and_update_limit(user_id, message.from_user.first_name): return
    history = await get_history_context(user_id)
    text = message.text.replace("/voice", "").replace("/ovoz", "").strip() or (history[-1]["content"] if history else "")
    path = await generate_voice_reply(text, user_id)
    await message.reply_voice(voice=types.FSInputFile(path))
    os.remove(path)

@dp.message(F.text.startswith("/image"))
async def handle_image(message: types.Message):
    if not await check_and_update_limit(message.chat.id, message.from_user.first_name): return
    prompt = message.text.replace("/image", "").strip()
    loop = asyncio.get_event_loop()
    img = await loop.run_in_executor(None, lambda: hf_client.text_to_image(prompt, model=IMAGE_GEN_MODEL))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    await message.reply_photo(photo=types.BufferedInputFile(buf.read(), filename="img.png"))

@dp.message(F.voice)
async def handle_voice_note(message: types.Message):
    if not await check_and_update_limit(message.chat.id, message.from_user.first_name): return
    voice = await bot.get_file(message.voice.file_id)
    buf = io.BytesIO()
    await bot.download_file(voice.file_path, destination=buf)
    buf.name = "voice.ogg"
    loop = asyncio.get_event_loop()
    trans = await loop.run_in_executor(None, lambda: hf_client.automatic_speech_recognition(buf, model=WHISPER_MODEL))
    await process_chat_intelligence(message, trans.text)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not await check_and_update_limit(message.chat.id, message.from_user.first_name): return
    photo = await bot.get_file(message.photo[-1].file_id)
    buf = io.BytesIO()
    await bot.download_file(photo.file_path, destination=buf)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, lambda: hf_client.chat.completions.create(model=VISION_MODEL, messages=[{"role": "user", "content": [{"type": "image", "image": buf.getvalue()}]}]))
    desc = res.choices[0].message.content
    await message.reply(desc)
    await save_to_memory(message.chat.id, "AI", desc)

@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text.startswith("/"): return
    await process_chat_intelligence(message, message.text)

async def process_chat_intelligence(message, query):
    user_id = message.chat.id
    if query == "/clear":
        await history_col.delete_many({"user_id": user_id})
        return await message.reply("Tarix tozalandi.")
    if not await check_and_update_limit(user_id, message.from_user.first_name): return
    
    msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}] + await get_history_context(user_id)
    msgs.append({"role": "user", "content": query})
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, lambda: hf_client.chat.completions.create(model=TEXT_MODEL, messages=msgs, max_tokens=250))
    reply = res.choices[0].message.content
    await save_to_memory(user_id, "User", query)
    await save_to_memory(user_id, "AI", reply)
    await message.reply(reply)

# --- WEBHOOK ---
async def handle_webhook(request):
    data = await request.json()
    await dp.feed_update(bot, types.Update(**data))
    return web.Response(text="OK")

# --- ADD THIS FUNCTION AT THE TOP LEVEL OF YOUR FILE ---
async def keep_alive():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # This pings your bot's own root URL every 5 minutes
                async with session.get(RENDER_EXTERNAL_URL) as response:
                    logging.info(f"Pinged {RENDER_EXTERNAL_URL}, status: {response.status}")
            except Exception as e:
                logging.error(f"Ping failed: {e}")
            await asyncio.sleep(300) # Wait 5 minutes

# --- UPDATE YOUR MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    
    # Start the keep_alive task
    loop.create_task(keep_alive())
    
    # Existing web server logic
    app = web.Application()
    # ... (rest of your app routing)
    
    # Start the web server
    web.run_app(app, port=int(os.environ.get("PORT", 10000)))
