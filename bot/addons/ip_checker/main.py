#!/usr/bin/env python3
"""
Аддон: IP Checker
Получает публичный IP через разные сервисы
Поддерживает: HTTP, TCP, UDP проверки
"""

import requests
import socket
import json
from typing import Dict, Any

class Addon:
    """Класс аддона IP Checker"""
    
    def __init__(self):
        self.name = "IP Checker"
        self.running = True
        self.services = {
            'http': [
                'https://api.ipify.org?format=json',
                'https://api.my-ip.io/ip.json',
                'https://ipapi.co/json/',
                'https://ip-api.com/json/',
                'https://httpbin.org/ip',
                'https://ifconfig.me/ip',
                'https://icanhazip.com',
                'https://ident.me'
            ],
            'udp': [
                ('8.8.8.8', 53),    # Google DNS
                ('1.1.1.1', 53),    # Cloudflare DNS
                ('208.67.222.222', 53),  # OpenDNS
            ],
            'tcp': [
                ('time.google.com', 80),
                ('google.com', 80),
                ('cloudflare.com', 80),
            ]
        }
    
    def start(self) -> Dict:
        """Запуск аддона"""
        self.running = True
        return {'status': 'started', 'message': 'IP Checker запущен'}
    
    def stop(self) -> Dict:
        """Остановка аддона"""
        self.running = False
        return {'status': 'stopped', 'message': 'IP Checker остановлен'}
    
    def status(self) -> Dict:
        """Статус аддона"""
        return {'status': 'running' if self.running else 'stopped'}
    
    def execute(self, command: str, args: list = None) -> Dict:
        """Выполняет команду аддона"""
        if command == 'get_ip':
            return self.get_ip()
        elif command == 'check_ports':
            return self.check_ports()
        elif command == 'all':
            return self.get_all_info()
        else:
            return {'error': f'Неизвестная команда: {command}'}
    
    def get_ip(self) -> Dict:
        """Получает публичный IP через HTTP сервисы"""
        results = []
        for url in self.services['http']:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        # Парсим IP из разных форматов
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
                        'service': url.replace('https://', '').replace('http://', '').split('/')[0],
                        'ip': ip,
                        'success': True
                    })
            except Exception as e:
                results.append({
                    'service': url.replace('https://', '').replace('http://', '').split('/')[0],
                    'error': str(e),
                    'success': False
                })
        
        # Находим уникальные IP
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
    
    def get_public_ip(self) -> str:
        """Быстрый способ получить IP"""
        result = self.get_ip()
        return result.get('ip', 'Не удалось получить IP')
    
    def check_ports(self) -> Dict:
        """Проверяет открытые порты через внешние сервисы"""
        results = {
            'tcp': [],
            'udp': [],
            'http': []
        }
        
        # Проверяем UDP через DNS сервера
        for server, port in self.services['udp']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2)
                sock.sendto(b'test', (server, port))
                sock.close()
                results['udp'].append({
                    'server': server,
                    'port': port,
                    'status': 'connected'
                })
            except Exception as e:
                results['udp'].append({
                    'server': server,
                    'port': port,
                    'status': 'error',
                    'error': str(e)
                })
        
        # Проверяем TCP
        for host, port in self.services['tcp']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((host, port))
                sock.close()
                results['tcp'].append({
                    'host': host,
                    'port': port,
                    'status': 'open' if result == 0 else 'closed'
                })
            except Exception as e:
                results['tcp'].append({
                    'host': host,
                    'port': port,
                    'status': 'error',
                    'error': str(e)
                })
        
        return results
    
    def get_all_info(self) -> Dict:
        """Получает всю информацию"""
        ip_info = self.get_ip()
        port_info = self.check_ports()
        
        return {
            'ip_info': ip_info,
            'port_info': port_info,
            'summary': {
                'public_ip': ip_info.get('ip'),
                'http_services': len([r for r in ip_info.get('all_results', []) if r.get('success')]),
                'udp_available': len([r for r in port_info.get('udp', []) if r.get('status') == 'connected']),
                'tcp_open': len([r for r in port_info.get('tcp', []) if r.get('status') == 'open'])
            }
        }


# ==================== ФУНКЦИИ ДЛЯ ИСПОЛЬЗОВАНИЯ ИЗ БОТА ====================

def get_ip() -> str:
    """Быстрое получение IP (для использования в боте)"""
    addon = Addon()
    return addon.get_public_ip()

def get_ip_detailed() -> Dict:
    """Детальная информация об IP"""
    addon = Addon()
    return addon.get_ip()

def get_full_info() -> Dict:
    """Вся информация (IP + порты)"""
    addon = Addon()
    return addon.get_all_info()
