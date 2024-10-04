# telegram_bot.py
import logging
import os
import decimal
import hmac
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlencode
from aiohttp import web

import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
YOOMONEY_SECRET = os.getenv('YOOMONEY_SECRET')
YOOMONEY_WALLET = os.getenv('YOOMONEY_WALLET')
NOTIFICATION_URL = os.getenv('NOTIFICATION_URL')
DB_FILE = 'subscriptions.db'
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

from db import (
    add_user,
    get_subscriptions,
    save_subscription,
    extend_subscription,
    save_purchase_history
)
from vpn_manager import create_vpn_key_with_name, manager


# Обработчики команд и сообщений
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    # Добавляем пользователя в таблицу users
    await add_user(user_id)
    # Проверяем, использовал ли пользователь тестовую подписку
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute('SELECT COUNT(*) FROM test_usage WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        has_used_test = row[0] > 0

    # Формируем клавиатуру
    keyboard_buttons = [
        [InlineKeyboardButton(text="Мои ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="Оплатить подписку",
                              callback_data="buy_new_key")],
        [InlineKeyboardButton(text="Инструкция", callback_data="instruction")],
    ]

    if not has_used_test:
        keyboard_buttons.insert(1, [InlineKeyboardButton(
            text="Тест VPN на час", callback_data="test_vpn")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("Выберите действие:", reply_markup=keyboard)


@dp.callback_query(F.data == "instruction")
async def handle_instruction(callback_query: types.CallbackQuery):
    instruction_text = (
        "Для того чтобы начать пользоваться VPN, установите приложение Outline VPN:\n\n"
        "👉 Для iOS: [Установить](https://apps.apple.com/us/app/outline-app/id1356177741)\n"
        "👉 Для Android: [Установить](https://play.google.com/store/apps/details?id=org.outline.android.client&pli=1)\n"
        "👉 Для MacOS: [Установить](https://apps.apple.com/us/app/outline-app/id1356177741)\n"
        "👉 Для Windows: [Установить](https://s3.amazonaws.com/outline-releases/client/windows/stable/Outline-Client.exe)\n\n"
        "После оплаты подписки вы получите ключ.\n"
        "Его нужно скопировать, перейти в приложение, нажать 'Добавить сервер' и вставить в поле ввода.\n\n"
        "По всем вопросам писать @hrv90\n"
    )
    await callback_query.message.answer(instruction_text, parse_mode="Markdown", disable_web_page_preview=True)


@dp.callback_query(F.data == "my_keys")
async def handle_my_keys(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    subscriptions = await get_subscriptions(user_id)
    if subscriptions:
        await callback_query.message.answer("Ваши ключи:")
        for sub in subscriptions:
            # Отправляем ключ
            await callback_query.message.answer(sub['access_url'])
            # Вычисляем оставшееся время
            time_left = sub['expires_at'] - datetime.now(timezone.utc)
            total_seconds_left = time_left.total_seconds()
            if total_seconds_left > 86400:
                days_left = int(total_seconds_left // 86400)
                await callback_query.message.answer(f"До окончания подписки осталось {days_left} дней")
            elif 3600 < total_seconds_left <= 86400:
                hours_left = int(total_seconds_left // 3600)
                await callback_query.message.answer(f"До окончания подписки осталось менее {hours_left} часов")
            elif 0 < total_seconds_left <= 3600:
                minutes_left = int(total_seconds_left // 60)
                await callback_query.message.answer(f"До окончания подписки осталось менее {minutes_left} минут")
            else:
                await callback_query.message.answer("Срок действия подписки истек")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="Продлить подписку", callback_data="renew_subscription")],
                [InlineKeyboardButton(
                    text="Купить еще ключ", callback_data="buy_new_key")]
            ]
        )
        await callback_query.message.answer("Чтобы продлить подписку или купить новый ключ, нажмите кнопку ниже.",
                                            reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="Оформить подписку", callback_data="buy_new_key")],
            ]
        )
        await callback_query.message.answer("У вас нет активных подписок.", reply_markup=keyboard)


@dp.callback_query(F.data == "buy_new_key")
async def handle_buy_new_key(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="30 дней - 200 рублей", callback_data="new_subscribe_30")],
            [InlineKeyboardButton(
                text="90 дней - 500 рублей", callback_data="new_subscribe_90")],
            [InlineKeyboardButton(
                text="180 дней - 1000 рублей", callback_data="new_subscribe_180")],
        ]
    )
    await callback_query.message.answer("Выберите период подписки для нового ключа:", reply_markup=keyboard)


