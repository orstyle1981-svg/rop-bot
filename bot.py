import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiohttp import web
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import filters
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # ID группы (с минусом)
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL DSN
PORT = int(os.getenv("PORT", 10000))  # Порт для Render (по умолчанию 10000)

# Цена в копейках (400 рублей = 40000 копеек)
PRICE_AMOUNT = 40000
SUBSCRIPTION_DAYS = 30  # срок подписки в днях

# ===== ИНИЦИАЛИЗАЦИЯ =====
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

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
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    """Приветственное сообщение с инлайн-кнопками."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить подписку 400 ₽", callback_data="buy")],
            [
                InlineKeyboardButton(text="🔐 Политика обработки данных", callback_data="policy"),
                InlineKeyboardButton(text="🔄 Перезапуск", callback_data="restart")
            ],
            [InlineKeyboardButton(text="🆘 Поддержка @GETOURS_support", url="https://t.me/GETOURS_support")]
        ]
    )
    await message.answer(
        "🔐 **Добро пожаловать!**\n\n"
        "Это бот доступа в закрытый чат «РОП: рабочие вопросы».\n\n"
        "В чате разбираем реальные ситуации из практики отчётности РОП.\n"
        "На вопросы отвечают ведущие эксперты РФ в сфере РОП:\n"
        "Н. Беляева эксперт-эколог, руководитель правового экспертного бюро Дельфи.\n\n"
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

@dp.message_handler(commands=['policy'])
async def cmd_policy(message: types.Message):
    """Отправляет текст политики обработки данных."""
    policy_text = (
        "🔐 **Политика обработки данных**\n\n"
        "Чат-бот @QA_Delfi_bot используется для предоставления доступа к закрытому каналу «РОП: рабочие вопросы».\n\n"
        "**Оператор:**\n"
        "Беляева Наталья Сергеевна\n"
        "ИНН 470604654570\n\n"
        "**Какие данные используются**\n"
        "Чат-бот не запрашивает и не собирает персональные данные пользователей.\n\n"
        "При оформлении доступа к каналу оплата может осуществляться через сторонние платежные сервисы. Такие сервисы могут обрабатывать данные пользователя (например, адрес электронной почты) для оформления платежа и направления кассового чека в соответствии со своей политикой обработки персональных данных.\n\n"
        "**Цель использования данных**\n"
        "Данные используются исключительно для:\n"
        "• оформления оплаты доступа\n"
        "• направления кассовых чеков\n"
        "• предоставления доступа к закрытому каналу «РОП: рабочие вопросы»\n\n"
        "**Согласие пользователя**\n"
        "Продолжая использование чат-бота, пользователь подтверждает согласие с настоящими условиями."
    )
    await message.answer(policy_text, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data == 'buy')
async def process_buy_callback(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки «Купить» — отправляет счёт."""
    await bot.answer_callback_query(callback_query.id)
    await send_invoice(callback_query.from_user.id)

@dp.callback_query_handler(lambda c: c.data == 'policy')
async def process_policy_callback(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки «Политика»."""
    await bot.answer_callback_query(callback_query.id)
    await cmd_policy(callback_query.message)

@dp.callback_query_handler(lambda c: c.data == 'restart')
async def process_restart_callback(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки «Перезапуск» — просто запускает /start."""
    # Сначала выполняем основное действие, потом отвечаем на callback
    await cmd_start(callback_query.message)
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(commands=['buy'])
async def cmd_buy(message: types.Message):
    """Команда /buy (на случай, если пользователь введёт вручную)."""
    await send_invoice(message.from_user.id)

async def send_invoice(chat_id: int):
    """Отправляет инвойс с кнопкой оплаты и данными для чека."""
    prices = [LabeledPrice(label="Подписка на 1 месяц", amount=PRICE_AMOUNT)]

    # Данные для чека
    receipt_data = {
        "receipt": {
            "items": [
                {
                    "description": "Подписка на 1 месяц",
                    "quantity": 1.0,
                    "amount": {
                        "value": f"{PRICE_AMOUNT/100:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_prepayment",
                    "payment_subject": "commodity"
                }
            ],
            "tax_system_code": 3
        }
    }

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
        need_email=True,
        provider_data=receipt_data,
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data == 'cancel_payment')
async def process_cancel_callback(callback_query: types.CallbackQuery):
    """Отмена оплаты."""
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "❌ Оплата отменена.")

@dp.pre_checkout_query_handler()
async def pre_checkout_handler(query: PreCheckoutQuery):
    """Обязательный обработчик предоплаты."""
    # Правильный способ для aiogram 2.x
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
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
@dp.chat_member_handler()
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

# ===== ПРОСТОЙ HTTP-СЕРВЕР ДЛЯ RENDER =====
async def handle_health(request):
    """Обработчик для проверки здоровья от Render."""
    return web.Response(text="OK")

async def run_web_server():
    """Запускает aiohttp сервер на указанном порту."""
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    logging.info(f"Запуск HTTP-сервера на порту {PORT} для проверок Render")
    await site.start()

# ===== ЗАПУСК =====
async def main():
    """Главная функция запуска бота и HTTP-сервера."""
    try:
        # Принудительно сбрасываем вебхук, чтобы избежать конфликтов
        await bot.delete_webhook(drop_pending_updates=True)

        logging.info("Инициализация базы данных...")
        await init_db()
        logging.info("Запуск фоновой задачи...")
        asyncio.create_task(subscription_checker())
        # Запускаем HTTP-сервер параллельно с ботом
        asyncio.create_task(run_web_server())
        logging.info("Запуск polling...")
        await dp.start_polling()
    except Exception as e:
        logging.exception("Критическая ошибка в main")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
