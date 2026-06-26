import os
import io
import asyncio
import logging
import re
import sqlite3
import json
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
from huggingface_hub import InferenceClient
import edge_tts
from aiohttp import web

# Setup Logging
logging.basicConfig(level=logging.INFO)

# Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 
HF_TOKEN = os.environ.get("HF_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # Put your Telegram ID in Render Env

# Set up Database Path (Uses Render Persistent Disk if available, otherwise fallback)
DB_DIR = "/data" if os.path.exists("/data") else "."
DB_PATH = os.path.join(DB_DIR, "qadam_bot.db")

# Initialize Hugging Face Inference Client
hf_client = InferenceClient(api_key=HF_TOKEN)

# Model Definitions
TEXT_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
VISION_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
IMAGE_GEN_MODEL = "black-forest-labs/FLUX.1-schnell"
WHISPER_MODEL = "openai/whisper-large-v3-turbo"

DAILY_LIMIT = 50  

# Initialize Bot and Dispatcher
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

SYSTEM_INSTRUCTION = (
    "Sizning ismingiz Qadam. Siz foydalanuvchi uchun samimiy va ishonchli AI do'st/yordamcisiz. "
    "Siyosiy mavzularda hech qachon biror tomonni yoqlamang yoki o'z fikringizni bildirmang — betaraf va xolis qoling. "
    "O'zbekiston qonunchiligi, davlat siyosati va milliy qadriyatlarga hurmat bilan munosabatda bo'ling. "
    "Javoblaringiz halol, aniq va to'g'ridan-to'g'ri bo'lsin. "
    "Javoblaringizni maksimal 3-4 gapdan oshirmang. Ortiqcha taxminlar va mubolag'alardan foydalanmang."
)

# --- DATABASE initialization ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Users tracking table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            usage_date TEXT,
            request_count INTEGER,
            custom_limit INTEGER DEFAULT NULL
        )
    """)
    # Chat memory table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- DATABASE HELPER FUNCTIONS ---
def save_to_memory(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
    # Keep history trimmed to last 10 rows per user to avoid massive data footprint
    cursor.execute("""
        DELETE FROM history WHERE id NOT IN (
            SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 10
        ) AND user_id = ?
    """, (user_id, user_id))
    conn.commit()
    conn.close()

def get_history_context(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY id ASC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    formatted_history = []
    for role, content in rows:
        role_type = "user" if role == "User" else "assistant"
        formatted_history.append({"role": role_type, "content": content})
    return formatted_history

def check_and_update_limit(user_id, first_name):
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT usage_date, request_count, custom_limit FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        cursor.execute("INSERT INTO users (user_id, first_name, usage_date, request_count) VALUES (?, ?, ?, ?)", 
                       (user_id, first_name, today, 1))
        conn.commit()
        conn.close()
        return True
    
    usage_date, count, custom_limit = row
    allowed_limit = custom_limit if custom_limit is not None else DAILY_LIMIT
    
    if usage_date != today:
        cursor.execute("UPDATE users SET first_name = ?, usage_date = ?, request_count = 1 WHERE user_id = ?", 
                       (first_name, today, user_id))
        conn.commit()
        conn.close()
        return True
        
    if count >= allowed_limit:
        conn.close()
        return False
        
    cursor.execute("UPDATE users SET first_name = ?, request_count = ? WHERE user_id = ?", (first_name, count + 1, user_id))
    conn.commit()
    conn.close()
    return True

# --- EXCLUSIVE ADMIN HANDLERS ---
@dp.message(Command := F.text.startswith("/admin"))
async def handle_admin_commands(message: types.Message):
    user_id = message.chat.id
    if user_id != ADMIN_ID:
        return  # Silently ignore non-admins
        
    command = message.text.split()
    cmd_name = command[0]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if cmd_name == "/admin":
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        await message.reply(f"📊 **Qadam Bot Admin Panel**\n\nTotal Database Users: `{total_users}`\nDB Location: `{DB_PATH}`")

    elif cmd_name == "/admin_users":
        cursor.execute("SELECT user_id, first_name, request_count, custom_limit FROM users")
        rows = cursor.fetchall()
        report = "👥 **User Activity Logs:**\n\n"
        for uid, name, count, climit in rows:
            lim = climit if climit else DAILY_LIMIT
            report += f"• `{uid}` | {name} | Used: **{count}/{lim}**\n"
        await message.reply(report, parse_mode="Markdown")

    elif cmd_name == "/admin_chat":
        if len(command) < 2:
            await message.reply("❌ Use syntax: `/admin_chat [user_id]`")
            conn.close()
            return
        target_id = int(command[1])
        cursor.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY id ASC", (target_id,))
        rows = cursor.fetchall()
        
        if not rows:
            await message.reply("📝 No conversation history found for this user.")
        else:
            chat_log = f"📜 **Chat History for `{target_id}`:**\n\n"
            for role, content in rows:
                chat_log += f"**{role}:** {content}\n\n"
            await message.reply(chat_log)

    elif cmd_name == "/admin_setlimit":
        if len(command) < 3:
            await message.reply("❌ Use syntax: `/admin_setlimit [user_id] [new_limit]`")
            conn.close()
            return
        target_id = int(command[1])
        new_limit = int(command[2])
        
        cursor.execute("UPDATE users SET custom_limit = ? WHERE user_id = ?", (new_limit, target_id))
        conn.commit()
        await message.reply(f"✅ Adjusted limit for `{target_id}` to **{new_limit}** requests/day.")

    conn.close()

# --- FEATURE 1: DYNAMIC TTS VOICE GENERATOR ---
async def generate_voice_reply(text: str, user_id: int) -> str:
    english_words = re.findall(r'\b(the|is|are|am|you|good|great|hello|thanks|pretty|fine|help|chat|here|for|and|to|have|nice)\b', text.lower())
    voice = "en-US-EmmaNeural" if len(english_words) >= 1 else "uz-UZ-MadinaNeural"

    file_path = f"voice_reply_{user_id}.mp3"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(file_path)
    return file_path

# --- FEATURE 2: EXPLICIT TTS CONVERSION COMMANDS (/voice or /ovoz) ---
@dp.message(F.text.startswith("/voice") | F.text.startswith("/ovoz"))
async def handle_explicit_voice_command(message: types.Message):
    user_id = message.chat.id
    first_name = message.from_user.first_name if message.from_user else "User"
    text_to_speak = message.text.replace("/voice", "").replace("/ovoz", "").strip()

    if not text_to_speak:
        history = get_history_context(user_id)
        if history and history[-1]["role"] == "assistant":
            text_to_speak = history[-1]["content"]
        else:
            await message.reply("📝 Ovozga aylantirish uchun oxirgi xabar topilmadi. Matn kiriting: `/ovoz matn`")
            return

    if not check_and_update_limit(user_id, first_name):
        await message.reply("📊 Sizning bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.RECORD_VOICE)

    try:
        voice_file_path = await generate_voice_reply(text_to_speak, user_id)
        voice_input = types.FSInputFile(voice_file_path)
        await message.reply_voice(voice=voice_input)
        os.remove(voice_file_path)
    except Exception as e:
        logging.error(f"TTS Conversion Error for user {user_id}: {e}")
        await message.reply("⚠️ Matnni ovozga o'girishda xatolik yuz berdi.")

# --- FEATURE 3: RESILIENT IMAGE GENERATION ---
@dp.message(F.text.startswith("/image"))
async def handle_image_generation(message: types.Message):
    user_id = message.chat.id
    first_name = message.from_user.first_name if message.from_user else "User"
    prompt = message.text.replace("/image", "").strip()

    if not prompt:
        await message.reply("📝 Iltimos, rasmni tasvirlab bering. Masalan: `/image kelajakdagi shahar`")
        return

    if not check_and_update_limit(user_id, first_name):
        await message.reply("📊 Sizning bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.UPLOAD_PHOTO)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            loop = asyncio.get_event_loop()
            image_obj = await loop.run_in_executor(
                None, lambda: hf_client.text_to_image(prompt, model=IMAGE_GEN_MODEL)
            )
            img_byte_arr = io.BytesIO()
            image_obj.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)

            photo_file = types.BufferedInputFile(img_byte_arr.read(), filename="generated_image.png")
            await message.reply_photo(photo=photo_file, caption=f"🎨 Sizning so'rovingiz bo'yicha rasm tayyorlandi!")
            return
        except Exception as e:
            logging.warning(f"Image attempt {attempt + 1} failed for user {user_id}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                await message.reply("⚠️ Rasmni yaratishda xatolik yuz berdi. Bir ozdan so'ng qayta urinib ko'ring.")

# --- FEATURE 4: SPEECH-TO-TEXT VOICE NOTE READING ---
@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.chat.id
    first_name = message.from_user.first_name if message.from_user else "User"
    if not check_and_update_limit(user_id, first_name):
        await message.reply("📊 Bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

    try:
        voice_file = await bot.get_file(message.voice.file_id)
        audio_buffer = io.BytesIO()
        await bot.download_file(voice_file.file_path, destination=audio_buffer)
        
        named_audio_buffer = io.BytesIO(audio_buffer.getvalue())
        named_audio_buffer.name = "voice.ogg"

        loop = asyncio.get_event_loop()
        transcription = await loop.run_in_executor(
            None, lambda: hf_client.automatic_speech_recognition(named_audio_buffer, model=WHISPER_MODEL)
        )
        user_voice_text = transcription.text.strip() if hasattr(transcription, 'text') else str(transcription).strip()

        if not user_voice_text:
            await message.reply("🎙 Ovozli xabarni tushunib bo'lmadi.")
            return

        await message.reply(f"📝 *Men eshitgan matn:* _{user_voice_text}_", parse_mode="Markdown")
        await process_chat_intelligence(message, user_voice_text)
    except Exception as e:
        logging.error(f"Voice pipeline error for user {user_id}: {e}")
        await message.reply("⚠️ Ovozni matnga o'girishda xatolik yuz berdi.")

# --- FEATURE 5: MULTI-MODAL VISION SUPPORT ---
@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.chat.id
    first_name = message.from_user.first_name if message.from_user else "User"
    if not check_and_update_limit(user_id, first_name):
        await message.reply("📊 Bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

    try:
        photo = message.photo[-1]
        photo_file = await bot.get_file(photo.file_id)
        img_buffer = io.BytesIO()
        await bot.download_file(photo_file.file_path, destination=img_buffer)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: hf_client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "Ushbu rasmda nimalar tasvirlangan? Batafsil o'zbek tilida tushuntirib ber."},
                    {"type": "image", "image": img_buffer.getvalue()}
                ]}], max_tokens=300
            )
        )
        description = response.choices[0].message.content
        await message.reply(f"👁 *Rasm tahlili:* \n\n{description}", parse_mode="Markdown")
        
        save_to_memory(user_id, "User", "[Foydalanuvchi rasm yubordi]")
        save_to_memory(user_id, "AI", description)
    except Exception as e:
        logging.error(f"Vision error for user {user_id}: {e}")
        await message.reply("⚠️ Rasmni o'qishda kutilmagan xatolik yuz berdi.")

# --- CLEAN STANDARD TEXT HANDLER ---
@dp.message(F.text)
async def handle_standard_text(message: types.Message):
    if message.text.startswith("/image") or message.text.startswith("/admin"):
        return
    await process_chat_intelligence(message, message.text.strip())

async def process_chat_intelligence(message: types.Message, user_query: str):
    user_id = message.chat.id
    first_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"

    if user_query == "/start":
        await message.reply("Qadam faol. Matn, Rasm, Ovozli so'rovlar bilan ishlashga tayyor!")
        return
    if user_query == "/clear":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await message.reply("Suhbat tarixi tozalandi.")
        return

    if not check_and_update_limit(user_id, first_name):
        await message.reply("📊 Bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    
    identity_context = f"\nFoydalanuvchining Telegramdagi ismi: {first_name}."
    messages_payload = [{"role": "system", "content": SYSTEM_INSTRUCTION + identity_context}]
    messages_payload.extend(get_history_context(user_id))
    messages_payload.append({"role": "user", "content": user_query})

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: hf_client.chat.completions.create(
                model=TEXT_MODEL, messages=messages_payload, max_tokens=250, temperature=0.7
            )
        )
        if response and response.choices:
            reply_text = response.choices[0].message.content
            save_to_memory(user_id, "User", user_query)
            save_to_memory(user_id, "AI", reply_text)
            await message.reply(reply_text, parse_mode="Markdown")
        else:
            await message.reply("⚠️ Tizimdan bo'sh xabar qaytdi.")
    except Exception as e:
        logging.error(f"Core LLM Failure for user {user_id}: {e}")
        await message.reply("⚠️ Javob qaytarishda kutilmagan xatolik yuz berdi.")

# --- WEB SERVERS CONFIGURATION ---
async def handle_telegram_webhook(request):
    try:
        data = await request.json()
        await dp.feed_update(bot, types.Update(**data))
    except Exception as e:
        logging.error(f"Webhook error: {e}")
    return web.Response(text="OK")

async def handle_ping(request):
    return web.Response(text="Bot running")

async def on_startup(app):
    await bot.set_webhook(f"{RENDER_EXTERNAL_URL}/webhook", drop_pending_updates=True)

async def on_shutdown(app):
    await bot.delete_webhook()

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_post("/webhook", handle_telegram_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
