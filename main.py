import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN
from database import init_db
from handlers import router
from scheduler import start_scheduler, shutdown_scheduler

logging.basicConfig(level=logging.INFO)

async def main():
    init_db()
    logging.info("База данных инициализирована")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await start_scheduler(bot)

    # allowed_updates обязателен чтобы Telegram присылал chat_join_request события
    # dp.resolve_used_update_types() автоматически определяет нужные типы из зарегистрированных хэндлеров
    allowed_updates = dp.resolve_used_update_types()
    logging.info(f"Бот запущен. Подписка на типы обновлений: {allowed_updates}")

    try:
        await dp.start_polling(bot, allowed_updates=allowed_updates)
    finally:
        await shutdown_scheduler()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
