from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def main_menu(user_id: int, is_admin: bool = False):
    """Главное меню для обычного пользователя и админа"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Текущий пиар", callback_data="current_round")
    builder.button(text="🎯 Откликнуться на пиар", callback_data="join_round")
    builder.button(text="💰 Мой баланс поинтов", callback_data="my_balance")
    builder.button(text="🏆 Тир-лист", callback_data="leaderboard")
    builder.button(text="📜 Моя статистика", callback_data="my_stats")
    builder.button(text="⚠️ Пожаловаться", callback_data="complaint_menu")
    if is_admin:
        builder.button(text="🛠 Админ-панель", callback_data="admin_panel")
    builder.adjust(2)
    return builder.as_markup()

def admin_panel_menu():
    """Меню админ-панели"""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать новый раунд", callback_data="admin_create_round")
    builder.button(text="⏹ Завершить текущий раунд", callback_data="admin_stop_round")
    builder.button(text="📊 Список пользователей", callback_data="admin_users_list")
    builder.button(text="🚫 Чёрный список", callback_data="admin_blacklist")
    builder.button(text="⚠️ Жалобы", callback_data="admin_complaints")
    builder.button(text="📜 История раундов", callback_data="admin_round_history")
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(2)
    return builder.as_markup()

def round_control_keyboard():
    """Кнопки для управления раундом (после создания админом)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить и запустить", callback_data="admin_confirm_round")
    builder.button(text="❌ Отменить создание", callback_data="admin_cancel_round")
    builder.adjust(1)
    return builder.as_markup()

def join_round_keyboard(round_active: bool):
    """Кнопка для отклика на пиар"""
    if round_active:
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Откликнуться", callback_data="join_round_action")
        builder.button(text="🔙 Назад", callback_data="main_menu")
        builder.adjust(1)
        return builder.as_markup()
    else:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="main_menu")
        return builder.as_markup()

def winner_confirmation_keyboard():
    """Кнопки для победителя: начать пиар или отказаться"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Начать пиар моего канала", callback_data="winner_accept")
    builder.button(text="❌ Отказаться", callback_data="winner_decline")
    builder.adjust(1)
    return builder.as_markup()

def complaint_keyboard():
    """Кнопки для подачи жалобы"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Написать жалобу", callback_data="complaint_write")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def back_to_main_menu():
    """Кнопка возврата в главное меню"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    return builder.as_markup()

def pagination_keyboard(page: int, total_pages: int, prefix: str):
    """Кнопки пагинации для списков (пользователи, история и т.д.)"""
    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text="◀️ Назад", callback_data=f"{prefix}_page_{page-1}")
    if page < total_pages - 1:
        builder.button(text="Вперёд ▶️", callback_data=f"{prefix}_page_{page+1}")
    builder.button(text="🔙 В админ-меню", callback_data="admin_panel")
    builder.adjust(2 if (page > 0 and page < total_pages - 1) else 1)
    return builder.as_markup()

def complaint_resolution_keyboard(complaint_id: int):
    """Кнопки для решения жалобы (админ)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять жалобу", callback_data=f"complaint_accept_{complaint_id}")
    builder.button(text="❌ Отклонить", callback_data=f"complaint_reject_{complaint_id}")
    builder.adjust(1)
    return builder.as_markup()

def user_management_keyboard(user_id: int, is_banned: bool):
    """Кнопки для управления пользователем (забанен/разбан)"""
    builder = InlineKeyboardBuilder()
    if is_banned:
        builder.button(text="🔓 Разбанить", callback_data=f"unban_user_{user_id}")
    else:
        builder.button(text="🔨 Забанить", callback_data=f"ban_user_{user_id}")
    builder.button(text="🔙 Назад", callback_data="admin_users_list")
    builder.adjust(1)
    return builder.as_markup()