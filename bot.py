import os
import asyncio
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6887251996

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# ================= DATABASE =================
db = sqlite3.connect("database.db")
db.row_factory = sqlite3.Row
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    status TEXT,
    subscription_end TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    year INTEGER,
    genre TEXT,
    rating TEXT,
    description TEXT,
    code TEXT UNIQUE,
    file_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    receipt_file_id TEXT,
    status TEXT,
    created_at TEXT
)
""")
db.commit()

# ================= HELPERS =================
def get_user(tg_id):
    return cursor.execute(
        "SELECT * FROM users WHERE telegram_id=?",
        (tg_id,)
    ).fetchone()

def is_admin(user_id):
    return user_id == ADMIN_ID

def subscription_active(user):
    if not user or user["status"] != "active":
        return False
    return datetime.fromisoformat(user["subscription_end"]) > datetime.now()

# ================= /start =================
@dp.message(Command("start"))
async def start(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
            (
                message.from_user.id,
                message.from_user.first_name,
                message.from_user.username,
                "expired",
                None
            )
        )
        db.commit()

    text = (
        "🎬 <b>Kino botga xush kelibsiz!</b>\n\n"
        "📌 Bot oylik obuna asosida ishlaydi.\n"
        "📅 Obuna muddati: <b>30 kun</b>\n\n"
        "🔍 Kino nomi yoki kodi yuboring."
    )
    await message.answer(text)

# ================= PAYMENT =================
@dp.message(F.photo | F.document)
async def receive_receipt(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        return

    pending = cursor.execute(
        "SELECT * FROM payments WHERE telegram_id=? AND status='pending'",
        (message.from_user.id,)
    ).fetchone()
    if pending:
        await message.answer("⏳ Oldingi to‘lov hali tekshirilmoqda.")
        return

    file_id = (
        message.photo[-1].file_id
        if message.photo else
        message.document.file_id
    )

    cursor.execute(
        "INSERT INTO payments (telegram_id, receipt_file_id, status, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, file_id, "pending", datetime.now().isoformat())
    )
    db.commit()

    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Tasdiqlayman",
        callback_data=f"approve:{message.from_user.id}"
    )

    await bot.send_document(
        ADMIN_ID,
        file_id,
        caption=(
            f"💳 <b>Yangi to‘lov</b>\n\n"
            f"👤 {message.from_user.first_name}\n"
            f"🔗 @{message.from_user.username}\n"
            f"🆔 {message.from_user.id}\n"
            f"📅 30 kun"
        ),
        reply_markup=kb.as_markup()
    )

    await message.answer("✅ Chek qabul qilindi. Tekshiruv kutilmoqda.")

# ================= ADMIN APPROVE =================
@dp.callback_query(F.data.startswith("approve"))
async def approve_payment(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    tg_id = int(call.data.split(":")[1])

    payment = cursor.execute(
        "SELECT * FROM payments WHERE telegram_id=? AND status='pending'",
        (tg_id,)
    ).fetchone()
    if not payment:
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    user = get_user(tg_id)
    now = datetime.now()

    if user["subscription_end"]:
        end = datetime.fromisoformat(user["subscription_end"])
        if end > now:
            new_end = end + timedelta(days=30)
        else:
            new_end = now + timedelta(days=30)
    else:
        new_end = now + timedelta(days=30)

    cursor.execute(
        "UPDATE users SET status='active', subscription_end=? WHERE telegram_id=?",
        (new_end.isoformat(), tg_id)
    )
    cursor.execute(
        "UPDATE payments SET status='approved' WHERE id=?",
        (payment["id"],)
    )
    db.commit()

    await bot.send_message(
        tg_id,
        f"🎉 <b>Obuna faollashtirildi!</b>\n\n📅 Tugash sanasi: {new_end.date()}"
    )

    await call.message.edit_caption(call.message.caption + "\n\n✅ Tasdiqlandi")
    await call.answer("Tasdiqlandi")

# ================= MOVIE SEARCH =================
@dp.message()
async def search_movie(message: Message):
    user = get_user(message.from_user.id)
    if not subscription_active(user):
        await message.answer("❌ Obuna faol emas.")
        return

    query = message.text.lower()

    movies = cursor.execute(
        "SELECT * FROM movies WHERE title LIKE ? OR code=?",
        (f"%{query}%", query)
    ).fetchall()

    if not movies:
        await message.answer("❌ Kino topilmadi.")
        return

    movie = movies[0]
    await bot.send_video(
        message.chat.id,
        movie["file_id"],
        caption=(
            f"🎬 <b>{movie['title']}</b>\n"
            f"📅 {movie['year']}\n"
            f"🧩 {movie['genre']}\n"
            f"⭐ {movie['rating']}\n\n"
            f"{movie['description']}"
        )
    )

# ================= SUBSCRIPTION CHECK =================
async def check_subscriptions():
    users = cursor.execute(
        "SELECT * FROM users WHERE status='active'"
    ).fetchall()

    for user in users:
        end = datetime.fromisoformat(user["subscription_end"])
        if end < datetime.now():
            cursor.execute(
                "UPDATE users SET status='expired' WHERE telegram_id=?",
                (user["telegram_id"],)
            )
            await bot.send_message(
                user["telegram_id"],
                "⛔ Obuna muddati tugadi."
            )
    db.commit()

async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, "interval", days=1)
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())