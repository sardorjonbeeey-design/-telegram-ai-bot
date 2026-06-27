import os, asyncio, logging, itertools
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton
import google.generativeai as genai
from motor.motor_asyncio import AsyncIOMotorClient
from langdetect import detect
import edge_tts
from aiohttp import web

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
MONGODB_URI = os.environ.get("MONGODB_URI")
GEMINI_KEYS = os.environ.get("GEMINI_KEYS", "").split(",")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = AsyncIOMotorClient(MONGODB_URI)["qadam_db"]
history_col = db["history"]

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
    "Sen — loʻnda va aniq javob beradigan oʻzbek AI yordamchisan.

QOIDALAR:
- Til: faqat oʻzbek (lotin). Rus/ingliz/krill aralashsa — toʻgʻrilab yoz.
- Uzunlik: 1-4 gap. Kerak boʻlsa roʻyxat yoki table.
- Uslub: birinchi gapda mohiyat. Suv, "hozir…", "keling…" larsiz.
- Bilmasang — "Buni bilmayman" deb ayt. Uydirma yoʻq.
- Foydalanuvchi tarixidan kelib chiq, lekin takrorlama.
- Har bir javobda yangi narsa bor. Format — oddiy matn.
"
)

# --- UTILS ---
async def text_to_speech(text, lang_code):
    voices = {"uz": "uz-UZ-MadinaNeural", "en": "en-US-JennyNeural", 
              "ru": "ru-RU-SvetlanaNeural", "tr": "tr-TR-AhmetNeural", "ar": "ar-SA-ZariyahNeural"}
    voice = voices.get(lang_code, "en-US-JennyNeural")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save("response.mp3")
    return "response.mp3"

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/help"), KeyboardButton(text="/voice")]], resize_keyboard=True)
    await msg.reply("Assalomu alaykum! Men Qadamman. Doim to'g'ri ma'lumot berishga harakat qilaman.", reply_markup=kb)

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.reply("Usage:\n/voice - Send voice message to get text + response\nJust text - Ask me anything.")

@dp.message(Command("admin_logs"))
async def admin_logs(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    cursor = history_col.find().sort("_id", -1).limit(10)
    async for doc in cursor:
        await msg.answer(f"👤 User: {doc['user_id']}\n💬 Q: {doc.get('question')}\n🤖 A: {doc.get('content')}")

@dp.message(F.text)
async def chat(msg: Message):
    try: lang = detect(msg.text)
    except: lang = "en"
    await bot.send_chat_action(msg.chat.id, "typing")
    try:
        res = gemini.model.generate_content(f"{SYSTEM_INSTRUCTION}\n\nUser: {msg.text}")
        await history_col.insert_one({"user_id": msg.chat.id, "question": msg.text, "content": res.text})
        audio = await text_to_speech(res.text, lang)
        await msg.reply_voice(voice=FSInputFile(audio))
        await msg.reply(res.text)
    except Exception as e:
        if "429" in str(e): 
            gemini.rotate()
            await msg.reply("Limit reached. Retrying...")
        else: await msg.reply("Error occurred.")

# --- WEB SERVER & MAIN ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, '0.0.0.0', port).start()
    logging.info(f"Web server bound to port {port}")

async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
