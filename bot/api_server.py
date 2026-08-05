#!/usr/bin/env python3
"""
API сервер для регистрации кодов (опционально)
Можно использовать вместо --add-code
"""

import json
import logging
from flask import Flask, request, jsonify
from central_bot import SessionManager

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

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
    
    if manager.add_code(code, ip, password):
        logging.info(f"✅ Код {code} зарегистрирован через API")
        return jsonify({'status': 'success', 'code': code}), 200
    else:
        return jsonify({'error': 'Code already exists'}), 409

@app.route('/health', methods=['GET'])
def health():
    stats = manager.get_stats()
    return jsonify({
        'status': 'ok',
        'sessions': stats['sessions'],
        'trusted': stats['trusted']
    })

@app.route('/stats', methods=['GET'])
def stats():
    return jsonify(manager.get_stats())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
