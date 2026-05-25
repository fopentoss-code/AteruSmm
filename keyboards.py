from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def main_menu(user_id: int, is_admin: bool = False):
    """Главное меню для обычного пользователя и админа"""
    builder = InlineKeyboardBuilder()
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

def winner_confirmation_keyboard():
    """Кнопки для победителя: начать пиар или отказаться"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Начать пиар моего канала", callback_data="winner_accept")
    builder.button(text="❌ Отказаться", callback_data="winner_decline")
    builder.adjust(1)
    return builder.as_markup()

def complaint_resolution_keyboard(complaint_id: int):
    """Кнопки для решения жалобы (админ)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять жалобу", callback_data=f"complaint_accept_{complaint_id}")
    builder.button(text="❌ Отклонить", callback_data=f"complaint_reject_{complaint_id}")
    builder.adjust(1)
    return builder.as_markup()

def back_to_main_menu():
    """Кнопка возврата в главное меню"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    return builder.as_markup()

def balance_button():
    """Кнопка для отображения баланса (используется в итогах раунда)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Мой баланс", callback_data="my_balance")
    return builder.as_markup()

def user_stats_keyboard():
    """Клавиатура для личных сообщений с кнопками обновления"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 Тир-лист", callback_data="leaderboard")
    builder.button(text="📊 Моя статистика", callback_data="my_stats")
    builder.button(text="🔄 Обновить тир-лист", callback_data="refresh_leaderboard")
    builder.button(text="🔄 Обновить статистику", callback_data="refresh_stats")
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(2)
    return builder.as_markup()