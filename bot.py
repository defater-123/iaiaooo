#!/usr/bin/env python3
"""
Telegram Bot для управления GitHub VPS
Использует python-telegram-bot версии 20.0+
"""

import asyncio
import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from typing import List, Set

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
TRUSTED_USERS: Set[int] = set()
ACCESS_CODE = ""
SSH_PASSWORD = ""
SERVER_IP = ""
GIST_TOKEN = ""
GIST_ID = ""
IS_AUTHENTICATED = False  # Флаг, что код уже был использован (опционально)

# Файл для локального хранения (как резерв)
TRUSTED_FILE = "trusted_users.json"


def load_trusted_from_gist() -> Set[int]:
    """Загружает список доверенных пользователей из Gist"""
    global TRUSTED_USERS
    
    if not GIST_TOKEN or not GIST_ID:
        logger.warning("GIST_TOKEN или GIST_ID не заданы, пробую локальный файл")
        return load_trusted_from_local()
    
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            gist_data = response.json()
            files = gist_data.get('files', {})
            if 'trusted.json' in files:
                content = files['trusted.json'].get('content', '[]')
                users = json.loads(content)
                TRUSTED_USERS = set(users)
                logger.info(f"Загружено {len(TRUSTED_USERS)} доверенных пользователей из Gist")
                return TRUSTED_USERS
        else:
            logger.error(f"Ошибка загрузки Gist: {response.status_code}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке из Gist: {e}")
    
    # Если Gist не доступен, пробуем локальный файл
    return load_trusted_from_local()


def load_trusted_from_local() -> Set[int]:
    """Загружает список из локального файла (резерв)"""
    global TRUSTED_USERS
    
    try:
        if os.path.exists(TRUSTED_FILE):
            with open(TRUSTED_FILE, 'r') as f:
                users = json.load(f)
                TRUSTED_USERS = set(users)
                logger.info(f"Загружено {len(TRUSTED_USERS)} пользователей из локального файла")
                return TRUSTED_USERS
    except Exception as e:
        logger.error(f"Ошибка загрузки локального файла: {e}")
    
    TRUSTED_USERS = set()
    return TRUSTED_USERS


def save_trusted_to_gist(users: Set[int]) -> bool:
    """Сохраняет список доверенных пользователей в Gist"""
    if not GIST_TOKEN or not GIST_ID:
        return save_trusted_to_local(users)
    
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        data = {
            "files": {
                "trusted.json": {
                    "content": json.dumps(list(users), indent=2)
                }
            }
        }
        
        response = requests.patch(url, headers=headers, json=data, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"Сохранено {len(users)} пользователей в Gist")
            return True
        else:
            logger.error(f"Ошибка сохранения в Gist: {response.status_code}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении в Gist: {e}")
    
    # Если Gist не доступен, сохраняем локально
    return save_trusted_to_local(users)


def save_trusted_to_local(users: Set[int]) -> bool:
    """Сохраняет список в локальный файл (резерв)"""
    try:
        with open(TRUSTED_FILE, 'w') as f:
            json.dump(list(users), f, indent=2)
        logger.info(f"Сохранено {len(users)} пользователей в локальный файл")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения локального файла: {e}")
        return False


def is_trusted(user_id: int) -> bool:
    """Проверяет, является ли пользователь доверенным"""
    return user_id in TRUSTED_USERS


def add_trusted(user_id: int) -> bool:
    """Добавляет пользователя в доверенные"""
    if user_id not in TRUSTED_USERS:
        TRUSTED_USERS.add(user_id)
        return save_trusted_to_gist(TRUSTED_USERS)
    return True


def remove_trusted(user_id: int) -> bool:
    """Удаляет пользователя из доверенных"""
    if user_id in TRUSTED_USERS:
        TRUSTED_USERS.remove(user_id)
        return save_trusted_to_gist(TRUSTED_USERS)
    return True


