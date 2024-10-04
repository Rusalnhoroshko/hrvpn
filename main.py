# main.py
import asyncio
import logging

from aiohttp import web
from dotenv import load_dotenv
load_dotenv()

from telegram_bot import dp, bot
from db import init_db
from tasks import check_subscriptions, sync_keys
from telegram_bot import yoomoney_notification


logging.getLogger('aiogram.event').setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,  # Уровень логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Формат сообщения
    handlers=[
        logging.FileHandler('bot.log'),  # Логи записываются в файл bot.log
        logging.StreamHandler()  # Логи продолжают выводиться в консоль
    ]
)

logging.info("Бот запущен")

app = web.Application()
app.router.add_post('/yoomoney_notification', yoomoney_notification)


async def main():
    await init_db()
    asyncio.create_task(check_subscriptions())
    asyncio.create_task(sync_keys())

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
