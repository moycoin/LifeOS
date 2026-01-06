#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, Dict, List, Any, Callable
from enum import Enum
from pathlib import Path
from PyQt5.QtGui import QFont
__version__ = '6.0.2'
JST = timezone(timedelta(hours=9))
def now_jst() -> datetime:
    return datetime.now(JST)
HYDRATION_INTERVAL_MINUTES = 90
AUTO_BREAK_IDLE_SECONDS = 900
PHYSICS_TICK_INTERVAL = 1.0
COMMAND_QUEUE_FILENAME = 'command_queue.json'
class Colors:
    BG_DARK = '#121212'
    BG_PANEL = '#1A1A1A'
    BG_CARD = '#1E1E1E'
    BG_ELEVATED = '#252525'
    CYAN = '#00D4AA'
    ORANGE = '#F39C12'
    RED = '#E74C3C'
    BLUE = '#3498DB'
    PURPLE = '#9B59B6'
    TEXT_PRIMARY = '#FFFFFF'
    TEXT_SECONDARY = '#B0B0B0'
    TEXT_DIM = '#606060'
    RING_READINESS = '#00D4AA'
    RING_FP = '#F39C12'
    RING_LOAD = '#E74C3C'
    BORDER = '#2A2A2A'
    BORDER_ACCENT = '#00D4AA'
class Fonts:
    FAMILY_LABEL = 'Yu Gothic UI, Meiryo, Microsoft YaHei, Segoe UI, sans-serif'
    FAMILY_NUMBER = 'Consolas, Yu Gothic UI, Meiryo, monospace'
    @staticmethod
    def label(size: int = 10, bold: bool = False) -> QFont:
        f = QFont('Yu Gothic UI', size)
        f.setStyleHint(QFont.SansSerif)
        if bold: f.setBold(True)
        return f
    @staticmethod
    def number(size: int = 14, bold: bool = True) -> QFont:
        f = QFont('Consolas', size)
        f.setStyleHint(QFont.Monospace)
        if bold: f.setBold(True)
        return f
class ActivityState(Enum):
    IDLE = 0
    LIGHT = 1
    MODERATE = 2
    DEEP_DIVE = 3
    HYPERFOCUS = 4
class CommandType(Enum):
    SET_GUI_RUNNING = 'SET_GUI_RUNNING'
    SHISHA_START = 'SHISHA_START'
    SHISHA_STOP = 'SHISHA_STOP'
    SET_AUDIO_MODE = 'SET_AUDIO_MODE'
    SET_MUTE = 'SET_MUTE'
    WAKE_MONITORS = 'WAKE_MONITORS'
    SLEEP_DETECTED = 'SLEEP_DETECTED'
    FORCE_OURA_REFRESH = 'FORCE_OURA_REFRESH'
@dataclass
class Command:
    cmd: str
    value: Any = None
    timestamp: str = ''
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = now_jst().isoformat()
    def to_dict(self) -> Dict:
        return {'cmd': self.cmd, 'value': self.value, 'timestamp': self.timestamp}
    @classmethod
    def from_dict(cls, d: Dict) -> 'Command':
        return cls(cmd=d.get('cmd', ''), value=d.get('value'), timestamp=d.get('timestamp', ''))
@dataclass
class EngineState:
    timestamp: datetime
    base_fp: float
    boost_fp: float
    effective_fp: float
    debt: float
    current_load: float
    readiness: int
    estimated_readiness: float
    continuous_work_hours: float
    decay_multiplier: float
    hours_since_wake: float
    activity_state: str
    boost_efficiency: float
    correction_factor: float
    estimated_hr: Optional[int] = None
    is_hr_estimated: bool = False
    hr_last_update: Optional[datetime] = None
    def validate(self) -> bool:
        if self.is_hr_estimated and self.estimated_hr is None:
            return False
        return True
@dataclass
class PredictionPoint:
    timestamp: datetime
    fp: float
    scenario: str
