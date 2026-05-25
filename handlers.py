import asyncio
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup, KeyboardButtonRequestChat,
    Chat, ChatJoinRequest
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import (
    ADMIN_ID, EXCHANGE_CHANNEL_ID, EXCHANGE_GROUP_ID,
    EXCHANGE_CHANNEL_USERNAME, EXCHANGE_GROUP_USERNAME,
    ROUND_DURATION_MINUTES, CONFIRMATION_TIMEOUT_MINUTES,
    MIN_POINTS_TO_WIN, INVITE_LINK_EXPIRE_HOURS, INVITE_LINK_EXTRA_MINUTES
)
import database as db
import keyboards as kb

router = Router()

# ---------------------- FSM состояния ----------------------
class CreateRoundStates(StatesGroup):
    waiting_for_channel_link = State()
    waiting_for_confirmation = State()
    waiting_for_channel_selection = State()

class ComplaintStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_reason = State()

class WinnerSetupStates(StatesGroup):
    waiting_for_channel_link = State()

class JoinRoundStates(StatesGroup):
    waiting_for_subscription_check = State()

# ---------------------- Вспомогательные функции ----------------------
async def check_exchange_subscriptions(user_id: int, bot: Bot) -> bool:
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
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

async def update_participant_invite_counts(round_data: dict, bot: Bot):
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
            await db.update_participant_points(p['user_id'], new_count)
    return await db.get_participants()

async def send_admin_round_stats(bot: Bot, round_data: dict, participants: list, winner_id: int = 0):
    channel = await db.get_channel_by_id(round_data['channel_id'])
    if channel and channel.get('username'):
        channel_name = f"@{channel['username']}"
    elif channel:
        channel_name = channel.get('title', 'канал')
    else:
        channel_name = f"ID {round_data['channel_id']}"
    start_time = round_data['start_time']
    end_time = datetime.now()
    duration = end_time - start_time
    lines = [
        f"📊 *Статистика завершённого раунда*",
        f"📅 Старт: {start_time}",
        f"⏱ Окончание: {end_time}",
        f"⏳ Длительность: {duration}",
        f"🔗 Пиаримый канал: {channel_name}",
        "",
        "👥 *Все участники (по убыванию поинтов):*"
    ]
    for i, p in enumerate(participants, 1):
        user = await db.get_user(p['user_id'])
        name = user.get('username') if user else str(p['user_id'])
        invite_link = p.get('invite_link', 'нет ссылки')
        lines.append(f"{i}. @{name} — {p['points']} поинтов (ссылка: {invite_link})")
    if winner_id != 0:
        winner_user = await db.get_user(winner_id)
        winner_name = winner_user.get('username') if winner_user else str(winner_id)
        lines.append(f"\n🏆 *Победитель:* @{winner_name}")
    else:
        lines.append("\n🏆 *Победитель не определён*")
    text = "\n".join(lines)
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Не удалось отправить статистику админу: {e}")

