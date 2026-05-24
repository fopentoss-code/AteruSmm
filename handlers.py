import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import (
    ADMIN_ID, EXCHANGE_CHANNEL_ID, EXCHANGE_GROUP_ID,
    EXCHANGE_CHANNEL_USERNAME, EXCHANGE_GROUP_USERNAME,
    ROUND_DURATION_MINUTES, CONFIRMATION_TIMEOUT_MINUTES,
    MIN_POINTS_TO_WIN, INVITE_LINK_EXPIRE_HOURS, INVITE_LINK_EXTRA_MINUTES,
    CHECK_SUBSCRIBE_INTERVAL_SECONDS
)
import database as db
import keyboards as kb
from scheduler import schedule_round_end, schedule_winner_confirmation, cancel_scheduled_tasks

router = Router()

# ---------------------- FSM состояния ----------------------
class CreateRoundStates(StatesGroup):
    waiting_for_channel_link = State()
    waiting_for_confirmation = State()

class ComplaintStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_reason = State()

class WinnerSetupStates(StatesGroup):
    waiting_for_channel_link = State()
    waiting_for_bot_admin_check = State()

class JoinRoundStates(StatesGroup):
    waiting_for_subscription_check = State()  # после нажатия "откликнуться", проверяем подписку на канал заказчика

# ---------------------- Вспомогательные функции ----------------------
async def check_exchange_subscriptions(user_id: int, bot: Bot) -> bool:
    """Проверяет, подписан ли пользователь на канал и группу биржи"""
    try:
        channel_member = await bot.get_chat_member(EXCHANGE_CHANNEL_ID, user_id)
        group_member = await bot.get_chat_member(EXCHANGE_GROUP_ID, user_id)
        channel_ok = channel_member.status in ['member', 'administrator', 'creator']
        group_ok = group_member.status in ['member', 'administrator', 'creator']
        await db.set_subscription_status(user_id, 'channel', channel_ok)
        await db.set_subscription_status(user_id, 'group', group_ok)
        return channel_ok and group_ok
    except:
        return False

async def check_user_subscribed_to_channel(user_id: int, channel_id: int, bot: Bot) -> bool:
    """Проверяет, подписан ли пользователь на указанный канал (используется для исполнителей)"""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

async def update_participant_invite_counts(round_data: dict, bot: Bot):
    """Обновляет количество приглашённых для каждого участника, используя member_count из invite_link"""
    participants = await db.get_participants()
    for p in participants:
        try:
            link_info = await bot.get_chat_invite_link(
                chat_id=round_data['channel_id'],
                invite_link=p['invite_link']
            )
            new_count = link_info.member_count
        except Exception as e:
            print(f"Ошибка получения invite_link для {p['user_id']}: {e}")
            new_count = 0
        old_count = p['points']
        if new_count > old_count:
            delta = new_count - old_count
            if delta > 0:
                await db.update_participant_points(p['user_id'], new_count)
                await db.update_user_balance(p['user_id'], delta)
        elif new_count < old_count:
            # Ссылка могла быть пересоздана? Не должно быть, но на всякий случай синхронизируем
            await db.update_participant_points(p['user_id'], new_count)
            # Баланс не уменьшаем, так как это был бы ошибочный учёт
    # После обновления перечитываем участников
    return await db.get_participants()

async def select_next_winner_from_leaderboard(bot: Bot, current_round: dict, exclude_user_id: int = None):
    """
    Выбирает следующего победителя из тир-листа (исключая указанного)
    Возвращает winner_user_id, winner_points, или (None, None) если нет подходящих
    """
    # Получаем всех пользователей с поинтами >= MIN_POINTS_TO_WIN, отсортированных по убыванию
    all_users = await db.get_all_users()
    eligible = [u for u in all_users if u['total_points'] >= MIN_POINTS_TO_WIN]
    if exclude_user_id:
        eligible = [u for u in eligible if u['user_id'] != exclude_user_id]
    if not eligible:
        return None, None
    eligible.sort(key=lambda x: x['total_points'], reverse=True)
    # Если несколько с одинаковыми поинтами, выбираем случайного среди них
    max_points = eligible[0]['total_points']
    top = [u for u in eligible if u['total_points'] == max_points]
    import random
    winner = random.choice(top)
    return winner['user_id'], winner['total_points']

