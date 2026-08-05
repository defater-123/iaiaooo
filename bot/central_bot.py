#!/usr/bin/env python3
"""
УМНЫЙ БОТ для управления VPS с поддержкой аддонов
"""

import os
import json
import logging
import argparse
import subprocess
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Set, Optional, List
from threading import Lock

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from addon_loader import AddonManager

# ==================== НАСТРОЙКА ====================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ПУТИ ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVES_DIR = os.path.join(BASE_DIR, "Saves")
USER_DIR = os.path.join(SAVES_DIR, "user")
SESSION_FILE = os.path.join(SAVES_DIR, "sessions.json")

os.makedirs(USER_DIR, exist_ok=True)
os.makedirs(SAVES_DIR, exist_ok=True)

# ==================== КОНФИГ ====================
CODE_EXPIRE_MINUTES = 10
CLEANUP_INTERVAL = 300

# ==================== ЗАГРУЗКА АДДОНОВ ====================
addon_manager = AddonManager()

# ==================== GIT ФУНКЦИИ ====================

def git_commit_and_push(file_path: str) -> bool:
    try:
        subprocess.run(['git', 'config', '--global', 'user.email', 'vps-bot@github.com'], 
                      capture_output=True, check=False)
        subprocess.run(['git', 'config', '--global', 'user.name', 'VPS Bot'], 
                      capture_output=True, check=False)
        
        status = subprocess.run(['git', 'status', '--porcelain', file_path], 
                               capture_output=True, text=True)
        if not status.stdout:
            return True
        
        subprocess.run(['git', 'add', file_path], capture_output=True, check=False)
        subprocess.run(['git', 'commit', '-m', f'Сохранить пользователя {os.path.basename(file_path)}'], 
                      capture_output=True, check=False)
        subprocess.run(['git', 'push'], capture_output=True, check=False)
        return True
    except Exception as e:
        logger.error(f"Ошибка git: {e}")
        return False

# ==================== РАБОТА С ФАЙЛАМИ ====================

def get_user_file(user_id: int) -> str:
    return os.path.join(USER_DIR, f"{user_id}.txt")

def save_user_data(user_id: int, data: Dict) -> None:
    try:
        file_path = get_user_file(user_id)
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Сохранен пользователь {user_id}")
        git_commit_and_push(file_path)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def load_user_data(user_id: int) -> Optional[Dict]:
    try:
        file_path = get_user_file(user_id)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
    return None

def delete_user_data(user_id: int) -> bool:
    try:
        file_path = get_user_file(user_id)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Удален пользователь {user_id}")
            subprocess.run(['git', 'add', file_path], capture_output=True, check=False)
            subprocess.run(['git', 'commit', '-m', f'Удалить пользователя {user_id}'], 
                          capture_output=True, check=False)
            subprocess.run(['git', 'push'], capture_output=True, check=False)
            return True
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
    return False

def get_all_users() -> List[int]:
    users = []
    try:
        for file in os.listdir(USER_DIR):
            if file.endswith('.txt'):
                try:
                    user_id = int(file.replace('.txt', ''))
                    users.append(user_id)
                except:
                    pass
    except Exception as e:
        logger.error(f"Ошибка списка пользователей: {e}")
    return users

def get_user_stats() -> Dict:
    users = get_all_users()
    active = 0
    for user_id in users:
        data = load_user_data(user_id)
        if data and data.get('active', False):
            expires = datetime.fromisoformat(data.get('expires', datetime.now().isoformat()))
            if datetime.now() < expires:
                active += 1
    return {'total': len(users), 'active': active}

