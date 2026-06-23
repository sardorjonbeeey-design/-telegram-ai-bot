import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction  # Clean, type-safe status actions
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

# --- GLOBAL IN-MEMORY CHAT STORAGE ---
CHAT_MEMORY = {}

SYSTEM_INSTRUCTION = (
    "You are a friendly, highly capable Telegram assistant named Qadam. "
    "Keep all responses brief, direct, and under 3-4 sentences unless providing code or structured analysis. "
    "Your style is clean, modern, and professional, mirroring luxury minimalism. "
    "Formatting Rule: Always format your text output cleanly. Use clear bold section headers for distinct parts, "
    "bullet points for lists, and monospaced code blocks for code or logs. Never send raw, unstructured walls of text."
    "You have access to Google Search for live, real-time facts—always use it when asked about current events, news, or setup details. "
    "You can natively process images, voice messages, and documents (PDF/TXT) sent by the user."
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

# --- 1. HANDLE TEXT MESSAGES & COMMANDS ---
@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Assalomu alaykum! Senga qanday yordam bera olaman? Matn, rasm, ovozli xabar yoki hujjat yuborishing mumkin! 🚀")
        return
        
    if user_query == "/clear":
        CHAT_MEMORY[user_id] = []
        await message.reply("Clear! Suhbat tarixi tozalandi. Yangi mavzuni boshlashimiz mumkin. 🧹")
        return

    # Trigger instant text feedback
    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    history = get_history_context(user_id)
    full_prompt = f"Conversation History:\n{history}\nCurrent User Message: {user_query}"

    await process_gemini_request(message, user_id, full_prompt, user_query)

# --- 2. HANDLE PHOTO MESSAGES ---
@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.chat.id
    user_query = message.caption.strip() if message.caption else "Analyze this image and describe what you see."
    
    # Trigger image-processing specific status feedback
    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.UPLOAD_PHOTO)
    photo = message.photo[-1]
    
    try:
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        image_part = genai_types.Part.from_bytes(data=file_bytes.read(), mime_type="image/jpeg")
        
        history = get_history_context(user_id)
        contents = [f"Conversation History:\n{history}\nUser Request about image: {user_query}", image_part]
        
        await process_gemini_request(message, user_id, contents, f"[Sent a Photo]: {user_query}")
    except Exception as e:
        logging.error(f"Photo processing failed: {e}")
        await message.reply("⚠️ Rasmni qayta ishlashda xatolik yuz berdi.")

# --- 3. HANDLE VOICE MESSAGES (VOICE-TO-TEXT) ---
@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.chat.id
    # Voice takes a second to process, show active typing signal right away
    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    voice = message.voice

    try:
        file_info = await bot.get_file(voice.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        audio_part = genai_types.Part.from_bytes(data=file_bytes.read(), mime_type="audio/ogg")
        
        history = get_history_context(user_id)
        contents = [f"Conversation History:\n{history}\nListen to this voice message and respond to it directly:", audio_part]
        
        await process_gemini_request(message, user_id, contents, "[Sent a Voice Note]")
    except Exception as e:
        logging.error(f"Voice note processing failed: {e}")
        await message.reply("⚠️ Ovozli xabarni o'qishda xatolik yuz berdi.")

# --- 4. HANDLE DOCUMENTS (PDF & TXT) ---
@dp.message(F.document)
async def handle_document_message(message: types.Message):
    user_id = message.chat.id
    user_query = message.caption.strip() if message.caption else "Summarize or explain the content of this file."
    doc = message.document
    
    mime = doc.mime_type if doc.mime_type else ""
    if not ("text" in mime or "pdf" in mime):
        await message.reply("⚠️ Iltimos, faqat PDF yoki matnli (.txt) hujjat yuboring.")
        return

    # Trigger visual feedback while reading the document structure
    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    try:
        file_info = await bot.get_file(doc.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        doc_part = genai_types.Part.from_bytes(data=file_bytes.read(), mime_type=mime)
        
        history = get_history_context(user_id)
        contents = [f"Conversation History:\n{history}\nUser query about this file: {user_query}", doc_part]
        
        await process_gemini_request(message, user_id, contents, f"[Sent a Document]: {user_query}")
    except Exception as e:
        logging.error(f"Document processing failed: {e}")
        await message.reply("⚠️ Hujjatni yuklashda xatolik yuz berdi.")

# --- COMMON GEMINI ENGINE PROCESSING WITH LIVE GOOGLE SEARCH ---
async def process_gemini_request(message, user_id, contents, memory_query):
    max_retries = 3
    retry_delay = 3

    for attempt in range(max_retries):
        try:
            response = ai_client.models.generate_content(
                model='gemini-1.5-flash',
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
                )
            )
            
            if response and response.text:
                reply_text = response.text
                save_to_memory(user_id, "User", memory_query)
                save_to_memory(user_id, "AI", reply_text)
                await message.reply(reply_text, parse_mode="Markdown")
                return
            else:
                raise Exception("Empty response payload received")

        except errors.APIError as api_err:
            logging.error(f"Gemini API Error (Attempt {attempt+1}): {api_err}")
            if api_err.code == 429:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                await message.reply("⚠️ Tizim xatoligi yuz berdi.")
                return
        except Exception as e:
            logging.error(f"General Loop Error (Attempt {attempt+1}): {e}")
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
    logging.info(f"Web service bound to port {port}")
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