async def finish_round_and_select_winner(bot: Bot, round_data: dict):
    participants = await update_participant_invite_counts(round_data, bot)
    if round_data.get('start_message_id'):
        try:
            await bot.delete_message(EXCHANGE_CHANNEL_ID, round_data['start_message_id'])
        except Exception as e:
            print(f"Не удалось удалить сообщение: {e}")
    participants.sort(key=lambda x: x['points'], reverse=True)
    eligible = [p for p in participants if p['points'] >= MIN_POINTS_TO_WIN]
    top_10 = participants[:10]
    lines = ["📊 *Тир-лист раунда (топ-10 по поинтам):*\n"]
    for i, p in enumerate(top_10, 1):
        user = await db.get_user(p['user_id'])
        name = user.get('username') if user else str(p['user_id'])
        lines.append(f"{i}. @{name} — {p['points']} поинтов")
    if not eligible:
        lines.append("\n🏆 *Победитель не определён* (никто не набрал минимальное количество поинтов).")
        final_text = "\n".join(lines)
        await bot.send_message(EXCHANGE_CHANNEL_ID, final_text, parse_mode="Markdown", reply_markup=kb.balance_button())
        await db.add_round_history(0, 0, round_data['start_time'], datetime.now(), 'no_winner', round_data['channel_id'])
        await send_admin_round_stats(bot, round_data, participants, winner_id=0)
        await db.delete_current_round()
        await db.clear_participants()
        return
    winner = eligible[0]
    winner_points = winner['points']
    winner_id = winner['user_id']
    lines.append(f"\n🏆 *Победитель:* @{await get_username(winner_id)} с {winner_points} поинтами.")
    lines.append("\n📩 Победителю отправлено уведомление в личные сообщения. Ему нужно подтвердить или отказаться от пиара своего канала в течение 10 минут.")
    final_text = "\n".join(lines)
    await bot.send_message(EXCHANGE_CHANNEL_ID, final_text, parse_mode="Markdown", reply_markup=kb.balance_button())
    history_id = await db.add_round_history(
        winner_user_id=winner_id,
        winner_points=winner_points,
        start_time=round_data['start_time'],
        end_time=datetime.now(),
        status='completed',
        channel_id=round_data['channel_id']
    )
    await send_admin_round_stats(bot, round_data, participants, winner_id)
    expires_at = datetime.now() + timedelta(minutes=CONFIRMATION_TIMEOUT_MINUTES)
    await db.set_pending_winner(winner_id, history_id, expires_at)
    try:
        await bot.send_message(
            winner_id,
            f"🎉 Поздравляем! Вы заняли первое место в конкурсе пиара с {winner_points} поинтами.\n\n"
            f"Желаете ли вы обнулить ваш текущий баланс поинтов и начать пиар своего канала?\n"
            f"У вас есть {CONFIRMATION_TIMEOUT_MINUTES} минут на решение.\n\n"
            f"Если вы откажетесь, ваши поинты будут конвертированы в коины (1 поинт = 1 коин), которые можно будет использовать в магазине бота.",
            reply_markup=kb.winner_confirmation_keyboard()
        )
    except Exception as e:
        print(f"Не удалось отправить сообщение победителю {winner_id}: {e}")
    from scheduler import schedule_winner_timeout
    await schedule_winner_timeout(bot, winner_id, history_id)
    await db.delete_current_round()
    await db.clear_participants()

async def get_username(user_id: int) -> str:
    user = await db.get_user(user_id)
    return user.get('username') if user and user.get('username') else str(user_id)

# ---------------------- Команды /start, /help ----------------------
@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id
    username = message.from_user.username
    await db.register_user(user_id, username)
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
    if await db.is_user_banned(user_id):
        await message.answer("🚫 Вы забанены в системе. Обратитесь к администратору.")
        return
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
        "За каждого приведённого подписчика вы получаете 1 поинт. По окончании раунда составляется тир-лист, победитель получает пиар своего канала.\n"
        "При отказе победителя поинты конвертируются в коины (1:1) для будущего магазина.\n\n"
        "Команды:\n"
        "/start — запуск бота и главное меню\n"
        "/help — эта справка\n"
        "/admin — панель администратора (только для владельца)"
    )
    await message.answer(help_text, parse_mode="Markdown")

# ---------------------- Главное меню и основные кнопки ----------------------
@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    subscribed = await check_exchange_subscriptions(user_id, bot)
    if not subscribed:
        await callback.message.edit_text("❗ Вы отписались от канала или группы. Пожалуйста, подпишитесь снова и нажмите /start.")
        await callback.answer()
        return
    is_admin = (user_id == ADMIN_ID)
    await callback.message.edit_text("Главное меню:", reply_markup=kb.main_menu(user_id, is_admin))
    await callback.answer()

