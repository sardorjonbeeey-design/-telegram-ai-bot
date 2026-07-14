import asyncio
import logging

from olx import search_olx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database import init_db, add_user, save_listing
from gemini import parse_message
from keyboards import location_keyboard

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class UserState(StatesGroup):
    waiting_location = State()
    waiting_description = State()


@dp.message(CommandStart())
async def start(message: Message):
    await add_user(message.from_user.id)

    await message.answer(
        "👋 Assalomu alaykum!\n\n"
        "Nima qidiryapsiz yoki nima sotmoqchisiz?\n\n"
        "Misollar:\n"
        "• iPhone 15 kerak\n"
        "• Samsung sotaman\n\n"
        "🇷🇺 Можно писать и на русском."
    )


@dp.message()
async def handle_message(message: Message, state: FSMContext):
    text = message.text or ""

    data = await parse_message(text)

    intent = data["intent"]
    product = data["product"]
    language = data["language"]

    if intent == "unknown":
        if language == "ru":
            await message.answer(
                "Я не понял.\n\n"
                "Например:\n"
                "• Куплю iPhone 15\n"
                "• Продам Samsung"
            )
        else:
            await message.answer(
                "Tushunmadim.\n\n"
                "Masalan:\n"
                "• iPhone 15 kerak\n"
                "• Samsung sotaman"
            )
        return

    await state.update_data(
        intent=intent,
        product=product,
        language=language
    )

    if language == "ru":
        text = "📍 Выберите город:"
    else:
        text = "📍 Joylashuvni tanlang:"

    await message.answer(
        text,
        reply_markup=location_keyboard(language)
    )

    await state.set_state(UserState.waiting_location)
    from olx import search_listings


@dp.callback_query(
    UserState.waiting_location,
    F.data.startswith("loc:")
)
async def location_selected(
    callback: CallbackQuery,
    state: FSMContext
):
    location = callback.data.split(":", 1)[1]

    data = await state.get_data()

    intent = data["intent"]
    product = data["product"]
    language = data["language"]

    await state.update_data(location=location)

    await callback.answer()

    if intent == "buy":
        if language == "ru":
            await callback.message.edit_text("🔎 Ищу объявления...")
        else:
            await callback.message.edit_text("🔎 E'lonlar qidirilmoqda...")

        listings = await search_olx(product)

        if not listings:
            if language == "ru":
                await callback.message.answer(
                    "😔 Ничего не найдено."
                )
            else:
                await callback.message.answer(
                    "😔 Hech narsa topilmadi."
                )

            await state.clear()
            return

        for item in listings:
            text = (
                f"📦 {item['title']}\n\n"
                f"💰 {item['price']}\n"
                f"📍 {item['location']}\n\n"
                f"{item['url']}"
            )

            await callback.message.answer(text)

        await state.clear()
        return

    if language == "ru":
        await callback.message.answer(
            "📝 Отправьте описание товара одним сообщением."
        )
    else:
        await callback.message.answer(
            "📝 Mahsulot tavsifini bitta xabarda yuboring."
        )

    await state.set_state(UserState.waiting_description)
    
@dp.message(UserState.waiting_description)
async def save_description(message: Message, state: FSMContext):
    data = await state.get_data()

    telegram_id = message.from_user.id
    product = data["product"]
    location = data["location"]
    language = data["language"]
    description = message.text or ""

    await save_listing(
        telegram_id=telegram_id,
        product=product,
        description=description,
        location=location,
        photos=""
    )

    if language == "ru":
        await message.answer(
            "✅ Ваше объявление успешно сохранено!"
        )
    else:
        await message.answer(
            "✅ E'loningiz muvaffaqiyatli saqlandi!"
        )

    await state.clear()


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())