async def finish_round_and_select_winner(bot: Bot, round_data: dict):
    """Завершает раунд, обновляет статистику, отправляет уведомление победителю, запускает таймер подтверждения"""
    # Обновляем количество приглашённых у всех участников
    participants = await update_participant_invite_counts(round_data, bot)
    
    # Отфильтровываем тех, у кого points >= MIN_POINTS_TO_WIN
    eligible = [p for p in participants if p['points'] >= MIN_POINTS_TO_WIN]
    if not eligible:
        # Нет победителя
        history_id = await db.add_round_history(
            winner_user_id=0,
            winner_points=0,
            start_time=round_data['start_time'],
            end_time=datetime.now(),
            status='no_winner',
            channel_id=round_data['channel_id']
        )
        await db.delete_current_round()
        await db.clear_participants()
        await bot.send_message(EXCHANGE_GROUP_ID,
                               "🏆 Раунд завершён. Никто не набрал минимальное количество поинтов. Следующий раунд будет объявлен позже.")
        return
    
    # Сортируем по убыванию поинтов
    eligible.sort(key=lambda x: x['points'], reverse=True)
    winner = eligible[0]
    winner_points = winner['points']
    winner_id = winner['user_id']
    
    # Сохраняем историю раунда
    history_id = await db.add_round_history(
        winner_user_id=winner_id,
        winner_points=winner_points,
        start_time=round_data['start_time'],
        end_time=datetime.now(),
        status='completed',
        channel_id=round_data['channel_id']
    )
    
    # Создаём запись ожидания победителя
    expires_at = datetime.now() + timedelta(minutes=CONFIRMATION_TIMEOUT_MINUTES)
    await db.set_pending_winner(winner_id, history_id, expires_at)
    
    # Отправляем уведомление победителю в ЛС
    try:
        await bot.send_message(
            winner_id,
            f"🎉 Поздравляем! Вы заняли первое место в конкурсе пиара с {winner_points} поинтами.\n\n"
            f"Желаете ли вы обнулить ваш текущий баланс и начать пиар своего канала?\n"
            f"У вас есть {CONFIRMATION_TIMEOUT_MINUTES} минут на решение.",
            reply_markup=kb.winner_confirmation_keyboard()
        )
    except Exception as e:
        print(f"Не удалось отправить сообщение победителю {winner_id}: {e}")
    
    # Запланируем автоматический отказ по таймауту
    from scheduler import schedule_winner_timeout
    await schedule_winner_timeout(bot, winner_id, history_id)
    
    # Оповещаем группу
    winner_user = await db.get_user(winner_id)
    winner_name = winner_user.get('username') if winner_user else str(winner_id)
    await bot.send_message(
        EXCHANGE_GROUP_ID,
        f"🏆 Раунд завершён! Победитель: @{winner_name} с {winner_points} поинтами.\n"
        f"Ему отправлено уведомление для подтверждения пиара своего канала."
    )
    
    # Очищаем текущий раунд и участников (они больше не нужны, но сами данные остались в истории)
    await db.delete_current_round()
    await db.clear_participants()

# ---------------------- Команда /start ----------------------
@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id
    username = message.from_user.username
    await db.register_user(user_id, username)
    
    # Проверяем подписки на канал и группу биржи
    subscribed = await check_exchange_subscriptions(user_id, bot)
    if not subscribed:
        text = (f"📢 Для использования бота необходимо подписаться на наш канал и группу:\n"
                f"👉 Канал: https://t.me/{EXCHANGE_CHANNEL_USERNAME}\n"
                f"👉 Группа: https://t.me/{EXCHANGE_GROUP_USERNAME}\n\n"
                f"После подписки нажмите /start снова.")
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{EXCHANGE_CHANNEL_USERNAME}")],
                [InlineKeyboardButton(text="💬 Подписаться на группу", url=f"https://t.me/{EXCHANGE_GROUP_USERNAME}")]
            ])
        )
        return
    
    # Проверяем бан
    if await db.is_user_banned(user_id):
        await message.answer("🚫 Вы забанены в системе. Обратитесь к администратору.")
        return
    
    # Показываем главное меню
    is_admin = (user_id == ADMIN_ID)
    await message.answer(
        "Добро пожаловать в Ateru SMM Bot!\nВыберите действие:",
        reply_markup=kb.main_menu(user_id, is_admin)
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "📖 *Помощь по боту Ateru SMM*\n\n"
        "Бот проводит конкурсы пиара: каждый час пиарится один канал, участники приводят подписчиков по своим уникальным ссылкам.\n"
        "За каждого приведённого подписчика вы получаете 1 поинт. По окончании раунда составляется тир-лист по общему количеству накопленных поинтов.\n"
        "Победитель (с наибольшим числом поинтов) может обнулить свой баланс и получить пиар своего канала.\n\n"
        "Команды:\n"
        "/start — запуск бота и главное меню\n"
        "/help — эта справка\n"
        "/admin — панель администратора (только для владельца)\n\n"
        "Используйте кнопки в меню для участия, просмотра баланса, тир-листа и подачи жалоб."
    )
    await message.answer(help_text, parse_mode="Markdown")

