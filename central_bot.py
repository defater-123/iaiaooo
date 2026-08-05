#!/usr/bin/env python3
"""
Центральный Telegram Бот для VPS
Режим: "Спящий" - слушает только коды, активируется только для валидных пользователей
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
from threading import Lock

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
MAX_ACTIVE_SESSIONS = 50  # Максимум одновременных сессий
CODE_EXPIRE_MINUTES = 10  # Код живет 10 минут после регистрации
CLEANUP_INTERVAL = 300    # Очистка каждые 5 минут

# ==================== ГЛОБАЛЬНЫЕ ДАННЫЕ ====================
class VPSManager:
    """Менеджер VPS сессий с потокобезопасностью"""
    
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}  # code -> session_data
        self.trusted_users: Set[int] = set()        # user_ids с активным доступом
        self.user_sessions: Dict[int, str] = {}     # user_id -> code
        self._lock = Lock()
        self._load_data()
    
    def _load_data(self):
        """Загружает данные из файлов"""
        try:
            # Загружаем активные сессии
            if os.path.exists('active_sessions.json'):
                with open('active_sessions.json', 'r') as f:
                    self.active_sessions = json.load(f)
                    logger.info(f"Загружено {len(self.active_sessions)} сессий")
            
            # Загружаем доверенных пользователей
            if os.path.exists('trusted_users.json'):
                with open('trusted_users.json', 'r') as f:
                    users = json.load(f)
                    self.trusted_users = set(users)
                    logger.info(f"Загружено {len(self.trusted_users)} доверенных пользователей")
            
            # Восстанавливаем user_sessions
            for code, data in self.active_sessions.items():
                user_id = data.get('user_id')
                if user_id:
                    self.user_sessions[user_id] = code
                    
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
    def _save_data(self):
        """Сохраняет данные в файлы"""
        try:
            with open('active_sessions.json', 'w') as f:
                json.dump(self.active_sessions, f, indent=2)
            
            with open('trusted_users.json', 'w') as f:
                json.dump(list(self.trusted_users), f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    def register_code(self, code: str, user_id: int, ip: str, password: str) -> bool:
        """Регистрирует новый код доступа"""
        with self._lock:
            # Проверяем лимит
            if len(self.active_sessions) >= MAX_ACTIVE_SESSIONS:
                logger.warning(f"Достигнут лимит сессий ({MAX_ACTIVE_SESSIONS})")
                return False
            
            # Удаляем старые сессии пользователя
            if user_id in self.user_sessions:
                old_code = self.user_sessions[user_id]
                if old_code in self.active_sessions:
                    del self.active_sessions[old_code]
                del self.user_sessions[user_id]
            
            # Добавляем новую сессию
            self.active_sessions[code] = {
                "user_id": user_id,
                "ip": ip,
                "password": password,
                "created_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat()
            }
            self.user_sessions[user_id] = code
            
            self._save_data()
            logger.info(f"Зарегистрирован код {code} для пользователя {user_id}")
            return True
    
    def validate_code(self, code: str, user_id: int) -> Optional[Dict]:
        """Проверяет и активирует код"""
        with self._lock:
            if code not in self.active_sessions:
                return None
            
            session = self.active_sessions[code]
            
            # Проверяем срок действия
            expires_at = datetime.fromisoformat(session['expires_at'])
            if datetime.now() > expires_at:
                # Код истек
                del self.active_sessions[code]
                if user_id in self.user_sessions:
                    del self.user_sessions[user_id]
                self._save_data()
                return None
            
            # Добавляем пользователя в доверенные
            self.trusted_users.add(user_id)
            
            # Возвращаем данные
            return {
                "ip": session['ip'],
                "password": session['password']
            }
    
    def revoke_access(self, user_id: int) -> bool:
        """Отзывает доступ у пользователя"""
        with self._lock:
            if user_id in self.user_sessions:
                code = self.user_sessions[user_id]
                if code in self.active_sessions:
                    del self.active_sessions[code]
                del self.user_sessions[user_id]
            
            if user_id in self.trusted_users:
                self.trusted_users.remove(user_id)
            
            self._save_data()
            return True
    
    def is_trusted(self, user_id: int) -> bool:
        """Проверяет, есть ли у пользователя активный доступ"""
        return user_id in self.trusted_users
    
    def cleanup_expired(self):
        """Очищает истекшие сессии"""
        with self._lock:
            expired_codes = []
            for code, data in self.active_sessions.items():
                expires_at = datetime.fromisoformat(data['expires_at'])
                if datetime.now() > expires_at:
                    expired_codes.append(code)
            
            for code in expired_codes:
                user_id = self.active_sessions[code].get('user_id')
                if user_id in self.user_sessions:
                    del self.user_sessions[user_id]
                del self.active_sessions[code]
            
            if expired_codes:
                self._save_data()
                logger.info(f"Удалено {len(expired_codes)} истекших сессий")
    
    def get_active_count(self) -> int:
        """Возвращает количество активных сессий"""
        return len(self.active_sessions)


# ==================== ИНИЦИАЛИЗАЦИЯ ====================
manager = VPSManager()
BOT_USERNAME = os.getenv('BOT_USERNAME', 'YourVPSCodeBot')

# ==================== ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start - минимальное приветствие"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    
    # Проверяем, есть ли у пользователя активный доступ
    if manager.is_trusted(user_id):
        # Если есть доступ - сразу даем информацию
        code = manager.user_sessions.get(user_id)
        if code and code in manager.active_sessions:
            session = manager.active_sessions[code]
            await update.message.reply_text(
                f"✅ <b>Доступ уже есть!</b>\n\n"
                f"🖥️ <b>Ваш сервер:</b>\n"
                f"🌐 IP: <code>{session['ip']}</code>\n"
                f"👤 Пользователь: <code>runner</code>\n"
                f"🔑 Пароль: <code>{session['password']}</code>\n\n"
                f"Подключение: <code>ssh runner@{session['ip']}</code>",
                parse_mode='HTML'
            )
            return
    
    # Если нет доступа - показываем минимальную инструкцию
    await update.message.reply_text(
        f"👋 Привет, {username}!\n\n"
        f"📌 Чтобы получить VPS:\n"
        f"1. Запусти GitHub Actions в своем репозитории\n"
        f"2. Получи код из логов\n"
        f"3. Отправь код мне\n\n"
        f"⚡ <i>Просто отправь код, и я дам доступ</i>",
        parse_mode='HTML'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик сообщений - проверяет только коды доступа"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    text = update.message.text.strip().upper()
    
    # Пропускаем, если это не код (не 8 символов и не все заглавные/цифры)
    if len(text) != 8 or not text.isalnum():
        # Не отвечаем на обычные сообщения (экономия ресурсов)
        logger.debug(f"Пропущено сообщение от {username}: {text[:20]}...")
        return
    
    # Проверяем, есть ли у пользователя уже доступ
    if manager.is_trusted(user_id):
        # Если уже есть доступ - просто даем инфо
        code = manager.user_sessions.get(user_id)
        if code and code in manager.active_sessions:
            session = manager.active_sessions[code]
            await update.message.reply_text(
                f"✅ <b>Доступ уже есть!</b>\n"
                f"IP: <code>{session['ip']}</code>\n"
                f"Пароль: <code>{session['password']}</code>",
                parse_mode='HTML'
            )
        return
    
    # Пытаемся активировать код
    session_data = manager.validate_code(text, user_id)
    
    if session_data:
        # УСПЕШНАЯ АКТИВАЦИЯ - бот "просыпается" для этого пользователя
        await update.message.reply_text(
            f"🎉 <b>ДОСТУП ПРЕДОСТАВЛЕН!</b>\n\n"
            f"🖥️ <b>Информация о сервере:</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌐 IP: <code>{session_data['ip']}</code>\n"
            f"👤 Пользователь: <code>runner</code>\n"
            f"🔑 Пароль: <code>{session_data['password']}</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🔹 <b>SSH подключение:</b>\n"
            f"<code>ssh runner@{session_data['ip']}</code>\n\n"
            f"⏱️ Сервер активен ~5ч 50м\n"
            f"🔄 Авто-перезапуск каждые 5ч 55м\n\n"
            f"<i>Код был активирован и больше не действителен</i>",
            parse_mode='HTML'
        )
        
        logger.info(f"✅ АКТИВИРОВАН: @{username} (ID: {user_id}) через код {text}")
    else:
        # НЕВЕРНЫЙ КОД - бот "спит", не отвечает
        # Но логируем для отладки
        logger.debug(f"❌ Неверный код от @{username}: {text}")


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /info - статистика бота"""
    user_id = update.effective_chat.id
    
    # Только доверенные пользователи могут видеть статистику
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ Доступ запрещен. Сначала получите код доступа.",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"🖥️ Активных серверов: {manager.get_active_count()}\n"
        f"👥 Доверенных пользователей: {len(manager.trusted_users)}\n"
        f"⏰ Время работы: uptime\n\n"
        f"<i>Бот работает в энергосберегающем режиме</i>",
        parse_mode='HTML'
    )


