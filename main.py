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

# --- IN-MEMORY HISTORY STORAGE ---
# This saves history in Python memory (RAM) instead of a database file
CHAT_MEMORY = {}

def save_message(user_id, role, content):
    if user_id not in CHAT_MEMORY:
        CHAT_MEMORY[user_id] = []
    
    CHAT_MEMORY[user_id].append({"role": role, "content": content})
    
    # Keep only the last 30 messages to avoid high memory usage
    if len(CHAT_MEMORY[user_id]) > 30:
        CHAT_MEMORY[user_id] = CHAT_MEMORY[user_id][-30:]

def get_chat_history(user_id):
    if user_id not in CHAT_MEMORY:
        return ""
    
    history_text = ""
    for msg in CHAT_MEMORY[user_id]:
        history_text += f"{msg['role']}: {msg['content']}\n"
    return history_text
# ---------------------------------

SYSTEM_INSTRUCTION = (
    "You are a friendly Telegram chat assistant. Keep all responses brief, direct, "
    "and under 3 sentences long. Avoid formatting long bullet points or essays. "
    "Be friendly but honest. Your response style should be identical to Claude's. "
    "Only claim to remember or know information if it exists explicitly in the Conversation History provided. "
    "If you have the exact info in history, answer 'yes'. If you have no info, be direct and say 'no' or that you don't know. "
    "Do not imagine or fabricate things unless specifically asked to create an image or a story."
)

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Assalomu alaykum! Senga qanday yordam bera olaman?")
        return

    # 1. Save the user's message to memory
    save_message(user_id, "User", user_query)

    # 2. Pull the history text
    history = get_chat_history(user_id)

    # 3. Format the context prompt
    full_prompt = f"""Conversation History:
{history}

Current User Message: {user_query}"""

    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_prompt,
                config={'system_instruction': SYSTEM_INSTRUCTION}
            )
            reply_text = response.text
            
            # 4. Save AI's response to memory
            save_message(user_id, "AI", reply_text)
            
            await message.reply(reply_text)
            return

        except errors.APIError as api_err:
            if api_err.code == 429:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                await message.reply("⚠️ Xatolik yuz berdi. Birozdan so'ng urinib ko'ring.")
                return
        except Exception as e:
            logging.error(f"API Error occurred: {e}")
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
