import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from huggingface_hub import InferenceClient
import edge_tts
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
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
    SYSTEM_INSTRUCTION = (
    "Sening isming Qadam. Sen foydalanuvchining yaqin do'stisan. "
    "Muloqot uslubing: "
    "1. Sen shunchaki yordamchi emassan, sen insoniy xususiyatlarga ega do'stsan. "
    "2. Har doim samimiy bo'l, hazillash, kerak bo'lsa tanqid qil va foydalanuvchini o'ylantiradigan fikrlar bildir. "
    "3. 'Sizga qanday yordam bera olaman?' kabi robotlashgan gaplarni umuman ishlatma. "
    "4. Foydalanuvchi qaysi tilda gaplashsa (O'zbek, English, Russian, Turkish, Arabic), o'sha tilda, lekin do'stona va jonli ohangda javob ber. "
    "5. Agar foydalanuvchi xato qilsa yoki noto'g'ri qaror qabul qilsa, uni xushmuomalalik bilan, lekin ochiqchasiga to'g'rilashdan tortinma. "
    "6. Javoblaring qisqa, o'tkir va insoniy bo'lsin."
)
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

# --- COMMAND HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.reply("Assalomu alaykum! Men Qadam AI yordamchisiman.")

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.reply("/qidir [matn] - Qidirish\n/voice [matn] - Ovoz chiqarish\n/clear - Tarixni tozalash")

@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    await history_col.delete_many({"user_id": msg.chat.id})
    await msg.reply("Suhbat tarixi tozalandi.")

@dp.message(Command("qidir"))
async def handle_search(msg: types.Message):
    query = msg.text.replace("/qidir", "", 1).strip()
    if not query:
        await msg.reply("Iltimos, qidirish uchun matn kiriting.")
        return
    await bot.send_chat_action(msg.chat.id, "typing")
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=2))
        search_data = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
    prompt = f"Ma'lumot: {search_data}\n\nSavol: {query}"
    res = hf_client.chat.completions.create(model="meta-llama/Llama-3.3-70B-Instruct", messages=[{"role": "user", "content": prompt}])
    await msg.reply(res.choices[0].message.content)

@dp.message(Command("voice", "ovoz"))
async def handle_voice(msg: types.Message):
    text = msg.text.replace("/voice", "").replace("/ovoz", "", 1).strip()
    if not text:
        hist = await get_history_context(msg.chat.id)
        text = hist[-1]["content"] if hist else "Assalomu alaykum."

    # SMART KEYWORD DETECTION
    # If it contains specific English common words, use Emma. Otherwise, Madina.
    english_keywords = ["hello", "hi", "what", "how", "the", "you", "is", "name"]
    is_english = any(word in text.lower() for word in english_keywords)
    
    voice = "en-US-EmmaNeural" if is_english else "uz-UZ-MadinaNeural"
    
    await bot.send_chat_action(msg.chat.id, "record_voice")
    path = f"voice_{msg.chat.id}.mp3"
    await edge_tts.Communicate(text, voice).save(path)
    await msg.reply_voice(voice=types.FSInputFile(path))
    if os.path.exists(path): os.remove(path)

# --- CHAT HANDLER ---
@dp.message(F.text)
async def chat(msg: types.Message):
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
        
@dp.message(Command("checkdb"))
async def cmd_checkdb(msg: types.Message):
    # This counts how many messages the bot has saved for you
    count = await history_col.count_documents({"user_id": msg.chat.id})
    await msg.reply(f"Do'stim, sening xotirangda hozir {count} ta xabar saqlangan.")

# --- RUNNER ---
async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 10000))).start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