async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /revoke - отозвать доступ"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    
    if manager.revoke_access(user_id):
        await update.message.reply_text(
            f"✅ <b>Доступ отозван</b>\n\n"
            f"Ваш сервер больше не активен.\n"
            f"Чтобы получить новый доступ, запустите workflow заново.",
            parse_mode='HTML'
        )
        logger.info(f"Пользователь @{username} (ID: {user_id}) отозвал доступ")
    else:
        await update.message.reply_text(
            f"ℹ️ У вас нет активного доступа.",
            parse_mode='HTML'
        )


async def my_server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /myserver - показать информацию о своем сервере"""
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ У вас нет активного доступа.\n"
            "Получите код через GitHub Actions.",
            parse_mode='HTML'
        )
        return
    
    code = manager.user_sessions.get(user_id)
    if code and code in manager.active_sessions:
        session = manager.active_sessions[code]
        await update.message.reply_text(
            f"🖥️ <b>Ваш сервер</b>\n\n"
            f"🌐 IP: <code>{session['ip']}</code>\n"
            f"👤 Пользователь: <code>runner</code>\n"
            f"🔑 Пароль: <code>{session['password']}</code>\n\n"
            f"SSH: <code>ssh runner@{session['ip']}</code>\n\n"
            f"⏱️ Активен до: ~5ч 50м",
            parse_mode='HTML'
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status - статус бота (скрытая)"""
    # Проверяем, что это администратор
    ADMIN_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    if update.effective_chat.id != ADMIN_ID:
        return
    
    await update.message.reply_text(
        f"📊 <b>Полная статистика</b>\n\n"
        f"Активных сессий: {manager.get_active_count()}\n"
        f"Доверенных пользователей: {len(manager.trusted_users)}\n"
        f"Всего сессий за всё время: {len(manager.active_sessions)}\n\n"
        f"<b>Активные сессии:</b>\n"
        + "\n".join([f"• {code}: {data['ip']}" for code, data in list(manager.active_sessions.items())[:10]]),
        parse_mode='HTML'
    )