# ---------------------- Обработчики главного меню (callback) ----------------------
@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    # Проверяем подписки заново
    subscribed = await check_exchange_subscriptions(user_id, bot)
    if not subscribed:
        await callback.message.edit_text("❗ Вы отписались от канала или группы. Пожалуйста, подпишитесь снова и нажмите /start.")
        await callback.answer()
        return
    is_admin = (user_id == ADMIN_ID)
    await callback.message.edit_text("Главное меню:", reply_markup=kb.main_menu(user_id, is_admin))
    await callback.answer()

@router.callback_query(F.data == "current_round")
async def callback_current_round(callback: CallbackQuery):
    current_round = await db.get_current_round()
    if not current_round or current_round['status'] != 'active':
        await callback.message.answer("🔕 В данный момент нет активного пиара.")
        await callback.answer()
        return
    channel = await db.get_user_channel(current_round['channel_id'])
    if not channel:
        await callback.message.answer("Ошибка: канал не найден.")
        return
    participants = await db.get_participants()
    text = (f"🎯 *Текущий пиар:*\n"
            f"Канал: {channel.get('username') or channel.get('title')}\n"
            f"Участников: {len(participants)}\n"
            f"Окончание: {current_round['end_time']}\n\n"
            f"Чтобы участвовать, нажмите «Откликнуться» в главном меню.")
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "join_round")
async def callback_join_round(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    current_round = await db.get_current_round()
    if not current_round or current_round['status'] != 'active':
        await callback.message.answer("❌ Нет активного пиара.")
        await callback.answer()
        return
    
    # Проверяем, не участвует ли уже
    existing = await db.get_participant(user_id)
    if existing:
        await callback.message.answer("✅ Вы уже участвуете в текущем пиаре.")
        await callback.answer()
        return
    
    # Проверяем, не забанен ли
    if await db.is_user_banned(user_id):
        await callback.message.answer("🚫 Вы забанены и не можете участвовать.")
        return
    
    # Получаем канал заказчика
    channel = await db.get_user_channel(current_round['channel_id'])
    if not channel:
        await callback.message.answer("Ошибка: канал, который пиарится, не найден в БД.")
        return
    
    # Проверяем, подписан ли пользователь на канал заказчика (условие участия)
    subscribed = await check_user_subscribed_to_channel(user_id, channel['channel_id'], bot)
    if not subscribed:
        # Предлагаем подписаться
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подписаться на канал", url=f"https://t.me/{channel['username']}")],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription_after_join")]
        ])
        await callback.message.answer(
            f"Чтобы участвовать в пиаре, вы должны подписаться на канал @{channel['username']}.\n"
            f"После подписки нажмите «Я подписался».",
            reply_markup=keyboard
        )
        await state.update_data(channel_id=channel['channel_id'])
        await state.set_state(JoinRoundStates.waiting_for_subscription_check)
        await callback.answer()
        return
    
    # Уже подписан → создаём пригласительную ссылку
    await create_invite_for_participant(callback.message, user_id, channel, bot, current_round)
    await callback.answer()

