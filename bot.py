import asyncio
import logging
import os
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # ID группы (с минусом)
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL DSN

# Цена в копейках (400 рублей = 40000 копеек)
PRICE_AMOUNT = 40000
SUBSCRIPTION_DAYS = 30  # срок подписки в днях

# ===== ИНИЦИАЛИЗАЦИЯ =====
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== БАЗА ДАННЫХ (POSTGRESQL) =====
async def init_db():
    """Создаёт таблицу подписок, если её нет."""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id BIGINT PRIMARY KEY,
            expires_at TIMESTAMP NOT NULL
        )
    ''')
    await conn.close()

async def save_subscription(user_id: int, days: int):
    """Сохраняет или обновляет подписку пользователя."""
    expires_at = datetime.now() + timedelta(days=days)
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('''
        INSERT INTO subscriptions (user_id, expires_at) VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET expires_at = $2
    ''', user_id, expires_at)
    await conn.close()

async def get_subscription(user_id: int):
    """Возвращает expires_at для пользователя или None."""
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow('SELECT expires_at FROM subscriptions WHERE user_id = $1', user_id)
    await conn.close()
    return row['expires_at'] if row else None

async def get_expired_users() -> list:
    """Возвращает список user_id с истекшей подпиской."""
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch('SELECT user_id FROM subscriptions WHERE expires_at < NOW()')
    await conn.close()
    return [row['user_id'] for row in rows]

async def delete_subscription(user_id: int):
    """Удаляет запись о подписке."""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('DELETE FROM subscriptions WHERE user_id = $1', user_id)
    await conn.close()

# ===== ОБРАБОТЧИКИ КОМАНД =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Приветственное сообщение с инлайн-кнопкой «Купить»."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить подписку 400 ₽", callback_data="buy")]
        ]
    )
    await message.answer(
        "🔐 **Добро пожаловать!**\n\n"
        "Это бот доступа в закрытый чат «РОП: рабочие вопросы».\n\n"
        "В чате разбираем реальные ситуации из практики отчётности РОП.\n"
        "На вопросы отвечают ведущие эксперты РФ в сфере РОП:\n"
        "Н. Беляева, В. Минеева, Г. Евстегнеева и другие специалисты.\n\n"
        "**Здесь можно:**\n"
        "• задать вопрос по отчётности РОП\n"
        "• обсудить проверки РПН\n"
        "• проверить свою логику перед сдачей отчёта\n"
        "• разобрать рабочую ситуацию с коллегами\n\n"
        "Также можно задать вопросы по курсу «РОП: с нуля до Pro для новичков».\n\n"
        "**Доступ предоставляется по подписке 400 ₽ / 30 дней.**\n"
        "После оплаты бот автоматически отправит ссылку для входа в чат.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == 'buy')
async def process_buy_callback(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки «Купить» — отправляет счёт."""
    await bot.answer_callback_query(callback_query.id)
    await send_invoice(callback_query.from_user.id)

@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    """Команда /buy (на случай, если пользователь введёт вручную)."""
    await send_invoice(message.from_user.id)

async def send_invoice(chat_id: int):
    """Отправляет инвойс с кнопкой оплаты."""
    prices = [LabeledPrice(label="Подписка на 1 месяц", amount=PRICE_AMOUNT)]

    # Инлайн-кнопка «Оплатить» (pay=True) и кнопка отмены
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 400 ₽", pay=True)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_payment")]
        ]
    )

    await bot.send_invoice(
        chat_id=chat_id,
        title="Доступ в закрытую группу",
        description="Месячная подписка на доступ к закрытому каналу по вопросам отчетности РОП",
        payload="month_sub",
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter="subscription",
        need_name=False,
        need_email=False,
        need_phone_number=False,
        need_shipping_address=False,
        is_flexible=False,
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == 'cancel_payment')
async def process_cancel_callback(callback_query: types.CallbackQuery):
    """Отмена оплаты."""
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "❌ Оплата отменена.")

@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    """Обязательный обработчик предоплаты."""
    await query.answer(ok=True)

@dp.message(lambda message: message.successful_payment is not None)
async def successful_payment_handler(message: types.Message):
    """Обработка успешной оплаты."""
    user_id = message.from_user.id
    await save_subscription(user_id, days=SUBSCRIPTION_DAYS)

    # Создаём одноразовую пригласительную ссылку в группу
    invite_link = await bot.create_chat_invite_link(
        chat_id=GROUP_ID,
        member_limit=1,
        name=f"user_{user_id}"
    )

    await message.answer(
        f"✅ Оплата прошла успешно!\n"
        f"Ваша ссылка для входа в группу (действует 7 дней, одна активация):\n"
        f"{invite_link.invite_link}\n\n"
        f"Подписка действует {SUBSCRIPTION_DAYS} дней. Не передавайте ссылку никому."
    )

# ===== МГНОВЕННЫЙ КИК НЕПЛАТЕЛЬЩИКОВ ПРИ ВХОДЕ =====
@dp.chat_member()
async def on_user_join(event: types.ChatMemberUpdated):
    """Проверяет подписку при входе в группу и кикает, если её нет."""
    # Проверяем, что событие — присоединение нового участника
    if (event.new_chat_member.status == "member" and
        event.old_chat_member.status == "left"):
        user_id = event.new_chat_member.user.id
        chat_id = event.chat.id

        expires_at = await get_subscription(user_id)

        if expires_at is None:
            # Нет записи о подписке
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id)  # сразу разбаним, чтобы мог написать боту
            await bot.send_message(user_id, "⛔ Доступ в группу только по платной подписке. Оформите через /buy")
        else:
            if expires_at < datetime.now():
                # Подписка истекла
                await bot.ban_chat_member(chat_id, user_id)
                await bot.unban_chat_member(chat_id, user_id)
                await bot.send_message(user_id, "⏳ Срок подписки истёк. Продлите через /buy")

# ===== ФОНОВАЯ ЗАДАЧА (ПРОВЕРКА ИСТЕКШИХ ПОДПИСОК РАЗ В СУТКИ) =====
async def subscription_checker():
    """Каждые 24 часа проверяет истекшие подписки и удаляет пользователей из группы."""
    while True:
        await asyncio.sleep(86400)  # 24 часа
        expired = await get_expired_users()
        for user_id in expired:
            try:
                await bot.ban_chat_member(
                    chat_id=GROUP_ID,
                    user_id=user_id,
                    until_date=int(datetime.now().timestamp()) + 31
                )
                await delete_subscription(user_id)
                await bot.send_message(user_id, "⏳ Ваша подписка истекла. Чтобы продлить, используйте /buy")
            except Exception as e:
                logging.error(f"Ошибка при удалении {user_id}: {e}")

# ===== ЗАПУСК =====
async def main():
    """Главная функция запуска бота."""
    try:
        logging.info("Инициализация базы данных...")
        await init_db()
        logging.info("Запуск фоновой задачи...")
        asyncio.create_task(subscription_checker())
        logging.info("Запуск polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.exception("Критическая ошибка в main")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())