@router.callback_query(F.data == "my_balance")
async def callback_my_balance(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    points = await db.get_user_points(user_id)
    coins = await db.get_user_coins(user_id)
    text = f"💰 Ваш баланс:\nПоинты: {points}\nКоины: {coins}\n\nПоинты начисляются за приглашённых подписчиков.\nКоины можно получить при отказе от пиара и затем использовать в магазине (скоро)."
    if callback.message.chat.type in ['channel', 'group', 'supergroup']:
        try:
            await bot.send_message(user_id, text, reply_markup=kb.user_stats_keyboard())
            await callback.answer("Баланс отправлен в личные сообщения.", show_alert=True)
        except:
            await callback.answer("Не удалось отправить ЛС. Напишите боту сами: @AteruSmmBot", show_alert=True)
    else:
        await callback.message.edit_text(text, reply_markup=kb.user_stats_keyboard())
        await callback.answer()

@router.callback_query(F.data == "leaderboard")
async def callback_leaderboard(callback: CallbackQuery, bot: Bot):
    users = await db.get_all_users()
    filtered = [u for u in users if u['total_points'] >= MIN_POINTS_TO_WIN]
    filtered.sort(key=lambda x: x['total_points'], reverse=True)
    if not filtered:
        text = f"🏆 Пока нет участников с достаточным количеством поинтов (минимум {MIN_POINTS_TO_WIN})."
    else:
        text = "🏆 *Тир-лист (по общему количеству поинтов)*\n\n"
        for i, u in enumerate(filtered[:25], 1):
            name = u['username'] or str(u['user_id'])
            text += f"{i}. @{name} — {u['total_points']} поинтов\n"
        text += f"\nМинимальный порог для победы: {MIN_POINTS_TO_WIN} поинтов."
    if callback.message.chat.type in ['channel', 'group', 'supergroup']:
        try:
            await bot.send_message(callback.from_user.id, text, parse_mode="Markdown", reply_markup=kb.user_stats_keyboard())
            await callback.answer("Тир-лист отправлен в личные сообщения.", show_alert=True)
        except:
            await callback.answer("Не удалось отправить ЛС.", show_alert=True)
    else:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.user_stats_keyboard())
        await callback.answer()