@dp.callback_query(F.data == "renew_subscription")
async def handle_renew_subscription(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    subscriptions = await get_subscriptions(user_id)
    if not subscriptions:
        await callback_query.message.answer("У вас нет активных подписок для продления.")
    elif len(subscriptions) == 1:
        # Если только одна подписка, сразу предлагаем выбрать период продления
        sub_id = subscriptions[0]['id']
        await choose_renewal_period(callback_query.message, sub_id)
    else:
        # Если несколько подписок, предлагаем выбрать какую продлить
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for sub in subscriptions:
            sub_id = sub['id']
            # последние 10 символов ключа для идентификации
            key_info = sub['access_url'][-10:]
            expires_at = sub['expires_at'].strftime('%d.%m.%Y')
            button_text = f"Ключ {key_info}, до {expires_at}"
            keyboard.inline_keyboard.append([InlineKeyboardButton(
                text=button_text, callback_data=f"choose_sub_{sub_id}")])
        await callback_query.message.answer("Выберите подписку для продления:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("choose_sub_"))
async def handle_choose_subscription(callback_query: types.CallbackQuery):
    sub_id = int(callback_query.data.split("_")[2])
    await choose_renewal_period(callback_query.message, sub_id)


@dp.callback_query(F.data.startswith("new_subscribe_"))
async def process_new_subscription(callback_query: types.CallbackQuery):
    period = int(callback_query.data.split("_")[2])
    amount_mapping = {30: 200, 90: 500, 180: 1000}
    amount = amount_mapping.get(period)

    if not amount:
        await callback_query.message.answer("Некорректный выбор периода подписки.")
        return

    user_id = callback_query.from_user.id

    # Генерируем уникальный label для платежа
    payment_label = f"{user_id}_{int(datetime.now().timestamp())}"

    # Удаляем сохранение purchase_history здесь
    # await save_purchase_history(user_id, amount, period, 'new_subscription', payment_label)

    # Генерируем ссылку на оплату
    payment_link = generate_payment_link(amount, payment_label, f"Подписка на {period} дней")

    # Создаем инлайн-кнопку с ссылкой на оплату
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оплатить {amount} рублей", url=payment_link)]
    ])

    # Отправляем сообщение с кнопкой оплаты
    await callback_query.message.answer(
        f"Для оплаты подписки на {period} дней нажмите кнопку ниже:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data.startswith("renew_"))
async def process_renew_subscription(callback_query: types.CallbackQuery):
    parts = callback_query.data.split("_")
    if len(parts) != 3:
        await callback_query.message.answer("Некорректные данные для продления подписки.")
        return

    sub_id = int(parts[1])
    period = int(parts[2])
    amount_mapping = {30: 200, 90: 500, 180: 1000}  # Цены в рублях
    amount = amount_mapping.get(period)

    if not amount:
        await callback_query.message.answer("Некорректный выбор периода подписки.")
        return

    user_id = callback_query.from_user.id

    payment_label = f"renew_{user_id}_{sub_id}_{int(datetime.now().timestamp())}"

    # Удаляем сохранение purchase_history здесь
    # await save_purchase_history(user_id, amount, period, 'renew_subscription', payment_label)

    # Генерируем ссылку на оплату
    payment_link = generate_payment_link(amount, payment_label, f"Продление подписки на {period} дней")

    # Создаем инлайн-кнопку с ссылкой на оплату
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оплатить {amount} рублей", url=payment_link)]
    ])

    # Отправляем сообщение с кнопкой оплаты
    await callback_query.message.answer(
        f"Для продления подписки на {period} дней нажмите кнопку ниже:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "pay_subscription")
async def handle_pay_subscription(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Купить еще ключ",
                                  callback_data="buy_new_key")],
            [InlineKeyboardButton(text="Продлить подписку",
                                  callback_data="renew_subscription")],
        ]
    )
    await callback_query.message.answer("Выберите действие:", reply_markup=keyboard)


