import os
import html as py_html
import sqlite3
import secrets
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =========================
# НАСТРОЙКИ
# =========================

TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
ADMIN_ID = 8596393850
DB_FILE = "giveaways.db"


# =========================
# БАЗА ДАННЫХ
# =========================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    winner_id INTEGER
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS participants (
    giveaway_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT,
    joined_at TEXT NOT NULL,
    UNIQUE(giveaway_id, user_id)
)
""")

db.commit()


# =========================
# СОСТОЯНИЯ СОЗДАНИЯ КОНКУРСА
# =========================

class CreateGiveaway(StatesGroup):
    channel = State()
    text = State()


# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def esc(value: str) -> str:
    return py_html.escape(value or "")


def join_keyboard(giveaway_id: int, count: int = 0):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎉 Участвовать ({count})",
                    callback_data=f"join:{giveaway_id}"
                )
            ]
        ]
    )


def admin_giveaway_keyboard(giveaway_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏁 Подвести итоги",
                    callback_data=f"finish:{giveaway_id}"
                )
            ]
        ]
    )


def get_participants(giveaway_id: int):
    return db.execute(
        "SELECT * FROM participants WHERE giveaway_id = ? ORDER BY joined_at",
        (giveaway_id,)
    ).fetchall()


async def publish_winner(bot: Bot, giveaway_id: int, winner_id: int):
    giveaway = db.execute(
        "SELECT * FROM giveaways WHERE id = ? AND status = 'active'",
        (giveaway_id,)
    ).fetchone()

    if not giveaway:
        return False, "Конкурс уже завершён или не найден."

    winner = db.execute(
        "SELECT * FROM participants WHERE giveaway_id = ? AND user_id = ?",
        (giveaway_id, winner_id)
    ).fetchone()

    if not winner:
        return False, "Участник не найден."

    name = winner["full_name"] or "Победитель"
    mention = f'<a href="tg://user?id={winner_id}">{esc(name)}</a>'

    result_text = (
        f"🏆 <b>Итоги конкурса</b>\n\n"
        f"Победитель: {mention}\n\n"
        f"Спасибо всем за участие!"
    )

    await bot.send_message(
        chat_id=giveaway["chat_id"],
        text=result_text
    )

    db.execute(
        "UPDATE giveaways SET status = 'finished', winner_id = ? WHERE id = ?",
        (winner_id, giveaway_id)
    )
    db.commit()

    try:
        await bot.edit_message_reply_markup(
            chat_id=giveaway["chat_id"],
            message_id=giveaway["message_id"],
            reply_markup=None
        )
    except Exception:
        pass

    return True, "Итоги опубликованы."


# =========================
# БОТ
# =========================

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()


@dp.message(Command("start"))
async def start(message: Message):
    text = (
        "Привет! 👋\n\n"
        "Я помогу создать конкурс в Telegram-канале.\n\n"
        "Команда создания конкурса:\n"
        "/newgiveaway"
    )

    if is_admin(message.from_user.id):
        text += "\n\nАдмин-панель:\n/admin"

    await message.answer(text)


@dp.message(Command("newgiveaway"))
async def new_giveaway(message: Message, state: FSMContext):
    await state.set_state(CreateGiveaway.channel)
    await message.answer(
        "Укажи канал, куда опубликовать конкурс.\n"
        "Пример: @my_channel\n\n"
        "Бот должен быть администратором канала с правом публикации сообщений."
    )


@dp.message(CreateGiveaway.channel)
async def get_channel(message: Message, state: FSMContext):
    channel = message.text.strip()

    await state.update_data(channel=channel)
    await state.set_state(CreateGiveaway.text)

    await message.answer(
        "Теперь отправь текст конкурса.\n\n"
        "Например:\n"
        "🎁 Разыгрываем подарок!\n"
        "Условия: подписаться и нажать кнопку участия."
    )


@dp.message(CreateGiveaway.text)
async def create_giveaway(message: Message, state: FSMContext):
    data = await state.get_data()
    channel = data["channel"]
    giveaway_text = message.text.strip()

    try:
        sent = await bot.send_message(
            chat_id=channel,
            text=(
                f"{esc(giveaway_text)}\n\n"
                "Участие проходит через кнопку ниже 👇"
            ),
            reply_markup=join_keyboard(0)
        )
    except Exception as e:
        await message.answer(
            "Не получилось опубликовать конкурс.\n\n"
            "Проверь:\n"
            "• бот добавлен администратором канала;\n"
            "• у бота есть право публиковать сообщения;\n"
            "• канал указан правильно."
        )
        print("Ошибка публикации:", e)
        await state.clear()
        return

    cursor = db.execute(
        """
        INSERT INTO giveaways
        (chat_id, message_id, text, status, created_at)
        VALUES (?, ?, ?, 'active', ?)
        """,
        (
            str(sent.chat.id),
            sent.message_id,
            giveaway_text,
            datetime.utcnow().isoformat()
        )
    )
    giveaway_id = cursor.lastrowid
    db.commit()

    await bot.edit_message_reply_markup(
        chat_id=sent.chat.id,
        message_id=sent.message_id,
        reply_markup=join_keyboard(giveaway_id, 0)
    )

    await message.answer(
        f"✅ Конкурс опубликован!\n"
        f"Номер конкурса: <b>#{giveaway_id}</b>\n\n"
        f"Когда захочешь завершить его, используй /admin."
    )

    await state.clear()


@dp.callback_query(F.data.startswith("join:"))
async def join_giveaway(callback: CallbackQuery):
    giveaway_id = int(callback.data.split(":")[1])

    giveaway = db.execute(
        "SELECT * FROM giveaways WHERE id = ? AND status = 'active'",
        (giveaway_id,)
    ).fetchone()

    if not giveaway:
        await callback.answer("Этот конкурс уже завершён.", show_alert=True)
        return

    user = callback.from_user

    try:
        db.execute(
            """
            INSERT INTO participants
            (giveaway_id, user_id, username, full_name, joined_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                giveaway_id,
                user.id,
                user.username,
                user.full_name,
                datetime.utcnow().isoformat()
            )
        )
        db.commit()
        answer = "Ты участвуешь! Удачи 🍀"
    except sqlite3.IntegrityError:
        answer = "Ты уже участвуешь в этом конкурсе."

    count = db.execute(
        "SELECT COUNT(*) FROM participants WHERE giveaway_id = ?",
        (giveaway_id,)
    ).fetchone()[0]

    try:
        await bot.edit_message_reply_markup(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            reply_markup=join_keyboard(giveaway_id, count)
        )
    except Exception:
        pass

    await callback.answer(answer)


