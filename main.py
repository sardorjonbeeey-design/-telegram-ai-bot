import os
import io
import asyncio
import logging
from datetime import date
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.chat_action import ChatAction
from huggingface_hub import InferenceClient
import edge_tts
from aiohttp import web
from PIL import Image

# Setup Logging
logging.basicConfig(level=logging.INFO)

# Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 
HF_TOKEN = os.environ.get("HF_TOKEN")

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

# --- FEATURE 1: TEXT-TO-SPEECH VOICE NOTES GENERATOR ---
async def generate_voice_reply(text: str, user_id: int) -> str:
    """Generates an Uzbek voice file using Microsoft Edge's free engine."""
    voice = "uz-UZ-MadinaNeural"
    file_path = f"voice_reply_{user_id}.mp3"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(file_path)
    return file_path

# --- FEATURE 2: FREE IMAGE GENERATION (/image command) ---
@dp.message(F.text.startswith("/image"))
async def handle_image_generation(message: types.Message):
    user_id = message.chat.id
    prompt = message.text.replace("/image", "").strip()

    if not prompt:
        await message.reply("📝 Iltimos, rasmni tasvirlab bering. Masalan: `/image kelajakdagi shahar`")
        return

    if not check_and_update_limit(user_id):
        await message.reply("📊 Sizning bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.UPLOAD_PHOTO)

    try:
        loop = asyncio.get_event_loop()
        # Request a free serverless image from Hugging Face FLUX
        pil_img = await loop.run_in_executor(
            None,
            lambda: hf_client.text_to_image(prompt, model=IMAGE_GEN_MODEL)
        )

        # Convert PIL Image back into runtime byte buffer to keep Render disk space clean
        img_byte_arr = io.BytesIO()
        pil_img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)

        photo_file = types.BufferedInputFile(img_byte_arr.read(), filename="generated_image.png")
        await message.reply_photo(photo=photo_file, caption=f"🎨 Sizning so'rovingiz bo'yicha rasm tayyorlandi!")
    except Exception as e:
        logging.error(f"Image gen error: {e}")
        await message.reply("⚠️ Rasmni yaratishda xatolik yuz berdi. Iltimos qayta urinib ko'ring.")

# --- FEATURE 3: SPEECH-TO-TEXT VOICE NOTE READING ---
@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.chat.id
    if not check_and_update_limit(user_id):
        await message.reply("📊 Bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

    try:
        voice_file = await bot.get_file(message.voice.file_id)
        audio_buffer = io.BytesIO()
        await bot.download_file(voice_file.file_path, destination=audio_buffer)
        audio_bytes = audio_buffer.getvalue()

        loop = asyncio.get_event_loop()
        # Transcribe voice audio using Whisper on Hugging Face
        transcription = await loop.run_in_executor(
            None,
            lambda: hf_client.automatic_speech_recognition(audio_bytes, model=WHISPER_MODEL)
        )
        
        user_voice_text = transcription.text.strip() if hasattr(transcription, 'text') else str(transcription).strip()

        if not user_voice_text:
            await message.reply("🎙 Ovozli xabarni tushunib bo'lmadi.")
            return

        await message.reply(f"📝 *Men eshitgan matn:* _{user_voice_text}_", parse_mode="Markdown")
        await process_chat_intelligence(message, user_voice_text)

    except Exception as e:
        logging.error(f"Voice pipeline error: {e}")
        await message.reply("⚠️ Ovozni matnga o'girishda xatolik yuz berdi.")

# --- FEATURE 4: MULTI-MODAL VISION SUPPORT (Photo recognition) ---
@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    user_id = message.chat.id
    if not check_and_update_limit(user_id):
        await message.reply("📊 Bugungi limitingiz tugadi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

    try:
        photo = message.photo[-1]
        photo_file = await bot.get_file(photo.file_id)
        img_buffer = io.BytesIO()
        await bot.download_file(photo_file.file_path, destination=img_buffer)
        img_bytes = img_buffer.getvalue()

        # Prompt vision model to describe the picture context
        vision_prompt = "Ushbu rasmda nimalar tasvirlangan? Batafsil o'zbek tilida tushuntirib ber."
        
        loop = asyncio.get_event_loop()
        # Pass payload to the vision model via chat format support
        response = await loop.run_in_executor(
            None,
            lambda: hf_client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {"type": "image", "image": img_bytes}
                    ]
                }],
                max_tokens=300
            )
        )
        
        description = response.choices[0].message.content
        await message.reply(f"👁 *Rasm tahlili:* \n\n{description}", parse_mode="Markdown")
        
        # Save structural context into text history
        save_to_memory(user_id, "User", "[Foydalanuvchi rasm yubordi]")
        save_to_memory(user_id, "AI", description)

    except Exception as e:
        logging.error(f"Vision engine error: {e}")
        await message.reply("⚠️ Rasmni o'qishda kutilmagan xatolik yuz berdi.")

# --- CORE PROCESSING HANDLING ENGINE ---
@dp.message(F.text)
async def handle_standard_text(message: types.Message):
    await process_chat_intelligence(message, message.text.strip())

async def process_chat_intelligence(message: types.Message, user_query: str):
    user_id = message.chat.id
    tg_first_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"

    if user_query == "/start":
        await message.reply("Qadam faol. Matn, Rasm, Ovoz va Tasvirlarni tahlil qilishga tayyor!")
        return
    if user_query == "/clear":
        if user_id in CHAT_MEMORY:
            CHAT_MEMORY[user_id] = []
        await message.reply("Suhbat tarixi tozalandi.")
        return

    await message.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    
    identity_context = f"\nFoydalanuvchining Telegramdagi ismi: {tg_first_name}."
    messages_payload = [{"role": "system", "content": SYSTEM_INSTRUCTION + identity_context}]
    messages_payload.extend(get_history_context(user_id))
    messages_payload.append({"role": "user", "content": user_query})

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: hf_client.chat.completions.create(
                model=TEXT_MODEL,
                messages=messages_payload,
                max_tokens=250,
                temperature=0.7
            )
        )

        if response and response.choices:
            reply_text = response.choices[0].message.content
            save_to_memory(user_id, "User", user_query)
            save_to_memory(user_id, "AI", reply_text)
            
            # Text Response Delivery
            await message.reply(reply_text, parse_mode="Markdown")
            
            # Deliver voice synthesis note as well
            try:
                voice_file_path = await generate_voice_reply(reply_text, user_id)
                voice_input = types.FSInputFile(voice_file_path)
                await message.reply_voice(voice=voice_input)
                os.remove(voice_file_path) # Wipe clean local MP3 instantly
            except Exception as v_err:
                logging.error(f"TTS asset delivery failed: {v_err}")
        else:
            await message.reply("⚠️ Tizimdan bo'sh xabar qaytdi.")

    except Exception as e:
        logging.error(f"Core LLM Failure: {e}")
        await message.reply("⚠️ Javob qaytarishda xatolik yuz berdi.")

# --- WEB SERVERS HOSTING CONFIGURATIONS ---
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
    await bot.set_webhook(webhook_url, drop_pending_updates=True)

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
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
