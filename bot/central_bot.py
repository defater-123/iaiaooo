#!/usr/bin/env python3
"""
УМНЫЙ БОТ для управления VPS
- Полное управление через команды
- Выполнение команд без /exec
- Поддержка нескольких серверов
- Мониторинг активности
"""

import os
import json
import logging
import argparse
import subprocess
import re
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
                    logger.info(f"Загружено {len(self.sessions)} сессий, {len(self.trusted)} пользователей")
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
                'expires': (datetime.now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat(),
                'last_activity': datetime.now().isoformat()
            }
            self._save()
            logger.info(f"✅ Код зарегистрирован: {code} для {ip}")
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
            data['activated_at'] = datetime.now().isoformat()
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
        # Считаем активных пользователей за последние 10 минут
        active_users = 0
        for code, data in self.sessions.items():
            if data.get('user_id'):
                last = datetime.fromisoformat(data.get('last_activity', datetime.now().isoformat()))
                if datetime.now() - last < timedelta(minutes=10):
                    active_users += 1
        
        return {
            'sessions': len(self.sessions),
            'trusted': len(self.trusted),
            'users': len(self.users),
            'active_users': active_users,
            'available_codes': len([c for c, d in self.sessions.items() if d.get('user_id') == 0])
        }

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
manager = SessionManager()
user_servers: Dict[int, Dict] = {}  # user_id -> server_data

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def execute_command(command: str) -> str:
    """Выполняет команду на сервере"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            executable='/bin/bash'
        )
        output = result.stdout if result.stdout else result.stderr
        if len(output) > 4000:
            output = output[:4000] + "\n\n... (обрезано)"
        return output if output else "✅ Команда выполнена (нет вывода)"
    except subprocess.TimeoutExpired:
        return "⏰ Команда выполнялась более 30 секунд"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def get_detailed_server_info() -> str:
    """Получает ДЕТАЛЬНУЮ информацию о сервере"""
    
    # Системная информация
    hostname = subprocess.getoutput("hostname")
    kernel = subprocess.getoutput("uname -r")
    os_version = subprocess.getoutput("cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2")
    
    # Аппаратная информация
    cpu_model = subprocess.getoutput("lscpu | grep 'Model name' | cut -d':' -f2 | xargs")
    cpu_cores = subprocess.getoutput("nproc")
    cpu_usage = subprocess.getoutput("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    
    # Память
    memory_total = subprocess.getoutput("free -h | awk 'NR==2 {print $2}'")
    memory_used = subprocess.getoutput("free -h | awk 'NR==2 {print $3}'")
    memory_free = subprocess.getoutput("free -h | awk 'NR==2 {print $4}'")
    
    # Диск
    disk_total = subprocess.getoutput("df -h / | awk 'NR==2 {print $2}'")
    disk_used = subprocess.getoutput("df -h / | awk 'NR==2 {print $3}'")
    disk_free = subprocess.getoutput("df -h / | awk 'NR==2 {print $4}'")
    disk_usage = subprocess.getoutput("df -h / | awk 'NR==2 {print $5}'")
    
    # Сеть
    ip = subprocess.getoutput("curl -s ifconfig.me")
    interfaces = subprocess.getoutput("ip -br addr | grep -v lo | awk '{print $1\": \"$3}'")
    
    # Процессы
    processes = subprocess.getoutput("ps aux | wc -l")
    load_avg = subprocess.getoutput("uptime | awk -F'load average:' '{print $2}' | xargs")
    
    # Время
    uptime = subprocess.getoutput("uptime -p")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Статистика бота
    stats = manager.get_stats()
    
    return (
        f"🖥️ <b>ДЕТАЛЬНАЯ ИНФОРМАЦИЯ О СЕРВЕРЕ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        f"📌 <b>Система:</b>\n"
        f"├─ Хост: <code>{hostname}</code>\n"
        f"├─ ОС: {os_version}\n"
        f"└─ Ядро: <code>{kernel}</code>\n\n"
        
        f"💻 <b>Аппаратное обеспечение:</b>\n"
        f"├─ CPU: {cpu_model}\n"
        f"├─ Ядра: {cpu_cores}\n"
        f"└─ Загрузка: {cpu_usage}%\n\n"
        
        f"🧠 <b>Память:</b>\n"
        f"├─ Всего: {memory_total}\n"
        f"├─ Использовано: {memory_used}\n"
        f"└─ Свободно: {memory_free}\n\n"
        
        f"💾 <b>Диск ( / ):</b>\n"
        f"├─ Всего: {disk_total}\n"
        f"├─ Использовано: {disk_used} ({disk_usage})\n"
        f"└─ Свободно: {disk_free}\n\n"
        
        f"🌐 <b>Сеть:</b>\n"
        f"├─ Публичный IP: <code>{ip}</code>\n"
        f"├─ Интерфейсы:\n"
        f"└─ {interfaces}\n\n"
        
        f"⚡ <b>Нагрузка:</b>\n"
        f"├─ Процессов: {processes}\n"
        f"├─ Load Average: {load_avg}\n"
        f"└─ Время работы: {uptime}\n\n"
        
        f"📊 <b>Статистика бота:</b>\n"
        f"├─ Активных серверов: {stats['sessions']}\n"
        f"├─ Доверенных пользователей: {stats['trusted']}\n"
        f"├─ Активных пользователей (10мин): {stats['active_users']}\n"
        f"├─ Доступных кодов: {stats['available_codes']}\n"
        f"└─ Поиск новых пользователей: 🔍 АКТИВЕН\n\n"
        
        f"⏰ <b>Время:</b>\n"
        f"└─ {current_time}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
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
            f"📌 <b>Как использовать:</b>\n"
            f"• Просто напиши любую команду (например, <code>ls -la</code>)\n"
            f"• Используй /help для списка команд\n\n"
            f"🔹 <b>Быстрые команды:</b>\n"
            f"<code>ls -la</code> - список файлов\n"
            f"<code>df -h</code> - дисковое пространство\n"
            f"<code>free -h</code> - память\n"
            f"<code>uptime</code> - время работы",
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
            f"/status - статус системы\n"
            f"/info - информация о сервере",
            parse_mode='HTML'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help - Список всех команд"""
    user_id = update.effective_chat.id
    is_trusted = manager.is_trusted(user_id)
    
    help_text = (
        "📚 <b>СПИСОК КОМАНД</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 <b>Основные команды:</b>\n"
        "/start - Главное меню\n"
        "/help - Это сообщение\n"
        "/status - Статус системы\n"
        "/info - Детальная информация о сервере\n\n"
    )
    
    if is_trusted:
        help_text += (
            "🔹 <b>Управление сервером:</b>\n"
            "/myserver - Данные для SSH\n"
            "/shell - Интерактивная оболочка\n"
            "/revoke - Отозвать доступ\n\n"
            "🔹 <b>Выполнение команд (без /exec):</b>\n"
            "Просто напиши любую команду!\n"
            "Примеры:\n"
            "• <code>ls -la</code> - список файлов\n"
            "• <code>df -h</code> - дисковое пространство\n"
            "• <code>free -h</code> - память\n"
            "• <code>uptime</code> - время работы\n"
            "• <code>whoami</code> - текущий пользователь\n"
            "• <code>pwd</code> - текущая директория\n"
            "• <code>ps aux</code> - процессы\n"
            "• <code>netstat -tulpn</code> - порты\n\n"
        )
    else:
        help_text += (
            "⚠️ <b>Нет доступа к серверу</b>\n"
            "Отправь код доступа, чтобы получить доступ.\n\n"
        )
    
    help_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    help_text += "<i>Бот поддерживает несколько серверов</i>"
    
    await update.message.reply_text(help_text, parse_mode='HTML')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status - Статус системы"""
    user_id = update.effective_chat.id
    session = manager.get_user_session(user_id)
    stats = manager.get_stats()
    
    status_text = (
        "📊 <b>СТАТУС СИСТЕМЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖥️ Активных серверов: {stats['sessions']}\n"
        f"👥 Доверенных пользователей: {stats['trusted']}\n"
        f"🟢 Активных сейчас: {stats['active_users']}\n"
        f"🔑 Доступных кодов: {stats['available_codes']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Поиск новых пользователей: 🔍 АКТИВЕН\n"
        f"⏰ Сессии обновляются каждые 6 часов\n"
    )
    
    if session:
        status_text += "\n✅ <b>Ваш сервер активен</b>"
    else:
        status_text += "\n⚠️ <b>У вас нет активного сервера</b>"
    
    await update.message.reply_text(status_text, parse_mode='HTML')

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/info - Детальная информация о сервере"""
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ Доступ запрещен. Отправьте код для авторизации.",
            parse_mode='HTML'
        )
        return
    
    info = get_detailed_server_info()
    await update.message.reply_text(info, parse_mode='HTML')

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
        f"🖥️ <b>ДАННЫЕ ДЛЯ ПОДКЛЮЧЕНИЯ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 IP: <code>{session['ip']}</code>\n"
        f"👤 Пользователь: <code>runner</code>\n"
        f"🔑 Пароль: <code>{session['password']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 <b>SSH команда:</b>\n"
        f"<code>ssh runner@{session['ip']}</code>\n\n"
        f"🔹 <b>Или просто пиши команды в чат!</b>\n"
        f"Например: <code>ls -la</code>",
        parse_mode='HTML'
    )

