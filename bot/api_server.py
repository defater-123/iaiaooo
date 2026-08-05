#!/usr/bin/env python3
"""
API для регистрации кодов от GitHub Actions
"""

from flask import Flask, request, jsonify
import json
import logging
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Используем тот же менеджер
from central_bot import SessionManager
manager = SessionManager()

@app.route('/register', methods=['POST'])
def register():
    """Регистрирует код от GitHub Actions"""
    data = request.json
    
    code = data.get('code')
    ip = data.get('ip')
    password = data.get('password')
    
    if not all([code, ip, password]):
        return jsonify({'error': 'Missing fields'}), 400
    
    if manager.register(code, ip, password):
        logging.info(f"✅ Зарегистрирован код {code} для {ip}")
        return jsonify({'status': 'success', 'code': code}), 200
    else:
        return jsonify({'error': 'Max sessions'}), 429

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'sessions': manager.stats()['sessions']})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