@dp.callback_query(F.data == "test_vpn")
async def handle_test_vpn(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id

    # Проверяем, использовал ли пользователь тестовую подписку
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute('SELECT COUNT(*) FROM test_usage WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        has_used_test = row[0] > 0

    if has_used_test:
        await callback_query.message.answer("Вы уже использовали тестовый период.")
        return

    # Создаём VPN-ключ на 1 час
    vpn_key_data = await create_vpn_key_with_name(user_id)
    if vpn_key_data:
        try:
            duration_hours = 1
            duration_days = duration_hours / 24  # Конвертируем часы в дни

            # Сохраняем подписку с точным временем окончания
            await save_subscription(user_id, vpn_key_data, duration_days)

            # Добавляем запись в test_usage
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute('INSERT INTO test_usage (user_id, used_at) VALUES (?, ?)',
                                 (user_id, datetime.now(timezone.utc).isoformat()))
                await db.commit()

            # Отправляем ключ пользователю
            await callback_query.message.answer("Ваш тестовый ключ:")
            await callback_query.message.answer(vpn_key_data['accessUrl'])
            await callback_query.message.answer("Ключ будет действителен в течение 1 часа.")

        except Exception as e:
            # Обработка исключений
            logging.error(f"Ошибка при сохранении тестовой подписки: {e}")
            # Удаляем ключ с сервера в случае ошибки
            try:
                manager.delete_key(vpn_key_data['id'])
                logging.error(f"Ключ {vpn_key_data['id']} удален с сервера из-за ошибки при сохранении в базе данных")
            except Exception as delete_exception:
                logging.error(f"Ошибка при удалении ключа {vpn_key_data['id']}: {delete_exception}")
            await callback_query.message.answer("Произошла ошибка при создании тестовой подписки. Попробуйте позже.")
    else:
        await callback_query.message.answer("Не удалось создать тестовый ключ. Попробуйте позже.")


async def choose_renewal_period(message: types.Message, sub_id):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="30 дней - 200 рублей", callback_data=f"renew_{sub_id}_30")],
            [InlineKeyboardButton(
                text="90 дней - 500 рублей", callback_data=f"renew_{sub_id}_90")],
            [InlineKeyboardButton(
                text="180 дней - 1000 рублей", callback_data=f"renew_{sub_id}_180")],
        ]
    )
    await message.answer("Выберите период продления подписки:", reply_markup=keyboard)


def generate_payment_link(amount, label, description):
    params = {
        'receiver': YOOMONEY_WALLET,  # Ваш идентификатор кошелька ЮMoney
        'quickpay-form': 'shop',
        'targets': description,
        'paymentType': 'AC',  # Способ оплаты: банковская карта
        'sum': amount,
        'label': label,  # Уникальный идентификатор платежа
        # 'successURL': SUCCESS_URL,  # URL для перенаправления после оплаты
        'formcomment': description,
        'short-dest': description,
        'comment': description,
        'need-fio': 'false',
        'need-email': 'false',
        'need-phone': 'false',
        'need-address': 'false',
        'notificationURL': NOTIFICATION_URL  # URL для уведомлений от ЮMoney
    }
    payment_link = f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(params)}"
    return payment_link