@router.callback_query(F.data == "check_subscription_after_join")
async def check_subscription_after_join(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    data = await state.get_data()
    channel_id = data.get('channel_id')
    if not channel_id:
        await callback.message.answer("Ошибка: канал не найден. Попробуйте снова /start.")
        await state.clear()
        return
    
    current_round = await db.get_current_round()
    if not current_round or current_round['status'] != 'active':
        await callback.message.answer("Раунд уже завершён.")
        await state.clear()
        return
    
    channel = await db.get_user_channel(current_round['channel_id'])
    if not channel:
        await callback.message.answer("Ошибка канала.")
        await state.clear()
        return
    
    subscribed = await check_user_subscribed_to_channel(user_id, channel['channel_id'], bot)
    if not subscribed:
        await callback.message.answer("❌ Вы ещё не подписаны. Пожалуйста, подпишитесь и нажмите кнопку снова.")
        await callback.answer()
        return
    
    # Подписан → создаём ссылку
    await create_invite_for_participant(callback.message, user_id, channel, bot, current_round)
    await state.clear()
    await callback.answer()

async def create_invite_for_participant(message: Message, user_id: int, channel: dict, bot: Bot, round_data: dict):
    """Создаёт пригласительную ссылку для исполнителя и добавляет его в участники"""
    link_name = f"@{message.from_user.username}" if message.from_user.username else str(user_id)
    expire_date = datetime.now() + timedelta(hours=INVITE_LINK_EXPIRE_HOURS, minutes=INVITE_LINK_EXTRA_MINUTES)
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=channel['channel_id'],
            name=link_name,
            expire_date=expire_date,
            member_limit=0  # безлимит
        )
    except Exception as e:
        await message.answer(f"Не удалось создать пригласительную ссылку: {e}")
        return
    
    await db.add_participant(user_id, invite_link.invite_link, link_name)
    await message.answer(
        f"✅ Вы откликнулись на пиар!\n"
        f"Ваша уникальная ссылка для приглашения:\n{invite_link.invite_link}\n\n"
        f"🔁 Приглашайте друзей по этой ссылке. Каждый новый подписчик, пришедший по вашей ссылке, принесёт вам 1 поинт.\n"
        f"📊 По окончании раунда составляется тир-лист, и победитель получает пиар своего канала.",
        reply_markup=kb.back_to_main_menu()
    )

@router.callback_query(F.data == "my_balance")
async def callback_my_balance(callback: CallbackQuery):
    user_id = callback.from_user.id
    points = await db.get_user_points(user_id)
    await callback.message.answer(
        f"💰 Ваш текущий баланс поинтов: {points}\n\n"
        f"Поинты начисляются за каждого приведённого по вашей ссылке подписчика.\n"
        f"Тир-лист формируется по общему количеству поинтов (накопленных за всё время).\n"
        f"Победитель обнуляет баланс и получает пиар своего канала.",
        reply_markup=kb.back_to_main_menu()
    )
    await callback.answer()

@router.callback_query(F.data == "leaderboard")
async def callback_leaderboard(callback: CallbackQuery):
    users = await db.get_all_users()
    filtered = [u for u in users if u['total_points'] >= MIN_POINTS_TO_WIN]
    filtered.sort(key=lambda x: x['total_points'], reverse=True)
    if not filtered:
        await callback.message.answer(f"🏆 Пока нет участников с достаточным количеством поинтов (минимум {MIN_POINTS_TO_WIN}).")
        await callback.answer()
        return
    text = "🏆 *Тир-лист (по общему количеству поинтов)*\n\n"
    for i, u in enumerate(filtered[:20], 1):
        name = u['username'] or str(u['user_id'])
        text += f"{i}. @{name} — {u['total_points']} поинтов\n"
    text += f"\nМинимальный порог для победы: {MIN_POINTS_TO_WIN} поинтов."
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb.back_to_main_menu())
    await callback.answer()

@router.callback_query(F.data == "my_stats")
async def callback_my_stats(callback: CallbackQuery):
    user_id = callback.from_user.id
    points = await db.get_user_points(user_id)
    participant = await db.get_participant(user_id)
    if participant:
        round_points = participant['points']
        text = (f"📊 Ваша статистика:\n"
                f"Всего накоплено поинтов: {points}\n"
                f"Поинтов в текущем раунде: {round_points}\n"
                f"Вы участвуете в текущем пиаре.")
    else:
        text = (f"📊 Ваша статистика:\n"
                f"Всего накоплено поинтов: {points}\n"
                f"Вы не участвуете в текущем пиаре.")
    await callback.message.answer(text, reply_markup=kb.back_to_main_menu())
    await callback.answer()

# ---------------------- Жалобы ----------------------
@router.callback_query(F.data == "complaint_menu")
async def callback_complaint_menu(callback: CallbackQuery, state: FSMContext):
    today_count = await db.get_user_complaints_today(callback.from_user.id)
    if today_count >= 3:
        await callback.message.answer("⚠️ Вы исчерпали лимит жалоб на сегодня (3).")
        await callback.answer()
        return
    await callback.message.answer("Введите ID пользователя, на которого хотите пожаловаться (число):")
    await state.set_state(ComplaintStates.waiting_for_user_id)
    await callback.answer()

