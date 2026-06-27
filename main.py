import os, asyncio, logging, itertools, uuid
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
import google.generativeai as genai
from langdetect import detect
import edge_tts

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
MONGODB_URI = os.environ.get("MONGODB_URI")
GEMINI_KEYS = os.environ.get("GEMINI_KEYS", "").split(",")

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = AsyncIOMotorClient(MONGODB_URI)["qadam_db"]
history_col = db["history"]

UZ_SYSTEM = "Sen lo'nda va aniq javob beradigan o'zbek AI yordamchisan. QOIDALAR: Til faqat o'zbek (lotin). 1-4 gap. Birinchi gapda mohiyat. Bilmasang 'Buni bilmayman' de. Uydirma yo'q."
EN_SYSTEM = "You are a concise Uzbek AI assistant. RULES: Reply in Uzbek (Latin) unless user writes English. 1-4 sentences. First sentence = answer. If unknown, say 'Buni bilmayman'."

VOICE_MAP = {"uz": "uz-UZ-MadinaNeural", "en": "en-US-JennyNeural", "ru": "ru-RU-SvetlanaNeural", "tr": "tr-TR-AhmetNeural"}

# --- GEMINI MANAGER ---
class GeminiManager:
    def __init__(self, api_keys: list[str]):
        self.keys = itertools.cycle(api_keys)
        self.current_key = next(self.keys)
        genai.configure(api_key=self.current_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

    def _rotate(self):
        self.current_key = next(self.keys)
        genai.configure(api_key=self.current_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")
        logging.info(f"Rotated key: {self.current_key[:8]}...")

    async def generate(self, prompt: str) -> str:
        for _ in range(3):
            try:
                resp = await self.model.generate_content_async(prompt)
                return resp.text.strip()
            except Exception as e:
                if "429" in str(e):
                    self._rotate()
                    await asyncio.sleep(1)
                else: raise e
        raise Exception("All keys exhausted")

gemini = GeminiManager(GEMINI_KEYS)

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.answer("Salom! Men oʻzbek AI yordamchiman. Savol yozing.")

@dp.message(Command("clear"))
async def cmd_clear(msg: Message):
    await history_col.delete_one({"_id": str(msg.from_user.id)})
    await msg.answer("Xotira tozalandi.")

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id == ADMIN_ID:
        total = await history_col.count_documents({})
        await msg.answer(f"Foydalanuvchilar: {total}")

@dp.message(F.text & ~F.command)
async def handle_msg(msg: Message):
    text = msg.text.strip()
    lang = "en" if "en" in detect(text) else "uz"
    prompt = f"{EN_SYSTEM if lang == 'en' else UZ_SYSTEM}\n\nUser: {text}"
    
    wait_msg = await msg.answer("⏳")
    try:
        reply = await gemini.generate(prompt)
        
        # History update
        await history_col.update_one(
            {"_id": str(msg.from_user.id)},
            {"$push": {"messages": {"$each": [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]}, "$slice": -20}},
            upsert=True
        )

        # TTS
        name = f"voice_{uuid.uuid4().hex[:8]}.mp3"
        comm = edge_tts.Communicate(reply, VOICE_MAP.get(lang, "uz-UZ-MadinaNeural"))
        await comm.save(name)
        
        await msg.answer_voice(voice=FSInputFile(name), caption=reply)
        await wait_msg.delete()
        if os.path.exists(name): os.remove(name)
    except Exception as e:
        await wait_msg.edit_text("Xatolik yuz berdi.")
        logging.error(f"Error: {e}")

# --- WEBHOOK & MAIN ---
# Webhook uchun yangi handler
async def on_startup(bot: Bot):
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    await bot.set_webhook(f"{external_url}/webhook")
    
async def handle_root(request):
    return web.Response(text="Bot is running!")

app = web.Application()
app.router.add_get("/", handle_root)  # Render uchun javob
app.router.add_post("/webhook", webhook_handler) # Telegram uchun webhook

from aiogram import types

async def webhook_handler(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_webhook(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Xatolik: {e}")
        return web.Response(status=200) # Telegram qayta urinmasligi uchun 200 qaytaring
    
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_webhook(bot, update)
    return web.Response(text="OK")

# main funksiyasini yangilash
async def main():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_post(f"/webhook", webhook_handler) # Tokenli yo'l xavfsizroq
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    await on_startup(bot)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
