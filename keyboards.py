from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


def location_keyboard(language: str = "uz") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if language == "ru":
        locations = [
            ("Ташкент", "Ташкент"),
            ("Самарканд", "Самарканд"),
            ("Бухара", "Бухара"),
            ("Андижан", "Андижан"),
            ("Фергана", "Фергана"),
            ("Наманган", "Наманган"),
            ("Навои", "Навои"),
            ("Хорезм", "Хорезм"),
            ("Кашкадарья", "Кашкадарья"),
            ("Сурхандарья", "Сурхандарья"),
            ("Сырдарья", "Сырдарья"),
            ("Джизак", "Джизак"),
            ("Каракалпакстан", "Каракалпакстан"),
        ]
    else:
        locations = [
            ("Toshkent", "Toshkent"),
            ("Samarqand", "Samarqand"),
            ("Buxoro", "Buxoro"),
            ("Andijon", "Andijon"),
            ("Farg'ona", "Farg'ona"),
            ("Namangan", "Namangan"),
            ("Navoiy", "Navoiy"),
            ("Xorazm", "Xorazm"),
            ("Qashqadaryo", "Qashqadaryo"),
            ("Surxondaryo", "Surxondaryo"),
            ("Sirdaryo", "Sirdaryo"),
            ("Jizzax", "Jizzax"),
            ("Qoraqalpog'iston", "Qoraqalpog'iston"),
        ]

    for text, value in locations:
        builder.button(
            text=text,
            callback_data=f"loc:{value}"
        )

    builder.adjust(2)

    return builder.as_markup()


def yes_no_keyboard(language: str = "uz") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if language == "ru":
        builder.button(text="✅ Да", callback_data="yes")
        builder.button(text="❌ Нет", callback_data="no")
    else:
        builder.button(text="✅ Ha", callback_data="yes")
        builder.button(text="❌ Yo'q", callback_data="no")

    builder.adjust(2)

    return builder.as_markup()