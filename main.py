import os
import asyncio
import logging
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError
from aiohttp import web

# Setup Logging
logging.basicConfig(level=logging.INFO)

# Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 

# Load and clean multi-API keys from a comma-separated string
RAW_KEYS = os.environ.get("GEMINI_API_KEY", "")
API_KEYS = [k.strip() for k in RAW_KEYS.split(",") if k.strip()]

CURRENT_KEY_INDEX = 0
DAILY_LIMIT = 50  

# Initialize Bot and Dispatcher
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# In-memory storage
CHAT_MEMORY = {}
USER_USAGE = {}

SYSTEM_INSTRUCTION = (
    "Sizning ismingiz Qadam. Siz foydalanuvchi uchun samimiy va ishonchli AI do'st/yordamchisiz. "
    "Siyosiy mavzularda hech qachon biror tomonni yoqlamang yoki o'z fikringizni bildirmang — betaraf va xolis qoling. "
    "O'zbekiston qonunchiligi, davlat siyosati va milliy qadriyatlarga hurmat bilan munosabatda bo'ling. "
    "Javoblaringiz halol, aniq va to'g'ridan-to'g'ri bo'lsin (Claude uslubida). "
    "Javoblaringizni maksimal 3-4 gapdan oshirmang. Ortiqcha taxminlar va mubolag'alardan foydalanmang."
)

def get_next_api_key():
    """Rotates to the next available API key in the list."""
    global CURRENT_KEY_INDEX
    if not API_KEYS:
        return None
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    selected_key = API_KEYS[CURRENT_KEY_INDEX]
    genai.configure(api_key=selected_key)
    logging.info(f"🔄 Rotated to API Key index: {CURRENT_KEY_INDEX}")
    return selected_key

# Configure the initial API key on startup
if API_KEYS:
    genai.configure(api_key=API_KEYS[CURRENT_KEY_INDEX])
else:
    logging.error("CRITICAL: No GEMINI_API_KEY found in environment variables!")

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

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Qadam faol. Kengaytirilgan limitlar rejimida ishlamoqda.")
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

    # Attempt loop for key rotation
    attempts = len(API_KEYS) if API_KEYS else 1
    for attempt in range(attempts):
        try:
            loop = asyncio.get_event_loop()
            model = genai.GenerativeModel('gemini-2.0-flash')

            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(full_prompt)
            )

            if response and response.text:
                reply_text = response.text
                save_to_memory(user_id, "User", user_query)
                save_to_memory(user_id, "AI", reply_text)
                await message.reply(reply_text, parse_mode="Markdown")
                return 
            else:
                await message.reply("⚠️ Sun'iy intellektdan bo'sh xabar qaytdi.")
                return

        except (ResourceExhausted, GoogleAPIError) as e:
            error_str = str(e)
            logging.warning(f"⚠️ Key index {CURRENT_KEY_INDEX} hit an error: {error_str}. Trying next key...")
            
            if attempt < attempts - 1:
                get_next_api_key()
                await asyncio.sleep(0.5) 
                continue
            else:
                # All keys exhausted or broken
                await message.reply(f"⏳ System overload or limit reached.\nDetails: `{error_str}`", parse_mode="Markdown")
                return
        except Exception as e:
            logging.error(f"Unexpected Pipeline Error: {str(e)}")
            await message.reply(f"⚠️ Unexpected error occurred:\n`{str(e)}`", parse_mode="Markdown")
            return

async def handle_telegram_webhook(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.error(f"Webhook structural error: {e}")
    return web.Response(text="OK")

async def handle_ping(request):
    return web.Response(text="Bot running")

async def on_startup(app):
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    logging.info(f"Setting webhook endpoint to: {webhook_url}")
    await bot.set_webhook(webhook_url, drop_pending_updates=True)

async def on_shutdown(app):
    logging.info("Tearing down webhook configuration...")
    await bot.delete_webhook()

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_post("/webhook", handle_telegram_webhook)
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    # Keeps the aiohttp web server alive infinitely
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