async def yoomoney_notification(request):
    data = await request.post()
    logging.info(f"Получено уведомление от ЮMoney: {data}")

    # Получаем параметры из уведомления
    notification_type = data.get('notification_type', '')
    operation_id = data.get('operation_id', '')
    amount = data.get('amount', '')
    currency = data.get('currency', '')
    datetime_str = data.get('datetime', '')
    sender = data.get('sender', '')
    codepro = data.get('codepro', '')
    label = data.get('label', '')
    sha1_hash = data.get('sha1_hash', '')
    withdraw_amount_str = data.get('withdraw_amount', '').replace(',', '.').strip()

    # Строка для проверки подписи
    params_list = [
        notification_type,
        operation_id,
        amount,
        currency,
        datetime_str,
        sender,
        codepro,
        YOOMONEY_SECRET,
        label
    ]
    params = '&'.join(params_list)

    # Проверяем подпись
    hash_digest = hashlib.sha1(params.encode('utf-8')).hexdigest()
    if not hmac.compare_digest(hash_digest, sha1_hash):
        logging.error("Неверная подпись в уведомлении от ЮMoney")
        return web.Response(text='Invalid signature')

    # Проверяем, не была ли уже обработана эта транзакция
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute('''
            SELECT COUNT(*) FROM purchase_history WHERE operation_id = ?
        ''', (operation_id,))
        row = await cursor.fetchone()
        if row[0] > 0:
            logging.info(f"Уведомление с operation_id {operation_id} уже обработано.")
            return web.Response(text='OK')  # Возвращаем OK, чтобы ЮMoney не отправлял повторные уведомления

    # Подпись верна, обрабатываем платеж
    if not label:
        logging.error("Отсутствует label в уведомлении")
        return web.Response(text='Invalid label')

    # Преобразуем withdraw_amount_str в Decimal
    try:
        paid_amount = decimal.Decimal(withdraw_amount_str)
        paid_amount = paid_amount.quantize(decimal.Decimal('1.00'))  # Округляем до 2 знаков
    except decimal.InvalidOperation:
        logging.error(f"Некорректная сумма withdraw_amount: {withdraw_amount_str}")
        return web.Response(text='Invalid withdraw_amount')

    logging.info(f"paid_amount: {paid_amount}")

    # Обновляем amount_mapping с использованием Decimal
    amount_mapping = {
        # decimal.Decimal('5.00'): 5,
        decimal.Decimal('200.00'): 30,
        decimal.Decimal('500.00'): 90,
        decimal.Decimal('1000.00'): 180
    }

    matching_amount = None
    for amt in amount_mapping:
        difference = abs(paid_amount - amt)
        logging.info(f"Сравнение paid_amount: {paid_amount} и amt: {amt}, разница: {difference}")
        if difference <= decimal.Decimal('0.01'):
            matching_amount = amt
            break

    logging.info(f"matching_amount: {matching_amount}")

    if not matching_amount:
        logging.error(f"Не удалось найти соответствие для paid_amount: {paid_amount}")
        # Попытка извлечь user_id для отправки сообщения
        if label.startswith('renew_') or label.startswith('new_subscribe_'):
            parts = label.split('_')
            if len(parts) >= 2:
                user_id_str = parts[1]
                if user_id_str.isdigit():
                    user_id = int(user_id_str)
                    await bot.send_message(user_id, "Получена неверная сумма оплаты.")
        return web.Response(text='Invalid amount')

    expected_period = amount_mapping[matching_amount]

    if label.startswith('renew_'):
        # Обработка продления подписки
        parts = label.split('_')
        if len(parts) < 4:
            logging.error(f"Некорректный формат label для продления: {label}")
            return web.Response(text='Invalid label format for renew')

        _, user_id_str, sub_id_str, _ = parts
        if not user_id_str.isdigit() or not sub_id_str.isdigit():
            logging.error(f"Некорректные идентификаторы в label: {label}")
            return web.Response(text='Invalid user or sub ID in label')

        user_id = int(user_id_str)
        sub_id = int(sub_id_str)

        # Продлеваем подписку
        await extend_subscription(user_id, sub_id, expected_period)
        # Сохраняем историю покупки с operation_id
        await save_purchase_history(user_id, float(matching_amount), expected_period, 'renew_subscription', label,
                                    operation_id)
        await bot.send_message(user_id, f"Оплата получена! Ваша подписка продлена на {expected_period} дней.")
        return web.Response(text='OK')

    else:
        # Обработка новых подписок
        parts = label.split('_')
        if len(parts) < 2:
            logging.error(f"Некорректный формат label для новой подписки: {label}")
            return web.Response(text='Invalid label format for new subscription')

        user_id_str = parts[0]
        if not user_id_str.isdigit():
            logging.error(f"Некорректный user_id в label: {label}")
            return web.Response(text='Invalid user ID in label')

        user_id = int(user_id_str)

        # Создаем VPN-ключ и сохраняем подписку
        vpn_key_data = await create_vpn_key_with_name(user_id)
        if vpn_key_data:
            await save_subscription(user_id, vpn_key_data, expected_period)
            # Сохраняем историю покупки с operation_id
            await save_purchase_history(user_id, float(matching_amount), expected_period, 'new_subscription', label,
                                        operation_id)
            # Создаем клавиатуру с кнопкой "Инструкция"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Инструкция", callback_data="instruction")]
                ]
            )
            # Отправляем сообщение с ключом и клавиатурой
            await bot.send_message(user_id, f"Оплата получена! Ваш ключ:")
            await bot.send_message(user_id, f"{vpn_key_data['accessUrl']}", reply_markup=keyboard)
            return web.Response(text='OK')
        else:
            await bot.send_message(user_id, "Ошибка при создании VPN-ключа.")
            return web.Response(text='Error creating VPN key')