# ==================== МЕНЕДЖЕР СЕССИЙ ====================

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
        self.users: Dict[int, str] = {}
        self.trusted: Set[int] = set()
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
            
            for user_id in get_all_users():
                user_data = load_user_data(user_id)
                if user_data and user_data.get('active', False):
                    if user_id not in self.users:
                        code = user_data.get('code')
                        if code and code in self.sessions:
                            self.users[user_id] = code
                            self.trusted.add(user_id)
                            self.sessions[code]['user_id'] = user_id
                            logger.info(f"Восстановлен пользователь {user_id}")
            
            self._save()
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
    
    def _save(self):
        try:
            data = {'sessions': self.sessions, 'trusted': list(self.trusted)}
            with open(SESSION_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    def add_code(self, code: str, ip: str, password: str) -> bool:
        with self._lock:
            if code in self.sessions:
                return False
            expires = (datetime.now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat()
            self.sessions[code] = {
                'ip': ip, 'password': password, 'user_id': 0,
                'created': datetime.now().isoformat(), 'expires': expires
            }
            self._save()
            logger.info(f"Код зарегистрирован: {code}")
            return True
    
    def activate(self, code: str, user_id: int) -> Optional[Dict]:
        with self._lock:
            if code not in self.sessions:
                return None
            data = self.sessions[code]
            if datetime.now() > datetime.fromisoformat(data['expires']):
                del self.sessions[code]
                self._save()
                return None
            
            data['user_id'] = user_id
            data['activated_at'] = datetime.now().isoformat()
            self.trusted.add(user_id)
            self.users[user_id] = code
            self._save()
            
            user_data = {
                'user_id': user_id, 'code': code,
                'ip': data['ip'], 'password': data['password'],
                'active': True, 'activated_at': data['activated_at'],
                'expires': data['expires'], 'last_activity': datetime.now().isoformat()
            }
            save_user_data(user_id, user_data)
            return {'ip': data['ip'], 'password': data['password']}
    
    def get_user_session(self, user_id: int) -> Optional[Dict]:
        if user_id not in self.users:
            return None
        code = self.users[user_id]
        if code in self.sessions:
            data = self.sessions[code]
            if datetime.now() < datetime.fromisoformat(data['expires']):
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
            delete_user_data(user_id)
            return True
    
    def cleanup(self):
        with self._lock:
            expired = []
            for code, data in self.sessions.items():
                if datetime.now() > datetime.fromisoformat(data['expires']):
                    expired.append(code)
            for code in expired:
                user_id = self.sessions[code].get('user_id')
                if user_id:
                    user_data = load_user_data(user_id)
                    if user_data:
                        user_data['active'] = False
                        save_user_data(user_id, user_data)
                    if user_id in self.users:
                        del self.users[user_id]
                    if user_id in self.trusted:
                        self.trusted.remove(user_id)
                del self.sessions[code]
            if expired:
                self._save()
    
    def get_stats(self) -> Dict:
        user_stats = get_user_stats()
        active_users = 0
        for data in self.sessions.values():
            if data.get('user_id'):
                last = datetime.fromisoformat(data.get('last_activity', datetime.now().isoformat()))
                if datetime.now() - last < timedelta(minutes=10):
                    active_users += 1
        return {
            'sessions': len(self.sessions),
            'trusted': len(self.trusted),
            'users': len(self.users),
            'active_users': active_users,
            'available_codes': len([c for c, d in self.sessions.items() if d.get('user_id') == 0]),
            'total_users': user_stats['total'],
            'total_active': user_stats['active']
        }

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
manager = SessionManager()

# ==================== СТРИМИНГ КОМАНД ====================

async def execute_command_streaming(command: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        status_msg = await update.message.reply_text(
            f"🔄 <b>Выполнение:</b> <code>{command}</code>\n\n"
            f"<i>⏳ Ожидание вывода...</i>",
            parse_mode='HTML'
        )
        
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True
        )
        
        output_lines = []
        last_update = time.time()
        
        while True:
            if process.returncode is not None:
                break
            
            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=0.3
                )
                if line:
                    decoded = line.decode('utf-8', errors='ignore').rstrip('\n')
                    if decoded:
                        output_lines.append(decoded + '\n')
            except asyncio.TimeoutError:
                pass
            
            try:
                line = await asyncio.wait_for(
                    process.stderr.readline(),
                    timeout=0.3
                )
                if line:
                    decoded = line.decode('utf-8', errors='ignore').rstrip('\n')
                    if decoded:
                        output_lines.append(decoded + '\n')
            except asyncio.TimeoutError:
                pass
            
            if process.returncode is not None:
                remaining_stdout = await process.stdout.read()
                if remaining_stdout:
                    decoded = remaining_stdout.decode('utf-8', errors='ignore')
                    if decoded:
                        output_lines.append(decoded)
                
                remaining_stderr = await process.stderr.read()
                if remaining_stderr:
                    decoded = remaining_stderr.decode('utf-8', errors='ignore')
                    if decoded:
                        output_lines.append(decoded)
                break
            
            if time.time() - last_update >= 0.5 and output_lines:
                current_output = ''.join(output_lines[-100:])
                if current_output:
                    display_output = current_output
                    if len(display_output) > 3800:
                        display_output = display_output[-3800:]
                    try:
                        await status_msg.edit_text(
                            f"🔄 <b>Выполнение:</b> <code>{command}</code>\n\n"
                            f"```\n{display_output}\n```\n\n"
                            f"<i>⏳ Ещё выполняется... (обновлено)</i>",
                            parse_mode='HTML'
                        )
                    except Exception as e:
                        logger.warning(f"Ошибка обновления: {e}")
                last_update = time.time()
        
        await process.wait()
        full_output = ''.join(output_lines)
        
        if process.returncode == 0:
            final_text = f"✅ <b>Команда выполнена</b>\n<code>{command}</code>\n\n"
        else:
            final_text = f"❌ <b>Команда завершилась с ошибкой</b> (код: {process.returncode})\n<code>{command}</code>\n\n"
        
        if full_output:
            display_output = full_output
            if len(display_output) > 3500:
                display_output = display_output[-3500:]
            final_text += f"```\n{display_output}\n```"
        else:
            final_text += "✅ Команда выполнена (нет вывода)"
        
        try:
            await status_msg.edit_text(final_text, parse_mode='HTML')
        except Exception as e:
            logger.warning(f"Ошибка финального обновления: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка выполнения команды: {e}")
        try:
            await update.message.reply_text(
                f"❌ <b>Ошибка выполнения</b>\n"
                f"<code>{command}</code>\n\n"
                f"```\n{str(e)}\n```",
                parse_mode='HTML'
            )
        except:
            pass

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    session = manager.get_user_session(user_id)
    
    if session:
        await update.message.reply_text(
            f"👋 С возвращением, {username}!\n\n"
            f"✅ <b>Ваш сервер активен</b>\n"
            f"🌐 IP: <code>{session['ip']}</code>\n\n"
            f"📌 Просто пиши команды или используй /addons\n\n"
            f"🔹 Команды: /help, /status, /info, /addons",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"👋 Привет, {username}!\n\n"
            f"📌 <b>Как получить VPS:</b>\n"
            f"1. Запусти GitHub Actions\n"
            f"2. Скопируй код из логов\n"
            f"3. Отправь код мне\n\n"
            f"🔹 Команды: /help, /status, /info, /addons",
            parse_mode='HTML'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    is_trusted = manager.is_trusted(user_id)
    
    help_text = (
        "📚 <b>СПИСОК КОМАНД</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 <b>Основные:</b>\n"
        "/start - Главное меню\n"
        "/help - Это сообщение\n"
        "/status - Статус системы\n"
        "/info - Информация о сервере\n"
        "/addons - Управление аддонами\n"
    )
    
    if is_trusted:
        help_text += (
            "\n🔹 <b>Управление:</b>\n"
            "/myserver - Данные для SSH\n"
            "/revoke - Отозвать доступ\n\n"
            "🔹 <b>Аддоны:</b>\n"
            "/addons - Показать все аддоны\n"
            "/addon [имя] - Управление аддоном\n\n"
            "🔥 <b>Доступные аддоны:</b>\n"
        )
        
        for addon in addon_manager.get_all_addons():
            status_icon = "🟢" if addon['running'] else "⚪"
            help_text += f"  {status_icon} {addon['name']} - {addon['description']}\n"
    else:
        help_text += "\n⚠️ Отправь код для доступа"
    
    await update.message.reply_text(help_text, parse_mode='HTML')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    session = manager.get_user_session(user_id)
    stats = manager.get_stats()
    
    addon_stats = addon_manager.get_all_addons()
    running_addons = len([a for a in addon_stats if a['running']])
    
    text = (
        f"📊 <b>СТАТУС СИСТЕМЫ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖥️ Активных серверов: {stats['sessions']}\n"
        f"👥 Доверенных пользователей: {stats['trusted']}\n"
        f"🟢 Активных сейчас: {stats['active_users']}\n"
        f"📁 Пользователей в базе: {stats['total_users']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Аддонов загружено: {len(addon_stats)}\n"
        f"▶️ Аддонов запущено: {running_addons}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Сохранено в: Saves/user/\n"
        f"🔄 Git: активен\n"
        f"🔥 Стриминг: включен\n"
    )
    
    if session:
        text += "\n✅ <b>Ваш сервер активен</b>"
    else:
        text += "\n⚠️ <b>Нет активного сервера</b>"
    
    await update.message.reply_text(text, parse_mode='HTML')

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    if not manager.is_trusted(user_id):
        await update.message.reply_text("⛔ Отправьте код для доступа", parse_mode='HTML')
        return
    
    try:
        addon = addon_manager.get_addon('ip_checker')
        if addon and addon.running:
            public_ip = "🚀 Аддон IP Checker запущен"
        else:
            public_ip = "Не удалось получить (аддон не загружен)"
    except:
        public_ip = "Не удалось получить (аддон не загружен)"
    
    hostname = subprocess.getoutput("hostname")
    kernel = subprocess.getoutput("uname -r")
    os_version = subprocess.getoutput("cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2")
    cpu_model = subprocess.getoutput("lscpu | grep 'Model name' | cut -d':' -f2 | xargs")
    cpu_cores = subprocess.getoutput("nproc")
    cpu_usage = subprocess.getoutput("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    memory = subprocess.getoutput("free -h | awk 'NR==2 {print $2\" / \"$3\" (использовано)\"}'")
    disk = subprocess.getoutput("df -h / | awk 'NR==2 {print $2\" / \"$3\" (\"$5\" использовано)\"}'")
    uptime = subprocess.getoutput("uptime -p")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = manager.get_stats()
    
    text = (
        f"🖥️ <b>ИНФОРМАЦИЯ О СЕРВЕРЕ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>Система:</b>\n"
        f"├─ ОС: {os_version}\n"
        f"├─ Ядро: {kernel}\n"
        f"└─ Хост: {hostname}\n\n"
        f"💻 <b>CPU:</b>\n"
        f"├─ Модель: {cpu_model}\n"
        f"├─ Ядра: {cpu_cores}\n"
        f"└─ Загрузка: {cpu_usage}%\n\n"
        f"🧠 <b>Память:</b> {memory}\n"
        f"💾 <b>Диск:</b> {disk}\n\n"
        f"🌐 <b>Публичный IP:</b> <code>{public_ip}</code>\n"
        f"⏱️ <b>Время работы:</b> {uptime}\n"
        f"⏰ <b>Текущее время:</b> {current_time}\n\n"
        f"📊 <b>Статистика бота:</b>\n"
        f"├─ Активных серверов: {stats['sessions']}\n"
        f"├─ Доверенных пользователей: {stats['trusted']}\n"
        f"├─ Пользователей в базе: {stats['total_users']}\n"
        f"└─ Сохранено в: Saves/user/\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Сервер активен ~6 часов</i>"
    )
    
    await update.message.reply_text(text, parse_mode='HTML')

async def myserver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    session = manager.get_user_session(user_id)
    if not session:
        await update.message.reply_text("❌ Нет активного сервера", parse_mode='HTML')
        return
    await update.message.reply_text(
        f"🖥️ <b>ДАННЫЕ ДЛЯ SSH</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 IP: <code>{session['ip']}</code>\n"
        f"👤 Пользователь: <code>runner</code>\n"
        f"🔑 Пароль: <code>{session['password']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"SSH: <code>ssh runner@{session['ip']}</code>",
        parse_mode='HTML'
    )

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    if manager.revoke(user_id):
        await update.message.reply_text("✅ Доступ отозван", parse_mode='HTML')
    else:
        await update.message.reply_text("ℹ️ Нет активного доступа", parse_mode='HTML')

# ==================== АДДОНЫ ====================

async def addons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список аддонов"""
    user_id = update.effective_chat.id
    if not manager.is_trusted(user_id):
        await update.message.reply_text("⛔ Отправьте код для доступа", parse_mode='HTML')
        return
    
    addons = addon_manager.get_all_addons()
    
    if not addons:
        await update.message.reply_text(
            "📦 <b>Аддоны не найдены</b>\n\n"
            "Создайте папку в bot/addons/ и добавьте addon.conf",
            parse_mode='HTML'
        )
        return
    
    keyboard = []
    for addon in addons:
        status_icon = "▶️" if addon['running'] else "⏸️"
        keyboard.append([
            InlineKeyboardButton(
                f"{status_icon} {addon['name']}",
                callback_data=f"addon_{addon['folder']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="addons_refresh")])
    
    text = (
        f"📦 <b>ДОСТУПНЫЕ АДДОНЫ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for addon in addons:
        status_text = "▶️ Запущен" if addon['running'] else "⏸️ Остановлен"
        text += (
            f"📌 <b>{addon['name']}</b>\n"
            f"├─ 📝 {addon['description']}\n"
            f"├─ 📊 Статус: {status_text}\n"
            f"├─ 👤 Автор: {addon['author']}\n"
            f"└─ 📂 /addon {addon['folder']}\n\n"
        )
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def addon_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Управление конкретным аддоном"""
    user_id = update.effective_chat.id
    if not manager.is_trusted(user_id):
        await update.message.reply_text("⛔ Отправьте код для доступа", parse_mode='HTML')
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя аддона\n"
            "Пример: <code>/addon ip_checker</code>\n"
            "Используйте /addons для списка",
            parse_mode='HTML'
        )
        return
    
    folder = context.args[0].lower()
    
    aliases = {
        'checker': 'ip_checker',
        'ip': 'ip_checker',
        'ipchecker': 'ip_checker',
        'myip': 'ip_checker'
    }
    
    if folder in aliases:
        folder = aliases[folder]
    
    addon = addon_manager.get_addon(folder)
    
    if not addon:
        available = [a['folder'] for a in addon_manager.get_all_addons()]
        if available:
            await update.message.reply_text(
                f"❌ Аддон <b>{context.args[0]}</b> не найден\n\n"
                f"📂 Доступные аддоны:\n"
                f"{', '.join(available)}\n\n"
                f"Используйте /addons для списка с кнопками",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"❌ Аддон <b>{context.args[0]}</b> не найден\n\n"
                "📦 Аддоны не загружены. Проверьте папку bot/addons/",
                parse_mode='HTML'
            )
        return
    
    info = addon.get_info()
    
    keyboard = [
        [
            InlineKeyboardButton("▶️ Запустить", callback_data=f"addon_start_{folder}"),
            InlineKeyboardButton("⏹️ Остановить", callback_data=f"addon_stop_{folder}")
        ],
        [
            InlineKeyboardButton("🔄 Статус", callback_data=f"addon_status_{folder}"),
            InlineKeyboardButton("📋 Инфо", callback_data=f"addon_info_{folder}")
        ],
        [InlineKeyboardButton("⬅️ Назад к аддонам", callback_data="addons_back")]
    ]
    
    status_text = "▶️ Запущен" if info['running'] else "⏸️ Остановлен"
    status_icon = "🟢" if info['running'] else "⚪"
    
    text = (
        f"{status_icon} <b>{info['name']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 {info['description']}\n"
        f"📊 Статус: {status_text}\n"
        f"📌 Версия: {info['version']}\n"
        f"👤 Автор: {info['author']}\n"
        f"📂 Папка: {folder}\n"
        f"📋 Команды: {', '.join(info.get('commands', []))}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def addon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопок аддонов"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_chat.id
    if not manager.is_trusted(user_id):
        await query.edit_message_text("⛔ Доступ запрещен", parse_mode='HTML')
        return
    
    data = query.data
    
    if data == "addons_back":
        await addons_command(update, context)
        return
    
    if data == "addons_refresh":
        addon_manager.reload()
        await addons_command(update, context)
        return
    
    parts = data.split('_', 2)
    
    if len(parts) < 2:
        await query.edit_message_text("❌ Неизвестная команда", parse_mode='HTML')
        return
    
    # Формат: addon_ip_checker (открыть меню)
    if len(parts) == 2:
        folder = parts[1]
        addon = addon_manager.get_addon(folder)
        if not addon:
            await query.edit_message_text(f"❌ Аддон {folder} не найден", parse_mode='HTML')
            return
        
        info = addon.get_info()
        
        keyboard = [
            [
                InlineKeyboardButton("▶️ Запустить", callback_data=f"addon_start_{folder}"),
                InlineKeyboardButton("⏹️ Остановить", callback_data=f"addon_stop_{folder}")
            ],
            [
                InlineKeyboardButton("🔄 Статус", callback_data=f"addon_status_{folder}"),
                InlineKeyboardButton("📋 Инфо", callback_data=f"addon_info_{folder}")
            ],
            [InlineKeyboardButton("⬅️ Назад к аддонам", callback_data="addons_back")]
        ]
        
        status_text = "▶️ Запущен" if info['running'] else "⏸️ Остановлен"
        status_icon = "🟢" if info['running'] else "⚪"
        
        text = (
            f"{status_icon} <b>{info['name']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 {info['description']}\n"
            f"📊 Статус: {status_text}\n"
            f"📌 Версия: {info['version']}\n"
            f"👤 Автор: {info['author']}\n"
            f"📂 Папка: {folder}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        
        await query.edit_message_text(
            text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Формат: addon_start_ip_checker, addon_stop_ip_checker и т.д.
    if len(parts) == 3:
        action = parts[1]
        folder = parts[2]
        
        addon = addon_manager.get_addon(folder)
        if not addon:
            await query.edit_message_text(f"❌ Аддон {folder} не найден", parse_mode='HTML')
            return
        
        if action == 'start':
            result = addon.start()
            await query.edit_message_text(
                f"🔄 <b>Запуск аддона</b>\n\n"
                f"📦 {addon.config['name']}\n"
                f"📊 Статус: {result.get('status', 'unknown')}\n"
                f"💬 {result.get('message', '')}",
                parse_mode='HTML'
            )
        elif action == 'stop':
            result = addon.stop()
            await query.edit_message_text(
                f"⏹️ <b>Остановка аддона</b>\n\n"
                f"📦 {addon.config['name']}\n"
                f"📊 Статус: {result.get('status', 'unknown')}\n"
                f"💬 {result.get('message', '')}",
                parse_mode='HTML'
            )
        elif action == 'status':
            result = addon.status()
            await query.edit_message_text(
                f"🔄 <b>Статус аддона</b>\n\n"
                f"📦 {addon.config['name']}\n"
                f"📊 Статус: {result.get('status', 'unknown')}",
                parse_mode='HTML'
            )
        elif action == 'info':
            info = addon.get_info()
            text = (
                f"📋 <b>Информация об аддоне</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📌 Название: {info['name']}\n"
                f"📌 Версия: {info['version']}\n"
                f"📌 Автор: {info['author']}\n"
                f"📌 Описание: {info['description']}\n"
                f"📌 Статус: {'▶️ Запущен' if info['running'] else '⏸️ Остановлен'}\n"
                f"📌 Тип: {info['type']}\n"
                f"📌 Команды: {', '.join(info.get('commands', []))}\n"
                f"📌 Автозапуск: {'Да' if info['startup'] == 'yes' else 'Нет'}"
            )
            await query.edit_message_text(text, parse_mode='HTML')
        else:
            await query.edit_message_text(f"❌ Неизвестное действие: {action}", parse_mode='HTML')
        return
    
    await query.edit_message_text("❌ Неизвестный формат команды", parse_mode='HTML')

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    text = update.message.text.strip()
    
    text_upper = text.upper()
    if len(text_upper) == 8 and text_upper.isalnum():
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
            await update.message.reply_text(
                f"🎉 <b>ДОСТУП ПРЕДОСТАВЛЕН!</b>\n\n"
                f"🌐 IP: <code>{session['ip']}</code>\n"
                f"🔑 Пароль: <code>{session['password']}</code>\n\n"
                f"SSH: <code>ssh runner@{session['ip']}</code>\n\n"
                f"💾 Сохранено в: Saves/user/{user_id}.txt\n"
                f"📌 Команды: /help, /addons\n"
                f"🔥 Все команды показывают прогресс в реальном времени!",
                parse_mode='HTML'
            )
            logger.info(f"АКТИВИРОВАН: @{username} (ID: {user_id}) код {text_upper}")
        return
    
    if manager.is_trusted(user_id):
        if text.startswith('/'):
            return
        
        user_data = load_user_data(user_id)
        if user_data:
            user_data['last_activity'] = datetime.now().isoformat()
            save_user_data(user_id, user_data)
        
        await execute_command_streaming(text, update, context)

# ==================== ОЧИСТКА ====================

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    manager.cleanup()

# ==================== ЗАПУСК ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True, help='Токен бота')
    parser.add_argument('--admin-id', help='ID администратора')
    parser.add_argument('--add-code', help='Добавить код: code:ip:password')
    args = parser.parse_args()
    
    if args.add_code:
        parts = args.add_code.split(':')
        if len(parts) == 3:
            code, ip, password = parts
            if manager.add_code(code, ip, password):
                print(f"Код {code} добавлен!")
                return 0
            else:
                print(f"Код {code} уже существует!")
                return 1
        else:
            print("Формат: code:ip:password")
            return 1
    
    if args.admin_id:
        os.environ['ADMIN_CHAT_ID'] = args.admin_id
    
    addon_manager.start_all_startup()
    
    app = Application.builder().token(args.token).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("myserver", myserver))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("addons", addons_command))
    app.add_handler(CommandHandler("addon", addon_command))
    
    app.add_handler(CallbackQueryHandler(addon_callback, pattern="^addon_"))
    app.add_handler(CallbackQueryHandler(addon_callback, pattern="^addons_"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_task, interval=CLEANUP_INTERVAL, first=10)
    
    logger.info("БОТ ЗАПУЩЕН!")
    logger.info(f"Пользователи сохраняются в: {USER_DIR}")
    logger.info(f"Аддонов загружено: {len(addon_manager.get_all_addons())}")
    logger.info("Просто пиши команды в чат - всё через стриминг!")
    app.run_polling()

if __name__ == '__main__':
    main()
