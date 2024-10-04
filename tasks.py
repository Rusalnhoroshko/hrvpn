# tasks.py
import aiosqlite
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from db import get_all_subscriptions, delete_subscription, update_subscription_async
from vpn_manager import manager
from telegram_bot import bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DB_FILE = 'subscriptions.db'


async def check_subscriptions():
    while True:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('''
                SELECT id, user_id, key_id, expires_at, notified_5_days, notified_1_day, notified_expired 
                FROM subscriptions
            ''')
            subscriptions = await cursor.fetchall()
            for sub in subscriptions:
                sub_id, user_id, key_id, expires_at_str, notified_5_days, notified_1_day, notified_expired = sub
                expires_at = datetime.fromisoformat(expires_at_str).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                time_left = expires_at - now
                total_seconds_left = time_left.total_seconds()

                # Проверка на 5 дней
                if 345600 < total_seconds_left <= 432000 and not notified_5_days:
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(
                                text="Продлить подписку", callback_data="renew_subscription")]
                        ]
                    )
                    try:
                        await bot.send_message(user_id, "До окончания вашей подписки осталось 5 дней.",
                                               reply_markup=keyboard)
                        await db.execute('''
                            UPDATE subscriptions SET notified_5_days = 1 WHERE id = ?
                        ''', (sub_id,))
                        await db.commit()
                        logging.info(f"Уведомление о 5 днях до окончания подписки отправлено пользователю {user_id}")
                    except Exception as e:
                        logging.error(f"Ошибка при отправке уведомления о 5 днях пользователю {user_id}: {e}")

                # Проверка на 1 день
                elif 0 < total_seconds_left <= 86400 and not notified_1_day:
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(
                                text="Продлить подписку", callback_data="renew_subscription")]
                        ]
                    )
                    try:
                        await bot.send_message(user_id, "До окончания вашей подписки остался 1 день.",
                                               reply_markup=keyboard)
                        await db.execute('''
                            UPDATE subscriptions SET notified_1_day = 1 WHERE id = ?
                        ''', (sub_id,))
                        await db.commit()
                        logging.info(f"Уведомление о 1 дне до окончания подписки отправлено пользователю {user_id}")
                    except Exception as e:
                        logging.error(f"Ошибка при отправке уведомления о 1 дне пользователю {user_id}: {e}")

                # Проверка на истечение подписки
                elif total_seconds_left <= 0 and not notified_expired:
                    try:
                        manager.delete_key(key_id)
                        logging.info(f"Ключ {key_id} удален с сервера")
                        await delete_subscription(sub_id, user_id)
                        keyboard = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(
                                    text="Оформить подписку", callback_data="buy_new_key")]
                            ]
                        )
                        await bot.send_message(user_id,
                                               "Ваша подписка истекла. Ключ был удален. Оформите подписку, чтобы получить новый ключ.",
                                               reply_markup=keyboard)
                        await db.execute('''
                            UPDATE subscriptions SET notified_expired = 1 WHERE id = ?
                        ''', (sub_id,))
                        await db.commit()
                        logging.info(f"Уведомление об истечении подписки отправлено пользователю {user_id}")
                    except Exception as e:
                        logging.error(
                            f"Ошибка при удалении ключа {key_id} или отправке уведомления пользователю {user_id}: {e}")
                        await bot.send_message(user_id,
                                               f"Произошла ошибка при удалении вашего ключа {key_id}. Пожалуйста, свяжитесь с поддержкой.")

        await asyncio.sleep(3600)  # Проверяем каждый час


async def sync_keys():
    while True:
        # Получаем все ключи с сервера
        server_keys = manager.get_keys()
        server_key_ids = set(key.key_id for key in server_keys)

        # Получаем все ключи из базы данных
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('SELECT key_id FROM subscriptions')
            rows = await cursor.fetchall()
            db_key_ids = set(row[0] for row in rows)

        # Ключи, которые есть на сервере, но отсутствуют в базе данных
        keys_only_on_server = server_key_ids - db_key_ids

        # Ключи, которые есть в базе данных, но отсутствуют на сервере
        keys_only_in_db = db_key_ids - server_key_ids

        # Удаляем ключи с сервера, которые не должны там быть
        for key_id in keys_only_on_server:
            try:
                manager.delete_key(key_id)
                logging.info(f"Ненужный ключ {key_id} удален с сервера")
            except Exception as e:
                logging.error(f"Ошибка при удалении ключа {key_id}: {e}")

        # Удаляем записи из базы данных для ключей, которых нет на сервере
        async with aiosqlite.connect(DB_FILE) as db:
            for key_id in keys_only_in_db:
                await db.execute('DELETE FROM subscriptions WHERE key_id = ?', (key_id,))
                logging.info(f"Запись для ключа {key_id} удалена из базы данных")
            await db.commit()

        await asyncio.sleep(600)  # Синхронизируем раз в 10 минут

