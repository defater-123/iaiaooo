#!/usr/bin/env python3
"""
Аддон: IP Checker
Получает публичный IP через разные сервисы
"""

import requests
import socket
from typing import Dict, Any

class Addon:
    def __init__(self):
        self.name = "IP Checker"
        self.running = True
        self.services = [
            'https://api.ipify.org?format=json',
            'https://api.my-ip.io/ip.json',
            'https://ipapi.co/json/',
            'https://ip-api.com/json/',
            'https://httpbin.org/ip',
            'https://ifconfig.me/ip',
            'https://icanhazip.com',
            'https://ident.me'
        ]
    
    def start(self) -> Dict:
        self.running = True
        return {'status': 'started', 'message': 'IP Checker запущен'}
    
    def stop(self) -> Dict:
        self.running = False
        return {'status': 'stopped', 'message': 'IP Checker остановлен'}
    
    def status(self) -> Dict:
        return {'status': 'running' if self.running else 'stopped'}
    
    def execute(self, command: str, args: list = None) -> Dict:
        if command == 'get_ip':
            return self.get_ip()
        else:
            return {'error': f'Неизвестная команда: {command}'}
    
    def get_ip(self) -> Dict:
        results = []
        for url in self.services:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if 'ip' in data:
                            ip = data['ip']
                        elif 'query' in data:
                            ip = data['query']
                        elif 'origin' in data:
                            ip = data['origin']
                        else:
                            ip = data.get('ipv4', data.get('ip', str(data)))
                    except:
                        ip = response.text.strip()
                    
                    results.append({
                        'service': url.replace('https://', '').split('/')[0],
                        'ip': ip,
                        'success': True
                    })
            except Exception as e:
                results.append({
                    'service': url.replace('https://', '').split('/')[0],
                    'error': str(e),
                    'success': False
                })
        
        ips = {}
        for r in results:
            if r.get('success') and r.get('ip'):
                ips[r['ip']] = ips.get(r['ip'], 0) + 1
        
        main_ip = max(ips.items(), key=lambda x: x[1])[0] if ips else None
        
        return {
            'status': 'success',
            'ip': main_ip,
            'all_results': results,
            'unique_ips': ips
        }


def get_ip() -> str:
    addon = Addon()
    result = addon.get_ip()
    return result.get('ip', 'Не удалось получить IP')
