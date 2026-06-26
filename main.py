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
from langdetect import detect, LangDetectException
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
    "Claude uslubida javob ber: ortiqcha gaplardan qoch, javobing har doim lo'nda va foydali bo'lsin. "
    "Agar savol qisqa bo'lsa, javob ham qisqa bo'lsin. Murakkab savollarga esa to'liq va tushunarli javob ber."
)

# --- TOOLS ---
async def save_to_memory(user_id, role, content):
    await history_col.insert_one({"user_id": user_id, "role": role, "content": content})
    if await history_col.count_documents({"user_id": user_id}) > 10:
        cursor = history_col.find({"user_id": user_id}).sort("_id", 1).limit(1)
        async for doc in cursor: await history_col.delete_one({"_id": doc["_id"]})

async def get_history_context(user_id):
    rows = await history_col.find({"user_id": user_id}).sort("_id", 1).to_list(length=10)
    return [{"role": "user" if r["role"] == "User" else "assistant", "content": r["content"]} for r in rows]

async def search_web(query: str):
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=3))
        return "\n".join([f"- {r['title']}: {r['body']}" for r in results])

# --- HANDLERS ---
@dp.message(F.text.startswith(("/voice", "/ovoz")))
async def handle_voice(msg: types.Message):
    text = msg.text.replace("/voice", "").replace("/ovoz", "").strip()
    if not text:
        hist = await get_history_context(msg.chat.id)
        text = hist[-1]["content"] if hist else "Assalomu alaykum."
    
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
    
    # Optional Web Search
    search_data = await search_web(msg.text) if "qidir" in msg.text.lower() else ""
    prompt = f"Qo'shimcha ma'lumot: {search_data}\n\nSavol: {msg.text}" if search_data else msg.text
    
    try:
        msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}] + await get_history_context(msg.chat.id)
        msgs.append({"role": "user", "content": prompt})
        
        res = hf_client.chat.completions.create(model="meta-llama/Llama-3.3-70B-Instruct", messages=msgs)
        reply = res.choices[0].message.content
        
        await save_to_memory(msg.chat.id, "User", msg.text)
        await save_to_memory(msg.chat.id, "AI", reply)
        await msg.reply(reply)
    except:
        await msg.reply("Kechirasiz, javob olishda xatolik yuz berdi.")

# --- RUNNER ---
async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