@router.callback_query(F.data == "my_stats")
async def callback_my_stats(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    points = await db.get_user_points(user_id)
    coins = await db.get_user_coins(user_id)
    participant = await db.get_participant(user_id)
    if participant:
        round_points = participant['points']
        text = (f"📊 Ваша статистика:\n"
                f"Всего поинтов: {points}\n"
                f"Коинов: {coins}\n"
                f"Поинтов в текущем раунде: {round_points}\n"
                f"Вы участвуете в текущем пиаре.")
    else:
        text = (f"📊 Ваша статистика:\n"
                f"Всего поинтов: {points}\n"
                f"Коинов: {coins}\n"
                f"Вы не участвуете в текущем пиаре.")
    if callback.message.chat.type in ['channel', 'group', 'supergroup']:
        try:
            await bot.send_message(user_id, text, reply_markup=kb.user_stats_keyboard())
            await callback.answer("Статистика отправлена в личные сообщения.", show_alert=True)
        except:
            await callback.answer("Не удалось отправить ЛС.", show_alert=True)
    else:
        await callback.message.edit_text(text, reply_markup=kb.user_stats_keyboard())
        await callback.answer()

# ---------------------- Кнопки обновления ----------------------
@router.callback_query(F.data == "refresh_leaderboard")
async def refresh_leaderboard(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    users = await db.get_all_users()
    filtered = [u for u in users if u['total_points'] >= MIN_POINTS_TO_WIN]
    filtered.sort(key=lambda x: x['total_points'], reverse=True)
    if not filtered:
        text = f"🏆 Пока нет участников с достаточным количеством поинтов (минимум {MIN_POINTS_TO_WIN})."
    else:
        text = "🏆 *Тир-лист (обновлён)*\n\n"
        for i, u in enumerate(filtered[:25], 1):
            name = u['username'] or str(u['user_id'])
            text += f"{i}. @{name} — {u['total_points']} поинтов\n"
        text += f"\nМинимальный порог для победы: {MIN_POINTS_TO_WIN} поинтов."
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.user_stats_keyboard())
    await callback.answer("Тир-лист обновлён!", show_alert=True)

@router.callback_query(F.data == "refresh_stats")
async def refresh_stats(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    points = await db.get_user_points(user_id)
    coins = await db.get_user_coins(user_id)
    participant = await db.get_participant(user_id)
    if participant:
        round_points = participant['points']
        text = (f"📊 Ваша статистика (обновлена):\n"
                f"Всего поинтов: {points}\n"
                f"Коинов: {coins}\n"
                f"Поинтов в текущем раунде: {round_points}\n"
                f"Вы участвуете в текущем пиаре.")
    else:
        text = (f"📊 Ваша статистика (обновлена):\n"
                f"Всего поинтов: {points}\n"
                f"Коинов: {coins}\n"
                f"Вы не участвуете в текущем пиаре.")
    await callback.message.edit_text(text, reply_markup=kb.user_stats_keyboard())
    await callback.answer("Статистика обновлена!", show_alert=True)

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

    request_channel = KeyboardButtonRequestChat(
        request_id=1,
        chat_is_channel=True,
        bot_is_member=False,
        bot_administrator_rights=None
    )
    button = KeyboardButton(text="📢 Выбрать канал для пиара", request_chat=request_channel)
    markup = ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True)

    await callback.message.answer(
        "Нажмите на кнопку ниже и выберите канал, который будет пиариться.\n"
        "При выборе обязательно предоставьте боту права администратора с правом 'Приглашать пользователей'.",
        reply_markup=markup
    )
    await state.set_state(CreateRoundStates.waiting_for_channel_selection)
    await callback.answer()

@router.message(CreateRoundStates.waiting_for_channel_selection, F.chat_shared)
async def admin_channel_selected(message: Message, state: FSMContext, bot: Bot):
    shared_chat = message.chat_shared
    if not shared_chat:
        await message.answer("❌ Не удалось получить информацию о канале. Попробуйте ещё раз.")
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        return

    channel_id = shared_chat.chat_id

    try:
        chat: Chat = await bot.get_chat(channel_id)
        channel_username = chat.username if chat.username else None
        title = chat.title
    except Exception as e:
        await message.answer(f"❌ Не удалось получить данные о канале. Ошибка: {e}")
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        return

    if chat.type != "channel":
        await message.answer("❌ Пожалуйста, выберите канал, а не группу.")
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        return

    bot_member = await bot.get_chat_member(channel_id, bot.id)
    if bot_member.status not in ['administrator', 'creator']:
        await message.answer("❌ Бот не является администратором канала. При выборе канала нужно добавить бота в администраторы.")
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        return
    if bot_member.status == 'administrator' and not bot_member.can_invite_users:
        await message.answer("❌ У бота нет права 'Приглашать пользователей'. Пожалуйста, выдайте это право при добавлении бота в администраторы.")
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        return

    await db.add_channel(ADMIN_ID, channel_id, channel_username, title, True)

    start_time = datetime.now()
    end_time = start_time + timedelta(minutes=ROUND_DURATION_MINUTES)
    await db.create_round(channel_id, start_time, end_time, waiting_for_admin=True, start_message_id=0)

    await state.update_data(channel_id=channel_id, end_time=end_time, title=title, username=channel_username)

    await message.answer(
        f"✅ Канал '{title}' выбран.\nОкончание раунда: {end_time}\nТеперь подтвердите запуск:",
        reply_markup=kb.round_control_keyboard()
    )
    await state.set_state(CreateRoundStates.waiting_for_confirmation)

@router.callback_query(F.data == "admin_confirm_round")
async def admin_confirm_round(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    current = await db.get_current_round()
    if not current:
        await callback.message.answer("Нет созданного раунда.")
        await callback.answer()
        return
    if current.get('waiting_for_admin') != 1:
        await callback.message.answer("Раунд уже был запущен или отменён.")
        await callback.answer()
        return
    channel = await db.get_channel_by_id(current['channel_id'])
    if not channel:
        await callback.message.answer("Ошибка: канал не найден в БД.")
        await callback.answer()
        return

    # СОЗДАНИЕ ОБЩЕЙ ПРИГЛАСИТЕЛЬНОЙ ССЫЛКИ (без member_limit)
    try:
        common_link = await bot.create_chat_invite_link(
            chat_id=channel['channel_id'],
            name="common_for_round",
            expire_date=datetime.now() + timedelta(hours=INVITE_LINK_EXPIRE_HOURS, minutes=INVITE_LINK_EXTRA_MINUTES),
            creates_join_request=True
        )
        common_link_url = common_link.invite_link
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось создать общую ссылку-приглашение: {e}")
        await callback.answer()
        return

    if channel.get('username'):
        channel_display = f"https://t.me/{channel['username']}"
    else:
        channel_display = common_link_url

    text = (
        f"🎉 *Старт пиара!*\n\n"
        f"🔗 Канал: {channel_display}\n"
        f"📅 Продолжительность: {ROUND_DURATION_MINUTES} минут.\n\n"
        f"👇 Нажмите кнопку ниже, чтобы участвовать и зарабатывать поинты!\n"
        f"Для участия необходимо быть подписанным на канал."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"join_round_{current['id']}_{current['channel_id']}")]
    ])
    try:
        sent_msg = await bot.send_message(EXCHANGE_CHANNEL_ID, text, parse_mode="Markdown", reply_markup=keyboard)
        await db.set_round_start_message_id(sent_msg.message_id)
    except Exception as e:
        await callback.message.answer(f"Не удалось отправить сообщение в канал: {e}")
        await callback.answer()
        return
    await db.set_round_waiting_for_admin(False)
    from scheduler import schedule_round_end
    await schedule_round_end(bot, current['end_time'])
    await callback.message.edit_text(f"✅ Раунд запущен! Сообщение отправлено в канал.\nОкончание: {current['end_time']}")
    await callback.answer()

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
    await finish_round_and_select_winner(bot, current)
    await callback.message.edit_text("✅ Раунд принудительно завершён.")
    await callback.answer()

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

