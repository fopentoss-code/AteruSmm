from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot

import database as db
from config import CHECK_SUBSCRIBE_INTERVAL_SECONDS, EXCHANGE_CHANNEL_ID, EXCHANGE_GROUP_ID

scheduler = AsyncIOScheduler()
_scheduled_jobs = {}

# ---------------------- Завершение раунда ----------------------
async def schedule_round_end(bot: Bot, end_time: datetime):
    job_id = f"round_end_{int(end_time.timestamp())}"
    if job_id in _scheduled_jobs:
        return
    trigger = DateTrigger(run_date=end_time)
    job = scheduler.add_job(
        _round_end_job,
        trigger=trigger,
        args=[bot],
        id=job_id,
        replace_existing=True
    )
    _scheduled_jobs[job_id] = job

async def _round_end_job(bot: Bot):
    current_round = await db.get_current_round()
    if not current_round or current_round['status'] != 'active':
        return
    from handlers import finish_round_and_select_winner
    await finish_round_and_select_winner(bot, current_round)

# ---------------------- Таймаут победителя ----------------------
async def schedule_winner_timeout(bot: Bot, user_id: int, history_id: int):
    from config import CONFIRMATION_TIMEOUT_MINUTES
    from handlers import winner_timeout
    run_time = datetime.now() + timedelta(minutes=CONFIRMATION_TIMEOUT_MINUTES)
    job_id = f"winner_timeout_{user_id}_{history_id}"
    if job_id in _scheduled_jobs:
        await cancel_winner_timeout(user_id)
    trigger = DateTrigger(run_date=run_time)
    job = scheduler.add_job(
        winner_timeout,
        trigger=trigger,
        args=[bot, user_id, history_id],
        id=job_id,
        replace_existing=True
    )
    _scheduled_jobs[job_id] = job

async def cancel_winner_timeout(user_id: int):
    to_remove = []
    for job_id, job in _scheduled_jobs.items():
        if job_id.startswith(f"winner_timeout_{user_id}_"):
            job.remove()
            to_remove.append(job_id)
    for job_id in to_remove:
        del _scheduled_jobs[job_id]

# Алиасы
schedule_winner_confirmation = schedule_winner_timeout
cancel_scheduled_tasks = cancel_winner_timeout

# ---------------------- Периодическая проверка подписок на канал/группу биржи ----------------------
async def schedule_periodic_subscription_check(bot: Bot):
    job_id = "subscription_check"
    if job_id in _scheduled_jobs:
        return
    trigger = IntervalTrigger(seconds=CHECK_SUBSCRIBE_INTERVAL_SECONDS)
    job = scheduler.add_job(
        _check_all_subscriptions,
        trigger=trigger,
        args=[bot],
        id=job_id,
        replace_existing=True
    )
    _scheduled_jobs[job_id] = job

async def _check_all_subscriptions(bot: Bot):
    users = await db.get_all_users()
    for user in users:
        user_id = user['user_id']
        try:
            channel_member = await bot.get_chat_member(EXCHANGE_CHANNEL_ID, user_id)
            group_member = await bot.get_chat_member(EXCHANGE_GROUP_ID, user_id)
            channel_ok = channel_member.status in ['member', 'administrator', 'creator']
            group_ok = group_member.status in ['member', 'administrator', 'creator']
            await db.set_subscription_status(user_id, 'channel', channel_ok)
            await db.set_subscription_status(user_id, 'group', group_ok)
        except Exception:
            await db.set_subscription_status(user_id, 'channel', False)
            await db.set_subscription_status(user_id, 'group', False)

# ---------------------- Запуск и остановка планировщика ----------------------
async def start_scheduler(bot: Bot):
    scheduler.start()
    await schedule_periodic_subscription_check(bot)

    # Перепланирование активного раунда, если есть
    current_round = await db.get_current_round()
    if current_round and current_round['status'] == 'active':
        end_time = datetime.fromisoformat(current_round['end_time'])
        if end_time > datetime.now():
            await schedule_round_end(bot, end_time)

    # Обработка ожидающего победителя
    pending = await db.get_pending_winner()
    if pending:
        expires_at = datetime.fromisoformat(pending['expires_at'])
        if expires_at <= datetime.now():
            from handlers import winner_timeout
            await winner_timeout(bot, pending['user_id'], pending['round_history_id'])
        else:
            await schedule_winner_timeout(bot, pending['user_id'], pending['round_history_id'])

async def shutdown_scheduler():
    scheduler.shutdown()