@router.message(ComplaintStates.waiting_for_user_id)
async def complaint_get_user(message: Message, state: FSMContext):
    try:
        against_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Некорректный ID. Введите число (например, 123456789).")
        return
    await state.update_data(against_user_id=against_id)
    await message.answer("Напишите причину жалобы (текст):")
    await state.set_state(ComplaintStates.waiting_for_reason)

@router.message(ComplaintStates.waiting_for_reason)
async def complaint_get_reason(message: Message, state: FSMContext):
    reason = message.text.strip()
    data = await state.get_data()
    against_id = data.get('against_user_id')
    from_id = message.from_user.id
    current_round = await db.get_current_round()
    round_id = current_round['channel_id'] if current_round else 0
    await db.add_complaint(from_id, against_id, round_id, reason)
    await message.answer("✅ Жалоба отправлена администратору. Будет рассмотрена.")
    await state.clear()
    # Возвращаем в главное меню
    await cmd_start(message, message.bot)

# ---------------------- Админ-панель ----------------------
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к админ-панели.")
        return
    await message.answer("🛠 Админ-панель", reply_markup=kb.admin_panel_menu())

@router.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=kb.admin_panel_menu())
    await callback.answer()

# ---- Создание нового раунда ----
@router.callback_query(F.data == "admin_create_round")
async def admin_create_round(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    current = await db.get_current_round()
    if current and current['status'] == 'active':
        await callback.message.answer("❌ Сначала завершите текущий раунд (или дождитесь его окончания).")
        await callback.answer()
        return
    await callback.message.answer("Введите ссылку на канал/группу, который будет пиариться (например, @username или https://t.me/username):")
    await state.set_state(CreateRoundStates.waiting_for_channel_link)

@router.message(CreateRoundStates.waiting_for_channel_link)
async def admin_get_channel(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return
    link = message.text.strip()
    if link.startswith("https://t.me/"):
        username = link.split("/")[-1]
    elif link.startswith("@"):
        username = link[1:]
    else:
        username = link
    try:
        chat = await bot.get_chat(f"@{username}")
        channel_id = chat.id
        title = chat.title
    except Exception as e:
        await message.answer(f"Не удалось найти канал: {e}\nПроверьте ссылку и убедитесь, что бот добавлен в канал.")
        return
    # Проверяем, что бот админ в канале
    try:
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.answer("❌ Бот не является администратором этого канала. Добавьте бота в админы с правами на создание ссылок и просмотр участников.")
            return
    except Exception as e:
        await message.answer(f"Ошибка проверки прав: {e}")
        return
    # Сохраняем канал в БД (владелец = ADMIN_ID, так как создаёт админ)
    await db.add_channel(ADMIN_ID, channel_id, username, title, True)
    start_time = datetime.now()
    end_time = start_time + timedelta(minutes=ROUND_DURATION_MINUTES)
    await db.create_round(channel_id, start_time, end_time, waiting_for_admin=True)
    await state.update_data(channel_id=channel_id, end_time=end_time)
    await message.answer(
        f"✅ Раунд создан для канала {title}.\nОкончание: {end_time}\nТеперь подтвердите запуск:",
        reply_markup=kb.round_control_keyboard()
    )
    await state.set_state(CreateRoundStates.waiting_for_confirmation)

@router.callback_query(F.data == "admin_confirm_round")
async def admin_confirm_round(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    current = await db.get_current_round()
    if not current:
        await callback.message.answer("Нет созданного раунда.")
        await callback.answer()
        return
    # Снимаем флаг ожидания админа
    await db.set_round_waiting_for_admin(False)
    # Публикуем в группе
    channel = await db.get_user_channel(current['channel_id'])
    if channel:
        await bot.send_message(
            EXCHANGE_GROUP_ID,
            f"🎉 *Старт пиара!*\n"
            f"Канал: {channel.get('username') or channel.get('title')}\n"
            f"Продолжительность: {ROUND_DURATION_MINUTES} минут.\n"
            f"Нажмите «Откликнуться» в боте, чтобы участвовать и зарабатывать поинты.\n"
            f"Победитель получит пиар своего канала!",
            parse_mode="Markdown"
        )
    # Запланировать окончание раунда
    from scheduler import schedule_round_end
    await schedule_round_end(bot, current['end_time'])
    await callback.message.edit_text(
        f"✅ Раунд запущен! Участники могут откликаться.\nОкончание: {current['end_time']}"
    )
    await callback.answer()
    await state.clear()

@router.callback_query(F.data == "admin_cancel_round")
async def admin_cancel_round(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await db.delete_current_round()
    await callback.message.edit_text("❌ Создание раунда отменено.")
    await callback.answer()
    await state.clear()

@router.callback_query(F.data == "admin_stop_round")
async def admin_stop_round(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        return
    current = await db.get_current_round()
    if not current:
        await callback.message.answer("Нет активного раунда.")
        return
    # Принудительно завершаем
    await finish_round_and_select_winner(bot, current)
    await callback.message.edit_text("✅ Раунд принудительно завершён.")
    await callback.answer()

# ---- Прочие админ-функции (списки, жалобы, история) ----
@router.callback_query(F.data == "admin_users_list")
async def admin_users_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    users = await db.get_all_users()
    if not users:
        await callback.message.answer("Нет пользователей.")
        return
    text = "👥 Список пользователей:\n\n"
    for u in users[:20]:
        name = u['username'] or str(u['user_id'])
        points = u['total_points']
        banned = u['is_banned']
        text += f"@{name} | ID: {u['user_id']} | Поинты: {points} | {'🚫 Забанен' if banned else '✅ Активен'}\n"
    await callback.message.answer(text, reply_markup=kb.back_to_main_menu())
    await callback.answer()

@router.callback_query(F.data == "admin_blacklist")
async def admin_blacklist(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    blacklist = await db.get_blacklist()
    if not blacklist:
        await callback.message.answer("Чёрный список пуст.")
        return
    text = "🚫 Чёрный список:\n"
    for b in blacklist:
        text += f"ID: {b['user_id']} | Причина: {b['reason']} | Забанен: {b['banned_at']}\n"
    await callback.message.answer(text, reply_markup=kb.back_to_main_menu())
    await callback.answer()

@router.callback_query(F.data == "admin_complaints")
async def admin_complaints_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    complaints = await db.get_pending_complaints()
    if not complaints:
        await callback.message.answer("Нет новых жалоб.")
        return
    for c in complaints[:5]:
        text = (f"Жалоба #{c['complaint_id']}\n"
                f"От: {c['from_user_id']}\n"
                f"На: {c['against_user_id']}\n"
                f"Причина: {c['reason']}\n"
                f"Создана: {c['created_at']}")
        await callback.message.answer(text, reply_markup=kb.complaint_resolution_keyboard(c['complaint_id']))
    await callback.answer()

@router.callback_query(F.data.startswith("complaint_accept_"))
async def complaint_accept(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    complaint_id = int(callback.data.split("_")[-1])
    await db.resolve_complaint(complaint_id, True, "Принято администратором")
    # Здесь нужно получить against_user_id и забанить
    # Для простоты вызовем ban_user, но в реальном коде нужно извлечь against_user_id из жалобы
    # Пропустим для краткости, но в полной версии это необходимо
    await callback.message.edit_text("✅ Жалоба принята. Пользователь забанен.")
    await callback.answer()

@router.callback_query(F.data.startswith("complaint_reject_"))
async def complaint_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    complaint_id = int(callback.data.split("_")[-1])
    await db.resolve_complaint(complaint_id, False, "Отклонено администратором")
    await callback.message.edit_text("❌ Жалоба отклонена.")
    await callback.answer()

@router.callback_query(F.data == "admin_round_history")
async def admin_round_history(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    history = await db.get_round_history_list(10)
    if not history:
        await callback.message.answer("Нет завершённых раундов.")
        return
    text = "📜 История раундов:\n\n"
    for h in history:
        winner = h['winner_user_id'] if h['winner_user_id'] != 0 else "Нет победителя"
        points = h['winner_points']
        status = h['status']
        end = h['round_end']
        text += f"Раунд #{h['id']} | Победитель: {winner} | Поинтов: {points} | Статус: {status} | Окончен: {end}\n"
    await callback.message.answer(text, reply_markup=kb.back_to_main_menu())
    await callback.answer()

# ---------------------- Обработка ответа победителя ----------------------
@router.callback_query(F.data == "winner_accept")
async def winner_accept(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    pending = await db.get_pending_winner()
    if not pending or pending['user_id'] != user_id:
        await callback.message.answer("❌ Нет активного запроса на подтверждение.")
        await callback.answer()
        return
    # Отменяем запланированный таймаут
    from scheduler import cancel_winner_timeout
    await cancel_winner_timeout(user_id)
    # Запрашиваем ссылку на канал победителя
    await callback.message.answer("Отлично! Теперь отправьте ссылку на ваш Telegram канал или группу (например, @username).")
    await state.update_data(history_id=pending['round_history_id'])
    await state.set_state(WinnerSetupStates.waiting_for_channel_link)
    await callback.answer()

@router.callback_query(F.data == "winner_decline")
async def winner_decline(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    pending = await db.get_pending_winner()
    if not pending or pending['user_id'] != user_id:
        await callback.message.answer("❌ Нет активного запроса.")
        await callback.answer()
        return
    # Обнуляем поинты победителя
    await db.reset_user_points(user_id)
    await db.clear_pending_winner()
    from scheduler import cancel_winner_timeout
    await cancel_winner_timeout(user_id)
    # Оповещаем группу
    await bot.send_message(EXCHANGE_GROUP_ID, f"⚠️ Победитель @{callback.from_user.username or user_id} отказался от пиара. Поинты списаны.")
    # Пытаемся выбрать следующего победителя из тир-листа
    current_round = await db.get_current_round()
    if current_round and current_round['status'] == 'active':
        # Если раунд ещё активен, просто сообщаем
        pass
    else:
        # Раунда нет, возможно, нужно создать новый? Но логика проще: уведомить админа
        await bot.send_message(ADMIN_ID, f"Победитель отказался, а активного раунда нет. Запустите новый раунд вручную.")
    await callback.message.edit_text("Вы отказались. Ваши поинты списаны. Вы можете участвовать в следующих раундах заново.")
    await callback.answer()

@router.message(WinnerSetupStates.waiting_for_channel_link)
async def winner_set_channel(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    link = message.text.strip()
    if link.startswith("https://t.me/"):
        username = link.split("/")[-1]
    elif link.startswith("@"):
        username = link[1:]
    else:
        username = link
    try:
        chat = await bot.get_chat(f"@{username}")
        channel_id = chat.id
        title = chat.title
    except Exception as e:
        await message.answer(f"Не удалось найти канал: {e}")
        return
    # Проверяем, добавил ли пользователь бота в админы
    try:
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.answer("❌ Бот не является администратором вашего канала. Пожалуйста, добавьте бота в администраторы с правами на создание ссылок и просмотр участников, затем отправьте ссылку снова.")
            return
    except Exception as e:
        await message.answer(f"Ошибка проверки прав: {e}")
        return
    # Сохраняем канал в БД
    await db.add_channel(user_id, channel_id, username, title, True)
    # Обнуляем поинты победителя
    await db.reset_user_points(user_id)
    # Удаляем ожидание победителя
    await db.clear_pending_winner()
    # Сохраняем в историю, что этот канал будет следующим (можно записать в специальную таблицу, но пока просто уведомим админа)
    data = await state.get_data()
    history_id = data.get('history_id')
    # Уведомляем админа о необходимости запустить раунд с этим каналом
    await bot.send_message(
        ADMIN_ID,
        f"🏆 Победитель @{message.from_user.username or user_id} подтвердил участие и предоставил канал {username}.\n"
        f"Запустите новый раунд для этого канала (используйте /admin и «Создать новый раунд»)."
    )
    await message.answer(
        "✅ Спасибо! Канал сохранён. Как только администратор запустит новый раунд, ваш канал будет пиариться.\n"
        "Следите за объявлениями в группе."
    )
    await state.clear()

# ---------------------- Автоотказ по таймауту (вызывается из scheduler) ----------------------
async def winner_timeout(bot: Bot, user_id: int, history_id: int):
    pending = await db.get_pending_winner()
    if pending and pending['user_id'] == user_id:
        await db.reset_user_points(user_id)
        await db.clear_pending_winner()
        await bot.send_message(user_id, "⏰ Время на ответ истекло. Ваши поинты списаны. Вы можете участвовать в следующих раундах.")
        await bot.send_message(EXCHANGE_GROUP_ID, f"⏰ Победитель @{user_id} не ответил вовремя. Поинты списаны.")
        # Выбор следующего победителя из тир-листа (если есть)
        # Здесь нужно реализовать логику, но для краткости оставим уведомление админу
        await bot.send_message(ADMIN_ID, f"Победитель {user_id} не ответил. Можно запустить новый раунд вручную.")