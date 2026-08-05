#!/usr/bin/env python3
"""
Сервер для Telegram Mini App
Обрабатывает API запросы от интерфейса
"""

import os
import json
import subprocess
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime
import base64

app = Flask(__name__)
CORS(app)

# ==================== КОНФИГ ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")
USER_DIR = os.path.join(BASE_DIR, "Saves", "user")

# ==================== ПОМОЩНИКИ ====================

def get_user_data(user_id):
    """Загружает данные пользователя"""
    file_path = os.path.join(USER_DIR, f"{user_id}.txt")
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return None

def execute_command(cmd):
    """Выполняет команду на сервере"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout if result.stdout else result.stderr
    except Exception as e:
        return f"Ошибка: {str(e)}"

def get_file_list(path):
    """Получает список файлов в директории"""
    try:
        files = []
        for item in os.listdir(path):
            full_path = os.path.join(path, item)
            stat = os.stat(full_path)
            files.append({
                'name': item,
                'is_dir': os.path.isdir(full_path),
                'size': stat.st_size,
                'permissions': oct(stat.st_mode)[-3:],
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        return files
    except Exception as e:
        return {'error': str(e)}

# ==================== API ЭНДПОИНТЫ ====================

@app.route('/api/auth', methods=['GET'])
def auth():
    """Проверяет доступ пользователя"""
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'trusted': False, 'error': 'No user ID'})
    
    user_data = get_user_data(int(user_id))
    if user_data and user_data.get('active', False):
        return jsonify({
            'trusted': True,
            'ip': user_data.get('ip'),
            'expires': user_data.get('expires')
        })
    return jsonify({'trusted': False})

@app.route('/api/stats', methods=['GET'])
def stats():
    """Статистика сервера"""
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'error': 'No user ID'})
    
    user_data = get_user_data(int(user_id))
    if not user_data or not user_data.get('active', False):
        return jsonify({'error': 'No access'})
    
    cpu = execute_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    ram = execute_command("free -h | awk 'NR==2 {print $3\"/\"$2}'")
    disk = execute_command("df -h / | awk 'NR==2 {print $5}'")
    uptime = execute_command("uptime -p | sed 's/up //'")
    ip = execute_command("curl -s ifconfig.me")
    
    return jsonify({
        'cpu': f"{cpu.strip()}%",
        'ram': ram.strip(),
        'disk': disk.strip(),
        'uptime': uptime.strip(),
        'ip': ip.strip() or '10.0.0.1'
    })

@app.route('/api/files', methods=['GET'])
def files():
    """Список файлов"""
    user_id = request.headers.get('X-User-ID')
    path = request.args.get('path', '/home/user')
    
    user_data = get_user_data(int(user_id))
    if not user_data or not user_data.get('active', False):
        return jsonify({'error': 'No access'})
    
    # Безопасность: разрешаем только /home/user и подпапки
    if not path.startswith('/home/user'):
        return jsonify({'error': 'Access denied'})
    
    full_path = os.path.join(BASE_DIR, path.lstrip('/'))
    if not os.path.exists(full_path):
        return jsonify({'error': 'Path not found'})
    
    file_list = get_file_list(full_path)
    if isinstance(file_list, dict) and 'error' in file_list:
        return jsonify({'error': file_list['error']})
    
    return jsonify({'files': file_list})

@app.route('/api/command', methods=['POST'])
def command():
    """Выполняет команду"""
    user_id = request.headers.get('X-User-ID')
    data = request.json
    cmd = data.get('command', '')
    
    user_data = get_user_data(int(user_id))
    if not user_data or not user_data.get('active', False):
        return jsonify({'error': 'No access'})
    
    # Безопасность: блокируем опасные команды
    dangerous = ['rm -rf', 'mkfs', 'dd if=', '> /dev', ':(){']
    for d in dangerous:
        if d in cmd.lower():
            return jsonify({'error': 'Command blocked for security'})
    
    output = execute_command(cmd)
    return jsonify({'output': output})

@app.route('/api/delete', methods=['POST'])
def delete():
    """Удаляет файл"""
    user_id = request.headers.get('X-User-ID')
    data = request.json
    path = data.get('path', '/home/user')
    name = data.get('name', '')
    
    user_data = get_user_data(int(user_id))
    if not user_data or not user_data.get('active', False):
        return jsonify({'error': 'No access'})
    
    if not path.startswith('/home/user'):
        return jsonify({'error': 'Access denied'})
    
    full_path = os.path.join(BASE_DIR, path.lstrip('/'), name)
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'})
    
    try:
        if os.path.isdir(full_path):
            os.rmdir(full_path)
        else:
            os.remove(full_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/download', methods=['GET'])
def download():
    """Скачивает файл"""
    user_id = request.headers.get('X-User-ID')
    path = request.args.get('path', '/home/user')
    name = request.args.get('name', '')
    
    user_data = get_user_data(int(user_id))
    if not user_data or not user_data.get('active', False):
        return jsonify({'error': 'No access'})
    
    if not path.startswith('/home/user'):
        return jsonify({'error': 'Access denied'})
    
    full_path = os.path.join(BASE_DIR, path.lstrip('/'), name)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({'error': 'File not found'})
    
    try:
        with open(full_path, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        return jsonify({'content': content})
    except Exception as e:
        return jsonify({'error': str(e)})

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
