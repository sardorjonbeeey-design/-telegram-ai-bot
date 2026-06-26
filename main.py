import os
import asyncio
import logging
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
from huggingface_hub import InferenceClient
from aiohttp import web

# Setup Logging
logging.basicConfig(level=logging.INFO)

# Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 
HF_TOKEN = os.environ.get("HF_TOKEN")

# Initialize Hugging Face Inference Client
# Using a powerful open-source 70B parameter model
MODEL_NAME = "meta-llama/Llama-3.3-70B-Instruct"
hf_client = InferenceClient(api_key=HF_TOKEN)

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
    "Javoblaringiz halol, aniq va to'g'ridan-to'g'ri bo'lsin. "
    "Javoblaringizni maksimal 3-4 gapdan oshirmang. Ortiqcha taxminlar va mubolag'alardan foydalanmang."
)

def save_to_memory(user_id, role, content):
    if user_id not in CHAT_MEMORY:
        CHAT_MEMORY[user_id] = []
    CHAT_MEMORY[user_id].append({"role": role, "content": content})
    if len(CHAT_MEMORY[user_id]) > 10:
        CHAT_MEMORY[user_id] = CHAT_MEMORY[user_id][-10:]

def get_history_context(user_id):
    if user_id not in CHAT_MEMORY:
        return []
    
    formatted_history = []
    for msg in CHAT_MEMORY[user_id]:
        role_type = "user" if msg['role'] == "User" else "assistant"
        formatted_history.append({"role": role_type, "content": msg['content']})
    return formatted_history

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
    
    # Safely extract Telegram profile name as a fallback default
    tg_first_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"

    if user_query == "/start":
        await message.reply("Qadam faol. Hugging Face serverless rejimida muvaffaqiyatli ishlamoqda.")
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

    # Dynamic Identity Safety injection
    identity_context = f"\nFoydalanuvchining Telegramdagi ismi: {tg_first_name}. Suhbat davomida uning ismi boshqa bo'lsa, o'sha ism bilan murojaat qiling."
    
    messages_payload = [{"role": "system", "content": SYSTEM_INSTRUCTION + identity_context}]
    messages_payload.extend(get_history_context(user_id))
    messages_payload.append({"role": "user", "content": user_query})

    try:
        loop = asyncio.get_event_loop()
        
        response = await loop.run_in_executor(
            None,
            lambda: hf_client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages_payload,
                max_tokens=250,
                temperature=0.7
            )
        )

        if response and response.choices:
            reply_text = response.choices[0].message.content
            
            # Save exact turn to context memory
            save_to_memory(user_id, "User", user_query)
            save_to_memory(user_id, "AI", reply_text)
            
            await message.reply(reply_text, parse_mode="Markdown")
            return 
        else:
            await message.reply("⚠️ Tizimdan bo'sh javob qaytdi.")
            return

    except Exception as e:
        logging.error(f"Hugging Face Pipeline Error: {str(e)}")
        await message.reply(f"⚠️ API Pipeline unexpected error:\n`{str(e)}`", parse_mode="Markdown")
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
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
