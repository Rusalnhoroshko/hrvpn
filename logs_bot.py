import asyncio
import aiosqlite
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Инициализация бота
TELEGRAM_BOT_TOKEN_LOGGER = os.getenv("TELEGRAM_BOT_TOKEN_LOGGER")
CHAT_ID = os.getenv("CHAT_ID")  # Ваш user_id для уведомлений
LOG_FILE = "./bot.log"  # Путь к файлу лога

bot = Bot(token=TELEGRAM_BOT_TOKEN_LOGGER)
dp = Dispatcher(storage=MemoryStorage())

logging.getLogger('aiogram.event').setLevel(logging.WARNING)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,  # Уровень логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Формат сообщения
    handlers=[
        logging.FileHandler('bot.log'),  # Логи записываются в файл bot.log
        logging.StreamHandler()  # Логи продолжают выводиться в консоль
    ]
)
logging.info("Бот запущен")


# Проверка доступа к боту
AUTHORIZED_USER_ID = CHAT_ID

def is_authorized_user(user_id):
    return user_id == AUTHORIZED_USER_ID

# Обработчик команды /start
@dp.message(Command("start"))
async def start(message: types.Message):
    if is_authorized_user(message.from_user.id):
        await message.answer("Привет, вы авторизованы для использования этого бота!")
    else:
        await message.answer("Извините, у вас нет доступа к этому боту.")

# Мониторинг файла лога
async def monitor_logs():
    async def send_telegram_message(text):
        """Функция для отправки уведомления в Telegram."""
        try:
            await bot.send_message(CHAT_ID, text)
        except Exception as e:
            logging.error(f"Ошибка при отправке сообщения в Telegram: {e}")

    logging.info("Начат мониторинг файла лога.")
    with open(LOG_FILE, 'r') as f:
        # Переход к концу файла, чтобы не отправлять старые записи
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if line:
                if "ERROR" in line or "INFO" in line or "WARNING" in line or "WARN" in line:
                    await send_telegram_message(f"Новое сообщение в логе: {line.strip()}")
            await asyncio.sleep(1)

# Запуск бота и мониторинга логов
async def main():
    asyncio.create_task(monitor_logs())  # Запуск мониторинга логов
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
