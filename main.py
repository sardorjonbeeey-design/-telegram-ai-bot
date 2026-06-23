import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
import google.generativeai as genai
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini konfiguratsiyasi
genai.configure(api_key=GEMINI_API_KEY)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Har bir foydalanuvchi uchun alohida izolatsiya qilingan xotira muhiti
CHAT_MEMORY = {}
from datetime import date

DAILY_LIMIT = 20  # bitta foydalanuvchi kuniga nechta xabar yubora oladi (o'zgartirishingiz mumkin)
USER_USAGE = {}

def check_and_update_limit(user_id):
    today = date.today().isoformat()
    usage = USER_USAGE.get(user_id)
    if usage is None or usage["date"] != today:
        USER_USAGE[user_id] = {"date": today, "count": 1}
        return True
    if usage["count"] >= DAILY_LIMIT:
        return False
    usage["count"] += 1
    return True
    
SYSTEM_INSTRUCTION = (
    "Sizning ismingiz Qadam. Siz foydalanuvchining shaxsiy, xolis va chuqur tahliliy psixologik yordamchisiz. "
    "Muloqotda moslanuvchan bo'lish uchun har bir foydalanuvchining kognitiv holati va hissiy energiyasini mukammal aks ettiring. "
    "Muloqot uslubingiz o'ta toza, to'g'ridan-to'g'ri va aniq bo'lishi shart (Claude uslubida). "
    "Javoblaringizni maksimal 3-4 gapdan oshirmang. Ortiqcha gaplar, taxminlar va mubolag'alardan foydalanmang."
)

def save_to_memory(user_id, role, content):
    if user_id not in CHAT_MEMORY:
        CHAT_MEMORY[user_id] = []
    CHAT_MEMORY[user_id].append({"role": role, "content": content})
    if len(CHAT_MEMORY[user_id]) > 10:
        CHAT_MEMORY[user_id] = CHAT_MEMORY[user_id][-10:]

def get_history_context(user_id):
    if user_id not in CHAT_MEMORY:
        return ""
    context = ""
    for msg in CHAT_MEMORY[user_id]:
        context += f"{msg['role']}: {msg['content']}\n"
    return context

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Qadam faol. Shaxsiy yordamchi rejimida ishga tushirildi. Savolingizni yozishingiz mumkin.")
        return
        
    if user_query == "/clear":
        if user_id in CHAT_MEMORY:
            CHAT_MEMORY[user_id] = []
        await message.reply("Suhbat tarixi tozalandi.")
        return
if not check_and_update_limit(user_id):
        await message.reply("📊 Sizning bugungi limitingiz tugadi. Limit ertaga tiklanadi.")
        return
    
    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    
    history = get_history_context(user_id)
    full_prompt = f"{SYSTEM_INSTRUCTION}\n\nSuhbat tarixi:\n{history}\nFoydalanuvchi: {user_query}\nJavob:"

    try:
        loop = asyncio.get_event_loop()
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        
        # Asinxron oqim to'silib qolmasligi uchun executor'da bajaramiz
        response = await loop.run_in_executor(
            None, 
            lambda: model.generate_content(full_prompt)
        )
        
        if response and response.text:
            reply_text = response.text
            save_to_memory(user_id, "User", user_query)
            save_to_memory(user_id, "AI", reply_text)
            await message.reply(reply_text, parse_mode="Markdown")
        else:
            await message.reply("⚠️ Sun'iy intellektdan bo'sh xabar qaytdi.")
            
    except Exception as e:
        logging.error(f"Gemini Pipeline Error: {str(e)}")
        # Xatolik kodini to'g'ridan-to'g'ri Telegramga chiqaradi, srazu sababini ko'ramiz
        await message.reply(f"⚠️ Xatolik yuz berdi:\n`{str(e)}`", parse_mode="Markdown")

async def handle_ping(request):
    return web.Response(text="Bot running")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    logging.info("Telegram polling boshlanmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
