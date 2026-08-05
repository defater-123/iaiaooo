#!/usr/bin/env python3
"""
Загрузчик аддонов для VPS бота
Поддерживает Python и Bash скрипты
"""

import os
import json
import importlib.util
import subprocess
import sys
from typing import Dict, List, Optional, Any

ADDONS_DIR = os.path.join(os.path.dirname(__file__), "addons")
ADDONS_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Saves", "addons")

os.makedirs(ADDONS_DIR, exist_ok=True)
os.makedirs(ADDONS_DATA, exist_ok=True)

print(f"📁 Папка аддонов: {ADDONS_DIR}")


class Addon:
    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(ADDONS_DIR, folder)
        self.config = self._load_config()
        self.instance = None
        self.running = False
        self.process = None
        
        self.state_file = os.path.join(ADDONS_DATA, f"{folder}.json")
        self._load_state()
        
        print(f"✅ Загружен аддон: {self.config.get('name', folder)}")
    
    def _load_config(self) -> Dict:
        config_path = os.path.join(self.path, "addon.conf")
        config = {
            'name': self.folder,
            'version': '1.0.0',
            'author': 'Unknown',
            'description': 'No description',
            'startup': 'no',
            'type': 'python',
            'main': 'main.py',
            'requirements': [],
            'commands': []
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
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
                print(f"⚠️ Ошибка загрузки конфига {self.folder}: {e}")
        
        return config
    
    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.running = data.get('running', False)
            except:
                pass
    
    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({'running': self.running}, f, indent=2)
        except:
            pass
    
    def get_info(self) -> Dict:
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
        if self.running:
            return {'status': 'already_running', 'message': 'Аддон уже запущен'}
        
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
        main_file = os.path.join(self.path, self.config['main'])
        
        if not os.path.exists(main_file):
            return {'status': 'error', 'message': f'Файл {main_file} не найден'}
        
        sys.path.insert(0, self.path)
        
        try:
            spec = importlib.util.spec_from_file_location('addon', main_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
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
        main_file = os.path.join(self.path, self.config['main'])
        
        if not os.path.exists(main_file):
            return {'status': 'error', 'message': f'Файл {main_file} не найден'}
        
        try:
            os.chmod(main_file, 0o755)
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


class AddonManager:
    def __init__(self):
        self.addons: Dict[str, Addon] = {}
        self._load_all()
    
    def _load_all(self):
        if not os.path.exists(ADDONS_DIR):
            print(f"⚠️ Папка {ADDONS_DIR} не существует, создаю...")
            os.makedirs(ADDONS_DIR, exist_ok=True)
            return
        
        print(f"📁 Сканируем папку: {ADDONS_DIR}")
        folders = os.listdir(ADDONS_DIR)
        print(f"📁 Найдено папок: {folders}")
        
        for folder in folders:
            folder_path = os.path.join(ADDONS_DIR, folder)
            if os.path.isdir(folder_path):
                config_path = os.path.join(folder_path, "addon.conf")
                if os.path.exists(config_path):
                    try:
                        addon = Addon(folder)
                        self.addons[folder] = addon
                    except Exception as e:
                        print(f"❌ Ошибка загрузки аддона {folder}: {e}")
                else:
                    print(f"⚠️ В папке {folder} нет addon.conf")
    
    def get_addon(self, folder: str) -> Optional[Addon]:
        return self.addons.get(folder)
    
    def get_all_addons(self) -> List[Dict]:
        return [addon.get_info() for addon in self.addons.values()]
    
    def get_addons_by_startup(self) -> List[Addon]:
        return [a for a in self.addons.values() if a.config.get('startup') == 'yes']
    
    def start_all_startup(self):
        for addon in self.get_addons_by_startup():
            if not addon.running:
                result = addon.start()
                print(f"🔄 Запуск {addon.config['name']}: {result}")
    
    def reload(self):
        self.addons.clear()
        self._load_all()