# ==================== ФОНОВЫЕ ЗАДАЧИ ====================

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодическая очистка истекших сессий"""
    manager.cleanup_expired()
    logger.info(f"Очистка выполнена. Активных сессий: {manager.get_active_count()}")


# ==================== MAIN ====================

def main() -> None:
    """Главная функция"""
    parser = argparse.ArgumentParser(description='Центральный Telegram Бот для VPS (спящий режим)')
    parser.add_argument('--token', required=True, help='Токен Telegram бота')
    parser.add_argument('--admin-id', help='ID администратора для статистики')
    
    args = parser.parse_args()
    
    if args.admin_id:
        os.environ['ADMIN_CHAT_ID'] = args.admin_id
    
    # Создаем приложение
    application = Application.builder().token(args.token).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_handler(CommandHandler("myserver", my_server_command))
    application.add_handler(CommandHandler("status", status_command))  # Скрытая команда для админа
    
    # Главный обработчик сообщений - проверяет только коды
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Фоновые задачи
    job_queue = application.job_queue
    if job_queue:
        # Очистка каждые 5 минут
        job_queue.run_repeating(cleanup_task, interval=CLEANUP_INTERVAL, first=10)
    
    # Запускаем бота
    logger.info("🤖 Бот запущен в СПЯЩЕМ режиме")
    logger.info(f"📊 Максимум сессий: {MAX_ACTIVE_SESSIONS}")
    logger.info(f"⏰ Время жизни кода: {CODE_EXPIRE_MINUTES} минут")
    logger.info("Ожидание кодов доступа...")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