async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shell - Интерактивная оболочка"""
    user_id = update.effective_chat.id
    
    if not manager.is_trusted(user_id):
        await update.message.reply_text(
            "⛔ Доступ запрещен. Отправьте код для авторизации.",
            parse_mode='HTML'
        )
        return
    
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
        [
            InlineKeyboardButton("📊 top -bn1", callback_data="shell_top"),
            InlineKeyboardButton("💻 uname -a", callback_data="shell_uname"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🖥️ <b>ИНТЕРАКТИВНАЯ ОБОЛОЧКА</b>\n\n"
        "Выберите команду или просто напишите её в чат!\n\n"
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
    
    cmd_map = {
        'shell_ls': 'ls -la',
        'shell_df': 'df -h',
        'shell_free': 'free -h',
        'shell_uptime': 'uptime',
        'shell_whoami': 'whoami',
        'shell_pwd': 'pwd',
        'shell_ps': 'ps aux | head -20',
        'shell_netstat': 'netstat -tulpn | head -20',
        'shell_top': 'top -bn1 | head -20',
        'shell_uname': 'uname -a',
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

# ==================== ОБРАБОТЧИК КОДОВ И КОМАНД ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает коды и команды (БЕЗ /exec)"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    text = update.message.text.strip()
    
    # Проверяем, не является ли сообщение кодом доступа
    text_upper = text.upper()
    if len(text_upper) == 8 and text_upper.isalnum():
        # Это код доступа
        if manager.is_trusted(user_id):
            session = manager.get_user_session(user_id)
            if session:
                await update.message.reply_text(
                    f"✅ Доступ уже есть!\nIP: <code>{session['ip']}</code>",
                    parse_mode='HTML'
                )
            return
        
        session = manager.activate(text_upper, user_id)
        
        if session:
            # Сохраняем данные для команд
            user_servers[user_id] = session
            
            await update.message.reply_text(
                f"🎉 <b>ДОСТУП ПРЕДОСТАВЛЕН!</b>\n\n"
                f"🖥️ <b>Информация о сервере:</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 IP: <code>{session['ip']}</code>\n"
                f"👤 Пользователь: <code>runner</code>\n"
                f"🔑 Пароль: <code>{session['password']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🔹 <b>Теперь можно:</b>\n"
                f"• Писать команды прямо в чат: <code>ls -la</code>\n"
                f"• Использовать /info для детальной информации\n"
                f"• Использовать /shell для интерактивной оболочки\n\n"
                f"⏱️ Сервер активен ~6 часов",
                parse_mode='HTML'
            )
            logger.info(f"✅ АКТИВИРОВАН: @{username} (ID: {user_id}) код {text_upper}")
        else:
            # Код не найден - молчим
            logger.debug(f"❌ Неверный код от @{username}: {text_upper}")
        return
    
    # ⭐ ОБРАБОТКА КОМАНД (без /exec)
    if manager.is_trusted(user_id):
        # Проверяем, не является ли это командой бота
        if text.startswith('/'):
            return  # Пропускаем, обработано другими handlers
        
        # ⭐ Выполняем команду на сервере
        await update.message.reply_text(
            f"🔄 <b>Выполнение:</b> <code>{text}</code>",
            parse_mode='HTML'
        )
        
        output = execute_command(text)
        
        # Проверяем на ошибки
        if "Ошибка" in output:
            await update.message.reply_text(
                f"❌ <b>Ошибка выполнения</b>\n\n"
                f"```\n{output}\n```",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"✅ <b>Команда выполнена</b>\n"
                f"<code>{text}</code>\n\n"
                f"```\n{output}\n```",
                parse_mode='HTML'
            )
    else:
        # Не доверенный пользователь - проверяем, может это команда?
        if text.startswith('/'):
            return  # Пропускаем команды
        else:
            # Не код и не команда - игнорируем (молчим)
            logger.debug(f"ℹ️ Игнорируем сообщение от @{username}: {text[:20]}...")

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
    app.add_handler(CommandHandler("shell", shell_command))
    app.add_handler(CommandHandler("revoke", revoke))
    
    # Callback для кнопок
    app.add_handler(CallbackQueryHandler(shell_callback, pattern="^shell_"))
    
    # Основной обработчик (коды + команды)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Очистка
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_task, interval=CLEANUP_INTERVAL, first=10)
    
    logger.info("🤖 УМНЫЙ БОТ ЗАПУЩЕН!")
    logger.info("📊 Поддерживает команды: /help, /status, /info, /shell")
    logger.info("📝 Просто пиши команды в чат (без /exec)!")
    app.run_polling()

if __name__ == '__main__':
    main()
