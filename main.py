import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from google import genai
from google.genai import errors

logging.basicConfig(level=logging.INFO)

# Change these strings to your actual keys!
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_FROM_BOTFATHER"
GEMINI_API_KEY = "YOUR_GOOGLE_AI_STUDIO_API_KEY"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "You are a friendly Telegram chat assistant. Keep all responses brief, direct, "
    "and under 3 sentences long. Avoid formatting long bullet points or essays. "
    "Be friendly but honest. Your response style should be identical to Claude: "
    "if you have the exact info answer 'yes', if you have no info be direct and say 'no'. "
    "Do not imagine or fabricate things unless specifically asked to create an image or a story."
)

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_query = message.text.strip()
    if user_query == "/start":
        await message.reply("Assalomu alaykum! Senga qanday yordam bera olaman?")
        return

    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_query,
                config={'system_instruction': SYSTEM_INSTRUCTION}
            )
            await message.reply(response.text)
            return

        except errors.APIError as api_err:
            if api_err.code == 429:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                await message.reply("⚠️ Xatolik yuz berdi. Birozdan so'ng urinib ko'ring.")
                return
        except Exception:
            await message.reply("⚠️ Xatolik yuz berdi.")
            return

    await message.reply("⏳ Hozirda server band. Iltimos, bir daqiqadan so'ng qayta yozing.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
