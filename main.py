@dp.message(F.text)
async def handle_message(message: types.Message):
    user_query = message.text.strip()
    user_id = message.chat.id

    if user_query == "/start":
        await message.reply("Assalomu alaykum! Senga qanday yordam bera olaman?")
        return

    # 1. Get or create the official chat session
    user_chat_session = get_or_create_chat(user_id)

    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            # 2. Run the Gemini call inside a thread so it doesn't block
            def call_gemini():
                return user_chat_session.send_message(user_query)
            
            response = await asyncio.to_thread(call_gemini)
            
            if response and response.text:
                await message.reply(response.text)
                return
            else:
                raise Exception("Empty response from Gemini")

        except errors.APIError as api_err:
            logging.error(f"Gemini API Error (Attempt {attempt+1}): {api_err}")
            if api_err.code == 429:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                await message.reply("⚠️ Xatolik yuz berdi. Birozdan so'ng urinib ko'ring.")
                return
        except Exception as e:
            logging.error(f"General Error (Attempt {attempt+1}): {e}")
            await asyncio.sleep(retry_delay)

    await message.reply("⏳ Hozirda server band. Iltimos, bir daqiqadan so'ng qayta yozing.")