# ---------------------- Обработка отклика на пиар (alert при отсутствии подписки) ----------------------
@router.callback_query(F.data.startswith("join_round_"))
async def join_round_callback(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    round_id = int(parts[2])
    channel_id = int(parts[3])
    user_id = callback.from_user.id

    current_round = await db.get_current_round()
    if not current_round or current_round['status'] != 'active':
        await callback.answer("Раунд уже завершён", show_alert=True)
        return

    existing = await db.get_participant(user_id)
    if existing:
        await callback.answer("Вы уже участвуете в этом пиаре", show_alert=True)
        return

    if await db.is_user_banned(user_id):
        await callback.answer("Вы забанены", show_alert=True)
        return

    channel = await db.get_channel_by_id(channel_id)
    if not channel:
        await callback.answer("Канал не найден", show_alert=True)
        return

    # Проверяем подписку на канал заказчика
    subscribed = await check_user_subscribed_to_channel(user_id, channel['channel_id'], bot)
    if not subscribed:
        if channel.get('username'):
            await callback.answer(f"❌ Вы не подписаны на канал @{channel['username']}. Подпишитесь и попробуйте снова.", show_alert=True)
        else:
            await callback.answer(f"❌ Вы не подписаны на канал. Подпишитесь и попробуйте снова.", show_alert=True)
        return

    # Создаём уникальную пригласительную ссылку для исполнителя (без member_limit)
    link_name = str(user_id)
    expire_date = datetime.now() + timedelta(hours=INVITE_LINK_EXPIRE_HOURS, minutes=INVITE_LINK_EXTRA_MINUTES)
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=channel['channel_id'],
            name=link_name,
            expire_date=expire_date,
            creates_join_request=True
        )
    except Exception as e:
        await callback.answer(f"Ошибка создания ссылки: {e}", show_alert=True)
        return

    await db.add_participant(user_id, invite_link.invite_link, link_name)

    try:
        await bot.send_message(
            user_id,
            f"✅ Вы успешно откликнулись на пиар канала @{channel['username'] if channel['username'] else 'канала'}!\n\n"
            f"Ваша уникальная пригласительная ссылка (действует {INVITE_LINK_EXPIRE_HOURS}ч {INVITE_LINK_EXTRA_MINUTES}мин):\n{invite_link.invite_link}\n\n"
            f"🔁 Приглашайте друзей по этой ссылке. Каждый новый подписчик принесёт вам 1 поинт.\n"
            f"📊 После окончания раунда вы увидите итоги.",
            reply_markup=kb.user_stats_keyboard()
        )
    except Exception as e:
        print(f"Не удалось отправить ЛС пользователю {user_id}: {e}")

    await callback.answer("✅ Вы участвуете! Ссылка отправлена в личные сообщения.", show_alert=True)

