import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from google import genai
from google.genai import errors
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- OFFICIAL SDK CHAT MEMORY STORAGE ---
# This tracks active chat sessions for each user automatically
ACTIVE_CHATS = {}

SYSTEM_INSTRUCTION = (
    "You are a friendly Telegram chat assistant. Keep all responses brief, direct, "
    "and under 3 sentences long. Avoid formatting long bullet points or essays. "
    "Be friendly but honest. Your response style should be identical to Claude's. "
    "Only claim to remember or know information if it exists explicitly in the chat history. "
    "If you have the exact info in history, answer 'yes'. If you have no info, be direct and say 'no' or that you don't know. "
    "Do not imagine or fabricate things unless specifically asked to create an image or a story."
)

def get_or_create_chat(user_id):
    # If this user doesn't have an active session, start an official Gemini Chat session
    if user_id not in ACTIVE_CHATS:
        ACTIVE_CHATS[user_id] = ai_client.chats.create(
            model="gemini-2.5-flash",
            config={'system_instruction': SYSTEM_INSTRUCTION}
        )
    return ACTIVE_CHATS[user_id]
# ----------------------------------------

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Assalomu alaykum! Senga qanday yordam bera olaman?")
        return

    # 1. Get this user's official chat session
    user_chat_session = get_or_create_chat(user_id)

    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            # 2. Send the message straight to the session thread
            # Use asyncio.to_thread to keep the sync API call from freezing the async bot loop
            response = await asyncio.to_thread(user_chat_session.send_message, user_query)
            
            await message.reply(response.text)
            return

        except errors.APIError as api_err:
            if api_err.code == 429:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                await message.reply("⚠️ Xatolik yuz berdi. Birozdan so'ng urinib ko'ring.")
                return
        except Exception as e:
            logging.error(f"Gemini API Session Error: {e}")
            await message.reply("⚠️ Xatolik yuz berdi.")
            return

    await message.reply("⏳ Hozirda server band. Iltimos, bir daqiqadan so'ng qayta yozing.")

async def handle_ping(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
