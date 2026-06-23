import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from google import genai
from google.genai import errors
from google.genai import types as genai_types
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- GLOBAL COMPLIANT CHAT MEMORY ---
CHAT_MEMORY = {}

SYSTEM_INSTRUCTION = (
    "You are a friendly Telegram chat assistant. Keep all responses brief, direct, "
    "and under 3 sentences long. Avoid formatting long bullet points or essays. "
    "Be friendly but honest. Your response style should be identical to Claude's. "
    "If the user sends an image, look at it carefully and describe exactly what you see "
    "or answer their question about it directly."
)

def save_to_memory(user_id, role, content):
    if user_id not in CHAT_MEMORY:
        CHAT_MEMORY[user_id] = []
    CHAT_MEMORY[user_id].append({"role": role, "content": content})
    if len(CHAT_MEMORY[user_id]) > 20:
        CHAT_MEMORY[user_id] = CHAT_MEMORY[user_id][-20:]

def get_history_context(user_id):
    if user_id not in CHAT_MEMORY:
        return ""
    context = ""
    for msg in CHAT_MEMORY[user_id]:
        context += f"{msg['role']}: {msg['content']}\n"
    return context

# --- 1. HANDLE TEXT MESSAGES ---
@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Assalomu alaykum! Menga rasm yoki matn yuborishingiz mumkin.")
        return

    history = get_history_context(user_id)
    full_prompt = f"Conversation History:\n{history}\nCurrent User Message: {user_query}"

    await process_gemini_request(message, user_id, full_prompt, user_query)

# --- 2. HANDLE PHOTO MESSAGES ---
@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.chat.id
    # Get any text sent along with the photo (caption), or default to a standard request
    user_query = message.caption.strip() if message.caption else "Analyze this image and tell me what you see."
    
    # Get the largest version of the photo to ensure best quality analysis
    photo = message.photo[-1]
    
    try:
        # Download the photo from Telegram servers into local memory
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        image_data = file_bytes.read()
        
        # Format the image into the Part structure the new SDK expects
        image_part = genai_types.Part.from_bytes(
            data=image_data,
            mime_type="image/jpeg"
        )
        
        history = get_history_context(user_id)
        contents = [
            f"Conversation History:\n{history}\nCurrent User Request about this image: {user_query}",
            image_part
        ]
        
        await process_gemini_request(message, user_id, contents, f"[Sent a photo]: {user_query}")
        
    except Exception as e:
        logging.error(f"Failed to process photo: {e}")
        await message.reply("⚠️ Rasmni yuklab olishda xatolik yuz berdi.")

# --- COMMON GEMINI PROCESSING CORE ---
async def process_gemini_request(message, user_id, contents, memory_query):
    max_retries = 3
    retry_delay = 3

    for attempt in range(max_retries):
        try:
            response = ai_client.models.generate_content(
                model='gemini-1.5-flash-002',
                contents=contents,
                config={'system_instruction': SYSTEM_INSTRUCTION}
            )
            
            if response and response.text:
                reply_text = response.text
                save_to_memory(user_id, "User", memory_query)
                save_to_memory(user_id, "AI", reply_text)
                await message.reply(reply_text)
                return
            else:
                raise Exception("Empty text field received from API")

        except errors.APIError as api_err:
            logging.error(f"Gemini API Error (Attempt {attempt+1}): {api_err}")
            if api_err.code == 429:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                await message.reply("⚠️ API xatoligi yuz berdi.")
                return
        except Exception as e:
            logging.error(f"General Connection Error (Attempt {attempt+1}): {e}")
            await asyncio.sleep(retry_delay)

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
    logging.info(f"Web server active on port {port}")
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