@router.callback_query(F.data.startswith("retry_join_"))
async def retry_join_callback(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    round_id = int(parts[2])
    channel_id = int(parts[3])
    user_id = callback.from_user.id

    current_round = await db.get_current_round()
    if not current_round or current_round['status'] != 'active':
        await callback.answer("Раунд уже завершён", show_alert=True)
        return

    existing = await db.get_participant(user_id)
    if existing:
        invite_link = existing.get('invite_link')
        if invite_link:
            try:
                await bot.send_message(
                    user_id,
                    f"✅ Вы уже откликнулись на пиар канала @{channel['username'] if channel['username'] else 'канала'}!\n\n"
                    f"Ваша пригласительная ссылка (действует {INVITE_LINK_EXPIRE_HOURS}ч {INVITE_LINK_EXTRA_MINUTES}мин):\n{invite_link}\n\n"
                    f"🔁 Приглашайте друзей.",
                    reply_markup=kb.user_stats_keyboard()
                )
            except:
                pass
            await callback.answer("Ссылка отправлена повторно в ЛС.", show_alert=True)
            return
        else:
            pass
    else:
        pass

    if await db.is_user_banned(user_id):
        await callback.answer("Вы забанены", show_alert=True)
        return

    channel = await db.get_channel_by_id(channel_id)
    if not channel:
        await callback.answer("Канал не найден", show_alert=True)
        return

    subscribed = await check_user_subscribed_to_channel(user_id, channel['channel_id'], bot)
    if not subscribed:
        if channel.get('username'):
            await callback.answer(f"❌ Вы не подписаны на канал @{channel['username']}. Подпишитесь и попробуйте снова.", show_alert=True)
        else:
            await callback.answer(f"❌ Вы не подписаны на канал. Подпишитесь и попробуйте снова.", show_alert=True)
        return

    link_name = str(user_id)
    expire_date = datetime.now() + timedelta(hours=INVITE_LINK_EXPIRE_HOURS, minutes=INVITE_LINK_EXTRA_MINUTES)
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=channel['channel_id'],
            name=link_name,
            expire_date=expire_date,
            creates_join_request=True
        )
    except Exception as e:
        await callback.answer(f"Ошибка создания ссылки: {e}", show_alert=True)
        return

    await db.add_participant(user_id, invite_link.invite_link, link_name)

    try:
        await bot.send_message(
            user_id,
            f"✅ Вы успешно откликнулись на пиар канала @{channel['username'] if channel['username'] else 'канала'}!\n\n"
            f"Ваша уникальная ссылка:\n{invite_link.invite_link}\n\n"
            f"Приглашайте друзей!",
            reply_markup=kb.user_stats_keyboard()
        )
    except Exception as e:
        print(f"Не удалось отправить ЛС: {e}")

    await callback.answer("✅ Ссылка отправлена в личные сообщения.", show_alert=True)

