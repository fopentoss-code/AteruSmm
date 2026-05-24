import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN
from database import init_db
from handlers import router
from scheduler import start_scheduler, shutdown_scheduler

# Настройка логирования
logging.basicConfig(level=logging.INFO)

async def main():
    # Инициализация базы данных
    init_db()
    logging.info("База данных инициализирована")

    # Создание бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Запуск планировщика (фоновые задачи)
    await start_scheduler(bot)

    # Запуск поллинга
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown_scheduler()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())