# =========================
# АДМИН-ПАНЕЛЬ
# =========================

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    giveaways = db.execute(
        "SELECT * FROM giveaways WHERE status = 'active' ORDER BY id DESC"
    ).fetchall()

    if not giveaways:
        await message.answer("Активных конкурсов нет.")
        return

    await message.answer("🛠 <b>Админ-панель</b>\nВыбери конкурс:")

    for giveaway in giveaways:
        count = db.execute(
            "SELECT COUNT(*) FROM participants WHERE giveaway_id = ?",
            (giveaway["id"],)
        ).fetchone()[0]

        await message.answer(
            f"Конкурс <b>#{giveaway['id']}</b>\n"
            f"Участников: <b>{count}</b>",
            reply_markup=admin_giveaway_keyboard(giveaway["id"])
        )


@dp.callback_query(F.data.startswith("finish:"))
async def finish_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    giveaway_id = int(callback.data.split(":")[1])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎲 Выбрать случайно",
                    callback_data=f"random:{giveaway_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Выбрать из списка",
                    callback_data=f"list:{giveaway_id}"
                )
            ]
        ]
    )

    await callback.message.answer(
        f"Как выбрать победителя конкурса #{giveaway_id}?",
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("random:"))
async def random_winner(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    giveaway_id = int(callback.data.split(":")[1])
    participants = get_participants(giveaway_id)

    if not participants:
        await callback.answer("Участников пока нет.", show_alert=True)
        return

    winner = secrets.choice(participants)

    ok, text = await publish_winner(
        bot,
        giveaway_id,
        winner["user_id"]
    )

    await callback.message.answer(
        f"🎲 {text}\n"
        f"Победитель: {esc(winner['full_name'])}"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("list:"))
async def participant_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    giveaway_id = int(callback.data.split(":")[1])
    participants = get_participants(giveaway_id)

    if not participants:
        await callback.answer("Участников пока нет.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()

    for participant in participants[:40]:
        name = participant["full_name"] or str(participant["user_id"])
        builder.button(
            text=f"👤 {name[:35]}",
            callback_data=f"choose:{giveaway_id}:{participant['user_id']}"
        )

    builder.adjust(1)

    await callback.message.answer(
        f"Выбери победителя конкурса #{giveaway_id}:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("choose:"))
async def choose_winner(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    _, giveaway_id, user_id = callback.data.split(":")

    ok, text = await publish_winner(
        bot,
        int(giveaway_id),
        int(user_id)
    )

    await callback.message.answer(f"✅ {text}")
    await callback.answer()


# =========================
# ЗАПУСК
# =========================

async def main():
    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())