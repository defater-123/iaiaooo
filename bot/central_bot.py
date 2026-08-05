#!/usr/bin/env python3
"""
УМНЫЙ БОТ для управления VPS
- Полное управление через команды
- Выполнение команд на сервере
- Поддержка нескольких серверов
"""

import os
import json
import logging
import argparse
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
from threading import Lock

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==================== НАСТРОЙКА ====================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГ ====================
SESSION_FILE = "sessions.json"
CODE_EXPIRE_MINUTES = 10
CLEANUP_INTERVAL = 300  # 5 минут

# ==================== МЕНЕДЖЕР СЕССИЙ ====================
class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}  # code -> data
        self.users: Dict[int, str] = {}      # user_id -> code
        self.trusted: Set[int] = set()       # user_id
        self._lock = Lock()
        self._load()
    
    def _load(self):
        try:
            if os.path.exists(SESSION_FILE):
                with open(SESSION_FILE, 'r') as f:
                    data = json.load(f)
                    self.sessions = data.get('sessions', {})
                    self.trusted = set(data.get('trusted', []))
                    for code, info in self.sessions.items():
                        if info.get('user_id'):
                            self.users[info['user_id']] = code
                    logger.info(f"Загружено {len(self.sessions)} сессий")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
    
    def _save(self):
        try:
            data = {
                'sessions': self.sessions,
                'trusted': list(self.trusted)
            }
            with open(SESSION_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    def add_code(self, code: str, ip: str, password: str) -> bool:
        with self._lock:
            if code in self.sessions:
                return False
            
            self.sessions[code] = {
                'ip': ip,
                'password': password,
                'user_id': 0,
                'created': datetime.now().isoformat(),
                'expires': (datetime.now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat()
            }
            self._save()
            logger.info(f"✅ Код зарегистрирован: {code}")
            return True
    
    def activate(self, code: str, user_id: int) -> Optional[Dict]:
        with self._lock:
            if code not in self.sessions:
                return None
            
            data = self.sessions[code]
            expires = datetime.fromisoformat(data['expires'])
            if datetime.now() > expires:
                del self.sessions[code]
                self._save()
                return None
            
            data['user_id'] = user_id
            self.trusted.add(user_id)
            self.users[user_id] = code
            self._save()
            return {'ip': data['ip'], 'password': data['password']}
    
    def get_user_session(self, user_id: int) -> Optional[Dict]:
        if user_id not in self.users:
            return None
        
        code = self.users[user_id]
        if code in self.sessions:
            data = self.sessions[code]
            expires = datetime.fromisoformat(data['expires'])
            if datetime.now() < expires:
                return {'ip': data['ip'], 'password': data['password']}
        return None
    
    def is_trusted(self, user_id: int) -> bool:
        return user_id in self.trusted
    
    def revoke(self, user_id: int) -> bool:
        with self._lock:
            if user_id in self.users:
                code = self.users[user_id]
                if code in self.sessions:
                    del self.sessions[code]
                del self.users[user_id]
            
            if user_id in self.trusted:
                self.trusted.remove(user_id)
            
            self._save()
            return True
    
    def cleanup(self):
        with self._lock:
            expired = []
            for code, data in self.sessions.items():
                expires = datetime.fromisoformat(data['expires'])
                if datetime.now() > expires:
                    expired.append(code)
            
            for code in expired:
                del self.sessions[code]
                for uid, c in list(self.users.items()):
                    if c == code:
                        del self.users[uid]
                        if uid in self.trusted:
                            self.trusted.remove(uid)
            
            if expired:
                self._save()
    
    def get_stats(self) -> Dict:
        return {
            'sessions': len(self.sessions),
            'trusted': len(self.trusted),
            'users': len(self.users)
        }

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
manager = SessionManager()
SERVER_IP = None
SSH_PASSWORD = None

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def execute_command(command: str) -> str:
    """Выполняет команду на сервере"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout if result.stdout else result.stderr
        if len(output) > 4000:
            output = output[:4000] + "\n\n... (обрезано)"
        return output if output else "✅ Команда выполнена (нет вывода)"
    except subprocess.TimeoutExpired:
        return "⏰ Команда выполнялась более 30 секунд"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def get_server_info() -> str:
    """Получает информацию о сервере"""
    uptime = subprocess.getoutput("uptime -p")
    disk = subprocess.getoutput("df -h / | awk 'NR==2 {print $5 \" использовано (\" $3 \"/\" $2 \")\"}'")
    memory = subprocess.getoutput("free -h | awk 'NR==2 {print $3 \"/\" $2}'")
    cpu = subprocess.getoutput("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    
    return (
        f"🖥️ <b>Информация о сервере</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 IP: <code>{SERVER_IP}</code>\n"
        f"👤 Пользователь: <code>runner</code>\n"
        f"🔑 Пароль: <code>{SSH_PASSWORD}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Время работы: {uptime}\n"
        f"💾 Диск: {disk}\n"
        f"🧠 Память: {memory}\n"
        f"⚡ CPU: {cpu}%\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Сервер активен ~6 часов</i>"
    )

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start - Главное меню"""
    username = update.effective_user.username or "без username"
    user_id = update.effective_chat.id
    
    session = manager.get_user_session(user_id)
    
    if session:
        await update.message.reply_text(
            f"👋 С возвращением, {username}!\n\n"
            f"✅ <b>Ваш сервер активен</b>\n"
            f"🌐 IP: <code>{session['ip']}</code>\n\n"
            f"Используй /help для списка команд",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"👋 Привет, {username}!\n\n"
            f"📌 <b>Как получить VPS:</b>\n"
            f"1. Запусти GitHub Actions в своем репозитории\n"
            f"2. Скопируй код из логов\n"
            f"3. Отправь код мне\n\n"
            f"🔹 <b>Команды:</b>\n"
            f"/help - список всех команд\n"
            f"/status - статус сервера",
            parse_mode='HTML'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help - Список всех команд"""
    user_id = update.effective_chat.id
    is_trusted = manager.is_trusted(user_id)
    
    help_text = (
        "📚 <b>Список команд</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 <b>Основные:</b>\n"
        "/start - Главное меню\n"
        "/help - Это сообщение\n"
        "/status - Статус сервера\n\n"
    )
    
    if is_trusted:
        help_text += (
            "🔹 <b>Управление сервером:</b>\n"
            "/info - Полная информация о сервере\n"
            "/myserver - Данные для SSH\n"
            "/exec &lt;команда&gt; - Выполнить команду\n"
            "/shell - Интерактивная оболочка\n"
            "/revoke - Отозвать доступ\n\n"
            "🔹 <b>Команды для выполнения:</b>\n"
            "<code>/exec ls -la</code> - список файлов\n"
            "<code>/exec df -h</code> - дисковое пространство\n"
            "<code>/exec free -h</code> - память\n"
            "<code>/exec uptime</code> - время работы\n"
            "<code>/exec whoami</code> - текущий пользователь\n"
            "<code>/exec pwd</code> - текущая директория\n\n"
        )
    else:
        help_text += (
            "⚠️ <b>Нет доступа к серверу</b>\n"
            "Отправь код доступа, чтобы получить доступ.\n\n"
        )
    
    help_text += "━" * 30 + "\n"
    help_text += "<i>Бот поддерживает несколько серверов</i>"
    
    await update.message.reply_text(help_text, parse_mode='HTML')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status - Статус сервера (работает даже без доступа)"""
    user_id = update.effective_chat.id
    session = manager.get_user_session(user_id)
    
    stats = manager.get_stats()
    
    status_text = (
        "📊 <b>Статус системы</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ Активных серверов: {stats['sessions']}\n"
        f"👥 Пользователей онлайн: {stats['trusted']}\n"
        f"⏰ Сессии обновляются каждые 6 часов\n"
    )
    
    if session:
        status_text += "\n✅ <b>Ваш сервер активен</b>"
    else:
        status_text += "\n⚠️ <b>У вас нет активного сервера</b>"
    
    await update.message.reply_text(status_text, parse_mode='HTML')

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/info - Полная информация о сервере (только для доверенных)"""
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ Доступ запрещен. Отправьте код для авторизации.",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        get_server_info(),
        parse_mode='HTML'
    )

async def myserver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myserver - Данные для SSH"""
    user_id = update.effective_chat.id
    session = manager.get_user_session(user_id)
    
    if not session:
        await update.message.reply_text(
            "❌ У вас нет активного сервера.\n"
            "Получите код через GitHub Actions.",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        f"🖥️ <b>Данные для подключения</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 IP: <code>{session['ip']}</code>\n"
        f"👤 Пользователь: <code>runner</code>\n"
        f"🔑 Пароль: <code>{session['password']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 <b>SSH команда:</b>\n"
        f"<code>ssh runner@{session['ip']}</code>",
        parse_mode='HTML'
    )

async def exec_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/exec <команда> - Выполнить команду на сервере"""
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ Доступ запрещен. Отправьте код для авторизации.",
            parse_mode='HTML'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите команду!\n"
            "Пример: <code>/exec ls -la</code>",
            parse_mode='HTML'
        )
        return
    
    command = ' '.join(context.args)
    
    # Безопасность: запрещаем опасные команды
    dangerous = ['rm -rf', 'mkfs', 'dd if=', '> /dev', ':(){', 'fork bomb']
    for d in dangerous:
        if d in command.lower():
            await update.message.reply_text(
                "⛔ Команда заблокирована из соображений безопасности",
                parse_mode='HTML'
            )
            return
    
    # Отправляем статус
    status_msg = await update.message.reply_text(
        f"🔄 <b>Выполнение:</b> <code>{command}</code>",
        parse_mode='HTML'
    )
    
    # Выполняем
    output = execute_command(command)
    
    await status_msg.edit_text(
        f"✅ <b>Команда выполнена</b>\n"
        f"<code>{command}</code>\n\n"
        f"```\n{output}\n```",
        parse_mode='HTML'
    )

async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shell - Интерактивная оболочка (сохраняет контекст)"""
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ Доступ запрещен. Отправьте код для авторизации.",
            parse_mode='HTML'
        )
        return
    
    # Создаем клавиатуру с быстрыми командами
    keyboard = [
        [
            InlineKeyboardButton("📂 ls -la", callback_data="shell_ls"),
            InlineKeyboardButton("💾 df -h", callback_data="shell_df"),
        ],
        [
            InlineKeyboardButton("🧠 free -h", callback_data="shell_free"),
            InlineKeyboardButton("⚡ uptime", callback_data="shell_uptime"),
        ],
        [
            InlineKeyboardButton("👤 whoami", callback_data="shell_whoami"),
            InlineKeyboardButton("📁 pwd", callback_data="shell_pwd"),
        ],
        [
            InlineKeyboardButton("🔄 ps aux", callback_data="shell_ps"),
            InlineKeyboardButton("🌐 netstat", callback_data="shell_netstat"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🖥️ <b>Интерактивная оболочка</b>\n\n"
        "Выберите команду или используйте /exec <команда>\n\n"
        "<i>Быстрые команды:</i>",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def shell_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопок в shell"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await query.edit_message_text("⛔ Доступ запрещен")
        return
    
    # Получаем команду из callback_data
    cmd_map = {
        'shell_ls': 'ls -la',
        'shell_df': 'df -h',
        'shell_free': 'free -h',
        'shell_uptime': 'uptime',
        'shell_whoami': 'whoami',
        'shell_pwd': 'pwd',
        'shell_ps': 'ps aux | head -20',
        'shell_netstat': 'netstat -tulpn | head -20',
    }
    
    command = cmd_map.get(query.data, 'ls -la')
    
    await query.edit_message_text(
        f"🔄 <b>Выполнение:</b> <code>{command}</code>",
        parse_mode='HTML'
    )
    
    output = execute_command(command)
    
    await query.edit_message_text(
        f"✅ <b>Команда выполнена</b>\n"
        f"<code>{command}</code>\n\n"
        f"```\n{output}\n```",
        parse_mode='HTML'
    )

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/revoke - Отозвать доступ"""
    user_id = update.effective_chat.id
    
    if manager.revoke(user_id):
        await update.message.reply_text(
            "✅ Доступ отозван.\n"
            "Запустите workflow заново для нового доступа.",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "ℹ️ У вас нет активного доступа.",
            parse_mode='HTML'
        )

# ==================== ОБРАБОТЧИК КОДОВ ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает только коды доступа (БЕЗ ответа на неверный формат)"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    text = update.message.text.strip().upper()
    
    # ⭐ НЕ ОТВЕЧАЕМ НА НЕВЕРНЫЙ ФОРМАТ (чтобы не было спама)
    if len(text) != 8 or not text.isalnum():
        return  # ❌ МОЛЧИМ
    
    # Проверяем, есть ли уже доступ
    if manager.is_trusted(user_id):
        session = manager.get_user_session(user_id)
        if session:
            await update.message.reply_text(
                f"✅ Доступ уже есть!\nIP: <code>{session['ip']}</code>",
                parse_mode='HTML'
            )
        return
    
    # Активируем код
    session = manager.activate(text, user_id)
    
    if session:
        # Сохраняем глобальные переменные для команд
        global SERVER_IP, SSH_PASSWORD
        SERVER_IP = session['ip']
        SSH_PASSWORD = session['password']
        
        await update.message.reply_text(
            f"🎉 <b>ДОСТУП ПРЕДОСТАВЛЕН!</b>\n\n"
            f"🖥️ <b>Информация о сервере:</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌐 IP: <code>{session['ip']}</code>\n"
            f"👤 Пользователь: <code>runner</code>\n"
            f"🔑 Пароль: <code>{session['password']}</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🔹 <b>SSH подключение:</b>\n"
            f"<code>ssh runner@{session['ip']}</code>\n\n"
            f"🔹 <b>Команды для управления:</b>\n"
            f"/help - список всех команд\n"
            f"/info - информация о сервере\n"
            f"/exec - выполнить команду\n\n"
            f"⏱️ Сервер активен ~6 часов",
            parse_mode='HTML'
        )
        logger.info(f"✅ АКТИВИРОВАН: @{username} (ID: {user_id}) код {text}")
    else:
        # ⭐ КОД НЕ НАЙДЕН - МОЛЧИМ (без ответа)
        logger.debug(f"❌ Неверный код от @{username}: {text}")

# ==================== ОЧИСТКА ====================

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    manager.cleanup()

# ==================== ЗАПУСК ====================

def main():
    parser = argparse.ArgumentParser(description='Умный Telegram бот для VPS')
    parser.add_argument('--token', required=True, help='Токен бота')
    parser.add_argument('--admin-id', help='ID администратора')
    parser.add_argument('--add-code', help='Добавить код: code:ip:password')
    args = parser.parse_args()
    
    # Добавление кода из workflow
    if args.add_code:
        parts = args.add_code.split(':')
        if len(parts) == 3:
            code, ip, password = parts
            if manager.add_code(code, ip, password):
                print(f"✅ Код {code} добавлен!")
                return 0
            else:
                print(f"❌ Код {code} уже существует!")
                return 1
        else:
            print("❌ Формат: code:ip:password")
            return 1
    
    # Запуск бота
    if args.admin_id:
        os.environ['ADMIN_CHAT_ID'] = args.admin_id
    
    app = Application.builder().token(args.token).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("myserver", myserver))
    app.add_handler(CommandHandler("exec", exec_command))
    app.add_handler(CommandHandler("shell", shell_command))
    app.add_handler(CommandHandler("revoke", revoke))
    
    # Callback для кнопок
    app.add_handler(CallbackQueryHandler(shell_callback, pattern="^shell_"))
    
    # Обработчик кодов (НЕ ОТВЕЧАЕТ на неверный формат)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Очистка
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_task, interval=CLEANUP_INTERVAL, first=10)
    
    logger.info("🤖 УМНЫЙ БОТ ЗАПУЩЕН!")
    logger.info("📊 Поддерживает команды /help, /status, /info, /exec, /shell")
    app.run_polling()

if __name__ == '__main__':
    main()