# ---------------------- Обработчик заявок на вступление (начисление поинтов) ----------------------
@router.chat_join_request()
async def handle_join_request(join_request: ChatJoinRequest, bot: Bot):
    invite_link = join_request.invite_link
    user_id = join_request.from_user.id
    username = join_request.from_user.username or join_request.from_user.first_name

    try:
        await join_request.approve()
    except Exception as e:
        print(f"Не удалось одобрить заявку: {e}")
        return

    if not invite_link or not invite_link.name:
        return

    try:
        inviter_id = int(invite_link.name)
    except ValueError:
        return

    inviter = await db.get_user(inviter_id)
    if not inviter:
        return

    await db.update_user_balance(inviter_id, 1)
    try:
        await bot.send_message(
            inviter_id,
            f"🎉 Пользователь @{username} подписался по вашей ссылке! Вы получили +1 поинт."
        )
    except:
        pass

    current_round = await db.get_current_round()
    if current_round and current_round['status'] == 'active':
        participant = await db.get_participant(inviter_id)
        if participant:
            new_points = participant['points'] + 1
            await db.update_participant_points(inviter_id, new_points)

# ---------------------- Обработка ответа победителя ----------------------
@router.callback_query(F.data == "winner_accept")
async def winner_accept(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    pending = await db.get_pending_winner()
    if not pending or pending['user_id'] != user_id:
        await callback.message.answer("❌ Нет активного запроса на подтверждение.")
        await callback.answer()
        return
    from scheduler import cancel_winner_timeout
    await cancel_winner_timeout(user_id)
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
    winner_points = await db.get_user_points(user_id)
    if winner_points > 0:
        success = await db.convert_points_to_coins(user_id, winner_points)
        if success:
            await bot.send_message(user_id, f"🪙 Ваши {winner_points} поинтов конвертированы в {winner_points} коинов. Коины можно будет использовать в магазине.")
        else:
            await bot.send_message(user_id, "❌ Ошибка конвертации поинтов.")
    await db.clear_pending_winner()
    from scheduler import cancel_winner_timeout
    await cancel_winner_timeout(user_id)
    await bot.send_message(EXCHANGE_GROUP_ID, f"⚠️ Победитель @{callback.from_user.username or user_id} отказался от пиара. Его поинты конвертированы в коины.")
    await callback.message.edit_text("Вы отказались. Ваши поинты конвертированы в коины. Вы можете участвовать в следующих раундах заново.")
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
    try:
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.answer("❌ Бот не является администратором вашего канала. Пожалуйста, добавьте бота в администраторы с правами на создание ссылок и просмотр участников, затем отправьте ссылку снова.")
            return
    except Exception as e:
        await message.answer(f"Ошибка проверки прав: {e}")
        return
    await db.add_channel(user_id, channel_id, username, title, True)
    await db.reset_user_points(user_id)
    await db.clear_pending_winner()
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

# ---------------------- Автоотказ по таймауту ----------------------
async def winner_timeout(bot: Bot, user_id: int, history_id: int):
    pending = await db.get_pending_winner()
    if pending and pending['user_id'] == user_id:
        winner_points = await db.get_user_points(user_id)
        if winner_points > 0:
            await db.convert_points_to_coins(user_id, winner_points)
            await bot.send_message(user_id, f"⏰ Время на ответ истекло. Ваши {winner_points} поинтов конвертированы в коины.")
        else:
            await bot.send_message(user_id, "⏰ Время на ответ истекло. Поинтов не было, коины не начислены.")
        await db.clear_pending_winner()
        await bot.send_message(EXCHANGE_GROUP_ID, f"⏰ Победитель @{user_id} не ответил вовремя. Поинты конвертированы в коины.")
        await bot.send_message(ADMIN_ID, f"Победитель {user_id} не ответил. Можно запустить новый раунд вручную.")