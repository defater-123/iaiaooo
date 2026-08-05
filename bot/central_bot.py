#!/usr/bin/env python3
"""
Центральный Telegram Бот для VPS
Спящий режим - отвечает только на валидные коды
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГ ====================
MAX_SESSIONS = 50
CODE_EXPIRE_MINUTES = 10
CLEANUP_INTERVAL = 300  # 5 минут

# ==================== МЕНЕДЖЕР СЕССИЙ ====================
class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}  # code -> data
        self.trusted: Set[int] = set()       # user_id
        self.user_codes: Dict[int, str] = {} # user_id -> code
        self._lock = Lock()
        self._load()
    
    def _load(self):
        try:
            if os.path.exists('sessions.json'):
                with open('sessions.json', 'r') as f:
                    data = json.load(f)
                    self.sessions = data.get('sessions', {})
                    self.trusted = set(data.get('trusted', []))
                    # Восстанавливаем user_codes
                    for code, info in self.sessions.items():
                        if 'user_id' in info and info['user_id']:
                            self.user_codes[info['user_id']] = code
                    logger.info(f"Загружено: {len(self.sessions)} сессий, {len(self.trusted)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
    
    def _save(self):
        try:
            data = {
                'sessions': self.sessions,
                'trusted': list(self.trusted)
            }
            with open('sessions.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    def register(self, code: str, ip: str, password: str) -> bool:
        with self._lock:
            if len(self.sessions) >= MAX_SESSIONS:
                return False
            
            # Удаляем старые сессии пользователя (если есть)
            for old_code, data in list(self.sessions.items()):
                if data.get('user_id') == 0:  # Временные коды без user_id
                    del self.sessions[old_code]
            
            self.sessions[code] = {
                'ip': ip,
                'password': password,
                'user_id': 0,  # Пока неизвестен
                'created': datetime.now().isoformat(),
                'expires': (datetime.now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat()
            }
            self._save()
            return True
    
    def activate(self, code: str, user_id: int) -> Optional[Dict]:
        with self._lock:
            if code not in self.sessions:
                return None
            
            data = self.sessions[code]
            
            # Проверяем срок
            expires = datetime.fromisoformat(data['expires'])
            if datetime.now() > expires:
                del self.sessions[code]
                self._save()
                return None
            
            # Активируем для пользователя
            data['user_id'] = user_id
            self.trusted.add(user_id)
            self.user_codes[user_id] = code
            self._save()
            
            return {'ip': data['ip'], 'password': data['password']}
    
    def get_user_session(self, user_id: int) -> Optional[Dict]:
        code = self.user_codes.get(user_id)
        if code and code in self.sessions:
            data = self.sessions[code]
            # Проверяем срок
            expires = datetime.fromisoformat(data['expires'])
            if datetime.now() < expires:
                return {'ip': data['ip'], 'password': data['password']}
        return None
    
    def revoke(self, user_id: int) -> bool:
        with self._lock:
            if user_id in self.user_codes:
                code = self.user_codes[user_id]
                if code in self.sessions:
                    del self.sessions[code]
                del self.user_codes[user_id]
            
            if user_id in self.trusted:
                self.trusted.remove(user_id)
            
            self._save()
            return True
    
    def is_trusted(self, user_id: int) -> bool:
        return user_id in self.trusted
    
    def cleanup(self):
        with self._lock:
            expired = []
            for code, data in self.sessions.items():
                expires = datetime.fromisoformat(data['expires'])
                if datetime.now() > expires:
                    expired.append(code)
            
            for code in expired:
                del self.sessions[code]
                # Если код был привязан к пользователю
                for uid, c in list(self.user_codes.items()):
                    if c == code:
                        del self.user_codes[uid]
                        if uid in self.trusted:
                            self.trusted.remove(uid)
            
            if expired:
                self._save()
                logger.info(f"Удалено {len(expired)} истекших сессий")
    
    def stats(self) -> Dict:
        return {
            'sessions': len(self.sessions),
            'trusted': len(self.trusted)
        }

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
manager = SessionManager()

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start - приветствие"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    
    # Проверяем, есть ли уже доступ
    session = manager.get_user_session(user_id)
    if session:
        await update.message.reply_text(
            f"✅ <b>Ваш сервер активен!</b>\n\n"
            f"🌐 IP: <code>{session['ip']}</code>\n"
            f"🔑 Пароль: <code>{session['password']}</code>\n\n"
            f"Подключение: <code>ssh runner@{session['ip']}</code>",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        f"👋 Привет, {username}!\n\n"
        f"📌 <b>Как получить VPS:</b>\n"
        f"1. Форкни репозиторий\n"
        f"2. Запусти GitHub Actions\n"
        f"3. Скопируй код из логов\n"
        f"4. Отправь код мне\n\n"
        f"⚡ <i>Просто отправь 8-значный код</i>",
        parse_mode='HTML'
    )

async def myserver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myserver - показать данные сервера"""
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
    """/revoke - отозвать доступ"""
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

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats - статистика (только для админа)"""
    ADMIN_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    if update.effective_chat.id != ADMIN_ID:
        return
    
    s = manager.stats()
    await update.message.reply_text(
        f"📊 <b>Статистика</b>\n\n"
        f"Активных сессий: {s['sessions']}\n"
        f"Доверенных пользователей: {s['trusted']}",
        parse_mode='HTML'
    )

# ==================== ОСНОВНОЙ ОБРАБОТЧИК ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает только коды доступа"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    text = update.message.text.strip().upper()
    
    # ⭐ КЛЮЧЕВОЙ МОМЕНТ: проверяем, что это код (8 символов, только буквы/цифры)
    if len(text) != 8 or not text.isalnum():
        return  # 🚫 ИГНОРИРУЕМ - бот "спит"
    
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
    
    # Пытаемся активировать код
    session = manager.activate(text, user_id)
    
    if session:
        # ✅ УСПЕШНАЯ АКТИВАЦИЯ
        await update.message.reply_text(
            f"🎉 <b>ДОСТУП ПРЕДОСТАВЛЕН!</b>\n\n"
            f"🌐 IP: <code>{session['ip']}</code>\n"
            f"👤 Пользователь: <code>runner</code>\n"
            f"🔑 Пароль: <code>{session['password']}</code>\n\n"
            f"SSH: <code>ssh runner@{session['ip']}</code>\n\n"
            f"⏱️ Сервер активен ~6 часов",
            parse_mode='HTML'
        )
        logger.info(f"✅ АКТИВИРОВАН: @{username} (ID: {user_id}) код {text}")
    else:
        # ❌ НЕВЕРНЫЙ КОД - молчим (спящий режим)
        logger.debug(f"❌ Неверный код от @{username}: {text}")

# ==================== ОЧИСТКА ====================

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодическая очистка"""
    manager.cleanup()

# ==================== ЗАПУСК ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True, help='Токен бота')
    parser.add_argument('--admin-id', help='ID админа')
    args = parser.parse_args()
    
    if args.admin_id:
        os.environ['ADMIN_CHAT_ID'] = args.admin_id
    
    app = Application.builder().token(args.token).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myserver", myserver))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("stats", stats))
    
    # Обработчик сообщений (только коды)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Периодическая очистка
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_task, interval=CLEANUP_INTERVAL, first=10)
    
    logger.info("🤖 Бот запущен (спящий режим)")
    logger.info(f"📊 Макс. сессий: {MAX_SESSIONS}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