def get_server_info() -> str:
    """Возвращает информацию о сервере"""
    uptime = subprocess.getoutput("uptime -p")
    disk = subprocess.getoutput("df -h / | awk 'NR==2 {print $5 \" used (\" $3 \"/\" $2 \")\"}'")
    
    return (
        f"🖥️ <b>Информация о сервере</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 IP: <code>{SERVER_IP}</code>\n"
        f"👤 Пользователь: <code>runner</code>\n"
        f"🔑 Пароль: <code>{SSH_PASSWORD}</code>\n"
        f"⏱️ Время работы: {uptime}\n"
        f"💾 Диск: {disk}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Сервер будет активен ~5ч 50м</i>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user_id = update.effective_chat.id
    username = update.effective_user.username or "без username"
    
    await update.message.reply_text(
        f"👋 Привет, <b>{username}</b>!\n\n"
        f"Я бот для управления VPS через GitHub Actions.\n\n"
        f"📌 <b>Инструкция:</b>\n"
        f"1️⃣ Введи <b>код доступа</b>, который был тебе выдан\n"
        f"2️⃣ После входа ты получишь SSH-реквизиты\n"
        f"3️⃣ Твой ID будет сохранён для будущих сессий\n\n"
        f"🔹 <b>Доступные команды:</b>\n"
        f"/start - показать это сообщение\n"
        f"/info - показать информацию о сервере (только для доверенных)\n"
        f"/exec <команда> - выполнить команду на сервере (только для доверенных)\n"
        f"/revoke - отозвать доступ (только для админа)\n\n"
        f"⚡ <i>Введи код доступа, чтобы получить доступ</i>",
        parse_mode='HTML'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user_id = update.effective_chat.id
    text = update.message.text.strip()
    username = update.effective_user.username or "без username"
    
    # Проверяем, не является ли сообщение кодом доступа
    if text == ACCESS_CODE:
        # Аутентификация успешна
        if add_trusted(user_id):
            # Отправляем информацию о сервере
            await update.message.reply_text(
                f"✅ <b>Доступ предоставлен!</b>\n\n"
                f"{get_server_info()}\n\n"
                f"🔹 Теперь вы можете использовать команды:\n"
                f"/info - показать информацию о сервере\n"
                f"/exec &lt;команда&gt; - выполнить команду\n"
                f"/start - справка",
                parse_mode='HTML'
            )
            
            # Отправляем уведомление админу (если есть)
            # ADMIN_ID можно передать через аргументы
            logger.info(f"Новый пользователь авторизован: @{username} (ID: {user_id})")
        else:
            await update.message.reply_text(
                "❌ <b>Ошибка сохранения</b>\n"
                "Не удалось сохранить ваши данные. Попробуйте позже.",
                parse_mode='HTML'
            )
    elif is_trusted(user_id):
        # Если пользователь уже доверенный, но отправил не команду
        await update.message.reply_text(
            "ℹ️ Вы уже авторизованы!\n"
            "Используйте /info для получения данных или /exec для выполнения команд.",
            parse_mode='HTML'
        )
    else:
        # Неверный код
        await update.message.reply_text(
            f"❌ <b>Неверный код доступа</b>\n\n"
            f"Попробуйте ещё раз. Если у вас нет кода, обратитесь к администратору.",
            parse_mode='HTML'
        )


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /info - показать информацию о сервере"""
    user_id = update.effective_chat.id
    
    if not is_trusted(user_id):
        await update.message.reply_text(
            "⛔ <b>Доступ запрещён</b>\n"
            "Сначала введите код доступа для авторизации.",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        get_server_info(),
        parse_mode='HTML'
    )


async def exec_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /exec - выполнить команду на сервере"""
    user_id = update.effective_chat.id
    
    if not is_trusted(user_id):
        await update.message.reply_text(
            "⛔ <b>Доступ запрещён</b>\n"
            "Сначала введите код доступа для авторизации.",
            parse_mode='HTML'
        )
        return
    
    # Проверяем, есть ли команда
    if not context.args:
        await update.message.reply_text(
            "❌ <b>Укажите команду</b>\n"
            "Пример: <code>/exec ls -la</code>\n"
            "Пример: <code>/exec df -h</code>",
            parse_mode='HTML'
        )
        return
    
    command = ' '.join(context.args)
    
    # Безопасность: запрещаем опасные команды
    dangerous_commands = ['rm -rf', 'mkfs', 'dd if=', '> /dev/sd', ':(){ :|:& };:']
    for dangerous in dangerous_commands:
        if dangerous in command.lower():
            await update.message.reply_text(
                "⛔ <b>Команда заблокирована из соображений безопасности</b>",
                parse_mode='HTML'
            )
            return
    
    # Отправляем статус
    status_msg = await update.message.reply_text(
        f"🔄 <b>Выполнение команды...</b>\n"
        f"<code>{command}</code>",
        parse_mode='HTML'
    )
    
    try:
        # Выполняем команду с таймаутом 30 секунд
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            executable='/bin/bash'
        )
        
        output = result.stdout if result.stdout else result.stderr
        
        # Обрезаем длинный вывод
        if len(output) > 4000:
            output = output[:4000] + "\n\n... (обрезано)"
        
        if result.returncode == 0:
            await status_msg.edit_text(
                f"✅ <b>Команда выполнена</b>\n"
                f"<code>{command}</code>\n\n"
                f"```\n{output}\n```",
                parse_mode='HTML'
            )
        else:
            await status_msg.edit_text(
                f"❌ <b>Ошибка выполнения</b> (код: {result.returncode})\n"
                f"<code>{command}</code>\n\n"
                f"```\n{output}\n```",
                parse_mode='HTML'
            )
    except subprocess.TimeoutExpired:
        await status_msg.edit_text(
            f"⏰ <b>Таймаут выполнения</b>\n"
            f"Команда выполнялась более 30 секунд:\n"
            f"<code>{command}</code>",
            parse_mode='HTML'
        )
    except Exception as e:
        await status_msg.edit_text(
            f"⚠️ <b>Ошибка</b>\n"
            f"<code>{str(e)}</code>",
            parse_mode='HTML'
        )


async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /revoke - отозвать доступ у пользователя (только для админа)"""
    user_id = update.effective_chat.id
    
    # Проверяем, является ли пользователь админом (можно указать конкретный ID)
    ADMIN_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    
    if user_id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ <b>Доступ запрещён</b>\n"
            "Только администратор может отзывать доступ.",
            parse_mode='HTML'
        )
        return
    
    # Если указан ID пользователя
    if context.args:
        try:
            target_id = int(context.args[0])
            if remove_trusted(target_id):
                await update.message.reply_text(
                    f"✅ <b>Доступ отозван</b>\n"
                    f"Пользователь с ID <code>{target_id}</code> больше не имеет доступа.",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    "❌ <b>Ошибка</b>\n"
                    "Не удалось отозвать доступ.",
                    parse_mode='HTML'
                )
        except ValueError:
            await update.message.reply_text(
                "❌ <b>Неверный формат</b>\n"
                "Используйте: <code>/revoke 123456789</code>",
                parse_mode='HTML'
            )
    else:
        # Показываем список пользователей
        if TRUSTED_USERS:
            users_list = "\n".join([f"• <code>{uid}</code>" for uid in TRUSTED_USERS])
            await update.message.reply_text(
                f"👥 <b>Доверенные пользователи</b>\n\n"
                f"{users_list}\n\n"
                f"Чтобы отозвать доступ: <code>/revoke ID</code>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                "📭 <b>Нет доверенных пользователей</b>",
                parse_mode='HTML'
            )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    
    if update and update.effective_chat:
        await update.message.reply_text(
            "⚠️ <b>Произошла ошибка</b>\n"
            "Попробуйте позже или обратитесь к администратору.",
            parse_mode='HTML'
        )


def main() -> None:
    """Главная функция"""
    global ACCESS_CODE, SSH_PASSWORD, SERVER_IP, GIST_TOKEN, GIST_ID
    
    parser = argparse.ArgumentParser(description='Telegram Bot для управления VPS')
    parser.add_argument('--token', required=True, help='Токен Telegram бота')
    parser.add_argument('--code', required=True, help='Код доступа')
    parser.add_argument('--password', required=True, help='SSH пароль')
    parser.add_argument('--ip', required=True, help='IP адрес сервера')
    parser.add_argument('--gist-token', help='Токен для доступа к Gist')
    parser.add_argument('--gist-id', help='ID Gist для хранения доверенных пользователей')
    parser.add_argument('--admin-id', help='ID администратора (для команды /revoke)')
    
    args = parser.parse_args()
    
    ACCESS_CODE = args.code
    SSH_PASSWORD = args.password
    SERVER_IP = args.ip
    GIST_TOKEN = args.gist_token or os.getenv('GIST_TOKEN', '')
    GIST_ID = args.gist_id or os.getenv('GIST_ID', '')
    
    if args.admin_id:
        os.environ['ADMIN_CHAT_ID'] = args.admin_id
    
    # Загружаем доверенных пользователей
    load_trusted_from_gist()
    
    # Создаём приложение
    application = Application.builder().token(args.token).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("exec", exec_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запущен. Ожидание сообщений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
