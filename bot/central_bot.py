#!/usr/bin/env python3
"""
УМНЫЙ БОТ - Всё в одном!
- Хранит сессии в памяти
- Автоматически даёт доступ по коду
- Может добавлять коды через аргументы командной строки
- Никаких API не нужно (но поддерживает)
"""

import os
import json
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
from threading import Lock

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

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
        """Загружает сессии из файла"""
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
        """Сохраняет сессии в файл"""
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
        """Добавляет новый код (из workflow или вручную)"""
        with self._lock:
            # Проверяем, не занят ли код
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
            logger.info(f"✅ Код зарегистрирован: {code} для IP {ip}")
            return True
    
    def activate(self, code: str, user_id: int) -> Optional[Dict]:
        """Активирует код для пользователя"""
        with self._lock:
            if code not in self.sessions:
                return None
            
            data = self.sessions[code]
            
            # Проверяем срок
            expires = datetime.fromisoformat(data['expires'])
            if datetime.now() > expires:
                del self.sessions[code]
                self._save()
                logger.info(f"Код {code} истек")
                return None
            
            # Активируем
            data['user_id'] = user_id
            self.trusted.add(user_id)
            self.users[user_id] = code
            self._save()
            
            logger.info(f"✅ Код {code} активирован для пользователя {user_id}")
            return {'ip': data['ip'], 'password': data['password']}
    
    def get_user_session(self, user_id: int) -> Optional[Dict]:
        """Получает сессию пользователя"""
        if user_id not in self.users:
            return None
        
        code = self.users[user_id]
        if code in self.sessions:
            data = self.sessions[code]
            # Проверяем срок
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
            logger.info(f"Доступ отозван для пользователя {user_id}")
            return True
    
    def cleanup(self):
        """Удаляет истекшие сессии"""
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
                logger.info(f"Удалено {len(expired)} истекших сессий")
    
    def get_stats(self) -> Dict:
        return {
            'sessions': len(self.sessions),
            'trusted': len(self.trusted),
            'users': len(self.users)
        }

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
manager = SessionManager()

# ==================== ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = update.effective_user.username or "без username"
    
    session = manager.get_user_session(update.effective_chat.id)
    if session:
        await update.message.reply_text(
            f"✅ <b>Ваш сервер активен!</b>\n\n"
            f"🌐 IP: <code>{session['ip']}</code>\n"
            f"🔑 Пароль: <code>{session['password']}</code>\n\n"
            f"SSH: <code>ssh runner@{session['ip']}</code>",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        f"👋 Привет, {username}!\n\n"
        f"📌 Отправь код доступа, который ты видишь в логах GitHub Actions.\n\n"
        f"Код выглядит так: <code>A1B2C3D4</code>\n\n"
        f"🔹 Команды:\n"
        f"/start - это сообщение\n"
        f"/myserver - показать данные сервера\n"
        f"/revoke - отозвать доступ",
        parse_mode='HTML'
    )

async def myserver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        f"🖥️ <b>Ваш сервер</b>\n\n"
        f"🌐 IP: <code>{session['ip']}</code>\n"
        f"👤 Пользователь: <code>runner</code>\n"
        f"🔑 Пароль: <code>{session['password']}</code>\n\n"
        f"SSH: <code>ssh runner@{session['ip']}</code>",
        parse_mode='HTML'
    )

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Только для администратора"""
    ADMIN_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    if update.effective_chat.id != ADMIN_ID:
        return
    
    stats = manager.get_stats()
    await update.message.reply_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"Активных сессий: {stats['sessions']}\n"
        f"Доверенных пользователей: {stats['trusted']}\n"
        f"Активных пользователей: {stats['users']}",
        parse_mode='HTML'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает коды доступа"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    text = update.message.text.strip().upper()
    
    # Проверяем формат кода
    if len(text) != 8 or not text.isalnum():
        await update.message.reply_text(
            f"❌ Неверный формат!\n\n"
            f"Код должен быть 8 символов (буквы и цифры).\n"
            f"Пример: <code>A1B2C3D4</code>",
            parse_mode='HTML'
        )
        return
    
    # Проверяем, есть ли уже доступ
    if manager.is_trusted(user_id):
        session = manager.get_user_session(user_id)
        if session:
            await update.message.reply_text(
                f"✅ Доступ уже есть!\n"
                f"IP: <code>{session['ip']}</code>",
                parse_mode='HTML'
            )
        return
    
    # Активируем код
    session = manager.activate(text, user_id)
    
    if session:
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
            f"⏱️ Сервер активен ~6 часов\n"
            f"🔄 Авто-перезапуск каждые 6 часов",
            parse_mode='HTML'
        )
        logger.info(f"✅ АКТИВИРОВАН: @{username} (ID: {user_id}) код {text}")
    else:
        await update.message.reply_text(
            f"❌ <b>Неверный код!</b>\n\n"
            f"Код <code>{text}</code> не найден.\n"
            f"Проверьте код в логах GitHub Actions.\n\n"
            f"💡 Код действителен только {CODE_EXPIRE_MINUTES} минут после генерации.",
            parse_mode='HTML'
        )

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    manager.cleanup()

# ==================== ЗАПУСК ====================

def main():
    parser = argparse.ArgumentParser(description='Умный Telegram бот для VPS')
    parser.add_argument('--token', required=True, help='Токен бота')
    parser.add_argument('--admin-id', help='ID администратора (для /stats)')
    parser.add_argument('--add-code', help='Добавить код: code:ip:password')
    args = parser.parse_args()
    
    # Если нужно добавить код (из workflow)
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
    
    # Запускаем бота
    if args.admin_id:
        os.environ['ADMIN_CHAT_ID'] = args.admin_id
    
    app = Application.builder().token(args.token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myserver", myserver))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Очистка каждые 5 минут
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_task, interval=CLEANUP_INTERVAL, first=10)
    
    logger.info("🤖 УМНЫЙ БОТ ЗАПУЩЕН!")
    logger.info(f"📊 Активных сессий: {len(manager.sessions)}")
    app.run_polling()

if __name__ == '__main__':
    main()
