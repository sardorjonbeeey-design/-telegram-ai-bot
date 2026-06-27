import os, asyncio, logging, itertools, uuid
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
    def __init__(self, api_keys: list[str]):

        self.keys = itertools.cycle(api_keys)
        self.current_key = next(self.keys)
        self._configure()

    def _configure(self):
        genai.configure(api_key=self.current_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")
        logging.info(f"Gemini with key: {self.current_key[:8]}...")

    def rotate(self):
        self.current_key = next(self.keys)
        self._configure()

    async def generate(self, prompt: str | list) -> str:
        for attempt in range(len(self.keys)):
            try:
                resp = await self.model.generate_content_async(prompt)
                return resp.text.strip()
            except Exception as e:
                logging.warning(f"Key {self.current_key[:8]} failed: {e}")
                self.rotate()
                await asyncio.sleep(1)
        raise Exception("All Gemini keys exhausted")

gemini = GeminiManager(GEMINI_KEYS)

SYSTEM_INSTRUCTION = (
  UZ_SYSTEM = """Sen lo'nda va aniq javob beradigan o'zbek AI yordamchisan.
QOIDALAR:
- Til: faqat o'zbek (lotin). Rus/ingliz/krill aralashsa to'g'rilab yoz.
- Uzunlik: 1-4 gap. Kerak bo'lsa ro'yxat yoki kod.
- Birinchi gapda mohiyat. "Hozir...", "Keling..." larsiz.
- Bilmasang "Buni bilmayman" de. Uydirma yo'q.
- Suhbat tarixidan kelib chiq, lekin takrorlama."""

EN_SYSTEM = """You are a concise Uzbek AI assistant.
RULES:
- Reply in Uzbek (Latin script) unless user writes English.
- 1-4 sentences. First sentence = answer.
- No filler. No "I understand". No "Great question".
- If unknown, say "Buni bilmayman"."""

VOICE_MAP = {
    "uz": "uz-UZ-MadinaNeural",
    "en": "en-US-JennyNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "tr": "tr-TR-AhmetNeural"
}
)

# --- UTILS ---
async def text_to_speech(text: str, lang_code: str) -> str:
    voices = {
        "uz": "uz-UZ-MadinaNeural",
        "en": "en-US-JennyNeural",
        "ru": "ru-RU-SvetlanaNeural",
        "tr": "tr-TR-AhmetNeural",
        "ar": "ar-SA-ZariyahNeural"
    }
    voice = voices.get(lang_code, "en-US-JennyNeural")
    filename = f"response_{uuid.uuid4().hex[:8]}.mp3"

    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(filename)
        return filename
    except Exception:
        # fallback: inglizcha ovoz
        communicate = edge_tts.Communicate(text, "en-US-JennyNeural")
        await communicate.save(filename)
        return filename

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
    if msg.from_user.id != ADMIN_ID:
        return await msg.reply("❌ Faqat admin uchun")

    args = msg.text.split(maxsplit=1)

    # ─── Umumiy stat ──────────────────────────────────────
    if len(args) == 1:
        total = await history_col.count_documents({})
        text = f"📊 **Foydalanuvchilar:** {total}\n\n"

        cursor = history_col.find().sort("_id", 1).limit(30)
        async for user in cursor:
            uid = user["_id"]
            msgs = user.get("messages", [])
            last = msgs[-2:] if msgs else []
            text += f"`{uid}` — {len(msgs)} ta xabar\n"
            for m in last:
                role = "👤" if m["role"] == "user" else "🤖"
                content = m["content"][:80] + ("…" if len(m["content"]) > 80 else "")
                text += f"  {role} {content}\n"
            text += "\n"

        await msg.reply(text, parse_mode="Markdown")

    # ─── Aniq user ────────────────────────────────────────
    else:
        try:
            target = int(args[1])
        except ValueError:
            return await msg.reply("❌ Noto'g'ri ID. Raqam kiriting.")

        user = await history_col.find_one({"_id": target})
        if not user:
            return await msg.reply(f"❌ `{target}` topilmadi")

        msgs = user.get("messages", [])
        text = f"📋 **{target}** — {len(msgs)} ta xabar\n\n"
        for m in msgs[-40:]:  # oxirgi 40 ta
            role = "👤" if m["role"] == "user" else "🤖"
            content = m["content"][:200] + ("…" if len(m["content"]) > 200 else "")
            text += f"{role} {content}\n\n"

        # agar juda uzun bo'lsa, faylga yoz
        if len(text) > 4000:
            with open(f"stats_{target}.txt", "w") as f:
                f.write(text)
            await msg.reply_document(FSInputFile(f"stats_{target}.txt"))
            os.remove(f"stats_{target}.txt")
        else:
            await msg.reply(text, parse_mode="Markdown")

@dp.message(F.text & ~F.command)
async def chat(msg: Message):
    try:
        lang = detect(msg.text)
    except:
        lang = "uz"

    await bot.send_chat_action(msg.chat.id, "typing")

    # History dan context yuklash
    user_data = await history_col.find_one({"_id": msg.chat.id})
    history = user_data.get("messages", []) if user_data else []
    context = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    prompt = f"{SYSTEM_INSTRUCTION}\n\n{context}\nuser: {msg.text}\nassistant:"

    try:
        res = gemini.model.generate_content(prompt)

        # History ga saqlash
        await history_col.update_one(
            {"_id": msg.chat.id},
            {"$push": {"messages": {"$each": [
                {"role": "user", "content": msg.text},
                {"role": "assistant", "content": res.text}
            ]}, "$slice": -20}},
            upsert=True
        )

        # TTS + voice
        audio = await text_to_speech(res.text, lang if lang in VOICES else "uz")
        await msg.reply_voice(voice=FSInputFile(audio), caption=res.text)

        if os.path.exists(audio):
            os.remove(audio)

    except Exception as e:
        if "429" in str(e):
            gemini.rotate()
            await asyncio.sleep(1)
            # qayta urinish — ixtiyoriy
        await msg.reply("Xatolik. Iltimos, qayta yozing.")

# --- WEB SERVER & MAIN ---
# --- WEB SERVER & MAIN ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    # Render avtomatik beradigan portni olamiz
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Web server port {port} da ishga tushdi.")

async def main():
    # 1. Serverni ishga tushiramiz (Render uchun)
    await start_web_server()
    # 2. Telegramdan eski xabarlarni tozalaymiz
    await bot.delete_webhook(drop_pending_updates=True)
    # 3. Pollingni boshlaymiz
    logging.info("Polling boshlandi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
