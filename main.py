import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
from google import genai
from google.genai import types as genai_types
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

CHAT_MEMORY = {}

# Exact Claude-style system prompt ensuring precise, professional, and adaptive tone
SYSTEM_INSTRUCTION = (
    "You are Qadam, an objective and deeply perceptive psychological assistant. "
    "Dynamically mirror the user's cognitive state and emotional energy to keep interactions flexible. "
    "Adopt a highly clean, direct, and ultra-precise response format. "
    "Keep answers under 3-4 sentences unless analyzing complex data arrays or providing code. "
    "If you completely lack verified information or metrics for a request, reply: 'I do not have access to that information.' "
    "If you have the data, present it directly. Never implement fluff, speculation, imagination, or conversational padding."
)

def save_to_memory(user_id, role, content):
    if user_id not in CHAT_MEMORY:
        CHAT_MEMORY[user_id] = []
    CHAT_MEMORY[user_id].append({"role": role, "content": content})
    if len(CHAT_MEMORY[user_id]) > 14:
        CHAT_MEMORY[user_id] = CHAT_MEMORY[user_id][-14:]

def get_history_context(user_id):
    if user_id not in CHAT_MEMORY:
        return ""
    context = ""
    for msg in CHAT_MEMORY[user_id]:
        context += f"{msg['role']}: {msg['content']}\n"
    return context

async def process_multimedia_message(message: types.Message, file_id: str, prompt_text: str):
    """Downloads files safely from Telegram and forwards them directly to the Gemini API."""
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path
    
    # Extract file contents as binary data
    file_bytes = await bot.download_file(file_path)
    file_data = file_bytes.read()
    
    # Establish correct MIME mapping for documents, audio, and photos
    ext = file_path.split('.')[-1].lower()
    mime_types = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'oga': 'audio/ogg', 'ogg': 'audio/ogg', 'mp3': 'audio/mp3',
        'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    }
    mime_type = mime_types.get(ext, 'application/octet-stream')

    history = get_history_context(message.chat.id)
    user_context = prompt_text if prompt_text else "Analyze this attachment according to your system formatting."
    full_prompt = f"History:\n{history}\nUser Request: {user_context}"

    try:
        response = ai_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[
                genai_types.Part.from_bytes(data=file_data, mime_type=mime_type),
                full_prompt
            ],
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[{"google_search": {}}]  # Active live Google Search grounding
            )
        )
        
        if response and response.text:
            reply_text = response.text
            save_to_memory(message.chat.id, "User", f"[Sent File: .{ext}] {prompt_text if prompt_text else ''}")
            save_to_memory(message.chat.id, "AI", reply_text)
            await message.reply(reply_text, parse_mode="Markdown")
        else:
            await message.reply("I do not have access to that information.")
    except Exception:
        logging.exception("Gemini Media Pipeline Exception:")
        await message.reply("⚠️ Service temporarily unavailable. Please try again later.")

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Qadam active. Send text, images, voice messages, or documents (PDF, Docx, Xlsx).")
        return
        
    if user_query == "/clear":
        if user_id in CHAT_MEMORY:
            CHAT_MEMORY[user_id] = []
        await message.reply("Memory reset successfully.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    history = get_history_context(user_id)
    full_prompt = f"History:\n{history}\nUser: {user_query}"

    try:
        response = ai_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=full_prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[{"google_search": {}}]  # Triggers Google Web Search when asking for news or stats
            )
        )
        
        if response and response.text:
            reply_text = response.text
            save_to_memory(user_id, "User", user_query)
            save_to_memory(user_id, "AI", reply_text)
            await message.reply(reply_text, parse_mode="Markdown")
        else:
            await message.reply("I do not have access to that information.")
    except Exception:
        logging.exception("Gemini Text Pipeline Exception:")
        await message.reply("⚠️ Service temporarily unavailable. Please try again later.")

# Specific Telegram format routing
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    photo_id = message.photo[-1].file_id
    await process_multimedia_message(message, photo_id, message.caption)

@dp.message(F.voice | F.audio)
async def handle_audio(message: types.Message):
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    await process_multimedia_message(message, file_id, message.caption)

@dp.message(F.document)
async def handle_document(message: types.Message):
    file_id = message.document.file_id
    await process_multimedia_message(message, file_id, message.caption)

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
    
    logging.info("Starting Telegram polling mechanics...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
