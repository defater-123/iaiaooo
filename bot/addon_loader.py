#!/usr/bin/env python3
"""
Загрузчик аддонов для VPS бота
Поддерживает Python и Bash скрипты
"""

import os
import json
import importlib
import subprocess
import sys
from typing import Dict, List, Optional, Any
from pathlib import Path

# ==================== КОНФИГ ====================
ADDONS_DIR = os.path.join(os.path.dirname(__file__), "addons")
ADDONS_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Saves", "addons")

os.makedirs(ADDONS_DIR, exist_ok=True)
os.makedirs(ADDONS_DATA, exist_ok=True)

# ==================== КЛАСС АДДОНА ====================

class Addon:
    """Класс для управления аддоном"""
    
    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(ADDONS_DIR, folder)
        self.config = self._load_config()
        self.instance = None
        self.running = False
        self.process = None
        
        # Загружаем состояние
        self.state_file = os.path.join(ADDONS_DATA, f"{folder}.json")
        self._load_state()
    
    def _load_config(self) -> Dict:
        """Загружает конфиг аддона"""
        config_path = os.path.join(self.path, "addon.conf")
        config = {
            'name': folder,
            'version': '1.0.0',
            'author': 'Unknown',
            'description': 'No description',
            'startup': 'no',
            'type': 'python',  # python или bash
            'main': 'main.py',
            'requirements': [],
            'commands': []
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if '=' in line and not line.startswith('[') and not line.startswith('#'):
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            
                            if key == 'name':
                                config['name'] = value
                            elif key == 'version':
                                config['version'] = value
                            elif key == 'author':
                                config['author'] = value
                            elif key == 'description':
                                config['description'] = value
                            elif key == 'startup':
                                config['startup'] = value.lower()
                            elif key == 'type':
                                config['type'] = value.lower()
                            elif key == 'main':
                                config['main'] = value
                            elif key == 'requirements':
                                config['requirements'] = [r.strip() for r in value.split(',') if r.strip()]
                            elif key == 'commands':
                                config['commands'] = [c.strip() for c in value.split(',') if c.strip()]
            except Exception as e:
                print(f"⚠️ Ошибка загрузки конфига {folder}: {e}")
        
        return config
    
    def _load_state(self):
        """Загружает состояние аддона"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.running = data.get('running', False)
            except:
                pass
    
    def _save_state(self):
        """Сохраняет состояние аддона"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump({'running': self.running}, f, indent=2)
        except:
            pass
    
    def get_info(self) -> Dict:
        """Возвращает информацию об аддоне"""
        return {
            'folder': self.folder,
            'name': self.config['name'],
            'version': self.config['version'],
            'author': self.config['author'],
            'description': self.config['description'],
            'startup': self.config['startup'],
            'type': self.config['type'],
            'running': self.running,
            'commands': self.config.get('commands', [])
        }
    
    def install_requirements(self) -> bool:
        """Устанавливает зависимости аддона"""
        if not self.config.get('requirements'):
            return True
        
        try:
            for req in self.config['requirements']:
                if req:
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', req],
                        capture_output=True,
                        check=False
                    )
            return True
        except Exception as e:
            print(f"❌ Ошибка установки зависимостей: {e}")
            return False
    
    def start(self) -> Dict:
        """Запускает аддон"""
        if self.running:
            return {'status': 'already_running', 'message': 'Аддон уже запущен'}
        
        # Устанавливаем зависимости
        self.install_requirements()
        
        try:
            if self.config['type'] == 'python':
                return self._start_python()
            elif self.config['type'] == 'bash':
                return self._start_bash()
            else:
                return {'status': 'error', 'message': f'Неизвестный тип: {self.config["type"]}'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
    
    def _start_python(self) -> Dict:
        """Запускает Python аддон"""
        main_file = os.path.join(self.path, self.config['main'])
        
        if not os.path.exists(main_file):
            return {'status': 'error', 'message': f'Файл {main_file} не найден'}
        
        # Добавляем путь к аддону
        sys.path.insert(0, self.path)
        
        try:
            # Импортируем модуль
            spec = importlib.util.spec_from_file_location('addon', main_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Создаем экземпляр класса Addon
            if hasattr(module, 'Addon'):
                self.instance = module.Addon()
                if hasattr(self.instance, 'start'):
                    result = self.instance.start()
                    self.running = True
                    self._save_state()
                    return {'status': 'started', 'message': 'Аддон запущен', 'result': result}
                else:
                    return {'status': 'error', 'message': 'Класс Addon не имеет метода start()'}
            else:
                return {'status': 'error', 'message': 'Класс Addon не найден в main.py'}
        except Exception as e:
            return {'status': 'error', 'message': f'Ошибка запуска: {str(e)}'}
    
    def _start_bash(self) -> Dict:
        """Запускает Bash аддон"""
        main_file = os.path.join(self.path, self.config['main'])
        
        if not os.path.exists(main_file):
            return {'status': 'error', 'message': f'Файл {main_file} не найден'}
        
        try:
            # Делаем файл исполняемым
            os.chmod(main_file, 0o755)
            
            # Запускаем в фоне
            self.process = subprocess.Popen(
                [main_file, 'start'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.running = True
            self._save_state()
            return {'status': 'started', 'message': 'Bash аддон запущен', 'pid': self.process.pid}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
    
    def stop(self) -> Dict:
        """Останавливает аддон"""
        if not self.running:
            return {'status': 'already_stopped', 'message': 'Аддон уже остановлен'}
        
        try:
            if self.config['type'] == 'python' and self.instance and hasattr(self.instance, 'stop'):
                result = self.instance.stop()
                self.running = False
                self._save_state()
                return {'status': 'stopped', 'message': 'Аддон остановлен', 'result': result}
            elif self.config['type'] == 'bash' and self.process:
                self.process.terminate()
                self.process.wait(timeout=5)
                self.running = False
                self._save_state()
                return {'status': 'stopped', 'message': 'Bash аддон остановлен'}
            else:
                self.running = False
                self._save_state()
                return {'status': 'stopped', 'message': 'Аддон остановлен'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
    
    def status(self) -> Dict:
        """Возвращает статус аддона"""
        try:
            if self.config['type'] == 'python' and self.instance and hasattr(self.instance, 'status'):
                result = self.instance.status()
                return {'status': 'running' if self.running else 'stopped', 'result': result}
            elif self.config['type'] == 'bash' and self.process:
                if self.process.poll() is None:
                    return {'status': 'running'}
                else:
                    self.running = False
                    self._save_state()
                    return {'status': 'stopped'}
            else:
                return {'status': 'running' if self.running else 'stopped'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
    
    def execute_command(self, command: str, args: list = None) -> Dict:
        """Выполняет команду аддона"""
        if not self.running:
            return {'status': 'error', 'message': 'Аддон не запущен'}
        
        try:
            if self.config['type'] == 'python' and self.instance and hasattr(self.instance, 'execute'):
                result = self.instance.execute(command, args or [])
                return {'status': 'success', 'result': result}
            else:
                return {'status': 'error', 'message': 'Метод execute() не найден'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}


# ==================== МЕНЕДЖЕР АДДОНОВ ====================

class AddonManager:
    """Управляет всеми аддонами"""
    
    def __init__(self):
        self.addons: Dict[str, Addon] = {}
        self._load_all()
    
    def _load_all(self):
        """Загружает все аддоны из папки"""
        if not os.path.exists(ADDONS_DIR):
            return
        
        for folder in os.listdir(ADDONS_DIR):
            folder_path = os.path.join(ADDONS_DIR, folder)
            if os.path.isdir(folder_path):
                config_path = os.path.join(folder_path, "addon.conf")
                if os.path.exists(config_path):
                    try:
                        addon = Addon(folder)
                        self.addons[folder] = addon
                        print(f"✅ Загружен аддон: {addon.config['name']}")
                    except Exception as e:
                        print(f"❌ Ошибка загрузки аддона {folder}: {e}")
    
    def get_addon(self, folder: str) -> Optional[Addon]:
        """Возвращает аддон по имени папки"""
        return self.addons.get(folder)
    
    def get_all_addons(self) -> List[Dict]:
        """Возвращает список всех аддонов"""
        return [addon.get_info() for addon in self.addons.values()]
    
    def get_addons_by_startup(self) -> List[Addon]:
        """Возвращает аддоны с startup=yes"""
        return [a for a in self.addons.values() if a.config.get('startup') == 'yes']
    
    def start_all_startup(self):
        """Запускает все аддоны с startup=yes"""
        for addon in self.get_addons_by_startup():
            if not addon.running:
                result = addon.start()
                print(f"🔄 Запуск {addon.config['name']}: {result}")
    
    def reload(self):
        """Перезагружает все аддоны"""
        self.addons.clear()
        self._load_all()
