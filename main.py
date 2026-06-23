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

# Claude uslubidagi, aniq va ortiqcha gaplarsiz o'zbekcha tizim ko'rsatmasi
SYSTEM_INSTRUCTION = (
    "Sizning ismingiz Qadam. Siz xolis, chuqur tahliliy va qat'iy psixologik yordamchisiz. "
    "Muloqotda moslanuvchan bo'lish uchun foydalanuvchining kognitiv holati va hissiy energiyasini aks ettiring. "
    "Muloqot uslubingiz o'ta toza, to'g'ridan-to'g'ri va aniq bo'lishi shart (Klode (Claude) uslubida). "
    "Murakkab ma'lumotlar tahlili yoki kod taqdim etilayotgan holatlardan tashqari, javoblaringizni 3-4 gapdan oshirmang. "
    "Agar so'ralgan ma'lumot yoki statistika sizda mutlaqo mavjud bo'lmasa, aniq qilib: 'Menda ushbu ma'lumotga kirish huquqi yo'q.' deb javob bering. "
    "Agar ma'lumot mavjud bo'lsa, uni to'g'ridan-to'g'ri taqdim eting. Ortiqcha gaplar, taxminlar va mubolag'alardan foydalanmang."
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
    """Telegramdan fayllarni yuklab oladi va to'g'ridan-to'g'ri Gemini API ga uzatadi."""
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path
    
    file_bytes = await bot.download_file(file_path)
    file_data = file_bytes.read()
    
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
    user_context = prompt_text if prompt_text else "Ushbu biriktirilgan faylni tizim formati bo'yicha tahlil qiling."
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
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            )
        )
        
        if response and response.text:
            reply_text = response.text
            save_to_memory(message.chat.id, "User", f"[Fayl yuborildi: .{ext}] {prompt_text if prompt_text else ''}")
            save_to_memory(message.chat.id, "AI", reply_text)
            await message.reply(reply_text, parse_mode="Markdown")
        else:
            await message.reply("Menda ushbu ma'lumotga kirish huquqi yo'q.")
    except Exception:
        logging.exception("Gemini Media Pipeline Exception:")
        await message.reply("⚠️ Xizmat vaqtincha imkonsiz. Iltimos, birozdan keyin qayta urinib ko'ring.")

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Qadam faol. Matn, rasm, ovozli xabar yoki hujjat (PDF, Docx, Xlsx) yuborishingiz mumkin.")
        return
        
    if user_query == "/clear":
        if user_id in CHAT_MEMORY:
            CHAT_MEMORY[user_id] = []
        await message.reply("Suhbat tarixi tozalandi.")
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
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            )
        )
        
        if response and response.text:
            reply_text = response.text
            save_to_memory(user_id, "User", user_query)
            save_to_memory(user_id, "AI", reply_text)
            await message.reply(reply_text, parse_mode="Markdown")
        else:
            await message.reply("Menda ushbu ma'lumotga kirish huquqi yo'q.")
    except Exception:
        logging.exception("Gemini Text Pipeline Exception:")
        await message.reply("⚠️ Xizmat vaqtincha imkonsiz. Iltimos, birozdan keyin qayta urinib ko'ring.")

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