@dataclass
class Snapshot:
    timestamp: datetime
    apm: float
    hr: Optional[int]
    state: EngineState
def safe_read_json(path: Path, default: Dict = None, logger: logging.Logger = None, max_retries: int = 3) -> Dict:
    if default is None:
        default = {}
    last_error = None
    for attempt in range(max_retries):
        try:
            if not path.exists():
                return default.copy()
            content = path.read_text(encoding='utf-8').strip()
            if not content:
                if logger:
                    logger.warning(f"Empty JSON file: {path}")
                return default.copy()
            return json.loads(content)
        except json.JSONDecodeError as e:
            if logger:
                logger.error(f"JSON decode error in {path}: {e}")
            return default.copy()
        except (PermissionError, OSError) as e:
            last_error = e
            if logger and attempt < max_retries - 1:
                logger.debug(f"Retry {attempt + 1}/{max_retries} for {path}: {e}")
            time.sleep(0.1)
        except Exception as e:
            if logger:
                logger.error(f"Unexpected error reading {path}: {e}")
            return default.copy()
    if logger and last_error:
        logger.error(f"All retries failed for {path}: {last_error}")
    return default.copy()
def safe_write_json(path: Path, data: Dict, logger: logging.Logger = None, max_retries: int = 3) -> bool:
    for attempt in range(max_retries):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix('.tmp')
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
            temp_path.replace(path)
            return True
        except (PermissionError, OSError) as e:
            if logger and attempt < max_retries - 1:
                logger.debug(f"Retry {attempt + 1}/{max_retries} for {path}: {e}")
            time.sleep(0.1)
        except Exception as e:
            if logger:
                logger.error(f"Unexpected error writing {path}: {e}")
            return False
    if logger:
        logger.error(f"All retries failed writing {path}")
    return False
class CommandQueue:
    def __init__(self, queue_path: Path, logger: logging.Logger = None):
        self.queue_path = queue_path
        self.logger = logger or logging.getLogger(__name__)
    def push(self, cmd: Command) -> bool:
        data = safe_read_json(self.queue_path, {'commands': []}, self.logger)
        if 'commands' not in data:
            data['commands'] = []
        data['commands'].append(cmd.to_dict())
        return safe_write_json(self.queue_path, data, self.logger)
    def push_many(self, cmds: List[Command]) -> bool:
        data = safe_read_json(self.queue_path, {'commands': []}, self.logger)
        if 'commands' not in data:
            data['commands'] = []
        for cmd in cmds:
            data['commands'].append(cmd.to_dict())
        return safe_write_json(self.queue_path, data, self.logger)
    def pop_all(self) -> List[Command]:
        data = safe_read_json(self.queue_path, {'commands': []}, self.logger)
        commands = [Command.from_dict(d) for d in data.get('commands', [])]
        if commands:
            safe_write_json(self.queue_path, {'commands': []}, self.logger)
        return commands
    def peek(self) -> List[Command]:
        data = safe_read_json(self.queue_path, {'commands': []}, self.logger)
        return [Command.from_dict(d) for d in data.get('commands', [])]
    def clear(self) -> bool:
        return safe_write_json(self.queue_path, {'commands': []}, self.logger)
def get_root_path() -> Path:
    return Path(__file__).parent.parent.resolve()
def get_command_queue_path() -> Path:
    return get_root_path() / 'logs' / COMMAND_QUEUE_FILENAME
def get_state_path() -> Path:
    return get_root_path() / 'logs' / 'daemon_state.json'
def get_config_path() -> Path:
    return get_root_path() / 'config.json'
def get_db_path() -> Path:
    return get_root_path() / 'Data' / 'life_os.db'
def get_style_path() -> Path:
    return get_root_path() / 'Data' / 'style.qss'
def enqueue_command(cmd_type: str, value: Any = None) -> bool:
    try:
        queue = CommandQueue(get_command_queue_path())
        return queue.push(Command(cmd=cmd_type, value=value))
    except Exception:
        return False
