#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import bisect
import sys
import os
import json
import subprocess
import time
import math
import random
import threading
import sqlite3
import signal
import atexit
import traceback
import pygame
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Deque
from collections import deque
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath, QPixmap,
    QLinearGradient, QRadialGradient, QFontMetrics, QPolygonF
)
from PyQt5.QtCore import QTimer, Qt, QRectF, QPointF, pyqtSignal, QObject, QFile, QTextStream, QPropertyAnimation, QEasingCurve
from PyQt5.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDesktopWidget, QDialog,
    QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QProgressBar, QPushButton, QScrollArea, QScrollBar, QSizePolicy,
    QSlider, QSpinBox, QStackedWidget, QStyleFactory, QTabWidget, QTextEdit, QVBoxLayout, QWidget
)
try:
    from pynput import keyboard, mouse
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
try:
    from core.home import AmbientSync, DesktopOrganizer, PHUE_AVAILABLE, REQUESTS_AVAILABLE as HOME_REQUESTS_AVAILABLE
    HOME_AVAILABLE = True
except ImportError:
    HOME_AVAILABLE = False
    PHUE_AVAILABLE = False
    HOME_REQUESTS_AVAILABLE = False

from core.types import (
    __version__,
    JST,
    now_jst,
    Colors,
    Fonts,
    ActivityState,
    EngineState,
    PredictionPoint,
    Snapshot,
    HYDRATION_INTERVAL_MINUTES,
    AUTO_BREAK_IDLE_SECONDS,
    safe_read_json,
    safe_write_json,
)
def get_root_path() -> Path:
    return Path(__file__).parent.resolve()
ROOT_PATH = get_root_path()
class NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, event): event.ignore()
class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event): event.ignore()
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))
try:
    from core.database import LifeOSDatabase
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
try:
    from core.engine import BioEngine
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False
    class BioEngine:
        def __init__(self, *args, **kwargs):
            pass
        def update(self, *args, **kwargs):
            return None
        def predict_trajectory(self, *args, **kwargs):
            return {'continue': [], 'rest': []}
        def get_status_code(self):
            return ("INITIALIZING", "")
        def get_recommended_break_time(self):
            return None
        def get_exhaustion_time(self):
            return None
        def set_readiness(self, r):
            pass
        def set_baseline_hr(self, hr):
            pass
        def get_health_metrics(self):
            return {'effective_fp': 100, 'current_load': 0, 'estimated_readiness': 75}
        def get_prediction_bars(self, hours=8):
            return []
try:
    from core.audio import NeuroSoundEngine
    AUDIO_ENGINE_AVAILABLE = True
    print("[AudioImport] NeuroSoundEngine: OK")
except ImportError as e:
    print(f"!!! CRITICAL AUDIO ERROR (NeuroSoundEngine): {e}")
    traceback.print_exc()
    AUDIO_ENGINE_AVAILABLE = False
    NeuroSoundEngine = None
except Exception as e:
    print(f"!!! CRITICAL AUDIO ERROR (NeuroSoundEngine): {e}")
    traceback.print_exc()
    AUDIO_ENGINE_AVAILABLE = False
    NeuroSoundEngine = None
try:
    from core.audio import NeuroSoundController
    print("[AudioImport] NeuroSoundController: OK")
except ImportError as e:
    print(f"[AudioImport] NeuroSoundController not available: {e}")
    NeuroSoundController = None
except Exception as e:
    print(f"[AudioImport] NeuroSoundController error: {e}")
    NeuroSoundController = None
CONFIG_PATH = ROOT_PATH / "config.json"
config = safe_read_json(CONFIG_PATH, {
    "oura": {"api_token": ""},
    "bio_feedback": {"break": {"enabled": True}},
    "shisha": {
        "ignition_time": 930, "ventilation_time": 240,
        "heat_soak_time": 510, "calibration_time": 180, "cruise_time": 3000
    },
    "system": {"volume": 1.0, "idle_threshold_minutes": 10}
})
STATE_PATH = ROOT_PATH / "logs" / "daemon_state.json"
PID_PATH = ROOT_PATH / "logs" / "daemon.pid"
STYLE_PATH = ROOT_PATH / "Data" / "style.qss"
IDEAL_SLEEP_SECONDS = 8 * 3600
DB_PATH = ROOT_PATH / "Data" / "life_os.db"
gui_db: Optional['LifeOSDatabase'] = None
def get_gui_db() -> Optional['LifeOSDatabase']:
    global gui_db
    if gui_db is None and DB_AVAILABLE:
        try:
            gui_db = LifeOSDatabase(str(DB_PATH))
        except Exception as e:
            print(f"[GUI] DB init failed: {e}")
    return gui_db
def gui_push_command(cmd: str, value=None):
    db = get_gui_db()
    if db:
        db.push_command(cmd, value)
def get_state_from_db() -> Dict:
    db = get_gui_db()
    if not db:
        return {'brain_state': {'effective_fp': 75.0, 'status_code': 'NO_DATABASE', 'activity_state': 'IDLE'}, 'oura_details': {}}
    try:
        return db.get_combined_state()
    except Exception as e:
        print(f"[GUI] DB read failed: {e}")
        return {'brain_state': {'effective_fp': 75.0, 'status_code': 'DB_ERROR', 'activity_state': 'IDLE'}, 'oura_details': {}}
def load_stylesheet() -> str:
    try:
        if STYLE_PATH.exists():
            return STYLE_PATH.read_text(encoding='utf-8')
    except Exception as e:
        print(f"Failed to load stylesheet: {e}")
    return ""

class InputSignals(QObject):
    key_pressed = pyqtSignal()
    mouse_clicked = pyqtSignal()

class GlobalInputListener:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.signals = InputSignals()
        self.key_count = 0
        self.click_count = 0
        self.last_reset = time.time()
        self.last_input_time = 0.0
        self.intensity_history: Deque[float] = deque(maxlen=100)
        if PYNPUT_AVAILABLE:
            try:
                keyboard.Listener(on_press=self._on_key, daemon=True).start()
                mouse.Listener(on_click=self._on_click, daemon=True).start()
            except:
                pass
    def _on_key(self, key):
        self.key_count += 1
        self.last_input_time = time.time()
        try:
            self.signals.key_pressed.emit()
        except:
            pass
    def _on_click(self, x, y, button, pressed):
        if pressed:
            self.click_count += 1
            self.last_input_time = time.time()
            try:
                self.signals.mouse_clicked.emit()
            except:
                pass
    def get_intensity(self) -> float:
        now = time.time()
        elapsed = now - self.last_reset
        if elapsed >= 0.1:
            total = self.key_count + self.click_count
            apm_eq = total * 600
            intensity = min(1.0, apm_eq / 200)
            self.intensity_history.append(intensity)
            self.key_count = 0
            self.click_count = 0
            self.last_reset = now
            return intensity
        return self.intensity_history[-1] if self.intensity_history else 0.0

input_listener = GlobalInputListener()
def get_average_sleep_from_db(days: int = 14) -> Optional[float]:
    try:
        summary_db = ROOT_PATH / "Data" / "summary.db"
        legacy_db = ROOT_PATH / "Data" / "life_os.db"
        db_path = summary_db if summary_db.exists() else legacy_db
        if not db_path.exists(): return None
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute('SELECT AVG(main_sleep_seconds) FROM daily_logs WHERE date >= date(\'now\', ?)', (f'-{days} days',))
        result = cursor.fetchone()
        conn.close()
        return float(result[0]) if result and result[0] else None
    except:
        return None
def format_sleep_debt(debt_seconds: Optional[int]) -> Tuple[str, str]:
    if debt_seconds is None: return ("--", Colors.TEXT_SECONDARY)
    hours = abs(debt_seconds) / 3600
    minutes = (abs(debt_seconds) % 3600) // 60
    if debt_seconds >= 7200: return (f"-{int(hours)}h {int(minutes):02d}m", Colors.RED)
    elif debt_seconds >= 3600: return (f"-{int(hours)}h {int(minutes):02d}m", Colors.ORANGE)
    elif debt_seconds > 0: return (f"-{int(hours)}h {int(minutes):02d}m", Colors.TEXT_SECONDARY)
    elif debt_seconds < 0: return (f"+{int(hours)}h {int(minutes):02d}m", Colors.CYAN)
    return ("0h", Colors.TEXT_SECONDARY)
class SmoothProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = 0.0
        self._target = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
    def setValue(self, value: int):
        self._target = max(self.minimum(), min(self.maximum(), value))
        if not self._timer.isActive(): self._timer.start(50)
    def _animate(self):
        if abs(self._current - self._target) < 0.5:
            self._current = float(self._target)
            self._timer.stop()
        else:
            self._current += (self._target - self._current) * 0.15
        super().setValue(int(round(self._current)))
    def setValueImmediate(self, value: int):
        self._target = self._current = float(max(self.minimum(), min(self.maximum(), value)))
        super().setValue(value)
        self._timer.stop()

class TrinityCircleWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.readiness = 75
        self.fp = 100
        self.load = 0.0
        self.target_readiness = 75
        self.target_fp = 100
        self.target_load = 0.0
        self.setMinimumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._animate)
    def set_data(self, readiness: int, fp: float, load: float):
        self.target_readiness = readiness
        self.target_fp = fp
        self.target_load = load
        if not self.anim_timer.isActive(): self.anim_timer.start(50)
    def _animate(self):
        dr = abs(self.target_readiness - self.readiness)
        df = abs(self.target_fp - self.fp)
        dl = abs(self.target_load - self.load)
        if dr < 0.5 and df < 0.5 and dl < 0.01:
            self.readiness, self.fp, self.load = self.target_readiness, self.target_fp, self.target_load
            self.anim_timer.stop()
        else:
            self.readiness += (self.target_readiness - self.readiness) * 0.1
            self.fp += (self.target_fp - self.fp) * 0.1
            self.load += (self.target_load - self.load) * 0.1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        size = min(width, height)

        cx = width / 2
        cy = height / 2

        outer_radius = size * 0.42
        middle_radius = size * 0.34
        inner_radius = size * 0.26
        ring_width = size * 0.045

        self._draw_ring(painter, cx, cy, outer_radius, ring_width,
                       self.readiness / 100, Colors.RING_READINESS)

        self._draw_ring(painter, cx, cy, middle_radius, ring_width,
                       min(1.0, self.fp / 100), Colors.RING_FP)

        self._draw_ring(painter, cx, cy, inner_radius, ring_width,
                       self.load, Colors.RING_LOAD)

        painter.setPen(QColor(Colors.TEXT_PRIMARY))
        painter.setFont(Fonts.number(int(size * 0.16), True))
        text_rect = QRectF(0, cy - size * 0.10, width, size * 0.18)
        painter.drawText(text_rect, Qt.AlignCenter, f"{int(self.fp)}")
        painter.setPen(QColor(Colors.TEXT_DIM))
        painter.setFont(Fonts.label(int(size * 0.05)))
        label_rect = QRectF(0, cy + size * 0.08, width, size * 0.10)
        painter.drawText(label_rect, Qt.AlignCenter, "FP")

    def _draw_ring(self, painter, cx, cy, radius, width, progress, color):
        pen = QPen(QColor(Colors.BG_ELEVATED), int(width))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawArc(rect, 0, 360 * 16)

        if progress > 0:
            pen = QPen(QColor(color), int(width))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)

            angle = int(progress * 360 * 16)
            painter.drawArc(rect, 90 * 16, -angle)

class ResourceTimelineWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.bars = []
        self.setMinimumSize(300, 180)

    def set_data(self, bars: List[Dict]):
        self.bars = bars
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        painter.fillRect(0, 0, width, height, QColor(Colors.BG_CARD))

        painter.setPen(QPen(QColor(Colors.BORDER), 1))
        painter.drawRect(0, 0, width - 1, height - 1)

        painter.setPen(QColor(Colors.ORANGE))
        painter.setFont(Fonts.label(11, True))
        painter.drawText(15, 22, "🔥 Resource Timeline")

        if not self.bars:
            return

        margin_left = 55
        margin_right = 60
        bar_area_width = width - margin_left - margin_right
        bar_height = 18
        bar_spacing = 8
        start_y = 45

        for i, bar in enumerate(self.bars[:5]):
            y = start_y + i * (bar_height + bar_spacing)

            painter.setPen(QColor(Colors.TEXT_SECONDARY))
            painter.setFont(Fonts.label(9))
            painter.drawText(10, y + 14, bar['label'])

            painter.fillRect(int(margin_left), int(y), int(bar_area_width), int(bar_height),
                           QColor(Colors.BG_ELEVATED))

            fp = bar.get('fp', 0)
            progress = min(1.0, max(0, fp / 100))
            bar_width = int(bar_area_width * progress)

            if bar_width > 0:
                color = bar.get('color', Colors.CYAN)
                painter.fillRect(int(margin_left), int(y), bar_width, int(bar_height),
                               QColor(color))

            painter.setPen(QColor(Colors.TEXT_PRIMARY))
            painter.setFont(Fonts.number(10))
            painter.drawText(int(margin_left + bar_area_width + 8), int(y + 14), f"{int(fp)} FP")

class ResourceCurveWidget(QWidget):
    COLOR_CONTINUE = '#FF6B00'
    COLOR_REST = '#00D4AA'
    def __init__(self):
        super().__init__()
        self.continue_points = []
        self.rest_points = []
        self.setMinimumSize(300, 140)
    def set_data(self, prediction: Dict):
        if not prediction:
            self.continue_points = []
            self.rest_points = []
        else:
            now = now_jst()
            cont_raw = prediction.get('continue', [])
            rest_raw = prediction.get('rest', [])
            if cont_raw and isinstance(cont_raw[0], dict):
                self.continue_points = [type('P', (), {'timestamp': now + timedelta(minutes=p.get('minutes', 0)), 'fp': p.get('fp', 0)})() for p in cont_raw]
            else:
                self.continue_points = cont_raw
            if rest_raw and isinstance(rest_raw[0], dict):
                self.rest_points = [type('P', (), {'timestamp': now + timedelta(minutes=p.get('minutes', 0)), 'fp': p.get('fp', 0)})() for p in rest_raw]
            else:
                self.rest_points = rest_raw
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        painter.fillRect(0, 0, width, height, QColor(Colors.BG_CARD))

        painter.setPen(QPen(QColor(Colors.BORDER), 1))
        painter.drawRect(0, 0, width - 1, height - 1)

        painter.setPen(QColor(Colors.ORANGE))
        painter.setFont(Fonts.label(11, True))
        painter.drawText(15, 22, "📈 Resource Trajectory")

        margin_left = 45
        margin_right = 15
        margin_top = 38
        margin_bottom = 30

        graph_width = width - margin_left - margin_right
        graph_height = height - margin_top - margin_bottom

        if graph_width <= 0 or graph_height <= 0:
            return

        painter.setPen(QPen(QColor(Colors.BG_ELEVATED), 1))
        painter.setFont(Fonts.label(8))

        for fp_val in [0, 25, 50, 75, 100]:
            y = margin_top + graph_height * (1 - fp_val / 100)

            painter.setPen(QPen(QColor(Colors.BG_ELEVATED), 1, Qt.DotLine))
            painter.drawLine(int(margin_left), int(y), int(width - margin_right), int(y))

            painter.setPen(QColor(Colors.TEXT_DIM))
            painter.drawText(5, int(y + 4), f"{fp_val}")

        x_labels = [(0, '+0h'), (60, '+1h'), (120, '+2h'), (240, '+4h')]
        max_minutes = 240

        painter.setFont(Fonts.label(8))
        painter.setPen(QColor(Colors.TEXT_DIM))

        for minutes, label in x_labels:
            x = margin_left + (minutes / max_minutes) * graph_width
            painter.drawText(int(x - 10), int(height - 8), label)

        if not self.continue_points and not self.rest_points:
            painter.setPen(QColor(Colors.TEXT_DIM))
            painter.setFont(Fonts.label(10))
            painter.drawText(QRectF(margin_left, margin_top, graph_width, graph_height),
                           Qt.AlignCenter, "No prediction data")
            return

        base_time = self.continue_points[0].timestamp if self.continue_points else now_jst()

        if self.rest_points:
            self._draw_curve(painter, self.rest_points, base_time, max_minutes,
                           margin_left, margin_top, graph_width, graph_height,
                           self.COLOR_REST, dashed=True)

        if self.continue_points:
            self._draw_curve(painter, self.continue_points, base_time, max_minutes,
                           margin_left, margin_top, graph_width, graph_height,
                           self.COLOR_CONTINUE, dashed=False)

        legend_y = margin_top + 5

        painter.setPen(QPen(QColor(self.COLOR_CONTINUE), 2))
        painter.drawLine(int(margin_left + graph_width - 100), int(legend_y),
                        int(margin_left + graph_width - 80), int(legend_y))
        painter.setPen(QColor(Colors.TEXT_SECONDARY))
        painter.setFont(Fonts.label(8))
        painter.drawText(int(margin_left + graph_width - 75), int(legend_y + 4), "Continue")

        legend_y += 12
        pen = QPen(QColor(self.COLOR_REST), 2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(int(margin_left + graph_width - 100), int(legend_y),
                        int(margin_left + graph_width - 80), int(legend_y))
        painter.setPen(QColor(Colors.TEXT_SECONDARY))
        painter.drawText(int(margin_left + graph_width - 75), int(legend_y + 4), "Rest")

    def _draw_curve(self, painter, points, base_time, max_minutes,
                   margin_left, margin_top, graph_width, graph_height,
                   color, dashed=False):
        if not points or len(points) < 2:
            return

        DEPLETED_THRESHOLD = 12
        DEPLETED_COLOR = Colors.RED

        current_path = QPainterPath()
        current_depleted = None
        first = True
        prev_x, prev_y = 0, 0

        for point in points:
            dt_minutes = (point.timestamp - base_time).total_seconds() / 60
            if dt_minutes > max_minutes:
                break

            x = margin_left + (dt_minutes / max_minutes) * graph_width

            fp = max(0, min(100, point.fp))
            y = margin_top + graph_height * (1 - fp / 100)

            is_depleted = fp < DEPLETED_THRESHOLD

            if first:
                current_path.moveTo(x, y)
                current_depleted = is_depleted
                first = False
            else:
                if is_depleted != current_depleted:
                    self._draw_path_segment(painter, current_path,
                                           DEPLETED_COLOR if current_depleted else color,
                                           dashed)
                    current_path = QPainterPath()
                    current_path.moveTo(prev_x, prev_y)
                    current_path.lineTo(x, y)
                    current_depleted = is_depleted
                else:
                    current_path.lineTo(x, y)

            prev_x, prev_y = x, y

        if not first:
            self._draw_path_segment(painter, current_path,
                                   DEPLETED_COLOR if current_depleted else color,
                                   dashed)

    def _draw_path_segment(self, painter, path, color, dashed=False):
        if dashed:
            pen = QPen(QColor(color), 2, Qt.DashLine)
        else:
            pen = QPen(QColor(color), 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

class InfoCardWidget(QWidget):
    def __init__(self, title: str = "LABEL"):
        super().__init__()
        self.title = title
        self.value = "--"
        self.sub_value = ""
        self.accent_color = Colors.CYAN
        self.is_highlighted = False
        self.setMinimumSize(120, 80)
        self.setMaximumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, value: str, sub_value: str = "", color: str = None, highlighted: bool = False):
        self.value = value
        self.sub_value = sub_value
        if color:
            self.accent_color = color
        self.is_highlighted = highlighted
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if self.is_highlighted:
            painter.fillRect(0, 0, width, height, QColor('#1A3A4A'))
        else:
            painter.fillRect(0, 0, width, height, QColor(Colors.BG_CARD))
        border_color = self.accent_color if self.is_highlighted else Colors.BORDER
        painter.setPen(QPen(QColor(border_color), 1))
        painter.drawRect(0, 0, width - 1, height - 1)
        cy = height // 2
        painter.setPen(QColor(Colors.TEXT_DIM))
        painter.setFont(Fonts.label(9))
        painter.drawText(QRectF(0, cy - 32, width, 16), Qt.AlignCenter, self.title)
        painter.setPen(QColor(self.accent_color))
        painter.setFont(Fonts.number(18, True))
        painter.drawText(QRectF(0, cy - 14, width, 28), Qt.AlignCenter, self.value)
        if self.sub_value:
            painter.setPen(QColor(Colors.TEXT_DIM))
            painter.setFont(Fonts.label(8))
            painter.drawText(QRectF(0, cy + 16, width, 14), Qt.AlignCenter, self.sub_value)

class TelemetryStripWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.glow_intensity = 0.0
        self.pulses: List[Dict] = []
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        if PYNPUT_AVAILABLE:
            input_listener.signals.key_pressed.connect(self._on_input)
            input_listener.signals.mouse_clicked.connect(self._on_input)
    def _on_input(self):
        self.glow_intensity = 1.0
        self.pulses.append({'x': 0.5, 'intensity': 1.0})
        if not self.timer.isActive(): self.timer.start(50)
        self._check_shisha_resume()
        self._check_sleep_wake()
        self._notify_user_activity()
    def _notify_user_activity(self):
        try:
            app = QApplication.instance()
            if app:
                for widget in app.topLevelWidgets():
                    if hasattr(widget, 'home_tab'):
                        home = widget.home_tab
                        if hasattr(home, 'ambient_sync') and home.ambient_sync:
                            home.ambient_sync.update_user_activity(True)
                            break
        except Exception: pass
    def _check_sleep_wake(self):
        try:
            state = get_state_from_db()
            if state.get('is_sleeping', False):
                gui_push_command('WAKE_MONITORS')
                app = QApplication.instance()
                if app:
                    for widget in app.topLevelWidgets():
                        if hasattr(widget, 'home_tab'):
                            home = widget.home_tab
                            if hasattr(home, 'ambient_sync') and home.ambient_sync:
                                home.ambient_sync.wake_monitors()
                                break
        except Exception:
            pass
    def _check_shisha_resume(self):
        try:
            state = get_state_from_db()
            is_shisha_active = state.get('is_shisha_active', False)
            app = QApplication.instance()
            if app and not is_shisha_active:
                for widget in app.topLevelWidgets():
                    if hasattr(widget, 'dashboard_tab'):
                        dashboard = widget.dashboard_tab
                        if hasattr(dashboard, 'neuro_sound') and dashboard.neuro_sound:
                            ns = dashboard.neuro_sound
                            if hasattr(ns, '_shisha_faded_out') and ns._shisha_faded_out:
                                ns.resume_from_shisha()
                                ns._shisha_faded_out = False
                            break
        except Exception:
            pass
    def _tick(self):
        self.glow_intensity *= 0.9
        for p in self.pulses:
            p['intensity'] *= 0.85
        self.pulses = [p for p in self.pulses if p['intensity'] > 0.05]
        if self.glow_intensity < 0.01 and not self.pulses:
            self.glow_intensity = 0.0
            self.timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width()
        height = self.height()

        base_color = QColor(Colors.BORDER)
        painter.fillRect(0, 0, width, height, base_color)

        if self.glow_intensity > 0.05:
            glow = QColor(Colors.CYAN)
            glow.setAlpha(int(self.glow_intensity * 100))
            painter.fillRect(0, 0, width, height, glow)

class DashboardTab(QWidget):
    HEADER_STYLE_BASE = """
        QFrame#statusFrame {{
            background-color: #1A1A1A;
            border: none;
            border-left: 4px solid {color};
            border-radius: 4px;
            margin: 0px;
            padding: 0px;
        }}
    """
    COLOR_RECOVERY = '#27C93F'
    def __init__(self):
        super().__init__()
        try:
            pygame.mixer.init()
        except:
            pass
        self.neuro_sound: Optional[NeuroSoundEngine] = None
        self.neuro_controller: Optional[NeuroSoundController] = None
        if AUDIO_ENGINE_AVAILABLE and NeuroSoundEngine:
            try:
                print("=" * 50)
                print("[Audio Engine] Initializing...")
                data_path = ROOT_PATH / "Data"
                audio_config = config.get('audio', {})
                openai_config = config.get('openai', {})
                print(f"[Audio Engine] Data path: {data_path}")
                print(f"[Audio Engine] Config: enabled={audio_config.get('enabled', True)}, bgm_vol={audio_config.get('bgm_volume', 0.08)}")
                print(f"[NLC] Config loaded. API Key present: {'Yes' if openai_config.get('api_key') else 'No'}")
                self.neuro_sound = NeuroSoundEngine(data_path, config)
                print("[Audio Engine] Calling initialize()...")
                self.neuro_sound.initialize()
                if NeuroSoundController:
                    self.neuro_controller = NeuroSoundController(self.neuro_sound)
                    print("[Audio Engine] NeuroSoundController attached")
                else:
                    print("[Audio Engine] NeuroSoundController not available (standalone mode)")
                print(f"[Audio Engine] SUCCESS - initialized at {data_path}/sounds")
                print(f"[Audio Engine] BGM={audio_config.get('bgm_volume', 0.08)*100:.0f}%, NLC=Bio-Adaptive, Inertia={audio_config.get('state_inertia_seconds', 30)}s")
                print("=" * 50)
            except Exception as e:
                print(f"!!! AUDIO ENGINE INIT FAILED: {e}")
                traceback.print_exc()
                self.neuro_sound = None
                self.neuro_controller = None
        else:
            print(f"[Audio Engine] SKIPPED - AUDIO_ENGINE_AVAILABLE={AUDIO_ENGINE_AVAILABLE}, NeuroSoundEngine={NeuroSoundEngine is not None}")
        self._cached_state = {}
        self._cached_details = {}
        self._cached_brain_state = {}
        self._cached_effective_apm = 0
        self._smoothed_mouse_speed = 0.0
        self._mouse_speed_alpha = 0.15
        self.initUI()
        self.fast_timer = QTimer(self)
        self.fast_timer.timeout.connect(self.update_fast)
        self.fast_timer.start(100)
        self.slow_timer = QTimer(self)
        self.slow_timer.timeout.connect(self.update_slow)
        self.slow_timer.start(1000)
        self._is_minimized = False
        self._is_focused = True
        self._current_slow_interval = 1000
    def enterEvent(self, event):
        self._is_focused = True
        if self._current_slow_interval != 1000:
            self._current_slow_interval = 1000
            self.slow_timer.setInterval(1000)
        super().enterEvent(event)
    def leaveEvent(self, event):
        self._is_focused = False
        super().leaveEvent(event)
    def changeEvent(self, event):
        if event.type() == event.WindowStateChange:
            window = self.window()
            self._is_minimized = window.isMinimized() if window else False
        super().changeEvent(event)

    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(12, 4, 12, 8)
        self.status_frame = QFrame()
        self.status_frame.setObjectName("statusFrame")
        self.status_frame.setStyleSheet(self.HEADER_STYLE_BASE.format(color=Colors.CYAN))
        status_layout = QVBoxLayout(self.status_frame)
        status_layout.setContentsMargins(15, 8, 15, 8)
        status_layout.setSpacing(2)
        self.status_label = QLabel("INITIALIZING")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignLeft)
        self.status_label.setFont(Fonts.number(18, True))
        self.status_label.setStyleSheet(f"color: {Colors.CYAN}; letter-spacing: 1px; border: none; background: transparent;")
        status_layout.addWidget(self.status_label)
        self.status_sub = QLabel("APM: 0 | MOUSE: 0px/s | CORR: 0%")
        self.status_sub.setObjectName("subStatusLabel")
        self.status_sub.setAlignment(Qt.AlignLeft)
        self.status_sub.setFont(Fonts.label(11))
        self.status_sub.setTextFormat(Qt.RichText)
        self.status_sub.setStyleSheet("color: #666666; border: none; background: transparent;")
        status_layout.addWidget(self.status_sub)
        main_layout.addWidget(self.status_frame)
        main_layout.addStretch(1)
        circle_container = QHBoxLayout()
        circle_container.setContentsMargins(0, 0, 0, 0)
        circle_container.addStretch(1)
        self.trinity_circle = TrinityCircleWidget()
        self.trinity_circle.setFixedSize(380, 380)
        circle_container.addWidget(self.trinity_circle)
        circle_container.addStretch(1)
        main_layout.addLayout(circle_container)
        main_layout.addStretch(1)
        info_row = QHBoxLayout()
        info_row.setSpacing(20)
        self.info_widgets = {}
        for key, label, default in [('next_break', '☕ Next Break:', 'SAFE'), ('bedtime', '⚡ Bedtime:', 'SAFE'), ('recovery', '📋 Recovery:', '+0.0'), ('sleep', '💤 Sleep:', '--')]:
            item_layout = QHBoxLayout()
            item_layout.setSpacing(6)
            lbl = QLabel(label)
            lbl.setObjectName("footerLabel")
            lbl.setFont(Fonts.label(10))
            lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
            item_layout.addWidget(lbl)
            val = QLabel(default)
            val.setObjectName("footerValue")
            val.setFont(Fonts.number(14, True))
            val.setStyleSheet(f"color: {Colors.CYAN};")
            item_layout.addWidget(val)
            self.info_widgets[key] = val
            info_row.addLayout(item_layout)
        info_row.addStretch(1)
        self.mute_btn = QPushButton("🔇 Mute")
        self.mute_btn.setFont(Fonts.label(10))
        self.mute_btn.setFixedSize(80, 28)
        self.mute_btn.setCursor(Qt.PointingHandCursor)
        self.mute_btn.setStyleSheet(f"QPushButton{{background-color:{Colors.CYAN};color:{Colors.BG_DARK};border:none;border-radius:4px;font-weight:bold;}}QPushButton:hover{{background-color:{Colors.BLUE};}}")
        self.mute_btn.clicked.connect(self._toggle_mute)
        info_row.addWidget(self.mute_btn)
        main_layout.addLayout(info_row)
        main_layout.addSpacing(10)
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(15)
        self.resource_curve = ResourceCurveWidget()
        self.resource_curve.setMinimumSize(350, 140)
        self.resource_curve.setMaximumHeight(180)
        bottom_layout.addWidget(self.resource_curve, 3)
        cards_grid = QGridLayout()
        cards_grid.setSpacing(8)
        self.card_widgets = {}
        card_items = [('temp', 'TEMP', 0, 0), ('heart', 'HEART', 0, 1), ('rhr', 'RHR', 1, 0), ('stress', 'STRESS', 1, 1)]
        for key, title, row, col in card_items:
            card = InfoCardWidget(title)
            self.card_widgets[key] = card
            cards_grid.addWidget(card, row, col)
        bottom_layout.addLayout(cards_grid, 2)
        main_layout.addLayout(bottom_layout)
        self.telemetry = TelemetryStripWidget()
        main_layout.addWidget(self.telemetry)
        self.setLayout(main_layout)

    def _get_sleep_from_db(self) -> Optional[int]:
        try:
            summary_db = ROOT_PATH / "Data" / "summary.db"
            legacy_db = ROOT_PATH / "Data" / "life_os.db"
            db_path = summary_db if summary_db.exists() else legacy_db
            if not db_path.exists(): return None
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute('SELECT main_sleep_seconds FROM daily_logs WHERE date = date(\'now\') LIMIT 1')
            row = cursor.fetchone()
            conn.close()
            return row[0] if row and row[0] else None
        except:
            return None
    def update_fast(self):
        try:
            apm = self._cached_brain_state.get('apm', 0) or 0
            mouse_pixels = self._cached_brain_state.get('mouse_pixels', 0) or 0
            recent_corr = self._cached_brain_state.get('recent_correction_rate', 0) or 0
            corr_pct = int(recent_corr * 100)
            load = self._cached_brain_state.get('current_load', 0) or 0
            load_pct = int(load * 100)
            corr_color = Colors.TEXT_SECONDARY if corr_pct < 5 else (Colors.ORANGE if corr_pct <= 15 else Colors.RED)
            load_color = Colors.TEXT_SECONDARY if load_pct < 80 else Colors.RED
            self.status_sub.setText(f"APM: {apm} | MOUSE: {int(mouse_pixels)}px | CORR: <font color='{corr_color}'>{corr_pct}%</font> | LOAD: <font color='{load_color}'>{load_pct}%</font>")
        except Exception as e:
            pass

    def update_slow(self):
        if self._is_minimized: return
        try:
            state = get_state_from_db()
            details = state.get('oura_details', {})
            brain_state = state.get('brain_state', {})
            self._cached_state = state
            self._cached_details = details
            self._cached_brain_state = brain_state
            readiness = state.get('last_oura_score', 75) or 75
            is_shisha_active = state.get('is_shisha_active', False)
            phantom_recovery_accumulated = brain_state.get('phantom_recovery', 0) or 0
            total_nap_minutes = details.get('total_nap_minutes', 0.0) or 0.0
            fp = brain_state.get('effective_fp', 75.0) or 75.0
            load = brain_state.get('current_load', 0.0) or 0.0
            estimated_readiness = brain_state.get('estimated_readiness', readiness) or readiness
            activity_state = brain_state.get('activity_state', 'IDLE')
            idle_seconds = state.get('idle_seconds', 0)
            target_interval = 5000 if idle_seconds > 60 else 1000
            if self._current_slow_interval != target_interval:
                self._current_slow_interval = target_interval
                self.slow_timer.setInterval(target_interval)
            if self.neuro_controller:
                audio_state = 'SHISHA' if is_shisha_active else activity_state
                self.neuro_controller.update_state(audio_state, fp=fp, idle_seconds=idle_seconds)
                if self.neuro_sound: self.neuro_sound.update_bio_context(audio_state, load)
            elif self.neuro_sound:
                audio_state = 'SHISHA' if is_shisha_active else activity_state
                self.neuro_sound.set_mode(audio_state)
                self.neuro_sound.update_bio_context(audio_state, load)
            is_recovery_mode = activity_state == 'IDLE' and phantom_recovery_accumulated > 0
            op_code = brain_state.get('status_code', 'INITIALIZING')
            state_label = brain_state.get('state_label', 'IDLE')
            if is_shisha_active or 'SHISHA' in str(op_code):
                status_color = Colors.PURPLE
                display_status = 'SHISHA ACTIVE'
            elif is_recovery_mode:
                display_status = "RECOVERY ACTIVE"
                status_color = self.COLOR_RECOVERY
            elif 'CRITICAL' in str(op_code) or 'DEPLETED' in str(op_code):
                status_color = Colors.RED
                display_status = f"{op_code} [{state_label}]"
            elif 'WARNING' in str(op_code) or 'CAUTION' in str(op_code) or 'EXTENDED' in str(op_code) or 'HYDRATION' in str(op_code):
                status_color = Colors.ORANGE
                display_status = f"{op_code} [{state_label}]"
            else:
                status_color = Colors.CYAN
                display_status = f"{op_code} [{state_label}]" if op_code and state_label else (op_code or 'INITIALIZING')
            self.status_label.setText(display_status)
            self.status_frame.setStyleSheet(self.HEADER_STYLE_BASE.format(color=status_color))
            self.status_label.setStyleSheet(f"color: {status_color}; letter-spacing: 2px; border: none; background: transparent;")
            self.trinity_circle.set_data(int(estimated_readiness), fp, load)
            now = now_jst()
            recommended_break_iso = brain_state.get('recommended_break_iso')
            if recommended_break_iso:
                try:
                    recommended_break = datetime.fromisoformat(recommended_break_iso)
                    if recommended_break.tzinfo is None:
                        recommended_break = recommended_break.replace(tzinfo=JST)
                    remaining_to_break = (recommended_break - now).total_seconds()
                    self.info_widgets['next_break'].setText(recommended_break.strftime('%H:%M'))
                    self.info_widgets['next_break'].setStyleSheet(f"color: {Colors.RED if remaining_to_break < 1800 else (Colors.ORANGE if remaining_to_break < 3600 else Colors.CYAN)};")
                except:
                    self.info_widgets['next_break'].setText('--:--')
                    self.info_widgets['next_break'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
            else:
                self.info_widgets['next_break'].setText('--:--')
                self.info_widgets['next_break'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
            exhaustion_iso = brain_state.get('exhaustion_iso')
            if exhaustion_iso:
                try:
                    exhaustion_time = datetime.fromisoformat(exhaustion_iso)
                    if exhaustion_time.tzinfo is None:
                        exhaustion_time = exhaustion_time.replace(tzinfo=JST)
                    remaining_to_exhaustion = (exhaustion_time - now).total_seconds()
                    self.info_widgets['bedtime'].setText(exhaustion_time.strftime('%H:%M'))
                    self.info_widgets['bedtime'].setStyleSheet(f"color: {Colors.RED if remaining_to_exhaustion < 3600 else (Colors.ORANGE if remaining_to_exhaustion < 7200 else Colors.CYAN)};")
                except:
                    self.info_widgets['bedtime'].setText('--:--')
                    self.info_widgets['bedtime'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
            else:
                self.info_widgets['bedtime'].setText('--:--')
                self.info_widgets['bedtime'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
            oura_recovery = details.get('recovery_score', 0) or 0
            phantom_sum = brain_state.get('phantom_recovery_sum', 0) or 0
            ceiling = brain_state.get('recovery_ceiling', 100) or 100
            total_potential = oura_recovery + phantom_sum
            effective_recovery = max(0, min(total_potential, ceiling - fp))
            recovery_eff = brain_state.get('recovery_efficiency', 1.0) or 1.0
            self.info_widgets['recovery'].setText(f"+{effective_recovery:.1f}")
            recovery_color = self.COLOR_RECOVERY if recovery_eff >= 1.0 else (Colors.RED if recovery_eff < 0.5 else Colors.ORANGE)
            self.info_widgets['recovery'].setStyleSheet(f"color: {recovery_color};")
            main_sleep = self._get_sleep_from_db() or details.get('main_sleep_seconds') or 0
            if main_sleep < 1800:
                main_sleep = details.get('max_continuous_rest_seconds') or 0
            debt = (IDEAL_SLEEP_SECONDS - main_sleep) if main_sleep > 0 else None
            debt_text, debt_color = format_sleep_debt(debt)
            self.info_widgets['sleep'].setText(debt_text)
            self.info_widgets['sleep'].setStyleSheet(f"color: {debt_color};")
            prediction = brain_state.get('prediction', {'continue': [], 'rest': []})
            if prediction and (prediction.get('continue') or prediction.get('rest')):
                self.resource_curve.set_data(prediction)
            temp = details.get('temperature_deviation')
            self.card_widgets['temp'].set_data(f"{temp:+.2f}°C" if temp is not None else "--", "", Colors.BLUE if temp is not None else Colors.TEXT_DIM)
            hr = details.get('current_hr')
            hr_stream = details.get('hr_stream', [])
            hr_time = ""
            if hr_stream:
                try:
                    ts = datetime.fromisoformat(hr_stream[-1]['timestamp'])
                    hr_time = f"({ts.strftime('%H:%M')})"
                except:
                    pass
            is_hr_estimated = brain_state.get('is_hr_estimated', False)
            estimated_hr = brain_state.get('estimated_hr')
            if is_hr_estimated and estimated_hr is not None:
                true_rhr = details.get('true_rhr')
                jitter = random.randint(-2, 3)
                display_hr = estimated_hr + jitter
                if true_rhr:
                    display_hr = max(true_rhr + 5, display_hr)
                self.card_widgets['heart'].set_data(f"~{display_hr} bpm", "(EST)", Colors.TEXT_DIM)
            elif hr:
                self.card_widgets['heart'].set_data(f"{hr} bpm", hr_time, Colors.TEXT_PRIMARY)
            else:
                self.card_widgets['heart'].set_data("-- bpm", "", Colors.TEXT_DIM)
            rhr = details.get('true_rhr')
            rhr_time = ""
            rest_times = [e for e in hr_stream if e.get('source') == 'rest']
            if rest_times:
                try:
                    ts = datetime.fromisoformat(rest_times[-1]['timestamp'])
                    rhr_time = f"({ts.strftime('%H:%M')})"
                except:
                    pass
            self.card_widgets['rhr'].set_data(f"{rhr} bpm" if rhr else "-- bpm", rhr_time if rhr else "", Colors.CYAN if rhr else Colors.TEXT_DIM, False)
            stress_index = brain_state.get('stress_index', 0) or 0
            stress_color = Colors.RED if stress_index >= 80 else (Colors.ORANGE if stress_index >= 50 else Colors.CYAN)
            self.card_widgets['stress'].set_data(f"STR: {int(stress_index)}", "", stress_color)
            self.mute_btn.setText("🔊 Unmute" if state.get('is_muted') else "🔇 Mute")
        except Exception as e:
            print(f"Dashboard slow update error: {e}")

    def _toggle_mute(self):
        db = get_gui_db()
        if not db: return
        state = db.get_daemon_state()
        is_muted = not state.get('is_muted', False)
        db.update_daemon_state(is_muted=is_muted)
        if self.neuro_sound:
            self.neuro_sound.set_enabled(not is_muted)

class SequenceTab(QWidget):
    STAGES = [
        {'name': 'IGNITION', 'key': 'ignition_time', 'color': Colors.CYAN, 'voice': 'phase1_ignition.wav'},
        {'name': 'VENTILATION', 'key': 'ventilation_time', 'color': Colors.BLUE, 'voice': 'phase2_ventilation.wav'},
        {'name': 'HEAT SOAK', 'key': 'heat_soak_time', 'color': Colors.ORANGE, 'voice': 'phase3_heatsoak.wav'},
        {'name': 'CALIBRATION', 'key': 'calibration_time', 'color': Colors.RED, 'voice': 'phase4_calibration.wav'},
        {'name': 'CRUISE MODE', 'key': 'cruise_time', 'color': Colors.PURPLE, 'voice': 'phase5_termination.wav'},
    ]
    INTRO_VOICE = 'sys_intro_init.wav'
    def __init__(self, database=None):
        super().__init__()
        self.stages_config = []
        self.current_stage = 0
        self.remaining = 0
        self.is_running = False
        self.audio_thread = None
        self.database = database
        self._current_session_id: Optional[int] = None
        self._session_start_time: Optional[datetime] = None
        self._handle_incomplete_session()
        self._force_reset_shisha_state()
        try: pygame.mixer.init()
        except: pass
        self.initUI()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def _handle_incomplete_session(self):
        if self.database is None:
            return

        try:
            incomplete = self.database.get_incomplete_shisha_session()
            if incomplete:
                session_id = incomplete['id']
                start_time_str = incomplete['start_time']

                now = now_jst()
                self.database.end_shisha_session(session_id, now, completed=False)

                print(f"v3.7 Shisha Recovery: Auto-closed incomplete session "
                      f"(id={session_id}, started={start_time_str})")
        except Exception as e:
            print(f"v3.7 Shisha Recovery: Could not handle incomplete session ({e})")

    def _force_reset_shisha_state(self):
        try:
            db = get_gui_db()
            if db:
                state = db.get_daemon_state()
                if state.get('is_shisha_active', False):
                    db.update_daemon_state(is_shisha_active=False, current_shisha_session_id=None)
        except Exception:
            pass

    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("🌿 Shisha Sequence")
        title.setFont(Fonts.number(20, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        self.circle_widget = ShishaCircleWidget()
        self.circle_widget.setFixedSize(280, 280)

        circle_container = QHBoxLayout()
        circle_container.addStretch()
        circle_container.addWidget(self.circle_widget)
        circle_container.addStretch()
        main_layout.addLayout(circle_container)

        stages_layout = QHBoxLayout()
        stages_layout.setSpacing(10)
        stages_layout.setAlignment(Qt.AlignCenter)

        self.stage_labels = []
        for i, stage in enumerate(self.STAGES):
            lbl = QLabel(stage['name'])
            lbl.setFont(Fonts.label(9))
            lbl.setStyleSheet(f"""
                color: {Colors.TEXT_DIM};
                padding: 5px 10px;
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
            """)
            lbl.setAlignment(Qt.AlignCenter)
            self.stage_labels.append(lbl)
            stages_layout.addWidget(lbl)

        main_layout.addLayout(stages_layout)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(15)
        controls_layout.setAlignment(Qt.AlignCenter)

        self.start_btn = QPushButton("▶ START")
        self.start_btn.setFont(Fonts.number(14, True))
        self.start_btn.setFixedSize(140, 50)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.CYAN};
                color: {Colors.BG_DARK};
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BLUE};
            }}
            QPushButton:disabled {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_DIM};
            }}
        """)
        self.start_btn.clicked.connect(self._start)
        controls_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■  STOP")
        self.stop_btn.setFont(Fonts.number(14, True))
        self.stop_btn.setFixedSize(140, 50)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED};
                color: {Colors.TEXT_PRIMARY};
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: #C0392B;
            }}
            QPushButton:disabled {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_DIM};
            }}
        """)
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        controls_layout.addWidget(self.stop_btn)
        main_layout.addLayout(controls_layout)
        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)
        vol_row.addStretch()
        vol_row.addWidget(QLabel("Voice Volume:"))
        self.shisha_volume_slider = QSlider(Qt.Horizontal)
        self.shisha_volume_slider.setRange(0, 200)
        self.shisha_volume_slider.setValue(int(config.get('audio', {}).get('shisha_volume', 0.5) * 100))
        self.shisha_volume_slider.setFixedWidth(150)
        self.shisha_volume_slider.valueChanged.connect(self._on_shisha_volume_changed)
        vol_row.addWidget(self.shisha_volume_slider)
        self.shisha_vol_label = QLabel(f"{int(config.get('audio', {}).get('shisha_volume', 0.5) * 100)}%")
        self.shisha_vol_label.setMinimumWidth(45)
        vol_row.addWidget(self.shisha_vol_label)
        test_btn = QPushButton("🔊 Test")
        test_btn.setFixedWidth(60)
        test_btn.setCursor(Qt.PointingHandCursor)
        test_btn.setStyleSheet(f"QPushButton{{background-color:{Colors.BG_ELEVATED};color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:4px;padding:4px;}}QPushButton:hover{{background-color:{Colors.BLUE};}}")
        test_btn.clicked.connect(self._on_shisha_volume_test)
        vol_row.addWidget(test_btn)
        vol_row.addStretch()
        main_layout.addLayout(vol_row)
        timing_group = QGroupBox("Timing Settings")
        timing_group.setStyleSheet(f"QGroupBox{{font-size:11pt;color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:6px;margin-top:12px;padding-top:12px;}}QGroupBox::title{{subcontrol-origin:margin;left:10px;}}")
        timing_layout = QGridLayout()
        timing_layout.setHorizontalSpacing(15)
        timing_layout.setVerticalSpacing(8)
        self.shisha_spins = {}
        stages = [('ignition_time', 'Ignition', 930), ('ventilation_time', 'Ventilation', 240), ('heat_soak_time', 'Heat Soak', 510), ('calibration_time', 'Calibration', 180), ('cruise_time', 'Cruise', 3000)]
        for i, (key, label, default) in enumerate(stages):
            row, col = i // 3, (i % 3) * 2
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
            timing_layout.addWidget(lbl, row, col)
            spin = NoScrollDoubleSpinBox()
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.setRange(0.5, 120.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setValue(config.get('shisha', {}).get(key, default) / 60.0)
            spin.setSuffix(" min")
            spin.setStyleSheet(f"QDoubleSpinBox{{background-color:{Colors.BG_ELEVATED};color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:4px;padding:4px 6px;font-family:{Fonts.FAMILY_NUMBER};}}")
            spin.setFixedWidth(90)
            spin.valueChanged.connect(lambda v, k=key: self._on_timing_changed(k, v))
            timing_layout.addWidget(spin, row, col + 1)
            self.shisha_spins[key] = spin
        timing_group.setLayout(timing_layout)
        main_layout.addWidget(timing_group)
        main_layout.addStretch()
        self.setLayout(main_layout)
    def _on_timing_changed(self, key: str, value: float):
        global config
        if 'shisha' not in config: config['shisha'] = {}
        config['shisha'][key] = int(value * 60)
        safe_write_json(CONFIG_PATH, config)
    def _on_shisha_volume_changed(self, value: int):
        global config
        self.shisha_vol_label.setText(f"{value}%")
        if 'audio' not in config: config['audio'] = {}
        config['audio']['shisha_volume'] = value / 100.0
        safe_write_json(CONFIG_PATH, config)
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_shisha_volume(value / 100.0)
    def _on_shisha_volume_test(self):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound:
            sound_dir = ROOT_PATH / "Data" / "sounds" / "shisha"
            intro_path = sound_dir / self.INTRO_VOICE
            if intro_path.exists():
                neuro_sound.play_shisha_voice(intro_path)
                print("[Shisha] Test voice played")
            else:
                print(f"[Shisha] Test voice not found: {intro_path}")

    def _start(self):
        shisha_config = config.get('shisha', {})
        self.stages_config = []
        for stage in self.STAGES:
            self.stages_config.append({
                'name': stage['name'],
                'seconds': shisha_config.get(stage['key'], 420),
                'color': stage['color'],
                'voice': stage['voice']
            })

        self.current_stage = 0
        self.remaining = self.stages_config[0]['seconds']
        self.is_running = True

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self._session_start_time = now_jst()
        if self.database is not None:
            try:
                self._current_session_id = self.database.start_shisha_session(self._session_start_time)
            except Exception as e:
                self._current_session_id = None
        db = get_gui_db()
        if db:
            db.update_daemon_state(is_shisha_active=True, current_shisha_session_id=self._current_session_id)
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound:
            neuro_sound.enter_shisha_mode()
            neuro_sound._shisha_faded_out = True
        self._apply_shisha_preset(True)
        self.timer.start(1000)
        self._update_display()

    def _stop(self, completed: bool = True):
        self.timer.stop()
        self.is_running = False
        self.current_stage = 0
        self.remaining = 0

        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

        if self.database is not None and self._current_session_id is not None:
            try:
                end_time = now_jst()
                self.database.end_shisha_session(self._current_session_id, end_time, completed)
            except Exception:
                pass
        self._current_session_id = None
        self._session_start_time = None
        db = get_gui_db()
        if db:
            db.update_daemon_state(is_shisha_active=False, current_shisha_session_id=None)
        self._apply_shisha_preset(False)
        self._update_display()

    def _apply_shisha_preset(self, active: bool):
        try:
            app = QApplication.instance()
            if not app:
                return
            for widget in app.topLevelWidgets():
                if hasattr(widget, 'home_tab'):
                    home = widget.home_tab
                    if active:
                        home.apply_ac_preset_by_trigger('shisha')
                    else:
                        home.deactivate_ac_preset_by_trigger('shisha')
                    break
        except Exception:
            pass

    def force_stop_for_shutdown(self):
        if self.is_running:
            print("v3.7 Shisha Shutdown: Force stopping active session")
            self._stop(completed=False)

    def _tick(self):
        if not self.is_running:
            return

        self.remaining -= 1

        if self.remaining <= 0:
            self._play_voice(self.current_stage)
            self.current_stage += 1

            if self.current_stage >= len(self.stages_config):
                self._stop()
                return

            self.remaining = self.stages_config[self.current_stage]['seconds']

        self._update_display()

    def _update_display(self):
        if self.is_running and self.stages_config:
            stage = self.stages_config[self.current_stage]
            total = stage['seconds']
            progress = 1.0 - (self.remaining / total) if total > 0 else 0
            color = stage['color']
        else:
            progress = 0
            color = Colors.CYAN

        self.circle_widget.set_data(progress, self.remaining, color, self.is_running)

        for i, lbl in enumerate(self.stage_labels):
            if self.is_running:
                if i < self.current_stage:
                    lbl.setStyleSheet(f"""
                        color: {Colors.TEXT_PRIMARY};
                        padding: 5px 10px;
                        border: 1px solid {self.STAGES[i]['color']};
                        background-color: {self.STAGES[i]['color']};
                        border-radius: 4px;
                    """)
                elif i == self.current_stage:
                    lbl.setStyleSheet(f"""
                        color: {self.STAGES[i]['color']};
                        padding: 5px 10px;
                        border: 2px solid {self.STAGES[i]['color']};
                        border-radius: 4px;
                    """)
                else:
                    lbl.setStyleSheet(f"""
                        color: {Colors.TEXT_DIM};
                        padding: 5px 10px;
                        border: 1px solid {Colors.BORDER};
                        border-radius: 4px;
                    """)
            else:
                lbl.setStyleSheet(f"""
                    color: {Colors.TEXT_DIM};
                    padding: 5px 10px;
                    border: 1px solid {Colors.BORDER};
                    border-radius: 4px;
                """)

    def _play_voice(self, stage_index: int):
        def _resolve_voice_path(base_dir: Path, filename: str) -> Path:
            path = base_dir / filename
            return path if path.exists() else None
        def _get_duration(path: Path) -> float:
            try: return pygame.mixer.Sound(str(path)).get_length()
            except: return 3.0
        def play():
            try:
                db = get_gui_db()
                state = db.get_daemon_state() if db else {}
                if state.get('is_muted', False): return
                neuro_sound = self._get_neuro_sound_engine()
                sound_dir = ROOT_PATH / "Data" / "sounds" / "shisha"
                intro_path = _resolve_voice_path(sound_dir, self.INTRO_VOICE)
                if intro_path:
                    intro_duration = _get_duration(intro_path)
                    if neuro_sound:
                        neuro_sound.play_shisha_voice(intro_path)
                    else:
                        volume = config.get('system', {}).get('volume', 1.0)
                        pygame.mixer.music.set_volume(volume)
                        pygame.mixer.music.load(str(intro_path))
                        pygame.mixer.music.play()
                    time.sleep(intro_duration)
                    time.sleep(0.5)
                if stage_index < len(self.STAGES):
                    voice = self.STAGES[stage_index]['voice']
                    path = _resolve_voice_path(sound_dir, voice)
                    if path:
                        if neuro_sound: neuro_sound.play_shisha_voice(path)
                        else: pygame.mixer.music.load(str(path)); pygame.mixer.music.play()
            except Exception as e: print(f"Audio error: {e}")
        self.audio_thread = threading.Thread(target=play, daemon=True)
        self.audio_thread.start()

    def _get_neuro_sound_engine(self):
        try:
            main_window = self.window()
            if main_window and hasattr(main_window, 'dashboard_tab'):
                dashboard = main_window.dashboard_tab
                if hasattr(dashboard, 'neuro_sound') and dashboard.neuro_sound:
                    return dashboard.neuro_sound
        except:
            pass
        return None

class ShishaCircleWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.progress = 0.0
        self.remaining = 0
        self.color = Colors.CYAN
        self.is_running = False

    def set_data(self, progress: float, remaining: int, color: str, is_running: bool):
        self.progress = progress
        self.remaining = remaining
        self.color = color
        self.is_running = is_running
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        size = min(width, height)

        cx = width / 2
        cy = height / 2
        radius = size * 0.40
        ring_width = size * 0.06

        pen = QPen(QColor(Colors.BG_ELEVATED), int(ring_width))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawArc(rect, 0, 360 * 16)

        if self.is_running and self.progress > 0:
            pen = QPen(QColor(self.color), int(ring_width))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            angle = int(self.progress * 360 * 16)
            painter.drawArc(rect, 90 * 16, -angle)

        minutes = self.remaining // 60
        seconds = self.remaining % 60

        painter.setPen(QColor(Colors.TEXT_PRIMARY))
        painter.setFont(Fonts.number(int(size * 0.14), True))
        time_text = f"{minutes:02d}:{seconds:02d}"
        text_rect = QRectF(0, cy - size * 0.08, width, size * 0.14)
        painter.drawText(text_rect, Qt.AlignCenter, time_text)

        painter.setPen(QColor(self.color if self.is_running else Colors.TEXT_DIM))
        painter.setFont(Fonts.label(int(size * 0.05)))
        label = "ACTIVE" if self.is_running else "STANDBY"
        label_rect = QRectF(0, cy + size * 0.08, width, size * 0.08)
        painter.drawText(label_rect, Qt.AlignCenter, label)

class TimelineGraphCanvas(QWidget):
    VIEW_WINDOW_HOURS = 12
    CACHE_HOURS = 24
    FP_MOVING_AVERAGE_WINDOW = 5
    BPM_OUTLIER_THRESHOLD = 30
    BPM_EMA_ALPHA = 0.15
    BPM_MIN_VALID = 30
    BPM_MAX_VALID = 200
    COLOR_FP = '#F39C12'
    COLOR_BPM_DEFAULT = '#FFFFFF'
    COLOR_BPM_REST = '#00D4AA'
    COLOR_BPM_SHISHA = '#9B59B6'
    COLOR_BPM_STRESS = '#E74C3C'
    COLOR_SHISHA_BG = '#9B59B6'
    COLOR_REST_BG = '#00D4AA'
    COLOR_BPM_SHADOW = '#808080'
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hr_stream = []
        self.tactile_data = []
        self.shisha_sessions = []
        self.sleep_data = {}
        self.current_fp = None
        self.cached_tactile = []
        self.cached_shisha = []
        self.cached_sleep = {}
        self._cache_loaded = False
        self._cached_sleep_spans = []
        self.estimated_hr = None
        self.is_hr_estimated = False
        self.scroll_offset_hours = 0.0
        self._buffer = None
        self._buffer_valid = False
        self._data_hash = None
        self._is_scrolling = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._on_scroll_stop)
        self.setMinimumSize(800, 320)
    def _get_deterministic_offset(self, timestamp, scale=3.0):
        t = timestamp.timestamp()
        return scale * math.sin(t * 0.1) * math.sin(t * 0.37) * math.sin(t * 0.73)
    def set_scroll_offset(self, hours):
        old = self.scroll_offset_hours
        self.scroll_offset_hours = max(0, min(12, hours))
        if abs(old - self.scroll_offset_hours) > 0.001:
            self._is_scrolling = True
            self._scroll_timer.start(150)
            self._buffer_valid = False
            self.update()
    def _on_scroll_stop(self):
        self._is_scrolling = False
        self._buffer_valid = False
        self.update()
    def update_data(self, hr_stream, bio_engine=None, current_fp=None, estimated_hr=None, is_hr_estimated=False):
        new_hash = (len(hr_stream), hr_stream[-1].get('timestamp') if hr_stream else None)
        if new_hash != self._data_hash:
            self.hr_stream = hr_stream or []
            self._data_hash = new_hash
            self._buffer_valid = False
        if bio_engine:
            m = bio_engine.get_health_metrics()
            self.estimated_hr = m.get('estimated_hr')
            self.is_hr_estimated = m.get('is_hr_estimated', False)
            self.current_fp = m.get('effective_fp')
        else:
            if current_fp is not None: self.current_fp = current_fp
            if estimated_hr is not None: self.estimated_hr = estimated_hr
            self.is_hr_estimated = is_hr_estimated
        if not self._cache_loaded:
            self._load_all_cached_data()
        if not self._buffer_valid:
            self.update()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._buffer_valid = False
        self._buffer = None
    def hideEvent(self, event):
        super().hideEvent(event)
        self._buffer = None
        self._buffer_valid = False
    def showEvent(self, event):
        super().showEvent(event)
        self._buffer_valid = False
        self.update()
    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0: return
        if self._buffer is None or self._buffer.size() != self.size():
            self._buffer = QPixmap(self.size())
            self._buffer_valid = False
        if not self._buffer_valid:
            self._rebuild_buffer()
            self._buffer_valid = True
        painter.drawPixmap(0, 0, self._buffer)
    def _rebuild_buffer(self):
        self._buffer.fill(QColor(Colors.BG_CARD))
        p = QPainter(self._buffer)
        if not self._is_scrolling:
            p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin = {'left': 55, 'right': 25, 'top': 35, 'bottom': 45}
        gw, gh = w - margin['left'] - margin['right'], h - margin['top'] - margin['bottom']
        if gw <= 0 or gh <= 0:
            p.end()
            return
        now = now_jst()
        view_end = now - timedelta(hours=self.scroll_offset_hours)
        view_start = view_end - timedelta(hours=self.VIEW_WINDOW_HOURS)
        p.setClipRect(margin['left'], margin['top'], gw, gh)
        self._draw_context_bg(p, view_start, view_end, margin, gw, gh)
        self._draw_grid(p, view_start, view_end, margin, gw, gh)
        self._draw_fp_bars(p, view_start, view_end, margin, gw, gh)
        self._draw_bpm(p, view_start, view_end, margin, gw, gh)
        p.setClipping(False)
        self._draw_axis(p, margin, gh)
        self._draw_time_axis(p, view_start, view_end, margin, gw, gh)
        self._draw_legend(p, w)
        p.end()
    def get_view_params(self):
        now = now_jst()
        view_end = now - timedelta(hours=self.scroll_offset_hours)
        view_start = view_end - timedelta(hours=self.VIEW_WINDOW_HOURS)
        margin = {'left': 55, 'right': 25, 'top': 35, 'bottom': 45}
        gw = self.width() - margin['left'] - margin['right']
        gh = self.height() - margin['top'] - margin['bottom']
        return view_start, view_end, margin, gw, gh
    def refresh_from_db(self):
        self._cache_loaded = False
        self._load_all_cached_data()
        self._buffer_valid = False
        self.update()
    def _load_all_cached_data(self):
        if not self.isVisible() and self._cache_loaded: return
        try:
            metrics_db = ROOT_PATH / "Data" / "metrics.db"
            summary_db = ROOT_PATH / "Data" / "summary.db"
            legacy_db = ROOT_PATH / "Data" / "life_os.db"
            now = now_jst()
            start = now - timedelta(hours=self.CACHE_HOURS)
            self.cached_tactile = []
            self.cached_shisha = []
            self.hr_stream = []
            db_path = metrics_db if metrics_db.exists() else legacy_db
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tactile_logs'")
                if cursor.fetchone():
                    cursor.execute('SELECT timestamp, effective_fp FROM tactile_logs WHERE timestamp >= ? AND effective_fp IS NOT NULL ORDER BY timestamp ASC', (start.isoformat(),))
                    self.cached_tactile = [dict(r) for r in cursor.fetchall()]
                if not self.cached_tactile:
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='brain_metrics'")
                    if cursor.fetchone():
                        cursor.execute('SELECT timestamp, effective_fp FROM brain_metrics WHERE timestamp >= ? AND effective_fp IS NOT NULL ORDER BY timestamp ASC', (start.isoformat(),))
                        self.cached_tactile = [dict(r) for r in cursor.fetchall()]
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='heartrate_logs'")
                if cursor.fetchone():
                    cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? ORDER BY timestamp ASC', (start.isoformat(),))
                    self.hr_stream = [{'timestamp': r['timestamp'], 'bpm': r['bpm'], 'source': r['source']} for r in cursor.fetchall()]
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shisha_logs'")
                if cursor.fetchone():
                    cursor.execute('SELECT id, start_time, end_time FROM shisha_logs WHERE start_time >= ? OR end_time >= ? OR end_time IS NULL ORDER BY start_time', (start.isoformat(), start.isoformat()))
                    for r in cursor.fetchall():
                        st = datetime.fromisoformat(r['start_time']).replace(tzinfo=JST) if r['start_time'] else None
                        et = datetime.fromisoformat(r['end_time']).replace(tzinfo=JST) if r['end_time'] else None
                        if st: self.cached_shisha.append({'start': st, 'end': et})
                conn.close()
            if not self.cached_shisha and summary_db.exists():
                conn = sqlite3.connect(str(summary_db))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shisha_logs'")
                if cursor.fetchone():
                    cursor.execute('SELECT id, start_time, end_time FROM shisha_logs WHERE start_time >= ? OR end_time >= ? OR end_time IS NULL ORDER BY start_time', (start.isoformat(), start.isoformat()))
                    for r in cursor.fetchall():
                        st = datetime.fromisoformat(r['start_time']).replace(tzinfo=JST) if r['start_time'] else None
                        et = datetime.fromisoformat(r['end_time']).replace(tzinfo=JST) if r['end_time'] else None
                        if st: self.cached_shisha.append({'start': st, 'end': et})
                conn.close()
            self._cache_loaded = True
        except Exception as e:
            print(f"[Timeline] Cache load error: {e}")
    def _draw_context_bg(self, p, vs, ve, m, gw, gh):
        vh = self.VIEW_WINDOW_HOURS
        sc = QColor(self.COLOR_SHISHA_BG)
        sc.setAlpha(70)
        for s in self.cached_shisha:
            st, et = s['start'], s['end'] or now_jst()
            if et < vs or st > ve: continue
            r1 = max(0, (st - vs).total_seconds() / (vh * 3600))
            r2 = min(1, (et - vs).total_seconds() / (vh * 3600))
            x1, x2 = m['left'] + r1 * gw, m['left'] + r2 * gw
            p.fillRect(QRectF(x1, m['top'], x2 - x1, gh), sc)
        rc = QColor(self.COLOR_REST_BG)
        rc.setAlpha(25)
        rest_spans = self._extract_rest_spans(vs, ve)
        for st, et in rest_spans:
            r1 = max(0, (st - vs).total_seconds() / (vh * 3600))
            r2 = min(1, (et - vs).total_seconds() / (vh * 3600))
            x1, x2 = m['left'] + r1 * gw, m['left'] + r2 * gw
            p.fillRect(QRectF(x1, m['top'], x2 - x1, gh), rc)
    def _extract_rest_spans(self, vs, ve):
        if not self.hr_stream: return []
        raw_spans, cur_start, cur_end = [], None, None
        for e in sorted(self.hr_stream, key=lambda x: x.get('timestamp', '')):
            try:
                ts = datetime.fromisoformat(e['timestamp'])
                if ts.tzinfo is None: ts = ts.replace(tzinfo=JST)
                src = e.get('source', 'awake')
                if src == 'rest':
                    if cur_start is None: cur_start = ts
                    cur_end = ts
                else:
                    if cur_start is not None:
                        raw_spans.append((cur_start, cur_end))
                        cur_start, cur_end = None, None
            except: continue
        if cur_start is not None: raw_spans.append((cur_start, cur_end))
        sleep_spans = []
        for st, et in raw_spans:
            dur_min = (et - st).total_seconds() / 60
            h = st.hour
            is_night = h >= 18 or h <= 10
            if dur_min >= 90 and is_night:
                sleep_spans.append((st, et + timedelta(minutes=5)))
        self._cached_sleep_spans = sleep_spans
        return [(st, et) for st, et in sleep_spans if not (et < vs or st > ve)]
    def _draw_grid(self, p, vs, ve, m, gw, gh):
        vh = self.VIEW_WINDOW_HOURS
        graph_bottom = m['top'] + gh
        p.setPen(QPen(QColor(Colors.BORDER), 1, Qt.DotLine))
        for i in range(4):
            y = m['top'] + (i / 4) * gh
            p.drawLine(int(m['left']), int(y), int(m['left'] + gw), int(y))
        for i in range(13):
            ratio = i / 12
            x = m['left'] + ratio * gw
            lt = vs + timedelta(hours=ratio * vh)
            is_midnight = lt.hour == 0 and lt.minute < 60
            if is_midnight:
                grad = QLinearGradient(x, m['top'], x, graph_bottom)
                grad.setColorAt(0, QColor(231, 76, 60, 200))
                grad.setColorAt(0.5, QColor(231, 76, 60, 80))
                grad.setColorAt(1, QColor(231, 76, 60, 200))
                p.setPen(QPen(QBrush(grad), 2))
            else:
                p.setPen(QPen(QColor(Colors.BORDER), 1, Qt.SolidLine))
            p.drawLine(int(x), int(m['top']), int(x), int(graph_bottom))
    def _draw_time_axis(self, p, vs, ve, m, gw, gh):
        vh = self.VIEW_WINDOW_HOURS
        h = self.height()
        graph_bottom = m['top'] + gh
        last_date = None
        for i in range(13):
            ratio = i / 12
            x = m['left'] + ratio * gw
            lt = vs + timedelta(hours=ratio * vh)
            is_midnight = lt.hour == 0 and lt.minute < 60
            current_date = lt.strftime('%m/%d')
            p.setPen(QColor(Colors.BORDER))
            p.drawLine(int(x), int(graph_bottom), int(x), int(graph_bottom + 4))
            if i % 2 == 0:
                if is_midnight:
                    p.setFont(Fonts.number(9, True))
                    p.setPen(QColor('#E74C3C'))
                    p.drawText(int(x - 22), h - 8, current_date)
                    p.setFont(Fonts.label(8))
                    p.setPen(QColor(Colors.TEXT_DIM))
                    p.drawText(int(x - 12), h - 22, '00:00')
                else:
                    p.setFont(Fonts.label(8))
                    show_date = (i == 0) or (last_date and current_date != last_date)
                    if show_date:
                        p.setPen(QColor(Colors.TEXT_SECONDARY))
                        p.drawText(int(x - 22), h - 8, current_date)
                        p.setPen(QColor(Colors.TEXT_DIM))
                        p.drawText(int(x - 12), h - 22, lt.strftime('%H:%M'))
                    else:
                        p.setPen(QColor(Colors.TEXT_DIM))
                        p.drawText(int(x - 12), h - 15, lt.strftime('%H:%M'))
                last_date = current_date
    def _draw_fp_bars(self, p, vs, ve, m, gw, gh):
        vh = self.VIEW_WINDOW_HOURS
        fp_max = gh * 0.5
        fp_bottom = m['top'] + gh
        BUCKET_MINUTES = 3
        EMA_ALPHA = 0.3
        buckets = {}
        for e in self.cached_tactile:
            try:
                ts = datetime.fromisoformat(e['timestamp'])
                if ts.tzinfo is None: ts = ts.replace(tzinfo=JST)
                fp = e.get('effective_fp')
                if fp is None or ts < vs or ts > ve: continue
                bucket_key = int((ts - vs).total_seconds() / (BUCKET_MINUTES * 60))
                if bucket_key not in buckets: buckets[bucket_key] = []
                buckets[bucket_key].append(fp)
            except: continue
        if not buckets: return
        points = []
        for bk in sorted(buckets.keys()):
            avg_fp = sum(buckets[bk]) / len(buckets[bk])
            t_ratio = (bk * BUCKET_MINUTES * 60) / (vh * 3600)
            points.append((t_ratio, avg_fp))
        if len(points) < 2: return
        smoothed = [points[0][1]]
        for i in range(1, len(points)):
            smoothed.append(EMA_ALPHA * points[i][1] + (1 - EMA_ALPHA) * smoothed[-1])
        path = QPainterPath()
        first_x = m['left'] + points[0][0] * gw
        path.moveTo(first_x, fp_bottom)
        for i, (t_ratio, _) in enumerate(points):
            x = m['left'] + t_ratio * gw
            bh = max(0, min(1, smoothed[i] / 100)) * fp_max
            y = fp_bottom - bh
            if i == 0: path.lineTo(x, y)
            else:
                prev_x = m['left'] + points[i-1][0] * gw
                prev_bh = max(0, min(1, smoothed[i-1] / 100)) * fp_max
                prev_y = fp_bottom - prev_bh
                cx = (prev_x + x) / 2
                path.cubicTo(cx, prev_y, cx, y, x, y)
        last_x = m['left'] + points[-1][0] * gw
        path.lineTo(last_x, fp_bottom)
        path.closeSubpath()
        grad = QLinearGradient(0, fp_bottom - fp_max, 0, fp_bottom)
        grad.setColorAt(0, QColor(243, 156, 18, 180))
        grad.setColorAt(1, QColor(243, 156, 18, 60))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawPath(path)
        edge_path = QPainterPath()
        edge_path.moveTo(first_x, fp_bottom - max(0, min(1, smoothed[0] / 100)) * fp_max)
        for i, (t_ratio, _) in enumerate(points):
            if i == 0: continue
            x = m['left'] + t_ratio * gw
            bh = max(0, min(1, smoothed[i] / 100)) * fp_max
            y = fp_bottom - bh
            prev_x = m['left'] + points[i-1][0] * gw
            prev_bh = max(0, min(1, smoothed[i-1] / 100)) * fp_max
            prev_y = fp_bottom - prev_bh
            cx = (prev_x + x) / 2
            edge_path.cubicTo(cx, prev_y, cx, y, x, y)
        p.setPen(QPen(QColor(243, 156, 18, 220), 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(edge_path)
        if self.current_fp and self.current_fp > 0:
            now = now_jst()
            ratio = (now - vs).total_seconds() / (vh * 3600)
            x = m['left'] + ratio * gw
            if m['left'] <= x <= m['left'] + gw:
                bh = max(0, min(1, self.current_fp / 100)) * fp_max
                p.setPen(QPen(QColor(243, 156, 18), 3))
                p.setBrush(QColor(243, 156, 18, 200))
                p.drawEllipse(QPointF(x, fp_bottom - bh), 4, 4)
    def _draw_bpm(self, p, vs, ve, m, gw, gh):
        if not self.hr_stream: return
        vh = self.VIEW_WINDOW_HOURS
        filt = self._filter_bpm(self.hr_stream)
        if not filt: return
        raw_pts = []
        for e in sorted(filt, key=lambda x: x.get('timestamp', '')):
            try:
                ts = datetime.fromisoformat(e['timestamp'])
                if ts.tzinfo is None: ts = ts.replace(tzinfo=JST)
                bpm = e.get('bpm')
                if bpm is None: continue
                src = e.get('source', 'awake')
                ratio = (ts - vs).total_seconds() / (vh * 3600)
                x = m['left'] + ratio * gw
                yr = max(0, min(1, bpm / 120))
                y = m['top'] + (1 - yr) * gh
                in_shisha = self._in_shisha(ts)
                in_sleep = any(st <= ts <= et for st, et in self._cached_sleep_spans)
                if src == 'shadow': c = self.COLOR_BPM_SHADOW
                elif in_shisha: c = self.COLOR_BPM_SHISHA
                elif in_sleep: c = self.COLOR_BPM_REST
                elif bpm > 100: c = self.COLOR_BPM_STRESS
                else: c = self.COLOR_BPM_DEFAULT
                raw_pts.append({'x': x, 'y': y, 'c': c, 's': src == 'shadow', 'src': src, 'ts': ts})
            except: continue
        if len(raw_pts) < 2: return
        GAP_THRESHOLD = 300
        INTERP_INTERVAL = 60
        pts = [raw_pts[0]]
        for i in range(1, len(raw_pts)):
            gap_sec = (raw_pts[i]['ts'] - raw_pts[i-1]['ts']).total_seconds()
            if gap_sec > GAP_THRESHOLD:
                p1, p2 = raw_pts[i-1], raw_pts[i]
                n_interp = max(2, int(gap_sec / INTERP_INTERVAL))
                for j in range(1, n_interp):
                    t = j / n_interp
                    ix = p1['x'] + (p2['x'] - p1['x']) * t
                    base_y = p1['y'] + (p2['y'] - p1['y']) * t
                    wave = math.sin(t * math.pi * 4 + p1['x'] * 0.1) * 3
                    iy = base_y + wave
                    ic = p1['c'] if t < 0.5 else p2['c']
                    its = p1['ts'] + timedelta(seconds=gap_sec * t)
                    pts.append({'x': ix, 'y': iy, 'c': ic, 's': False, 'src': 'interp', 'ts': its})
            pts.append(raw_pts[i])
        def catmull_rom_spline(v0, v1, v2, v3, t):
            t2, t3 = t * t, t * t * t
            return 0.5 * ((2 * v1) + (-v0 + v2) * t + (2*v0 - 5*v1 + 4*v2 - v3) * t2 + (-v0 + 3*v1 - 3*v2 + v3) * t3)
        def draw_smooth_segment(seg_pts, color):
            if len(seg_pts) < 2: return
            path = QPainterPath()
            path.moveTo(seg_pts[0]['x'], seg_pts[0]['y'])
            for i in range(len(seg_pts) - 1):
                i0 = seg_pts[max(0, i - 1)]
                i1 = seg_pts[i]
                i2 = seg_pts[min(len(seg_pts) - 1, i + 1)]
                i3 = seg_pts[min(len(seg_pts) - 1, i + 2)]
                for t in [0.25, 0.5, 0.75, 1.0]:
                    nx = catmull_rom_spline(i0['x'], i1['x'], i2['x'], i3['x'], t)
                    ny = catmull_rom_spline(i0['y'], i1['y'], i2['y'], i3['y'], t)
                    path.lineTo(nx, ny)
            pen = QPen(QColor(color), 2.5)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)
        segments = []
        cur_seg = [pts[0]]
        cur_color = pts[0]['c']
        for i in range(1, len(pts)):
            pt = pts[i]
            if pt['c'] == cur_color:
                cur_seg.append(pt)
            else:
                cur_seg.append(pt)
                segments.append((cur_seg, cur_color))
                cur_seg = [pt]
                cur_color = pt['c']
        if cur_seg: segments.append((cur_seg, cur_color))
        for seg, color in segments:
            draw_smooth_segment(seg, color)
        if self.is_hr_estimated and self.estimated_hr:
            now = now_jst()
            ratio = (now - vs).total_seconds() / (vh * 3600)
            x = m['left'] + ratio * gw
            yr = max(0, min(1, self.estimated_hr / 120))
            y = m['top'] + (1 - yr) * gh
            if pts:
                lx, ly = pts[-1]['x'], pts[-1]['y']
                dx = x - lx
                path = QPainterPath()
                path.moveTo(lx, ly)
                path.cubicTo(lx + dx * 0.4, ly, lx + dx * 0.6, y, x, y)
                sp = QPen(QColor(self.COLOR_BPM_SHADOW), 2.0)
                sp.setCapStyle(Qt.RoundCap)
                p.setPen(sp)
                p.drawPath(path)
            p.setBrush(QColor(self.COLOR_BPM_SHADOW))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, y), 4, 4)
            p.setPen(QColor(self.COLOR_BPM_SHADOW))
            f = p.font()
            f.setPointSize(8)
            p.setFont(f)
            p.drawText(int(x + 8), int(y + 4), f"~{self.estimated_hr}")
    def _filter_bpm(self, stream):
        if not stream: return []
        ss = sorted(stream, key=lambda e: e.get('timestamp', ''))
        valid = []
        last = None
        for e in ss:
            bpm = e.get('bpm')
            if bpm is None or bpm < self.BPM_MIN_VALID or bpm > self.BPM_MAX_VALID: continue
            if last is not None and abs(bpm - last) > self.BPM_OUTLIER_THRESHOLD: continue
            valid.append(e)
            last = bpm
        if not valid: return []
        alpha = self.BPM_EMA_ALPHA
        sm = []
        ema = valid[0].get('bpm', 60)
        for e in valid:
            raw = e.get('bpm', ema)
            ema = alpha * raw + (1 - alpha) * ema
            ne = e.copy()
            ne['bpm'] = round(ema, 1)
            sm.append(ne)
        return sm
    def _in_shisha(self, ts):
        for s in self.cached_shisha:
            st, et = s['start'], s['end'] or now_jst()
            if st <= ts <= et: return True
        return False
    def _draw_axis(self, p, m, gh):
        p.setPen(QColor(Colors.TEXT_DIM))
        p.setFont(Fonts.label(8))
        for i, v in enumerate([120, 90, 60, 30]):
            y = m['top'] + (i / 4) * gh
            p.drawText(5, int(y + 4), str(v))
        p.setPen(QColor(Colors.TEXT_SECONDARY))
        p.drawText(int(m['left'] - 30), int(m['top'] - 5), "BPM")
    def _draw_legend(self, p, w):
        y = 18
        p.setFont(Fonts.label(8))
        x = w - 300
        fc = QColor(self.COLOR_FP)
        fc.setAlpha(150)
        p.fillRect(int(x), int(y - 8), 4, 16, fc)
        p.setPen(QColor(Colors.TEXT_DIM))
        p.drawText(int(x + 10), int(y + 4), "FP")
        legs = [(self.COLOR_BPM_DEFAULT, "BPM"), (self.COLOR_BPM_REST, "Rest"), (self.COLOR_BPM_SHISHA, "Shisha")]
        x = w - 240
        for c, l in legs:
            p.setPen(QPen(QColor(c), 2.5))
            p.drawLine(int(x), int(y), int(x + 12), int(y))
            p.setPen(QColor(Colors.TEXT_DIM))
            p.drawText(int(x + 18), int(y + 4), l)
            x += 55

class TimelineOverlay(QWidget):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.mouse_x = -1
        self.hover_time = None
        self.hover_bpm = None
        self.hover_fp = None
        self.is_dragging = False
        self.drag_start_x = 0
        self.drag_start_offset = 0.0
        self.on_scroll_changed = None
        self._hr_timestamps = []
        self._fp_timestamps = []
        self.setMouseTracking(True)
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        vs, ve, m, gw, gh = self.canvas.get_view_params()
        if gw <= 0 or gh <= 0: return
        self._draw_cursor(p, m, gw, gh)
        self._draw_hover(p, self.width(), m)
    def _draw_cursor(self, p, m, gw, gh):
        if self.is_dragging or self.mouse_x < m['left'] or self.mouse_x > m['left'] + gw: return
        pen = QPen(QColor(Colors.TEXT_DIM), 1, Qt.DashLine)
        p.setPen(pen)
        p.drawLine(int(self.mouse_x), int(m['top']), int(self.mouse_x), int(m['top'] + gh))
    def _draw_hover(self, p, w, m):
        if self.hover_time is None or self.is_dragging: return
        parts = [self.hover_time.strftime('%H:%M')]
        if self.hover_bpm is not None: parts.append(f"BPM: {self.hover_bpm}")
        if self.hover_fp is not None: parts.append(f"FP: {self.hover_fp:.1f}")
        txt = " | ".join(parts)
        p.setFont(Fonts.label(10, True))
        tr = p.fontMetrics().boundingRect(txt)
        bx = min(self.mouse_x + 10, w - tr.width() - 30)
        by = m['top'] + 15
        bg = QColor(Colors.BG_ELEVATED)
        bg.setAlpha(230)
        p.fillRect(int(bx - 5), int(by - 15), tr.width() + 15, 22, bg)
        p.setPen(QColor(Colors.CYAN))
        p.drawText(int(bx), int(by), txt)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            vs, ve, m, gw, gh = self.canvas.get_view_params()
            if m['left'] <= event.x() <= m['left'] + gw:
                self.is_dragging = True
                self.drag_start_x = event.x()
                self.drag_start_offset = self.canvas.scroll_offset_hours
                self.setCursor(Qt.ClosedHandCursor)
                self.hover_time = None
                self.hover_bpm = None
                self.hover_fp = None
                self.update()
    def mouseMoveEvent(self, event):
        vs, ve, m, gw, gh = self.canvas.get_view_params()
        if self.is_dragging:
            dx = event.x() - self.drag_start_x
            hpp = self.canvas.VIEW_WINDOW_HOURS / gw if gw > 0 else 0
            new_off = self.drag_start_offset + dx * hpp
            self.canvas.set_scroll_offset(new_off)
            if self.on_scroll_changed:
                self.on_scroll_changed(self.canvas.scroll_offset_hours)
        else:
            self.mouse_x = event.x()
            if m['left'] <= self.mouse_x <= m['left'] + gw:
                ratio = (self.mouse_x - m['left']) / gw
                self.hover_time = vs + timedelta(hours=ratio * self.canvas.VIEW_WINDOW_HOURS)
                self.hover_bpm = self._find_bpm(self.hover_time)
                self.hover_fp = self._find_fp(self.hover_time)
            else:
                self.hover_time = None
                self.hover_bpm = None
                self.hover_fp = None
            self.update()
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_dragging:
            self.is_dragging = False
            self.setCursor(Qt.ArrowCursor)
            self.update()
    def leaveEvent(self, event):
        if not self.is_dragging:
            self.hover_time = None
            self.hover_bpm = None
            self.hover_fp = None
            self.mouse_x = -1
            self.update()
    def wheelEvent(self, event):
        d = event.angleDelta().y()
        off = self.canvas.scroll_offset_hours
        if d > 0: self.canvas.set_scroll_offset(off - 0.5)
        else: self.canvas.set_scroll_offset(off + 0.5)
        if self.on_scroll_changed:
            self.on_scroll_changed(self.canvas.scroll_offset_hours)
    def _find_bpm(self, tt):
        stream = self.canvas.hr_stream
        if not stream: return None
        if len(self._hr_timestamps) != len(stream):
            self._hr_timestamps = []
            for e in stream:
                try:
                    ts = datetime.fromisoformat(e['timestamp'])
                    if ts.tzinfo is None: ts = ts.replace(tzinfo=JST)
                    self._hr_timestamps.append(ts.timestamp())
                except: self._hr_timestamps.append(0)
        tts = tt.timestamp()
        idx = bisect.bisect_left(self._hr_timestamps, tts)
        cands = []
        if idx > 0: cands.append(idx - 1)
        if idx < len(self._hr_timestamps): cands.append(idx)
        bi, bd = None, 300
        for i in cands:
            d = abs(self._hr_timestamps[i] - tts)
            if d < bd: bd, bi = d, i
        return stream[bi].get('bpm') if bi is not None else None
    def _find_fp(self, tt):
        tact = self.canvas.cached_tactile
        if not tact: return None
        if len(self._fp_timestamps) != len(tact):
            self._fp_timestamps = []
            for e in tact:
                try:
                    ts = datetime.fromisoformat(e['timestamp'])
                    if ts.tzinfo is None: ts = ts.replace(tzinfo=JST)
                    self._fp_timestamps.append(ts.timestamp())
                except: self._fp_timestamps.append(0)
        tts = tt.timestamp()
        idx = bisect.bisect_left(self._fp_timestamps, tts)
        cands = []
        if idx > 0: cands.append(idx - 1)
        if idx < len(self._fp_timestamps): cands.append(idx)
        bi, bd = None, 120
        for i in cands:
            d = abs(self._fp_timestamps[i] - tts)
            if d < bd: bd, bi = d, i
        return tact[bi].get('effective_fp') if bi is not None else None

class TimelineGraphContainer(QWidget):
    MAX_SCROLL = 60
    def __init__(self):
        super().__init__()
        self.canvas = TimelineGraphCanvas()
        self.overlay = TimelineOverlay(self.canvas)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.canvas, 1)
        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.setRange(0, self.MAX_SCROLL)
        self.scrollbar.setValue(self.MAX_SCROLL)
        self.scrollbar.setPageStep(12)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_changed)
        self.scrollbar.setStyleSheet("QScrollBar:horizontal{height:14px;background:#1A1A1A;border-radius:7px;}QScrollBar::handle:horizontal{background:#444;border-radius:6px;min-width:50px;}QScrollBar::handle:horizontal:hover{background:#555;}QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}")
        main_layout.addWidget(self.scrollbar)
        self.overlay.setParent(self)
        self.overlay.raise_()
        self.overlay.on_scroll_changed = self._sync_scrollbar
        self.setMinimumSize(800, 340)
    def _on_scrollbar_changed(self, value):
        offset = self.MAX_SCROLL - value
        self.canvas.set_scroll_offset(offset)
    def _sync_scrollbar(self, offset):
        self.scrollbar.blockSignals(True)
        self.scrollbar.setValue(self.MAX_SCROLL - int(offset))
        self.scrollbar.blockSignals(False)
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        step = 2 if delta > 0 else -2
        new_val = max(0, min(self.MAX_SCROLL, self.canvas.scroll_offset_hours + step))
        self.canvas.set_scroll_offset(new_val)
        self._sync_scrollbar(new_val)
        event.accept()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(0, 0, self.width(), self.canvas.height())
    def update_data(self, hr_stream, bio_engine=None, current_fp=None, estimated_hr=None, is_hr_estimated=False):
        self.canvas.update_data(hr_stream, bio_engine, current_fp, estimated_hr, is_hr_estimated)
        self.overlay._hr_timestamps = []
        self.overlay._fp_timestamps = []

class AnalysisTab(QWidget):
    def __init__(self):
        super().__init__()
        self.hr_stream = []
        try:
            from core.database import LifeOSDatabase
            self.database = LifeOSDatabase(str(ROOT_PATH / "Data" / "life_os.db"))
        except:
            self.database = None
        self._initialize_from_db()
        self.initUI()
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_analysis)
        self.update_timer.start(5000)
    def _initialize_from_db(self):
        try:
            metrics_db = ROOT_PATH / "Data" / "metrics.db"
            legacy_db = ROOT_PATH / "Data" / "life_os.db"
            db_path = metrics_db if metrics_db.exists() else legacy_db
            if not db_path.exists(): return
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            now = now_jst()
            start = now - timedelta(hours=24)
            cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? ORDER BY timestamp ASC', (start.isoformat(),))
            rows = cursor.fetchall()
            conn.close()
            if rows:
                self.hr_stream = [{'timestamp': r['timestamp'], 'bpm': r['bpm'], 'source': r['source']} for r in rows]
        except: pass
    def initUI(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)
        tr = QHBoxLayout()
        title = QLabel("📊 Analytics - 12h Timeline (Drag for 7 days)")
        title.setFont(Fonts.number(16, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        tr.addWidget(title)
        tr.addStretch()
        desc = QLabel("Hover for details | Drag for history")
        desc.setFont(Fonts.label(9))
        desc.setStyleSheet(f"color: {Colors.TEXT_DIM};")
        tr.addWidget(desc)
        layout.addLayout(tr)
        self.graph = TimelineGraphContainer()
        layout.addWidget(self.graph, 1)
        sf = QFrame()
        sf.setStyleSheet(f"QFrame {{ background-color: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER}; border-radius: 6px; }}")
        sl = QHBoxLayout(sf)
        sl.setSpacing(25)
        sl.setContentsMargins(15, 10, 15, 10)
        self.stats_labels = {}
        items = [('true_rhr', 'TRUE RHR'), ('current_hr', 'CURRENT HR'), ('current_fp', 'CURRENT FP'), ('wake_time', 'WAKE TIME'), ('main_sleep', 'MAIN SLEEP'), ('sleep_eff', 'SLEEP EFF'), ('deep_sleep', 'DEEP'), ('last_sync', 'LAST SYNC')]
        for k, lbl in items:
            item = QVBoxLayout()
            item.setSpacing(2)
            l = QLabel(lbl)
            l.setFont(Fonts.label(8))
            l.setStyleSheet(f"color: {Colors.TEXT_DIM};")
            item.addWidget(l)
            v = QLabel("--")
            v.setFont(Fonts.number(13, True))
            if k == 'current_fp': v.setStyleSheet(f"color: {Colors.ORANGE};")
            elif k in ('sleep_eff', 'deep_sleep'): v.setStyleSheet(f"color: {Colors.BLUE};")
            elif k == 'last_sync': v.setStyleSheet("color: #2ECC71;")
            else: v.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
            item.addWidget(v)
            self.stats_labels[k] = v
            sl.addLayout(item)
        sl.addStretch()
        layout.addWidget(sf)
        self.setLayout(layout)
        self.update_analysis()
    def update_analysis(self):
        try:
            state = get_state_from_db()
            details = state.get('oura_details', {})
            bs = state.get('brain_state', {})
            now = now_jst()
            if self.database:
                try:
                    start = now - timedelta(hours=24)
                    self.hr_stream = self.database.get_heartrate_range(start, now) or []
                except:
                    self.hr_stream = details.get('hr_stream', [])
            else:
                self.hr_stream = details.get('hr_stream', [])
            last_oura_ts = None
            if self.hr_stream:
                for e in reversed(self.hr_stream):
                    if e.get('source') != 'shadow':
                        try:
                            ots = datetime.fromisoformat(e['timestamp'])
                            if ots.tzinfo is None: ots = ots.replace(tzinfo=JST)
                            last_oura_ts = ots
                            break
                        except: pass
            cfp = bs.get('effective_fp')
            ehr = bs.get('estimated_hr') or details.get('current_hr')
            ise = bs.get('is_hr_estimated', False)
            self.graph.update_data(self.hr_stream, None, current_fp=cfp, estimated_hr=ehr, is_hr_estimated=ise)
            self.graph.canvas.refresh_from_db()
            trhr = details.get('true_rhr')
            self.stats_labels['true_rhr'].setText(f"{trhr} bpm" if trhr else "--")
            if ise and ehr is not None:
                j = random.randint(-2, 3)
                dhr = ehr + j
                if trhr: dhr = max(trhr + 5, dhr)
                self.stats_labels['current_hr'].setText(f"~{dhr} bpm (EST)")
                self.stats_labels['current_hr'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
            elif details.get('current_hr'):
                self.stats_labels['current_hr'].setText(f"{details.get('current_hr')} bpm")
                self.stats_labels['current_hr'].setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
            else:
                self.stats_labels['current_hr'].setText("--")
                self.stats_labels['current_hr'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
            self.stats_labels['current_fp'].setText(f"{cfp:.1f}" if cfp is not None else "--")
            if details.get('wake_anchor_iso'):
                try:
                    w = datetime.fromisoformat(details['wake_anchor_iso'])
                    self.stats_labels['wake_time'].setText(w.strftime('%H:%M'))
                except: self.stats_labels['wake_time'].setText("--")
            else:
                self.stats_labels['wake_time'].setText("--")
            msl = details.get('main_sleep_seconds')
            if msl is None or msl < 1800:
                tn = details.get('total_nap_minutes', 0) or 0
                msl = int(tn * 60)
            self.stats_labels['main_sleep'].setText(f"{msl // 3600}h {(msl % 3600) // 60}m" if msl else "--")
            conts = details.get('contributors', {})
            se = conts.get('efficiency')
            ds = conts.get('deep_sleep')
            self.stats_labels['sleep_eff'].setText(f"{se}%" if se is not None else "--")
            self.stats_labels['deep_sleep'].setText(f"{ds}%" if ds is not None else "--")
            if last_oura_ts:
                self.stats_labels['last_sync'].setText(last_oura_ts.strftime('%m/%d %H:%M'))
                age_h = (now - last_oura_ts).total_seconds() / 3600
                if age_h < 1: self.stats_labels['last_sync'].setStyleSheet("color: #2ECC71;")
                elif age_h < 6: self.stats_labels['last_sync'].setStyleSheet(f"color: {Colors.CYAN};")
                elif age_h < 24: self.stats_labels['last_sync'].setStyleSheet(f"color: {Colors.ORANGE};")
                else: self.stats_labels['last_sync'].setStyleSheet(f"color: {Colors.RED};")
            else:
                self.stats_labels['last_sync'].setText("--")
                self.stats_labels['last_sync'].setStyleSheet(f"color: {Colors.TEXT_DIM};")
        except Exception as e:
            print(f"Analysis error: {e}")

class SettingsTab(QWidget):

    AMBIENT_SOURCES = ['Rain', 'Fire']

    def __init__(self, neuro_sound=None):
        super().__init__()
        self.neuro_sound = neuro_sound

        self.ambient_slot_checks = []
        self.ambient_slot_combos = []
        self.ambient_slot_sliders = []
        self.ambient_slot_labels = []

        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("⚙️ Settings")
        title.setFont(Fonts.number(16, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        layout.addWidget(title)

        oura_group = self._create_group("Oura Ring")
        oura_layout = oura_group.layout()

        token_layout = QHBoxLayout()
        token_layout.addWidget(QLabel("API Token:"))
        self.token_input = QLineEdit()
        self.token_input.setText(config.get('oura', {}).get('api_token', ''))
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 8px;
                font-family: {Fonts.FAMILY_NUMBER};
            }}
        """)
        token_layout.addWidget(self.token_input)
        oura_layout.addLayout(token_layout)
        layout.addWidget(oura_group)
        audio_group = self._create_group("Audio & Environment")
        audio_main_layout = audio_group.layout()

        audio_cfg = config.get('audio', {})

        self.audio_enabled_check = QCheckBox("Enable Audio Engine")
        self.audio_enabled_check.setChecked(audio_cfg.get('enabled', True))
        self.audio_enabled_check.stateChanged.connect(self._on_audio_enabled_changed)
        audio_main_layout.addWidget(self.audio_enabled_check)

        volume_grid = QGridLayout()
        volume_grid.setColumnStretch(1, 1)
        volume_grid.setColumnMinimumWidth(0, 70)
        volume_grid.setColumnMinimumWidth(2, 45)
        volume_grid.setColumnMinimumWidth(3, 60)
        volume_grid.setHorizontalSpacing(10)
        volume_grid.setVerticalSpacing(8)

        row = 0

        vol_header = QLabel("─── Volume Controls ───")
        vol_header.setAlignment(Qt.AlignCenter)
        volume_grid.addWidget(vol_header, row, 0, 1, 4)
        row += 1

        master_label = QLabel("Master:")
        master_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        volume_grid.addWidget(master_label, row, 0)

        self.master_volume_slider = QSlider(Qt.Horizontal)
        self.master_volume_slider.setRange(0, 100)
        self.master_volume_slider.setValue(int(audio_cfg.get('master_volume', 1.0) * 100))
        self.master_volume_slider.valueChanged.connect(self._on_master_volume_changed)
        volume_grid.addWidget(self.master_volume_slider, row, 1)

        self.master_vol_label = QLabel(f"{int(audio_cfg.get('master_volume', 1.0) * 100)}%")
        self.master_vol_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.master_vol_label.setMinimumWidth(45)
        volume_grid.addWidget(self.master_vol_label, row, 2)

        volume_grid.addWidget(QLabel(""), row, 3)
        row += 1

        bgm_label = QLabel("BGM:")
        bgm_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        volume_grid.addWidget(bgm_label, row, 0)

        self.bgm_volume_slider = QSlider(Qt.Horizontal)
        self.bgm_volume_slider.setRange(0, 100)
        self.bgm_volume_slider.setValue(int(audio_cfg.get('bgm_volume', 0.08) * 100))
        self.bgm_volume_slider.valueChanged.connect(self._on_bgm_volume_changed)
        volume_grid.addWidget(self.bgm_volume_slider, row, 1)

        self.bgm_vol_label = QLabel(f"{int(audio_cfg.get('bgm_volume', 0.08) * 100)}%")
        self.bgm_vol_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.bgm_vol_label.setMinimumWidth(45)
        self.bgm_vol_label.setProperty("volumeType", "bgm")
        volume_grid.addWidget(self.bgm_vol_label, row, 2)

        self.bgm_enabled_check = QCheckBox("On")
        self.bgm_enabled_check.setChecked(audio_cfg.get('bgm_enabled', True))
        self.bgm_enabled_check.stateChanged.connect(self._on_bgm_enabled_changed)
        volume_grid.addWidget(self.bgm_enabled_check, row, 3)
        row += 1
        audio_main_layout.addLayout(volume_grid)
        ambient_header = QLabel("─── Ambient Mixer ───")
        ambient_header.setAlignment(Qt.AlignCenter)
        audio_main_layout.addWidget(ambient_header)

        ambient_grid = QGridLayout()
        ambient_grid.setColumnStretch(2, 1)
        ambient_grid.setColumnMinimumWidth(0, 55)
        ambient_grid.setColumnMinimumWidth(1, 70)
        ambient_grid.setColumnMinimumWidth(3, 45)
        ambient_grid.setHorizontalSpacing(8)
        ambient_grid.setVerticalSpacing(6)

        ambient_slots_cfg = audio_cfg.get('ambient_slots', [
            {'source': 'Rain', 'volume': 0.15, 'enabled': False},
            {'source': 'Fire', 'volume': 0.15, 'enabled': False},
            {'source': 'Rain', 'volume': 0.15, 'enabled': False},
        ])

        while len(ambient_slots_cfg) < 3:
            ambient_slots_cfg.append({'source': 'Rain', 'volume': 0.15, 'enabled': False})

        for i in range(3):
            slot_cfg = ambient_slots_cfg[i]

            check = QCheckBox(f"Slot{i+1}")
            check.setChecked(slot_cfg.get('enabled', False))
            check.stateChanged.connect(lambda state, idx=i: self._on_ambient_slot_enabled_changed(idx, state))
            ambient_grid.addWidget(check, i, 0)
            self.ambient_slot_checks.append(check)

            combo = QComboBox()
            combo.addItems(self.AMBIENT_SOURCES)
            current_source = slot_cfg.get('source', 'Rain')
            if current_source in self.AMBIENT_SOURCES:
                combo.setCurrentIndex(self.AMBIENT_SOURCES.index(current_source))
            combo.setMinimumWidth(70)
            combo.setMaximumWidth(90)
            combo.currentTextChanged.connect(lambda text, idx=i: self._on_ambient_slot_source_changed(idx, text))
            ambient_grid.addWidget(combo, i, 1)
            self.ambient_slot_combos.append(combo)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(slot_cfg.get('volume', 0.15) * 100))
            slider.valueChanged.connect(lambda val, idx=i: self._on_ambient_slot_volume_changed(idx, val))
            ambient_grid.addWidget(slider, i, 2)
            self.ambient_slot_sliders.append(slider)

            label = QLabel(f"{int(slot_cfg.get('volume', 0.15) * 100)}%")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setMinimumWidth(45)
            label.setProperty("volumeType", "ambient")
            ambient_grid.addWidget(label, i, 3)
            self.ambient_slot_labels.append(label)

        audio_main_layout.addLayout(ambient_grid)
        neuro_header = QLabel("─── Neuro Settings ───")
        neuro_header.setAlignment(Qt.AlignCenter)
        audio_main_layout.addWidget(neuro_header)
        neuro_layout = QHBoxLayout()
        neuro_layout.setSpacing(24)
        self.headphone_check = QCheckBox("Headphone Mode (Binaural)")
        self.headphone_check.setChecked(audio_cfg.get('headphone_mode', True))
        self.headphone_check.setToolTip("Binaural Beat for headphones, Isochronic Tone for speakers")
        self.headphone_check.stateChanged.connect(self._on_headphone_mode_changed)
        neuro_layout.addWidget(self.headphone_check)
        self.bas_check = QCheckBox("BAS (Left-Right Stim)")
        self.bas_check.setChecked(audio_cfg.get('bas_enabled', False))
        self.bas_check.setToolTip("Bilateral Alternating Stimulation - Left-right panning")
        self.bas_check.stateChanged.connect(self._on_bas_enabled_changed)
        neuro_layout.addWidget(self.bas_check)
        neuro_layout.addStretch()
        audio_main_layout.addLayout(neuro_layout)
        audio_info = QLabel("BGM: Binaural Beat (8% recommended) | Ambient: Rain / Fire")
        audio_info.setAlignment(Qt.AlignCenter)
        audio_main_layout.addWidget(audio_info)
        layout.addWidget(audio_group)
        nlc_group = self._create_group("Neuro-Learning (NLC)")
        nlc_layout = nlc_group.layout()
        nlc_enable_row = QHBoxLayout()
        self.nlc_enabled_check = QCheckBox("Enable Neuro-Linguistic Compiler")
        openai_cfg = config.get('openai', {})
        self.nlc_enabled_check.setChecked(openai_cfg.get('enabled', False))
        nlc_enable_row.addWidget(self.nlc_enabled_check)
        nlc_enable_row.addStretch()
        test_btn = QPushButton("🔊 Test")
        test_btn.setFixedWidth(60)
        test_btn.setCursor(Qt.PointingHandCursor)
        test_btn.setStyleSheet(f"QPushButton{{background-color:{Colors.BG_ELEVATED};color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:4px;padding:4px;}}QPushButton:hover{{background-color:{Colors.BLUE};}}")
        test_btn.clicked.connect(self._on_nlc_volume_test)
        nlc_enable_row.addWidget(test_btn)
        nlc_layout.addLayout(nlc_enable_row)
        api_row = QHBoxLayout()
        api_row.addWidget(QLabel("OpenAI API Key:"))
        self.openai_key_input = QLineEdit()
        self.openai_key_input.setText(openai_cfg.get('api_key', ''))
        self.openai_key_input.setEchoMode(QLineEdit.Password)
        self.openai_key_input.setStyleSheet(f"QLineEdit{{background-color:{Colors.BG_ELEVATED};color:{Colors.TEXT_PRIMARY};border:1px solid {Colors.BORDER};border-radius:4px;padding:8px;font-family:{Fonts.FAMILY_NUMBER};}}")
        api_row.addWidget(self.openai_key_input)
        nlc_layout.addLayout(api_row)
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("TTS Voice:"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'])
        self.voice_combo.setCurrentText(openai_cfg.get('voice', 'nova'))
        self.voice_combo.setStyleSheet(f"QComboBox{{background-color:{Colors.BG_ELEVATED};color:{Colors.TEXT_PRIMARY};border:1px solid {Colors.BORDER};border-radius:4px;padding:5px 8px;}}")
        voice_row.addWidget(self.voice_combo)
        voice_row.addStretch()
        nlc_layout.addLayout(voice_row)
        interval_header = QLabel("─── Learning Scheduler ───")
        interval_header.setAlignment(Qt.AlignCenter)
        interval_header.setStyleSheet(f"color: {Colors.TEXT_DIM};")
        nlc_layout.addWidget(interval_header)
        audio_cfg = config.get('audio', {})
        interval_grid = QGridLayout()
        interval_grid.setSpacing(8)
        interval_grid.addWidget(QLabel("Min Interval:"), 0, 0)
        self.learning_min_spin = NoScrollSpinBox()
        self.learning_min_spin.setRange(30, 600)
        self.learning_min_spin.setValue(audio_cfg.get('learning_interval_min', 120))
        self.learning_min_spin.setSuffix(" sec")
        self.learning_min_spin.setStyleSheet(f"QSpinBox{{background-color:{Colors.BG_ELEVATED};color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:4px;padding:5px 8px;}}")
        interval_grid.addWidget(self.learning_min_spin, 0, 1)
        interval_grid.addWidget(QLabel("Max Interval:"), 0, 2)
        self.learning_max_spin = NoScrollSpinBox()
        self.learning_max_spin.setRange(60, 900)
        self.learning_max_spin.setValue(audio_cfg.get('learning_interval_max', 300))
        self.learning_max_spin.setSuffix(" sec")
        self.learning_max_spin.setStyleSheet(f"QSpinBox{{background-color:{Colors.BG_ELEVATED};color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:4px;padding:5px 8px;}}")
        interval_grid.addWidget(self.learning_max_spin, 0, 3)
        nlc_layout.addLayout(interval_grid)
        nlc_info = QLabel("Bio-Adaptive Mixing: Volume auto-adjusts based on cognitive state")
        nlc_info.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 9pt;")
        nlc_info.setAlignment(Qt.AlignCenter)
        nlc_layout.addWidget(nlc_info)
        layout.addWidget(nlc_group)
        save_btn = QPushButton("💾 Save Settings")
        save_btn.setFont(Fonts.label(11, True))
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.CYAN};
                color: {Colors.BG_DARK};
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BLUE};
            }}
        """)
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignLeft)

        layout.addStretch()
        self.setLayout(layout)

    def _create_group(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setStyleSheet(f"""
            QGroupBox {{
                font-size: 11pt;
                color: {Colors.CYAN};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
            }}
        """)
        group.setLayout(QVBoxLayout())
        return group
    def _on_audio_enabled_changed(self, state):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_enabled(state == Qt.Checked)
    def _on_master_volume_changed(self, value):
        self.master_vol_label.setText(f"{value}%")
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_master_volume(value / 100.0)
    def _on_bgm_enabled_changed(self, state):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_bgm_enabled(state == Qt.Checked)
    def _on_bgm_volume_changed(self, value):
        self.bgm_vol_label.setText(f"{value}%")
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_bgm_volume(value / 100.0)
    def _on_nlc_volume_test(self):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound and neuro_sound.get_learning_files():
            neuro_sound.inject_learning_pulse()
            print("[NLC] Test pulse injected")
    def _on_ambient_slot_enabled_changed(self, index: int, state):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_ambient_slot(index, enabled=(state == Qt.Checked))
    def _on_ambient_slot_source_changed(self, index: int, source: str):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_ambient_slot(index, source=source)
    def _on_ambient_slot_volume_changed(self, index: int, value: int):
        if index < len(self.ambient_slot_labels): self.ambient_slot_labels[index].setText(f"{value}%")
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_ambient_slot(index, volume=value / 100.0)
    def _on_headphone_mode_changed(self, state):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_headphone_mode(state == Qt.Checked)
    def _on_bas_enabled_changed(self, state):
        neuro_sound = self._get_neuro_sound_engine()
        if neuro_sound: neuro_sound.set_bas_enabled(state == Qt.Checked)
    def _get_neuro_sound_engine(self):
        if self.neuro_sound: return self.neuro_sound
        try:
            main_window = self.window()
            if main_window and hasattr(main_window, 'dashboard_tab'):
                dashboard = main_window.dashboard_tab
                if hasattr(dashboard, 'neuro_sound') and dashboard.neuro_sound: return dashboard.neuro_sound
        except Exception: pass
        try:
            app = QApplication.instance()
            if app:
                for widget in app.topLevelWidgets():
                    if hasattr(widget, 'dashboard_tab'):
                        dashboard = widget.dashboard_tab
                        if hasattr(dashboard, 'neuro_sound') and dashboard.neuro_sound: return dashboard.neuro_sound
        except Exception: pass
        return None
    def save_settings(self):
        global config
        try:
            if 'oura' not in config:
                config['oura'] = {}
            if 'audio' not in config:
                config['audio'] = {}
            config['oura']['api_token'] = self.token_input.text()
            config['audio']['enabled'] = self.audio_enabled_check.isChecked()
            config['audio']['master_volume'] = self.master_volume_slider.value() / 100
            config['audio']['bgm_enabled'] = self.bgm_enabled_check.isChecked()
            config['audio']['bgm_volume'] = self.bgm_volume_slider.value() / 100
            ambient_slots = []
            for i in range(3):
                slot = {
                    'source': self.ambient_slot_combos[i].currentText(),
                    'volume': self.ambient_slot_sliders[i].value() / 100,
                    'enabled': self.ambient_slot_checks[i].isChecked(),
                }
                ambient_slots.append(slot)
            config['audio']['ambient_slots'] = ambient_slots

            config['audio']['headphone_mode'] = self.headphone_check.isChecked()
            config['audio']['bas_enabled'] = self.bas_check.isChecked()
            config['audio']['learning_interval_min'] = self.learning_min_spin.value()
            config['audio']['learning_interval_max'] = self.learning_max_spin.value()
            if 'openai' not in config:
                config['openai'] = {}
            config['openai']['enabled'] = self.nlc_enabled_check.isChecked()
            config['openai']['api_key'] = self.openai_key_input.text()
            config['openai']['voice'] = self.voice_combo.currentText()
            safe_write_json(CONFIG_PATH, config)
            print("v5.1.3: Settings saved")
            neuro_sound = self._get_neuro_sound_engine()
            if neuro_sound:
                neuro_sound.set_learning_interval(self.learning_min_spin.value(), self.learning_max_spin.value())
                if self.nlc_enabled_check.isChecked() and self.openai_key_input.text():
                    vocab_path = ROOT_PATH / "Data" / "vocab.json"
                    if vocab_path.exists():
                        neuro_sound.start_learning_compilation(vocab_path, self.openai_key_input.text(), self.voice_combo.currentText())
                        print(f"[NLC] Compilation triggered (voice={self.voice_combo.currentText()})")
        except Exception as e:
            print(f"Save error: {e}")
class HomeTab(QWidget):
    status_received = pyqtSignal(dict, dict, dict, dict, dict, dict)
    TOGGLE_SIZE = (36, 18)
    TOGGLE_STYLE_ON = "font-size:9px;font-weight:600;border-radius:9px;background:#238636;color:white;"
    TOGGLE_STYLE_OFF = "font-size:9px;font-weight:600;border-radius:9px;background:#484f58;color:#9198a1;"
    def __init__(self):
        super().__init__()
        self.status_received.connect(self._handle_status_update)
        self.ambient_sync: Optional[AmbientSync] = None
        self.desktop_organizer: Optional['DesktopOrganizer'] = None
        self._current_page = 'living'
        self._ac_widgets = {}
        self._ac_mode_map = {'AUTO': 'Auto', 'COOL': 'Cool', 'HEAT': 'Heat', 'DRY': 'Dry', 'FAN': 'Fan'}
        self._ac_mode_rev = {v: k for k, v in self._ac_mode_map.items()}
        self._ac_mode_icons = {'AUTO': '🔄', 'COOL': '❄️', 'HEAT': '🔥', 'DRY': '💧', 'FAN': '🌀'}
        self._ac_fan_map = {'AUTO': 'Auto', '1': 'Quiet', '2': '2', '3': '3', '4': '4', '5': 'Max'}
        self._ac_fan_rev = {v: k for k, v in self._ac_fan_map.items()}
        self._ac_vane_ud_map = {'SWING': 'Swing', 'AUTO': 'Auto', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5'}
        self._ac_vane_ud_rev = {v: k for k, v in self._ac_vane_ud_map.items()}
        self._ac_vane_lr_map = {'SWING': 'Swing', 'N-LEFT': 'N-Left', 'N-CENTER': 'N-Center', 'N-RIGHT': 'N-Right', 'M-LEFT': 'M-Left', 'M-CENTER': 'M-Center', 'M-RIGHT': 'M-Right', 'W-LEFT': 'W-Left', 'W-CENTER': 'Wide', 'W-RIGHT': 'W-Right'}
        self._ac_vane_lr_rev = {v: k for k, v in self._ac_vane_lr_map.items()}
        self._bravia_stats = {}
        self.hue_room_widgets = {}
        self.vol_profile_widgets = {}
        self._sidebar_items = {}
        self._climate_target_temp = 24
        self._climate_zones = [{'name': 'Boost', 'icon': '❄️', 'range': '-3°C below', 'target_diff': -3, 'temp_offset': 2, 'fan': '5', 'vane_ud': '5', 'vane_lr': 'W-CENTER'},{'name': 'Maintain', 'icon': '✅', 'range': '±2°C', 'target_diff': 2, 'fan': 'AUTO', 'vane_ud': 'SWING', 'vane_lr': 'M-CENTER'},{'name': 'Reduce', 'icon': '🔥', 'range': '+3°C above', 'target_diff': 3, 'temp_offset': -2, 'fan': '1', 'vane_ud': '1', 'vane_lr': 'M-CENTER'}]
        self._climate_enabled = False
        self.initUI()
        self._load_settings()
        self._init_home_system()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_status)
        self.timer.start(3000)
    def initUI(self):
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("background:#161b22;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 8, 0, 8)
        sidebar_layout.setSpacing(0)
        sections = [('Devices', [('bravia', '📺', 'BRAVIA', 'ON'),('hue', '💡', 'Hue Rooms', '8')]),('Climate', [('climate', '🌡️', 'Living Control', 'OFF'),('bedroom', '🌙', 'Bedroom', '18°C'),('preset', '⚙️', 'Presets', '')]),('Automation', [('away', '🚶', 'Away Detection', 'OFF')]),('System', [('connection', '🔌', 'Connection', '')])]
        for section_name, items in sections:
            title = QLabel(section_name)
            title.setStyleSheet("font-size:9px;color:#7d8590;padding:8px 14px 4px;text-transform:uppercase;letter-spacing:0.5px;background:transparent;")
            sidebar_layout.addWidget(title)
            for key, icon, text, badge in items:
                item = self._create_sidebar_item(key, icon, text, badge)
                sidebar_layout.addWidget(item)
                self._sidebar_items[key] = item
        sidebar_layout.addStretch()
        border = QFrame()
        border.setFixedWidth(1)
        border.setStyleSheet("background:#30363d;")
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet("background:#0d1117;")
        self._pages = {}
        for page_id in ['bravia', 'hue', 'living', 'bedroom', 'climate', 'preset', 'away', 'connection']:
            page = self._create_page(page_id)
            self._pages[page_id] = page
            self._content_stack.addWidget(page)
        main_layout.addWidget(sidebar)
        main_layout.addWidget(border)
        main_layout.addWidget(self._content_stack)
        self.setLayout(main_layout)
        self._select_sidebar('climate')
    def _create_sidebar_item(self, key: str, icon: str, text: str, badge: str) -> QWidget:
        item = QWidget()
        item.setFixedHeight(28)
        item.setCursor(Qt.PointingHandCursor)
        item.setProperty('key', key)
        item.setProperty('active', False)
        item.mousePressEvent = lambda e, k=key: self._select_sidebar(k)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)
        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(16)
        icon_lbl.setStyleSheet("font-size:12px;background:transparent;")
        icon_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_lbl)
        text_lbl = QLabel(text)
        text_lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
        layout.addWidget(text_lbl)
        layout.addStretch()
        badge_lbl = QLabel(badge)
        badge_lbl.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#30363d;color:#9198a1;min-width:20px;")
        badge_lbl.setFixedHeight(16)
        badge_lbl.setAlignment(Qt.AlignCenter)
        badge_lbl.setVisible(bool(badge))
        layout.addWidget(badge_lbl)
        item._icon = icon_lbl
        item._text = text_lbl
        item._badge = badge_lbl
        self._update_sidebar_item_style(item, False)
        return item
    def _update_sidebar_item_style(self, item: QWidget, active: bool):
        if active:
            item.setStyleSheet("background:#0d1117;")
            item._text.setStyleSheet("font-size:10px;color:#00d4aa;font-weight:500;background:transparent;")
            item._icon.setStyleSheet("font-size:12px;background:transparent;")
        else:
            item.setStyleSheet("background:transparent;")
            item._text.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            item._icon.setStyleSheet("font-size:12px;background:transparent;")
    def _update_sidebar_badge(self, key: str, text: str, style: str = 'default'):
        item = self._sidebar_items.get(key)
        if not item:
            return
        item._badge.setText(text)
        item._badge.setVisible(bool(text))
        if style == 'on':
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#238636;color:white;min-width:20px;")
        elif style == 'off':
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#484f58;color:#9198a1;min-width:20px;")
        elif style == 'heat':
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#da3633;color:white;min-width:20px;")
        elif style == 'cool':
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#1f6feb;color:white;min-width:20px;")
        elif style == 'dry':
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#58a6ff;color:white;min-width:20px;")
        elif style == 'auto':
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#238636;color:white;min-width:20px;")
        elif style.startswith('hue:'):
            bri = int(style.split(':')[1]) if ':' in style else 50
            r, g, b = self._hue_brightness_to_rgb(bri)
            item._badge.setStyleSheet(f"font-size:8px;padding:0px 5px;border-radius:4px;background:rgb({r},{g},{b});color:{'#000' if bri > 50 else '#fff'};min-width:20px;")
        else:
            item._badge.setStyleSheet("font-size:8px;padding:0px 5px;border-radius:4px;background:#30363d;color:#9198a1;min-width:20px;")
    def _hue_brightness_to_rgb(self, bri: int) -> tuple:
        bri = max(0, min(100, bri))
        base_r, base_g, base_b = 255, 180, 80
        factor = bri / 100
        return (int(base_r * factor), int(base_g * factor), int(base_b * factor))
    def _select_sidebar(self, key: str):
        for k, item in self._sidebar_items.items():
            self._update_sidebar_item_style(item, k == key)
        self._current_page = key
        if key in self._pages:
            self._content_stack.setCurrentWidget(self._pages[key])
    def _create_page(self, page_id: str) -> QWidget:
        if page_id == 'bravia':
            return self._create_bravia_page()
        elif page_id == 'hue':
            return self._create_hue_page()
        elif page_id == 'living':
            return self._create_ac_page('living', 'Living', '🏠')
        elif page_id == 'bedroom':
            return self._create_ac_page('bedroom', 'Bedroom', '🌙')
        elif page_id == 'climate':
            return self._create_climate_page()
        elif page_id == 'preset':
            return self._create_preset_page()
        elif page_id == 'away':
            return self._create_away_page()
        elif page_id == 'connection':
            return self._create_connection_page()
        return QWidget()
    def _create_scroll_page(self, title_icon: str, title_text: str) -> tuple:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#0d1117;}QScrollBar:vertical{width:8px;background:#0d1117;}QScrollBar::handle:vertical{background:#30363d;border-radius:4px;}QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setStyleSheet("background:#0d1117;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        header = QWidget()
        header.setStyleSheet("background:transparent;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(f"{title_icon} {title_text}")
        title.setStyleSheet("font-size:16px;color:#00d4aa;font-weight:600;background:transparent;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(header)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        return page, layout, header_layout
    def _create_card(self, title: str = '') -> tuple:
        card = QFrame()
        card_id = f"card_{id(card)}"
        card.setObjectName(card_id)
        card.setStyleSheet(f"#{card_id}{{background:#161b22;border:1px solid #30363d;border-radius:10px;}}#{card_id} *{{border:none;}}")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)
        if title:
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet("font-size:12px;color:#9198a1;background:transparent;")
            card_layout.addWidget(title_lbl)
        return card, card_layout
    def _create_bravia_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('📺', 'BRAVIA')
        card, card_layout = self._create_card()
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (key, label) in enumerate([('power', 'Power'), ('app', 'App'), ('volume', 'Volume'), ('saving', 'Saving')]):
            stat = QFrame()
            stat.setStyleSheet("QFrame{background:#21262d;border-radius:4px;border:none;}")
            stat_lay = QHBoxLayout(stat)
            stat_lay.setContentsMargins(10, 8, 10, 8)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            val = QLabel("--")
            val.setStyleSheet("font-size:11px;font-weight:500;color:#e6edf3;background:transparent;")
            stat_lay.addWidget(lbl)
            stat_lay.addStretch()
            stat_lay.addWidget(val)
            grid.addWidget(stat, i // 2, i % 2)
            self._bravia_stats[key] = val
        card_layout.addLayout(grid)
        layout.addWidget(card)
        sync_card, sync_layout = self._create_card()
        sync_header = QHBoxLayout()
        sync_header.setSpacing(8)
        sync_title = QLabel('🔆 Brightness Sync')
        sync_title.setStyleSheet("font-size:12px;font-weight:500;color:#e6edf3;background:transparent;")
        sync_header.addWidget(sync_title)
        sync_header.addStretch()
        self.sync_toggle = self._create_toggle_label(False)
        self.sync_toggle.mousePressEvent = lambda e: self._toggle_brightness_sync()
        sync_header.addWidget(self.sync_toggle)
        sync_layout.addLayout(sync_header)
        for lbl_text, spin_attr, icon_text in [("Hue >", "threshold_off_spin", "🔆 Bright"),("Hue <", "threshold_high_spin", "🌿 Eco")]:
            row = QFrame()
            row.setFixedHeight(32)
            row.setStyleSheet("QFrame{background:#21262d;border-radius:4px;}QFrame:hover{background:#282e36;}")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(12, 0, 12, 0)
            row_lay.setSpacing(10)
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
            row_lay.addWidget(lbl)
            spin = NoScrollSpinBox()
            spin.setRange(0, 100)
            spin.setSuffix("%")
            spin.setFixedSize(65, 22)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet("QSpinBox{background:#30363d;border:none;border-radius:4px;color:#00d4aa;font-size:11px;padding:0 4px;}QSpinBox::up-button,QSpinBox::down-button{width:0;}")
            setattr(self, spin_attr, spin)
            row_lay.addWidget(spin)
            row_lay.addStretch()
            icon_lbl = QLabel(icon_text)
            icon_lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            row_lay.addWidget(icon_lbl)
            sync_layout.addWidget(row)
        layout.addWidget(sync_card)
        vol_card, vol_layout = self._create_card()
        vol_header = QHBoxLayout()
        vol_header.setSpacing(8)
        vol_title = QLabel("🔊 App Volume")
        vol_title.setStyleSheet("font-size:12px;font-weight:500;color:#e6edf3;background:transparent;")
        vol_header.addWidget(vol_title)
        vol_header.addStretch()
        add_btn = QLabel("+ Add")
        add_btn.setFixedSize(36, 18)
        add_btn.setAlignment(Qt.AlignCenter)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet("font-size:9px;font-weight:600;border-radius:8px;background:#30363d;color:#9198a1;")
        add_btn.mousePressEvent = lambda e: self._add_volume_profile()
        vol_header.addWidget(add_btn)
        self.volume_auto_toggle = self._create_toggle_label(False)
        self.volume_auto_toggle.mousePressEvent = lambda e: self._toggle_volume_auto()
        vol_header.addWidget(self.volume_auto_toggle)
        vol_layout.addLayout(vol_header)
        self._vol_container = QVBoxLayout()
        self._vol_container.setSpacing(0)
        vol_layout.addLayout(self._vol_container)
        layout.addWidget(vol_card)
        layout.addStretch()
        return page
    def _create_hue_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('💡', 'Hue Rooms')
        home_cfg = config.get('home', {})
        self._hide_zone_members = home_cfg.get('hide_zone_members', False)
        self._focus_keep_rooms = home_cfg.get('focus_keep_rooms', [])
        self._focus_enabled = home_cfg.get('focus_lighting', False)
        hue_card, hue_layout = self._create_card()
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(0, 0, 0, 10)
        ctrl_row.setSpacing(6)
        self._hide_zones_btn = QPushButton("Hide Zones")
        self._hide_zones_btn.setFixedHeight(26)
        self._hide_zones_btn.setCursor(Qt.PointingHandCursor)
        self._hide_zones_btn.clicked.connect(self._toggle_hide_zones)
        self._update_ctrl_btn_style(self._hide_zones_btn, self._hide_zone_members)
        ctrl_row.addWidget(self._hide_zones_btn)
        self.focus_btn = QPushButton("Focus: dim others")
        self.focus_btn.setFixedHeight(26)
        self.focus_btn.setCursor(Qt.PointingHandCursor)
        self.focus_btn.clicked.connect(self._toggle_focus)
        self._update_ctrl_btn_style(self.focus_btn, self._focus_enabled)
        ctrl_row.addWidget(self.focus_btn)
        ctrl_row.addStretch()
        hue_layout.addLayout(ctrl_row)
        self._hue_grid_widget = QWidget()
        self._hue_grid_widget.setStyleSheet("background:transparent;")
        self._hue_grid = QGridLayout(self._hue_grid_widget)
        self._hue_grid.setSpacing(6)
        self._hue_grid.setContentsMargins(0, 0, 0, 0)
        hue_layout.addWidget(self._hue_grid_widget)
        layout.addWidget(hue_card)
        layout.addStretch()
        return page
    def _update_ctrl_btn_style(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setStyleSheet("QPushButton{background:#238636;color:white;border:none;border-radius:4px;font-size:10px;font-weight:500;padding:0 10px;}QPushButton:hover{background:#2ea043;}")
        else:
            btn.setStyleSheet("QPushButton{background:#30363d;color:#9198a1;border:none;border-radius:4px;font-size:10px;font-weight:500;padding:0 10px;}QPushButton:hover{background:#484f58;}")
    def _toggle_hide_zones(self):
        self._hide_zone_members = not self._hide_zone_members
        self._update_ctrl_btn_style(self._hide_zones_btn, self._hide_zone_members)
        if self.ambient_sync:
            self.ambient_sync.set_hide_zone_members(self._hide_zone_members)
        self._rebuild_hue_grid()
        self._auto_save_connection()
    def _create_ac_page(self, room_key: str, room_name: str, room_icon: str) -> QWidget:
        page, layout, header = self._create_scroll_page(room_icon, room_name)
        w = {'current_temp': 24, 'power_state': None, 'room_key': room_key}
        power_toggle = self._create_toggle_label(False)
        power_toggle.setText("--")
        power_toggle.mousePressEvent = lambda e, rk=room_key: self._toggle_ac_power_room(rk)
        header.addWidget(power_toggle)
        w['power_toggle'] = power_toggle
        status_card, status_layout = self._create_card()
        status_row = QHBoxLayout()
        status_row.setSpacing(14)
        ac_icon = QLabel(room_icon)
        ac_icon.setFixedSize(40, 40)
        ac_icon.setAlignment(Qt.AlignCenter)
        ac_icon.setStyleSheet("font-size:20px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #484f58,stop:1 #3d444d);border-radius:8px;")
        w['ac_icon'] = ac_icon
        status_row.addWidget(ac_icon)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        status_lbl = QLabel("--")
        status_lbl.setStyleSheet("font-size:13px;font-weight:500;color:#e6edf3;background:transparent;")
        w['status_lbl'] = status_lbl
        detail_lbl = QLabel("Fan: -- / UD: -- / LR: --")
        detail_lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
        w['detail_lbl'] = detail_lbl
        info_col.addWidget(status_lbl)
        info_col.addWidget(detail_lbl)
        status_row.addLayout(info_col)
        status_row.addStretch()
        temp_col = QVBoxLayout()
        temp_col.setAlignment(Qt.AlignRight)
        temp_value = QLabel("--°")
        temp_value.setStyleSheet("font-size:24px;font-weight:600;color:#e6edf3;background:transparent;")
        temp_value.setAlignment(Qt.AlignRight)
        w['temp_value'] = temp_value
        temp_label = QLabel("Set Temp")
        temp_label.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        temp_label.setAlignment(Qt.AlignRight)
        temp_col.addWidget(temp_value)
        temp_col.addWidget(temp_label)
        status_row.addLayout(temp_col)
        status_layout.addLayout(status_row)
        layout.addWidget(status_card)
        stats_card, stats_layout = self._create_card()
        stats_grid = QGridLayout()
        stats_grid.setSpacing(6)
        for i, (icon, label, val_key) in enumerate([('🌡️', 'Room', 'room_temp'), ('💨', 'Fan', 'fan'), ('↕', 'UD', 'vane_ud'), ('↔', 'LR', 'vane_lr')]):
            stat = QFrame()
            stat.setStyleSheet("QFrame{background:#21262d;border-radius:4px;border:none;}")
            stat_lay = QHBoxLayout(stat)
            stat_lay.setContentsMargins(10, 8, 10, 8)
            lbl = QLabel(f"{icon} {label}")
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            val = QLabel("--")
            val.setStyleSheet("font-size:11px;font-weight:500;color:#e6edf3;background:transparent;")
            stat_lay.addWidget(lbl)
            stat_lay.addStretch()
            stat_lay.addWidget(val)
            stats_grid.addWidget(stat, i // 2, i % 2)
            w[val_key] = val
        stats_layout.addLayout(stats_grid)
        layout.addWidget(stats_card)
        ctrl_title = QLabel("Control")
        ctrl_title.setStyleSheet("font-size:11px;color:#7d8590;text-transform:uppercase;letter-spacing:0.5px;background:transparent;margin-top:4px;")
        layout.addWidget(ctrl_title)
        ctrl_card, ctrl_layout = self._create_card()
        ctrl_grid = QGridLayout()
        ctrl_grid.setSpacing(6)
        menu_style = "QMenu{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:2px;}QMenu::item{padding:6px 12px;color:#e6edf3;}QMenu::item:selected{background:#30363d;}"
        btn_style = "QPushButton{background:#21262d;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:11px;padding:10px;}QPushButton:hover{background:#30363d;}QPushButton::menu-indicator{image:none;}"
        for i, (btn_key, icon, label, items, handler) in enumerate([('temp_btn', '🌡️', 'Temp', ['--'] + [f'{t}°C' for t in range(16, 32)], '_on_ac_temp_menu'),('fan_btn', '💨', 'Fan', list(self._ac_fan_map.values()), '_on_ac_fan_jp'),('vane_ud_btn', '↕', 'UD', list(self._ac_vane_ud_map.values()), '_on_ac_vane_ud_jp'),('vane_lr_btn', '↔', 'LR', list(self._ac_vane_lr_map.values()), '_on_ac_vane_lr_jp'),('mode_btn', '⚙', 'Mode', list(self._ac_mode_map.values()), '_on_ac_mode_jp')]):
            btn = QPushButton(f"{icon} {label}")
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.PointingHandCursor)
            menu = QMenu()
            menu.setStyleSheet(menu_style)
            for item in items:
                menu.addAction(item)
            def make_handler(h_name, r_key):
                return lambda action: getattr(self, h_name)(r_key, action.text())
            menu.triggered.connect(make_handler(handler, room_key))
            btn.setMenu(menu)
            ctrl_grid.addWidget(btn, i // 3, i % 3)
            w[btn_key] = btn
        ctrl_layout.addLayout(ctrl_grid)
        layout.addWidget(ctrl_card)
        layout.addStretch()
        self._ac_widgets[room_key] = w
        return page
    def _create_climate_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('🌡️', 'Living Control')
        self._climate_toggle = self._create_toggle_label(False)
        self._climate_toggle.mousePressEvent = lambda e: self._toggle_climate_control()
        header.addWidget(self._climate_toggle)
        target_card, target_layout = self._create_card('🎯 Target Temp')
        temp_row = QHBoxLayout()
        temp_row.setSpacing(12)
        temp_row.setAlignment(Qt.AlignVCenter)
        temp_box = QFrame()
        temp_box.setStyleSheet("QFrame{background:transparent;border:none;}")
        temp_box_lay = QHBoxLayout(temp_box)
        temp_box_lay.setContentsMargins(0, 0, 0, 0)
        temp_box_lay.setSpacing(2)
        temp_box_lay.setAlignment(Qt.AlignVCenter)
        temp_down = QPushButton("−")
        temp_down.setFixedSize(20, 20)
        temp_down.setStyleSheet("QPushButton{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#9198a1;font-size:14px;font-weight:600;}QPushButton:hover{background:#30363d;color:#e6edf3;}")
        temp_down.clicked.connect(lambda: self._adjust_climate_temp(-1))
        temp_box_lay.addWidget(temp_down, 0, Qt.AlignVCenter)
        self._climate_temp_lbl = QLabel(f"{self._climate_target_temp}°C")
        self._climate_temp_lbl.setFixedWidth(56)
        temp_color = '#58a6ff' if config.get('home', {}).get('climate_mode', 'COOL') == 'COOL' else '#f85149'
        self._climate_temp_lbl.setStyleSheet(f"font-size:18px;font-weight:600;color:{temp_color};background:transparent;")
        self._climate_temp_lbl.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        temp_box_lay.addWidget(self._climate_temp_lbl, 0, Qt.AlignVCenter)
        temp_up = QPushButton("+")
        temp_up.setFixedSize(20, 20)
        temp_up.setStyleSheet("QPushButton{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#9198a1;font-size:14px;font-weight:600;}QPushButton:hover{background:#30363d;color:#e6edf3;}")
        temp_up.clicked.connect(lambda: self._adjust_climate_temp(1))
        temp_box_lay.addWidget(temp_up, 0, Qt.AlignVCenter)
        temp_row.addWidget(temp_box)
        mode_box = QFrame()
        mode_box.setStyleSheet("QFrame{background:#21262d;border:1px solid #30363d;border-radius:8px;}")
        mode_box_lay = QHBoxLayout(mode_box)
        mode_box_lay.setContentsMargins(2, 2, 2, 2)
        mode_box_lay.setSpacing(2)
        mode_box_lay.setAlignment(Qt.AlignVCenter)
        mode_btn_style = "QPushButton{background:transparent;border:none;border-radius:6px;color:#9198a1;font-size:11px;padding:6px 10px;}QPushButton:hover{background:#30363d;}QPushButton:checked{background:#1a2332;color:#58a6ff;}"
        self._climate_cool_btn = QPushButton("❄️ Cool")
        self._climate_cool_btn.setCheckable(True)
        self._climate_cool_btn.setStyleSheet(mode_btn_style)
        self._climate_cool_btn.clicked.connect(lambda: self._set_climate_mode('COOL'))
        mode_box_lay.addWidget(self._climate_cool_btn, 0, Qt.AlignVCenter)
        self._climate_heat_btn = QPushButton("🔥 Heat")
        self._climate_heat_btn.setCheckable(True)
        self._climate_heat_btn.setStyleSheet(mode_btn_style)
        self._climate_heat_btn.clicked.connect(lambda: self._set_climate_mode('HEAT'))
        mode_box_lay.addWidget(self._climate_heat_btn, 0, Qt.AlignVCenter)
        temp_row.addWidget(mode_box)
        self._climate_mode = config.get('home', {}).get('climate_mode', 'COOL')
        if self._climate_mode == 'COOL':
            self._climate_cool_btn.setChecked(True)
        else:
            self._climate_heat_btn.setChecked(True)
        temp_row.addStretch()
        current_box = QVBoxLayout()
        current_box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        current_box.setSpacing(2)
        self._climate_current_lbl = QLabel("Current --°C")
        self._climate_current_lbl.setStyleSheet("font-size:13px;color:#9198a1;background:transparent;")
        self._climate_current_lbl.setAlignment(Qt.AlignRight)
        self._climate_diff_lbl = QLabel("")
        self._climate_diff_lbl.setStyleSheet("font-size:10px;color:#3fb950;background:transparent;")
        self._climate_diff_lbl.setAlignment(Qt.AlignRight)
        current_box.addWidget(self._climate_current_lbl)
        current_box.addWidget(self._climate_diff_lbl)
        temp_row.addLayout(current_box)
        target_layout.addLayout(temp_row)
        self._scale_container = QWidget()
        self._scale_container.setFixedHeight(50)
        self._scale_container.setStyleSheet("background:transparent;")
        scale_main = QVBoxLayout(self._scale_container)
        scale_main.setContentsMargins(0, 0, 0, 0)
        scale_main.setSpacing(2)
        pin_bar_container = QWidget()
        pin_bar_container.setFixedHeight(24)
        pin_bar_container.setStyleSheet("background:transparent;")
        self._climate_pin = QLabel("📍")
        self._climate_pin.setParent(pin_bar_container)
        self._climate_pin.setFixedSize(20, 20)
        self._climate_pin.setStyleSheet("font-size:14px;background:transparent;")
        self._climate_pin.move(0, 0)
        self._climate_pin_target_x = 0
        self._climate_pin_anim = QPropertyAnimation(self._climate_pin, b"pos")
        self._climate_pin_anim.setDuration(600)
        self._climate_pin_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._climate_wobble_timer = QTimer()
        self._climate_wobble_timer.timeout.connect(self._wobble_climate_pin)
        self._climate_wobble_timer.start(1500)
        self._pin_bar_container = pin_bar_container
        scale_main.addWidget(pin_bar_container)
        self._scale_bar = QFrame()
        self._scale_bar.setFixedHeight(6)
        self._scale_bar.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1f6feb,stop:0.5 #00d4aa,stop:1 #f85149);border-radius:3px;")
        scale_main.addWidget(self._scale_bar)
        scale_labels = QHBoxLayout()
        scale_labels.setContentsMargins(0, 2, 0, 0)
        lbl_16 = QLabel("16°C")
        lbl_16.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        lbl_zone = QLabel("Comfort Zone")
        lbl_zone.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        lbl_zone.setAlignment(Qt.AlignCenter)
        lbl_30 = QLabel("30°C")
        lbl_30.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        lbl_30.setAlignment(Qt.AlignRight)
        scale_labels.addWidget(lbl_16)
        scale_labels.addStretch()
        scale_labels.addWidget(lbl_zone)
        scale_labels.addStretch()
        scale_labels.addWidget(lbl_30)
        scale_main.addLayout(scale_labels)
        target_layout.addWidget(self._scale_container)
        layout.addWidget(target_card)
        zone_card, zone_layout = self._create_card('⚙ Zone Settings')
        zone_grid = QHBoxLayout()
        zone_grid.setSpacing(8)
        self._zone_cards = []
        for i, zone in enumerate(self._climate_zones):
            zc = QFrame()
            zc_id = f"zone_{i}"
            zc.setObjectName(zc_id)
            zc.setStyleSheet(f"#{zc_id}{{background:#21262d;border-radius:6px;border:1px solid transparent;}}#{zc_id}:hover{{border-color:#30363d;}}")
            zc.setCursor(Qt.PointingHandCursor)
            zc.mousePressEvent = lambda e, idx=i: self._edit_zone_settings(idx)
            zc_layout = QVBoxLayout(zc)
            zc_layout.setContentsMargins(12, 12, 12, 12)
            zc_layout.setSpacing(6)
            zc_layout.setAlignment(Qt.AlignCenter)
            zc_icon = QLabel(zone['icon'])
            zc_icon.setStyleSheet("font-size:20px;background:transparent;")
            zc_icon.setAlignment(Qt.AlignCenter)
            zc_layout.addWidget(zc_icon)
            zc_title = QLabel(zone['name'] + ' Mode')
            zc_title.setStyleSheet("font-size:12px;font-weight:500;color:#e6edf3;background:transparent;")
            zc_title.setAlignment(Qt.AlignCenter)
            zc_layout.addWidget(zc_title)
            zc_range = QLabel(f"Target {zone['range']}")
            zc_range.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;")
            zc_range.setAlignment(Qt.AlignCenter)
            zc_layout.addWidget(zc_range)
            fan_jp = self._ac_fan_map.get(zone['fan'], zone['fan'])
            vane_ud_jp = self._ac_vane_ud_map.get(zone['vane_ud'], zone['vane_ud'])
            vane_lr_jp = self._ac_vane_lr_map.get(zone['vane_lr'], zone['vane_lr'])
            offset = zone.get('temp_offset', 0)
            temp_str = f"Offset: {'+' if offset >= 0 else ''}{offset}°C\n" if i != 1 else ""
            zc_settings = QLabel(f"{temp_str}Fan: {fan_jp}\nUD: {vane_ud_jp}\nLR: {vane_lr_jp}")
            zc_settings.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            zc_settings.setAlignment(Qt.AlignCenter)
            zc_layout.addWidget(zc_settings)
            zone_grid.addWidget(zc)
            self._zone_cards.append({'frame': zc, 'icon': zc_icon, 'range': zc_range, 'settings': zc_settings, 'id': zc_id})
        zone_layout.addLayout(zone_grid)
        layout.addWidget(zone_card)
        threshold_row = QFrame()
        threshold_row.setStyleSheet("QFrame{background:#21262d;border-radius:6px;border:none;}")
        threshold_row_lay = QHBoxLayout(threshold_row)
        threshold_row_lay.setContentsMargins(12, 8, 12, 8)
        threshold_row_lay.setSpacing(8)
        heat_lbl = QLabel("❄️")
        heat_lbl.setStyleSheet("font-size:14px;background:transparent;")
        threshold_row_lay.addWidget(heat_lbl)
        self._threshold_heat_slider = QSlider(Qt.Horizontal)
        self._threshold_heat_slider.setRange(-10, -1)
        self._threshold_heat_slider.setValue(self._climate_zones[0].get('target_diff', -3))
        self._threshold_heat_slider.setFixedWidth(80)
        self._threshold_heat_slider.setStyleSheet("QSlider{background:transparent;}QSlider::groove:horizontal{height:4px;background:#30363d;border-radius:2px;}QSlider::handle:horizontal{width:12px;height:12px;margin:-4px 0;background:#238636;border-radius:6px;}QSlider::sub-page:horizontal{background:#238636;border-radius:2px;}")
        self._threshold_heat_slider.valueChanged.connect(self._on_threshold_slider_changed)
        threshold_row_lay.addWidget(self._threshold_heat_slider)
        self._threshold_heat_val = QLabel(f"{self._climate_zones[0].get('target_diff', -3)}°C")
        self._threshold_heat_val.setFixedWidth(35)
        self._threshold_heat_val.setStyleSheet("font-size:11px;color:#238636;background:transparent;")
        threshold_row_lay.addWidget(self._threshold_heat_val)
        threshold_row_lay.addStretch()
        mid_bar = QFrame()
        mid_bar.setFixedSize(60, 6)
        mid_bar.setStyleSheet("background:#1f6feb;border-radius:3px;")
        threshold_row_lay.addWidget(mid_bar)
        mid_lbl = QLabel("Maintain")
        mid_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;")
        threshold_row_lay.addWidget(mid_lbl)
        threshold_row_lay.addStretch()
        self._threshold_cool_val = QLabel(f"+{self._climate_zones[2].get('target_diff', 3)}°C")
        self._threshold_cool_val.setFixedWidth(35)
        self._threshold_cool_val.setAlignment(Qt.AlignRight)
        self._threshold_cool_val.setStyleSheet("font-size:11px;color:#f85149;background:transparent;")
        threshold_row_lay.addWidget(self._threshold_cool_val)
        self._threshold_cool_slider = QSlider(Qt.Horizontal)
        self._threshold_cool_slider.setRange(1, 10)
        self._threshold_cool_slider.setValue(self._climate_zones[2].get('target_diff', 3))
        self._threshold_cool_slider.setFixedWidth(80)
        self._threshold_cool_slider.setStyleSheet("QSlider{background:transparent;}QSlider::groove:horizontal{height:4px;background:#30363d;border-radius:2px;}QSlider::handle:horizontal{width:12px;height:12px;margin:-4px 0;background:#f85149;border-radius:6px;}QSlider::sub-page:horizontal{background:#f85149;border-radius:2px;}")
        self._threshold_cool_slider.valueChanged.connect(self._on_threshold_slider_changed)
        threshold_row_lay.addWidget(self._threshold_cool_slider)
        cool_lbl = QLabel("🔥")
        cool_lbl.setStyleSheet("font-size:14px;background:transparent;")
        threshold_row_lay.addWidget(cool_lbl)
        layout.addWidget(threshold_row)
        status_card, status_layout = self._create_card()
        status_grid = QGridLayout()
        status_grid.setSpacing(6)
        for i, (icon, label) in enumerate([('📍', 'Current Zone'), ('⏱', 'ETA')]):
            stat = QFrame()
            stat.setStyleSheet("QFrame{background:#21262d;border-radius:4px;border:none;}")
            stat_lay = QHBoxLayout(stat)
            stat_lay.setContentsMargins(10, 8, 10, 8)
            lbl = QLabel(f"{icon} {label}")
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            val = QLabel("--")
            val.setStyleSheet("font-size:11px;font-weight:500;color:#58a6ff;background:transparent;")
            stat_lay.addWidget(lbl)
            stat_lay.addStretch()
            stat_lay.addWidget(val)
            status_grid.addWidget(stat, 0, i)
            if i == 0:
                self._climate_zone_lbl = val
            else:
                self._climate_eta_lbl = val
        status_layout.addLayout(status_grid)
        layout.addWidget(status_card)
        co2_card, co2_layout = self._create_card('📊 CO2 Control')
        co2_header = QHBoxLayout()
        co2_header.setSpacing(10)
        self._co2_current_lbl = QLabel("Current --ppm")
        self._co2_current_lbl.setStyleSheet("font-size:13px;color:#9198a1;background:transparent;")
        co2_header.addWidget(self._co2_current_lbl)
        co2_header.addStretch()
        self._co2_auto_toggle = self._create_toggle_label(config.get('home', {}).get('co2_automation_enabled', False))
        self._co2_auto_toggle.mousePressEvent = lambda e: self._toggle_co2_automation()
        co2_header.addWidget(self._co2_auto_toggle)
        co2_layout.addLayout(co2_header)
        co2_rules_lbl = QLabel("Control Rules")
        co2_rules_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;margin-top:4px;")
        co2_layout.addWidget(co2_rules_lbl)
        self._co2_rules = config.get('home', {}).get('co2_rules', [{'threshold': 1200, 'fan': 'High', 'vent': 'high'}, {'threshold': 900, 'fan': 'Med', 'vent': 'low'}, {'threshold': 700, 'fan': 'Low', 'vent': 'off', 'below': True}])
        self._co2_rule_widgets = []
        seg_btn_style = "QPushButton{background:transparent;border:none;border-radius:4px;color:#9198a1;font-size:10px;padding:4px 8px;}QPushButton:hover{background:#30363d;}QPushButton:checked{background:#1a2332;color:#58a6ff;}"
        for i, rule in enumerate(self._co2_rules):
            row = QFrame()
            row.setFixedHeight(38)
            row.setStyleSheet("QFrame{background:#21262d;border-radius:4px;}")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(10, 0, 10, 0)
            row_lay.setSpacing(6)
            thresh_spin = NoScrollSpinBox()
            thresh_spin.setRange(400, 2000)
            thresh_spin.setValue(rule.get('threshold', 800))
            thresh_spin.setSingleStep(50)
            thresh_spin.setFixedSize(60, 26)
            thresh_spin.setStyleSheet("QSpinBox{background:#30363d;border:none;border-radius:4px;color:#e6edf3;font-size:11px;padding:0 6px;}QSpinBox::up-button,QSpinBox::down-button{width:0;}")
            thresh_spin.valueChanged.connect(lambda v, idx=i: self._update_co2_rule(idx, 'threshold', v))
            row_lay.addWidget(thresh_spin)
            cond_lbl = QLabel("ppm below" if rule.get('below') else "ppm above")
            cond_lbl.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
            cond_lbl.setFixedWidth(44)
            row_lay.addWidget(cond_lbl)
            row_lay.addSpacing(4)
            fan_icon = QLabel("🌀")
            fan_icon.setStyleSheet("font-size:10px;background:transparent;")
            row_lay.addWidget(fan_icon)
            fan_box = QFrame()
            fan_box.setStyleSheet("QFrame{background:#30363d;border-radius:6px;}")
            fan_box_lay = QHBoxLayout(fan_box)
            fan_box_lay.setContentsMargins(2, 2, 2, 2)
            fan_box_lay.setSpacing(0)
            fan_btns = {}
            for val in ['Lo', 'Md', 'Hi']:
                btn = QPushButton(val)
                btn.setCheckable(True)
                btn.setStyleSheet(seg_btn_style)
                btn.setFixedSize(30, 22)
                real_val = {'Lo': 'Low', 'Md': 'Med', 'Hi': 'High'}[val]
                btn.clicked.connect(lambda _, idx=i, v=real_val: self._set_co2_fan(idx, v))
                fan_box_lay.addWidget(btn)
                fan_btns[real_val] = btn
            fan_btns[rule.get('fan', 'Med')].setChecked(True)
            row_lay.addWidget(fan_box)
            row_lay.addSpacing(8)
            arrow = QLabel("→")
            arrow.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;")
            row_lay.addWidget(arrow)
            vent_icon = QLabel("💨")
            vent_icon.setStyleSheet("font-size:10px;background:transparent;")
            row_lay.addWidget(vent_icon)
            vent_box = QFrame()
            vent_box.setStyleSheet("QFrame{background:#30363d;border-radius:6px;}")
            vent_box_lay = QHBoxLayout(vent_box)
            vent_box_lay.setContentsMargins(2, 2, 2, 2)
            vent_box_lay.setSpacing(0)
            vent_btns = {}
            for val in ['Of', 'Lo', 'Hi']:
                btn = QPushButton(val)
                btn.setCheckable(True)
                btn.setStyleSheet(seg_btn_style)
                btn.setFixedSize(30, 22)
                real_val = {'Of': 'Off', 'Lo': 'Low', 'Hi': 'High'}[val]
                btn.clicked.connect(lambda _, idx=i, v=real_val.lower(): self._set_co2_vent(idx, v))
                vent_box_lay.addWidget(btn)
                vent_btns[real_val] = btn
            vent_map = {'off': 'Off', 'low': 'Low', 'high': 'High'}
            vent_btns[vent_map.get(rule.get('vent', 'low'), 'Low')].setChecked(True)
            row_lay.addWidget(vent_box)
            row_lay.addStretch()
            co2_layout.addWidget(row)
            self._co2_rule_widgets.append({'thresh': thresh_spin, 'fan_btns': fan_btns, 'vent_btns': vent_btns})
        co2_params_row = QHBoxLayout()
        co2_params_row.setSpacing(8)
        dwell_lbl = QLabel("Dwell")
        dwell_lbl.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        co2_params_row.addWidget(dwell_lbl)
        self._co2_dwell_spin = NoScrollSpinBox()
        self._co2_dwell_spin.setRange(1, 10)
        self._co2_dwell_spin.setValue(int(config.get('home', {}).get('co2_dwell_minutes', 3)))
        self._co2_dwell_spin.setFixedSize(50, 26)
        self._co2_dwell_spin.setStyleSheet("QSpinBox{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#e6edf3;font-size:11px;padding:0 6px;}QSpinBox::up-button,QSpinBox::down-button{width:0;}")
        self._co2_dwell_spin.valueChanged.connect(self._save_co2_settings)
        co2_params_row.addWidget(self._co2_dwell_spin)
        dwell_unit = QLabel("min to trigger")
        dwell_unit.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        co2_params_row.addWidget(dwell_unit)
        co2_params_row.addSpacing(16)
        cd_lbl = QLabel("Cooldown")
        cd_lbl.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        co2_params_row.addWidget(cd_lbl)
        self._co2_cooldown_spin = NoScrollSpinBox()
        self._co2_cooldown_spin.setRange(1, 30)
        self._co2_cooldown_spin.setValue(int(config.get('home', {}).get('co2_cooldown_minutes', 5)))
        self._co2_cooldown_spin.setFixedSize(50, 26)
        self._co2_cooldown_spin.setStyleSheet("QSpinBox{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#e6edf3;font-size:11px;padding:0 6px;}QSpinBox::up-button,QSpinBox::down-button{width:0;}")
        self._co2_cooldown_spin.valueChanged.connect(self._save_co2_settings)
        co2_params_row.addWidget(self._co2_cooldown_spin)
        cd_unit = QLabel("min")
        cd_unit.setStyleSheet("font-size:9px;color:#7d8590;background:transparent;")
        co2_params_row.addWidget(cd_unit)
        co2_params_row.addStretch()
        co2_layout.addLayout(co2_params_row)
        layout.addWidget(co2_card)
        layout.addStretch()
        self._last_temp_log_time = 0
        return page
    def _create_preset_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('⚙️', 'Presets')
        self._ac_presets = config.get('home', {}).get('ac_presets', [{'name': 'Shisha Mode', 'icon': '🌿', 'trigger': 'shisha', 'enabled': True, 'settings': {'temp': 25, 'fan': '3', 'vane_ud': 'SWING', 'vane_lr': 'W-CENTER'}}])
        self._preset_widgets = []
        preset_card, preset_layout = self._create_card()
        for i, preset in enumerate(self._ac_presets):
            row = QFrame()
            row.setFixedHeight(40)
            row.setStyleSheet("QFrame{background:#21262d;border-radius:4px;}QFrame:hover{background:#282e36;}")
            row.setCursor(Qt.PointingHandCursor)
            row.mousePressEvent = lambda e, idx=i: self._edit_ac_preset(idx)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(10, 0, 10, 0)
            row_lay.setSpacing(10)
            icon_lbl = QLabel(preset.get('icon', '⚙️'))
            icon_lbl.setFixedWidth(24)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet("font-size:16px;background:transparent;")
            row_lay.addWidget(icon_lbl)
            name_lbl = QLabel(preset.get('name', 'Preset'))
            name_lbl.setStyleSheet("font-size:12px;font-weight:500;color:#e6edf3;background:transparent;")
            row_lay.addWidget(name_lbl)
            settings = preset.get('settings', {})
            fan_jp = self._ac_fan_map.get(settings.get('fan', ''), settings.get('fan', ''))
            vane_ud_jp = self._ac_vane_ud_map.get(settings.get('vane_ud', ''), settings.get('vane_ud', ''))
            vane_lr_jp = self._ac_vane_lr_map.get(settings.get('vane_lr', ''), settings.get('vane_lr', ''))
            desc_parts = [f"{settings['temp']}°C" if settings.get('temp') else '', f"Fan:{fan_jp}" if fan_jp else '', f"UD:{vane_ud_jp}" if vane_ud_jp else '', f"LR:{vane_lr_jp}" if vane_lr_jp else '']
            desc_lbl = QLabel(' / '.join([p for p in desc_parts if p]) or '--')
            desc_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;")
            row_lay.addWidget(desc_lbl)
            row_lay.addStretch()
            toggle = QLabel("ON" if preset.get('enabled', False) else "OFF")
            toggle.setFixedSize(28, 16)
            toggle.setAlignment(Qt.AlignCenter)
            toggle.setStyleSheet(f"font-size:9px;font-weight:600;border-radius:8px;background:{'#238636' if preset.get('enabled', False) else '#484f58'};color:{'white' if preset.get('enabled', False) else '#9198a1'};")
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.mousePressEvent = lambda e, idx=i, t=toggle: (e.accept(), self._toggle_ac_preset_click(idx, t))
            row_lay.addWidget(toggle)
            preset_layout.addWidget(row)
            self._preset_widgets.append({'row': row, 'icon': icon_lbl, 'name': name_lbl, 'desc': desc_lbl, 'toggle': toggle})
        layout.addWidget(preset_card)
        layout.addStretch()
        return page
    def _toggle_ac_preset_click(self, idx: int, toggle: QLabel):
        enabled = toggle.text() == "OFF"
        toggle.setText("ON" if enabled else "OFF")
        toggle.setStyleSheet(f"font-size:9px;font-weight:600;border-radius:8px;background:{'#238636' if enabled else '#484f58'};color:{'white' if enabled else '#9198a1'};")
        self._ac_presets[idx]['enabled'] = enabled
        self._save_ac_presets()
    def _create_away_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('🚶', 'Away Detection')
        away_card, away_layout = self._create_card('🖥️ Away Monitor Control')
        away_toggle_row = QHBoxLayout()
        away_toggle_row.setSpacing(10)
        away_lbl = QLabel("Enabled")
        away_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        away_toggle_row.addWidget(away_lbl)
        self.away_toggle = self._create_toggle_label(False)
        self.away_toggle.mousePressEvent = lambda e: self._toggle_away_detection()
        away_toggle_row.addWidget(self.away_toggle)
        away_toggle_row.addStretch()
        away_layout.addLayout(away_toggle_row)
        away_delay_row = QHBoxLayout()
        away_delay_row.setSpacing(10)
        away_delay_lbl = QLabel("Away Delay")
        away_delay_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        away_delay_row.addWidget(away_delay_lbl)
        self.away_delay_spin = NoScrollDoubleSpinBox()
        self.away_delay_spin.setRange(1.0, 60.0)
        self.away_delay_spin.setSingleStep(1.0)
        self.away_delay_spin.setSuffix(" min")
        self.away_delay_spin.setValue(5.0)
        self.away_delay_spin.setStyleSheet("QDoubleSpinBox{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:4px 8px;color:#e6edf3;font-size:11px;}QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:0;}")
        self.away_delay_spin.setFixedWidth(80)
        away_delay_row.addWidget(self.away_delay_spin)
        away_delay_row.addStretch()
        away_layout.addLayout(away_delay_row)
        away_status_row = QHBoxLayout()
        away_status_row.setSpacing(10)
        away_status_lbl = QLabel("Status")
        away_status_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        away_status_row.addWidget(away_status_lbl)
        self._away_status_lbl = QLabel("Active")
        self._away_status_lbl.setStyleSheet("font-size:11px;color:#3fb950;background:transparent;")
        away_status_row.addWidget(self._away_status_lbl)
        away_status_row.addStretch()
        away_layout.addLayout(away_status_row)
        layout.addWidget(away_card)
        sleep_card, sleep_layout = self._create_card('💤 Sleep Detection')
        sleep_toggle_row = QHBoxLayout()
        sleep_toggle_row.setSpacing(10)
        sleep_lbl = QLabel("Enabled")
        sleep_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        sleep_toggle_row.addWidget(sleep_lbl)
        self.sleep_toggle = self._create_toggle_label(False)
        self.sleep_toggle.mousePressEvent = lambda e: self._toggle_sleep_detection()
        sleep_toggle_row.addWidget(self.sleep_toggle)
        sleep_toggle_row.addStretch()
        sleep_layout.addLayout(sleep_toggle_row)
        delay_row = QHBoxLayout()
        delay_row.setSpacing(10)
        delay_lbl = QLabel("Delay")
        delay_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        delay_row.addWidget(delay_lbl)
        self.sleep_delay_spin = NoScrollDoubleSpinBox()
        self.sleep_delay_spin.setRange(0.5, 30.0)
        self.sleep_delay_spin.setSingleStep(0.5)
        self.sleep_delay_spin.setSuffix(" min")
        self.sleep_delay_spin.setValue(1.0)
        self.sleep_delay_spin.setStyleSheet("QDoubleSpinBox{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:4px 8px;color:#e6edf3;font-size:11px;}QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:0;}")
        self.sleep_delay_spin.setFixedWidth(80)
        delay_row.addWidget(self.sleep_delay_spin)
        delay_row.addStretch()
        sleep_layout.addLayout(delay_row)
        layout.addWidget(sleep_card)
        layout.addStretch()
        return page
    def _create_volume_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('🔊', 'App Volume Profiles')
        vol_card, vol_layout = self._create_card()
        self._vol_container = QVBoxLayout()
        self._vol_container.setSpacing(0)
        vol_layout.addLayout(self._vol_container)
        layout.addWidget(vol_card)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("➕ Add Profile")
        add_btn.setFixedHeight(36)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet("QPushButton{background:#21262d;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:11px;}QPushButton:hover{background:#30363d;}")
        add_btn.clicked.connect(self._add_volume_profile)
        btn_row.addWidget(add_btn)
        layout.addLayout(btn_row)
        layout.addStretch()
        return page
    def _create_connection_page(self) -> QWidget:
        page, layout, header = self._create_scroll_page('🔌', 'Connection')
        input_style = "QLineEdit{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:6px 10px;color:#e6edf3;font-size:11px;}QLineEdit:focus{border-color:#58a6ff;}"
        hue_card, hue_layout = self._create_card('💡 Philips Hue')
        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel("Bridge IP")
        lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
        lbl.setFixedWidth(80)
        row.addWidget(lbl)
        self.hue_ip_input = QLineEdit()
        self.hue_ip_input.setPlaceholderText("192.168.x.x")
        self.hue_ip_input.setStyleSheet(input_style)
        row.addWidget(self.hue_ip_input)
        hue_layout.addLayout(row)
        layout.addWidget(hue_card)
        bravia_card, bravia_layout = self._create_card('📺 Sony BRAVIA')
        for lbl_text, attr, placeholder, password in [("IP Address", "bravia_ip_input", "192.168.x.x", False),("PSK", "bravia_psk_input", "Pre-Shared Key", True)]:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            lbl.setFixedWidth(80)
            row.addWidget(lbl)
            inp = QLineEdit()
            inp.setPlaceholderText(placeholder)
            inp.setStyleSheet(input_style)
            if password:
                inp.setEchoMode(QLineEdit.Password)
            setattr(self, attr, inp)
            row.addWidget(inp)
            bravia_layout.addLayout(row)
        layout.addWidget(bravia_card)
        ac_card, ac_layout = self._create_card('❄️ Mitsubishi Kirigamine')
        for lbl_text, attr, placeholder in [("Living IP", "kirigamine_ip_input", "192.168.x.x"),("Bedroom IP", "kirigamine_bedroom_ip_input", "192.168.x.x")]:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            lbl.setFixedWidth(80)
            row.addWidget(lbl)
            inp = QLineEdit()
            inp.setPlaceholderText(placeholder)
            inp.setStyleSheet(input_style)
            setattr(self, attr, inp)
            row.addWidget(inp)
            ac_layout.addLayout(row)
        layout.addWidget(ac_card)
        switchbot_card, switchbot_layout = self._create_card('🌡️ SwitchBot')
        token_row = QHBoxLayout()
        token_row.setSpacing(8)
        token_lbl = QLabel("Token")
        token_lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
        token_lbl.setFixedWidth(36)
        token_row.addWidget(token_lbl)
        self.switchbot_token_input = QLineEdit()
        self.switchbot_token_input.setPlaceholderText("API Token")
        self.switchbot_token_input.setStyleSheet(input_style)
        self.switchbot_token_input.setEchoMode(QLineEdit.Password)
        token_row.addWidget(self.switchbot_token_input)
        fetch_btn = QPushButton("🔄 Fetch")
        fetch_btn.setFixedWidth(60)
        fetch_btn.setStyleSheet("QPushButton{background:#238636;border:none;border-radius:4px;color:white;font-size:10px;padding:6px;}QPushButton:hover{background:#2ea043;}")
        fetch_btn.clicked.connect(self._fetch_switchbot_devices)
        token_row.addWidget(fetch_btn)
        switchbot_layout.addLayout(token_row)
        assign_lbl = QLabel("🎯 Sensor Assignment")
        assign_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;margin-top:8px;")
        switchbot_layout.addWidget(assign_lbl)
        combo_style = "QComboBox{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:4px 8px;color:#e6edf3;font-size:10px;}QComboBox:hover{border-color:#58a6ff;}QComboBox::drop-down{border:none;width:20px;}QComboBox::down-arrow{image:none;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid #9198a1;}QComboBox QAbstractItemView{background:#21262d;border:1px solid #30363d;color:#e6edf3;selection-background-color:#30363d;}"
        self._switchbot_combos = {}
        for key, label in [('living', '📍 Living Temp/Humid'), ('bedroom', '📍 Bedroom Temp/Humid'), ('co2', '📊 CO2 Sensor')]:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            lbl.setFixedWidth(100)
            row.addWidget(lbl)
            combo = QComboBox()
            combo.setStyleSheet(combo_style)
            combo.addItem("-- Not Set --", "")
            combo.currentIndexChanged.connect(self._auto_save_connection)
            row.addWidget(combo)
            switchbot_layout.addLayout(row)
            self._switchbot_combos[key] = combo
        auto_lbl = QLabel("⚡ CO2 Control Devices")
        auto_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;margin-top:8px;")
        switchbot_layout.addWidget(auto_lbl)
        for key, label in [('fan', '🌀 Fan (IR)'), ('vent_high', '💨 Vent (High)'), ('vent_low', '💨 Vent (Low)'), ('vent_off', '💨 Vent (Off)')]:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
            lbl.setFixedWidth(100)
            row.addWidget(lbl)
            combo = QComboBox()
            combo.setStyleSheet(combo_style)
            combo.addItem("-- Not Set --", "")
            combo.currentIndexChanged.connect(self._auto_save_connection)
            row.addWidget(combo)
            switchbot_layout.addLayout(row)
            self._switchbot_combos[key] = combo
        devices_header = QHBoxLayout()
        devices_header.setSpacing(8)
        devices_lbl = QLabel("📍 Detected Devices")
        devices_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;margin-top:8px;")
        devices_header.addWidget(devices_lbl)
        devices_header.addStretch()
        self._switchbot_devices_list = QLabel("(Press Fetch)")
        self._switchbot_devices_list.setStyleSheet("font-size:9px;color:#484f58;background:transparent;")
        self._switchbot_devices_list.setWordWrap(True)
        switchbot_layout.addLayout(devices_header)
        switchbot_layout.addWidget(self._switchbot_devices_list)
        self._switchbot_devices_cache = []
        self._switchbot_ir_cache = []
        layout.addWidget(switchbot_card)
        desktop_card, desktop_layout = self._create_card('🖥️ Desktop Organizer')
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_lbl = QLabel("Path:")
        path_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;")
        path_lbl.setFixedWidth(30)
        path_row.addWidget(path_lbl)
        self._desktop_path_input = QLineEdit()
        self._desktop_path_input.setPlaceholderText("C:\\Users\\...\\Desktop")
        self._desktop_path_input.setText(config.get('home', {}).get('desktop_path', ''))
        self._desktop_path_input.setStyleSheet("QLineEdit{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#e6edf3;font-size:10px;padding:4px 8px;}QLineEdit:focus{border-color:#58a6ff;}")
        self._desktop_path_input.textChanged.connect(self._on_desktop_path_changed)
        path_row.addWidget(self._desktop_path_input)
        desktop_layout.addLayout(path_row)
        desktop_row = QHBoxLayout()
        desktop_row.setSpacing(10)
        desktop_info = QLabel("Ghost file cleanup")
        desktop_info.setStyleSheet("font-size:10px;color:#9198a1;background:transparent;")
        desktop_row.addWidget(desktop_info)
        desktop_row.addStretch()
        self._desktop_org_toggle = self._create_toggle_label(False)
        self._desktop_org_toggle.mousePressEvent = lambda e: self._toggle_desktop_organizer()
        desktop_row.addWidget(self._desktop_org_toggle)
        desktop_layout.addLayout(desktop_row)
        layout.addWidget(desktop_card)
        for inp in [self.hue_ip_input, self.bravia_ip_input, self.bravia_psk_input, self.kirigamine_ip_input, self.kirigamine_bedroom_ip_input, self.switchbot_token_input]:
            inp.textChanged.connect(self._auto_save_connection)
        layout.addStretch()
        return page
    def _create_vol_row(self, app: str, enabled: bool, volume: int):
        row = QFrame()
        row.setFixedHeight(32)
        row.setStyleSheet("QFrame{background:#21262d;border-radius:4px;}QFrame:hover{background:#282e36;}")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(12, 0, 8, 0)
        row_lay.setSpacing(12)
        app_lbl = QLabel(app)
        app_lbl.setStyleSheet("font-size:11px;color:#e6edf3;background:transparent;")
        row_lay.addWidget(app_lbl)
        row_lay.addStretch()
        toggle = QLabel("ON" if enabled else "OFF")
        toggle.setFixedSize(28, 16)
        toggle.setAlignment(Qt.AlignCenter)
        toggle.setStyleSheet(f"font-size:9px;font-weight:600;border-radius:8px;background:{'#238636' if enabled else '#484f58'};color:{'white' if enabled else '#9198a1'};")
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.mousePressEvent = lambda e, t=toggle, a=app: self._on_vol_toggle_click(a, t)
        row_lay.addWidget(toggle)
        spin = NoScrollSpinBox()
        spin.setRange(0, 100)
        spin.setValue(volume)
        spin.setFixedSize(36, 20)
        spin.setAlignment(Qt.AlignCenter)
        spin.setStyleSheet("QSpinBox{background:#30363d;border:none;border-radius:4px;color:#00d4aa;font-size:10px;padding:0 4px;}QSpinBox::up-button,QSpinBox::down-button{width:0;}")
        spin.valueChanged.connect(self._save_vol_profiles)
        row_lay.addWidget(spin)
        del_btn = QLabel("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setAlignment(Qt.AlignCenter)
        del_btn.setStyleSheet("font-size:12px;color:#484f58;background:transparent;")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.mousePressEvent = lambda e, a=app: self._delete_volume_profile(a)
        row_lay.addWidget(del_btn)
        self._vol_container.addWidget(row)
        self.vol_profile_widgets[app] = {'container': row, 'toggle': toggle, 'spin': spin}
    def _on_vol_toggle_click(self, app: str, toggle: QLabel):
        enabled = toggle.text() == "OFF"
        toggle.setText("ON" if enabled else "OFF")
        toggle.setStyleSheet(f"font-size:9px;font-weight:600;border-radius:8px;background:{'#238636' if enabled else '#484f58'};color:{'white' if enabled else '#9198a1'};")
        self._save_vol_profiles()
    def _load_settings(self):
        home_cfg = config.get('home', {})
        self.hue_ip_input.setText(home_cfg.get('hue_ip', ''))
        self.bravia_ip_input.setText(home_cfg.get('bravia_ip', ''))
        self.bravia_psk_input.setText(home_cfg.get('bravia_psk', ''))
        self.kirigamine_ip_input.setText(home_cfg.get('kirigamine_ip', ''))
        self.kirigamine_bedroom_ip_input.setText(home_cfg.get('kirigamine_bedroom_ip', ''))
        self.switchbot_token_input.setText(home_cfg.get('switchbot_token', ''))
        devices = home_cfg.get('switchbot_devices', {})
        for key in ['living', 'bedroom', 'co2', 'fan', 'vent_high', 'vent_low', 'vent_off']:
            if key in devices and key in self._switchbot_combos:
                info = devices[key]
                dev_id = info.get('id', '') if isinstance(info, dict) else ''
                name = info.get('name', key) if isinstance(info, dict) else key
                combo = self._switchbot_combos[key]
                combo.blockSignals(True)
                combo.addItem(name, dev_id)
                combo.setCurrentIndex(combo.count() - 1)
                combo.blockSignals(False)
        if devices:
            lines = [f"📋 Configured: {len(devices)} devices"]
            for key, info in devices.items():
                name = info.get('name', key) if isinstance(info, dict) else key
                lines.append(f"  • {name}")
            self._switchbot_devices_list.setText("\n".join(lines))
            self._switchbot_devices_list.setStyleSheet("font-size:9px;color:#9198a1;background:#161b22;border-radius:4px;padding:8px;")
        thresholds = home_cfg.get('thresholds', {'off': 50, 'high': 5})
        self.threshold_off_spin.setValue(thresholds.get('off', 50))
        self.threshold_high_spin.setValue(thresholds.get('high', 5))
        self.sleep_delay_spin.setValue(home_cfg.get('sleep_detection_minutes', 1.0))
        self.away_delay_spin.setValue(home_cfg.get('away_detection_minutes', 5.0))
        if home_cfg.get('brightness_sync_enabled', home_cfg.get('auto_start', False)):
            self._update_toggle_label(self.sync_toggle, True)
        if home_cfg.get('volume_auto_enabled', False):
            self._update_toggle_label(self.volume_auto_toggle, True)
        if home_cfg.get('sleep_detection_enabled', False):
            self._update_toggle_label(self.sleep_toggle, True)
        if home_cfg.get('away_detection_enabled', False):
            self._update_toggle_label(self.away_toggle, True)
        self._climate_target_temp = home_cfg.get('climate_target_temp', 24)
        self._climate_enabled = home_cfg.get('climate_enabled', False)
        self._climate_zones = home_cfg.get('climate_zones', self._climate_zones)
        self._climate_temp_lbl.setText(f"{self._climate_target_temp}°C")
        if self._climate_enabled:
            self._update_toggle_label(self._climate_toggle, True)
        self._refresh_zone_ui()
        vol_profiles = home_cfg.get('volume_profiles', {'Spotify': {'enabled': False, 'volume': 20}, 'Netflix': {'enabled': False, 'volume': 20}, 'YouTube': {'enabled': False, 'volume': 20}})
        for app, data in vol_profiles.items():
            self._create_vol_row(app, data.get('enabled', False), data.get('volume', 20))
        self._bedroom_ac_settings = home_cfg.get('bedroom_ac', {})
        if self._bedroom_ac_settings:
            QTimer.singleShot(500, lambda: self._apply_bedroom_ac_settings_to_gui())
        self._init_sidebar_badges(home_cfg)
    def _create_toggle_label(self, on: bool = False) -> QLabel:
        toggle = QLabel("ON" if on else "OFF")
        toggle.setFixedSize(*self.TOGGLE_SIZE)
        toggle.setAlignment(Qt.AlignCenter)
        toggle.setStyleSheet(self.TOGGLE_STYLE_ON if on else self.TOGGLE_STYLE_OFF)
        toggle.setCursor(Qt.PointingHandCursor)
        return toggle
    def _update_toggle_label(self, lbl: QLabel, on: bool):
        lbl.setText("ON" if on else "OFF")
        lbl.setStyleSheet(self.TOGGLE_STYLE_ON if on else self.TOGGLE_STYLE_OFF)
    def _update_toggle_btn_style(self, btn: QPushButton, on: bool):
        btn.setText("ON" if on else "OFF")
        btn.setStyleSheet(f"QPushButton{{{self.TOGGLE_STYLE_ON if on else self.TOGGLE_STYLE_OFF}}}")
    def _init_sidebar_badges(self, home_cfg: Dict):
        self._update_sidebar_badge('hue', '8', 'default')
        away_on = home_cfg.get('away_detection_enabled', False)
        self._update_sidebar_badge('away', 'ON' if away_on else 'OFF', 'on' if away_on else 'off')
        climate_on = home_cfg.get('climate_enabled', False)
        self._update_sidebar_badge('climate', 'ON' if climate_on else 'OFF', 'on' if climate_on else 'off')
        self._update_sidebar_badge('bravia', '--', 'off')
        self._update_sidebar_badge('living', '--', 'default')
        self._update_sidebar_badge('bedroom', '--', 'default')
    def _toggle_brightness_sync(self):
        enabled = self.sync_toggle.text() == "OFF"
        self._update_toggle_label(self.sync_toggle, enabled)
        if enabled:
            if not self.ambient_sync:
                if not self._create_ambient_sync():
                    self._update_toggle_label(self.sync_toggle, False)
                    return
            if not self.ambient_sync.is_running():
                self.ambient_sync.start()
            self.ambient_sync.set_brightness_sync_enabled(True)
        else:
            if self.ambient_sync:
                self.ambient_sync.set_brightness_sync_enabled(False)
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['brightness_sync_enabled'] = enabled
        safe_write_json(CONFIG_PATH, config)
    def _toggle_volume_auto(self):
        enabled = self.volume_auto_toggle.text() == "OFF"
        self._update_toggle_label(self.volume_auto_toggle, enabled)
        if enabled:
            if not self.ambient_sync:
                if not self._create_ambient_sync():
                    self._update_toggle_label(self.volume_auto_toggle, False)
                    return
            if not self.ambient_sync.is_running():
                self.ambient_sync.start()
            self.ambient_sync.set_volume_auto_enabled(True)
        else:
            if self.ambient_sync:
                self.ambient_sync.set_volume_auto_enabled(False)
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['volume_auto_enabled'] = enabled
        safe_write_json(CONFIG_PATH, config)
    def _toggle_focus(self):
        self._focus_enabled = not getattr(self, '_focus_enabled', False)
        self._update_ctrl_btn_style(self.focus_btn, self._focus_enabled)
        if not self.ambient_sync:
            if not self._create_ambient_sync():
                self._focus_enabled = False
                self._update_ctrl_btn_style(self.focus_btn, False)
                return
        if not self.ambient_sync.is_running():
            if not self.ambient_sync.start():
                self._focus_enabled = False
                self._update_ctrl_btn_style(self.focus_btn, False)
                return
        self.ambient_sync.set_focus_lighting(self._focus_enabled, self._focus_keep_rooms)
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['focus_lighting'] = self._focus_enabled
        config['home']['focus_keep_rooms'] = self._focus_keep_rooms
        safe_write_json(CONFIG_PATH, config)
    def _toggle_sleep_detection(self):
        enabled = self.sleep_toggle.text() == "OFF"
        self._update_toggle_label(self.sleep_toggle, enabled)
        if self.ambient_sync and hasattr(self.ambient_sync, 'sleep_detector'):
            self.ambient_sync.sleep_detector.set_enabled(enabled)
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['sleep_detection_enabled'] = enabled
        config['home']['sleep_detection_minutes'] = self.sleep_delay_spin.value()
        safe_write_json(CONFIG_PATH, config)
    def _toggle_away_detection(self):
        enabled = self.away_toggle.text() == "OFF"
        self._update_toggle_label(self.away_toggle, enabled)
        self._update_sidebar_badge('away', 'ON' if enabled else 'OFF', 'on' if enabled else 'off')
        if self.ambient_sync and hasattr(self.ambient_sync, 'away_detector'):
            self.ambient_sync.set_away_detection(enabled, self.away_delay_spin.value())
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['away_detection_enabled'] = enabled
        config['home']['away_detection_minutes'] = self.away_delay_spin.value()
        safe_write_json(CONFIG_PATH, config)
    def _toggle_climate_control(self):
        self._climate_enabled = not self._climate_enabled
        self._update_toggle_label(self._climate_toggle, self._climate_enabled)
        self._update_sidebar_badge('climate', 'ON' if self._climate_enabled else 'OFF', 'on' if self._climate_enabled else 'off')
        if self.ambient_sync and self.ambient_sync.kirigamine:
            if self._climate_enabled:
                zone_idx = getattr(self, '_current_zone_idx', 1)
                zone = self._climate_zones[zone_idx]
                kwargs = {'power': True, 'fan': zone['fan'], 'vane_lr': zone['vane_lr']}
                vane_ud = zone['vane_ud']
                if vane_ud in ('SWING', 'AUTO'):
                    kwargs['vane_ud'] = vane_ud
                elif vane_ud.isdigit():
                    kwargs['vane_ud'] = 'MANUAL'
                    kwargs['vane_ud_pos'] = int(vane_ud)
                print(f"[Climate] Power ON with zone {zone_idx}: {kwargs}")
                threading.Thread(target=self.ambient_sync.kirigamine.set_state_with_retry, kwargs=kwargs, daemon=True).start()
            else:
                print("[Climate] Power OFF")
                threading.Thread(target=self.ambient_sync.kirigamine.set_state_with_retry, kwargs={'power': False}, daemon=True).start()
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['climate_enabled'] = self._climate_enabled
        safe_write_json(CONFIG_PATH, config)
    def _adjust_climate_temp(self, delta: int):
        self._climate_target_temp = max(16, min(30, self._climate_target_temp + delta))
        self._climate_temp_lbl.setText(f"{self._climate_target_temp}°C")
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['climate_target_temp'] = self._climate_target_temp
        safe_write_json(CONFIG_PATH, config)
    def _init_home_system(self):
        if not HOME_AVAILABLE:
            return
        home_cfg = config.get('home', {})
        if home_cfg.get('hue_ip'):
            self.ambient_sync = AmbientSync(home_cfg)
            self.ambient_sync.set_status_callback(self._on_status_update)
            self.ambient_sync.set_sleep_callback(self._on_sleep_state_changed)
            if self.ambient_sync.start():
                if self.sync_toggle.text() == "ON":
                    self.ambient_sync.set_brightness_sync_enabled(True)
                if self.volume_auto_toggle.text() == "ON":
                    self.ambient_sync.set_volume_auto_enabled(True)
                if getattr(self, '_focus_enabled', False):
                    self.ambient_sync.set_focus_lighting(True, self._focus_keep_rooms)
                if self._climate_enabled:
                    QTimer.singleShot(5000, self._apply_initial_zone)
        layout_path = str(ROOT_PATH / 'Data' / 'desktop_layout.json')
        custom_path = home_cfg.get('desktop_path', '')
        self.desktop_organizer = DesktopOrganizer(layout_path, custom_desktop_path=custom_path if custom_path else None)
        self.desktop_organizer.start()
        if home_cfg.get('desktop_organizer_enabled', False):
            self.desktop_organizer.set_enabled(True)
            if hasattr(self, '_desktop_org_toggle'):
                self._update_toggle_label(self._desktop_org_toggle, True)
    def _create_ambient_sync(self) -> bool:
        home_cfg = self._build_config()
        if not home_cfg.get('hue_ip'):
            return False
        self.ambient_sync = AmbientSync(home_cfg)
        self.ambient_sync.set_status_callback(self._on_status_update)
        self.ambient_sync.set_sleep_callback(self._on_sleep_state_changed)
        return True
    def _build_config(self) -> Dict:
        home_cfg = config.get('home', {})
        vol_profiles = {}
        if hasattr(self, 'vol_profile_widgets') and self.vol_profile_widgets:
            for app, widgets in self.vol_profile_widgets.items():
                vol_profiles[app] = {'enabled': widgets['toggle'].text() == 'ON', 'volume': widgets['spin'].value()}
        else:
            vol_profiles = home_cfg.get('volume_profiles', {})
        co2_enabled = self._co2_auto_toggle.text() == 'ON' if hasattr(self, '_co2_auto_toggle') else home_cfg.get('co2_automation_enabled', False)
        co2_rules = self._co2_rules if hasattr(self, '_co2_rules') else home_cfg.get('co2_rules', [])
        co2_dwell = self._co2_dwell_spin.value() if hasattr(self, '_co2_dwell_spin') else home_cfg.get('co2_dwell_minutes', 3)
        co2_cooldown = self._co2_cooldown_spin.value() if hasattr(self, '_co2_cooldown_spin') else home_cfg.get('co2_cooldown_minutes', 5)
        hue_ip = self.hue_ip_input.text() if hasattr(self, 'hue_ip_input') else home_cfg.get('hue_ip', '')
        bravia_ip = self.bravia_ip_input.text() if hasattr(self, 'bravia_ip_input') else home_cfg.get('bravia_ip', '')
        bravia_psk = self.bravia_psk_input.text() if hasattr(self, 'bravia_psk_input') else home_cfg.get('bravia_psk', '')
        kiri_ip = self.kirigamine_ip_input.text() if hasattr(self, 'kirigamine_ip_input') else home_cfg.get('kirigamine_ip', '')
        kiri_bed_ip = self.kirigamine_bedroom_ip_input.text() if hasattr(self, 'kirigamine_bedroom_ip_input') else home_cfg.get('kirigamine_bedroom_ip', '')
        sb_token = self.switchbot_token_input.text() if hasattr(self, 'switchbot_token_input') else home_cfg.get('switchbot_token', '')
        sb_devices = self._get_switchbot_devices() if hasattr(self, '_switchbot_combos') else home_cfg.get('switchbot_devices', {})
        thresh_off = self.threshold_off_spin.value() if hasattr(self, 'threshold_off_spin') else home_cfg.get('thresholds', {}).get('off', 50)
        thresh_high = self.threshold_high_spin.value() if hasattr(self, 'threshold_high_spin') else home_cfg.get('thresholds', {}).get('high', 5)
        sleep_min = self.sleep_delay_spin.value() if hasattr(self, 'sleep_delay_spin') else home_cfg.get('sleep_detection_minutes', 1.0)
        away_min = self.away_delay_spin.value() if hasattr(self, 'away_delay_spin') else home_cfg.get('away_detection_minutes', 5.0)
        brightness_sync = self.sync_toggle.text() == 'ON' if hasattr(self, 'sync_toggle') else home_cfg.get('brightness_sync_enabled', False)
        volume_auto = self.volume_auto_toggle.text() == 'ON' if hasattr(self, 'volume_auto_toggle') else home_cfg.get('volume_auto_enabled', False)
        focus_keep_rooms = self._focus_keep_rooms if hasattr(self, '_focus_keep_rooms') else home_cfg.get('focus_keep_rooms', [])
        hide_zone_members = self._hide_zone_members if hasattr(self, '_hide_zone_members') else home_cfg.get('hide_zone_members', False)
        focus_enabled = getattr(self, '_focus_enabled', False)
        return {'hue_ip': hue_ip, 'bravia_ip': bravia_ip, 'bravia_psk': bravia_psk, 'kirigamine_ip': kiri_ip, 'kirigamine_bedroom_ip': kiri_bed_ip, 'switchbot_token': sb_token, 'switchbot_devices': sb_devices, 'thresholds': {'off': thresh_off, 'high': thresh_high}, 'volume_profiles': vol_profiles, 'brightness_sync_enabled': brightness_sync, 'volume_auto_enabled': volume_auto, 'focus_lighting': focus_enabled, 'focus_keep_rooms': focus_keep_rooms, 'hide_zone_members': hide_zone_members, 'sleep_detection_enabled': self.sleep_toggle.text() == 'ON', 'sleep_detection_minutes': sleep_min, 'away_detection_enabled': self.away_toggle.text() == 'ON', 'away_detection_minutes': away_min, 'climate_enabled': self._climate_enabled, 'climate_target_temp': self._climate_target_temp, 'climate_zones': self._climate_zones, 'bedroom_ac': getattr(self, '_bedroom_ac_settings', {}), 'co2_automation_enabled': co2_enabled, 'co2_rules': co2_rules, 'co2_dwell_minutes': co2_dwell, 'co2_cooldown_minutes': co2_cooldown}
    def _auto_save_connection(self):
        if not hasattr(self, '_save_timer'):
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.timeout.connect(self._do_save_connection)
        self._save_timer.start(1000)
    def _do_save_connection(self):
        home_cfg = self._build_config()
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home'].update(home_cfg)
        safe_write_json(CONFIG_PATH, config)
        if self.ambient_sync:
            if home_cfg.get('switchbot_devices'):
                self.ambient_sync.update_switchbot_config(home_cfg['switchbot_devices'])
            self.ambient_sync.update_co2_config(home_cfg)
    def _toggle_co2_automation(self):
        enabled = self._co2_auto_toggle.text() != 'ON'
        self._update_toggle_label(self._co2_auto_toggle, enabled)
        self._save_co2_settings()
    def _update_co2_rule(self, idx: int, key: str, value):
        if idx < len(self._co2_rules):
            self._co2_rules[idx][key] = value
            self._save_co2_settings()
    def _set_co2_fan(self, idx: int, value: str):
        if idx >= len(self._co2_rule_widgets):
            return
        for v, btn in self._co2_rule_widgets[idx]['fan_btns'].items():
            btn.setChecked(v == value)
        self._update_co2_rule(idx, 'fan', value)
    def _set_co2_vent(self, idx: int, value: str):
        if idx >= len(self._co2_rule_widgets):
            return
        vent_map_rev = {'off': 'Off', 'low': 'Low', 'high': 'High'}
        display_val = vent_map_rev.get(value, 'Low')
        for v, btn in self._co2_rule_widgets[idx]['vent_btns'].items():
            btn.setChecked(v == display_val)
        self._update_co2_rule(idx, 'vent', value)
    def _save_co2_settings(self):
        if not hasattr(self, '_co2_auto_toggle'):
            return
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['co2_automation_enabled'] = self._co2_auto_toggle.text() == 'ON'
        config['home']['co2_rules'] = self._co2_rules
        config['home']['co2_dwell_minutes'] = self._co2_dwell_spin.value()
        config['home']['co2_cooldown_minutes'] = self._co2_cooldown_spin.value()
        safe_write_json(CONFIG_PATH, config)
        if self.ambient_sync:
            self.ambient_sync.update_co2_config(config['home'])
    def _apply_and_connect(self):
        home_cfg = self._build_config()
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home'].update(home_cfg)
        safe_write_json(CONFIG_PATH, config)
        if self.ambient_sync:
            self.ambient_sync.stop()
        self.ambient_sync = AmbientSync(home_cfg)
        self.ambient_sync.set_status_callback(self._on_status_update)
        self.ambient_sync.set_sleep_callback(self._on_sleep_state_changed)
        if self.ambient_sync.start():
            if self.sync_toggle.text() == 'ON':
                self.ambient_sync.set_brightness_sync_enabled(True)
            if self.volume_auto_toggle.text() == 'ON':
                self.ambient_sync.set_volume_auto_enabled(True)
            if self._climate_enabled:
                QTimer.singleShot(5000, self._apply_initial_zone)
    def _apply_initial_zone(self):
        if not self._climate_enabled or not self.ambient_sync:
            return
        zone_idx = getattr(self, '_current_zone_idx', 1)
        self._apply_zone_settings(zone_idx)
    def _on_status_update(self, hue: Dict, bravia: Dict, kirigamine: Dict, kirigamine_bedroom: Dict, switchbot_living: Dict = None, switchbot_bedroom: Dict = None):
        self.status_received.emit(hue, bravia, kirigamine, kirigamine_bedroom, switchbot_living or {}, switchbot_bedroom or {})
    def _handle_status_update(self, hue: Dict, bravia: Dict, kirigamine: Dict, kirigamine_bedroom: Dict, switchbot_living: Dict, switchbot_bedroom: Dict):
        pwr = bravia.get('power')
        self._bravia_stats['power'].setText('ON' if pwr else ('OFF' if pwr is False else '--'))
        if pwr:
            self._bravia_stats['power'].setStyleSheet("font-size:11px;font-weight:500;color:#3fb950;background:transparent;")
        elif pwr is False:
            self._bravia_stats['power'].setStyleSheet("font-size:11px;font-weight:500;color:#f85149;background:transparent;")
        else:
            self._bravia_stats['power'].setStyleSheet("font-size:11px;font-weight:500;color:#e6edf3;background:transparent;")
        self._bravia_stats['app'].setText(bravia.get('app', '--') or '--')
        vol = bravia.get('volume')
        self._bravia_stats['volume'].setText(str(vol) if vol is not None else '--')
        self._bravia_stats['saving'].setText(bravia.get('power_saving', '--') or '--')
        self._update_sidebar_badge('bravia', 'ON' if pwr else 'OFF', 'on' if pwr else 'off')
        self._update_hue_rooms(hue)
        self._update_ac_status('living', kirigamine, switchbot_living)
        self._update_ac_status('bedroom', kirigamine_bedroom, switchbot_bedroom)
        self._update_climate_status(kirigamine, switchbot_living)
    def _update_hue_rooms(self, hue: Dict):
        all_rooms = hue.get('all_rooms', {})
        if not all_rooms:
            return
        self._cached_all_rooms = all_rooms
        existing = set(self.hue_room_widgets.keys())
        current = set(all_rooms.keys())
        needs_rebuild = existing != current
        if needs_rebuild:
            self._rebuild_hue_grid()
            return
        total_bri, on_count = 0, 0
        for room, data in all_rooms.items():
            if room not in self.hue_room_widgets:
                continue
            w = self.hue_room_widgets[room]
            bri = int(data.get('bri', 0) * 100) if data.get('on') else 0
            w['bar'].setValue(bri)
            is_selected = room in self._focus_keep_rooms
            self._update_room_card_style(w, data.get('on', False), is_selected)
            if data.get('on'):
                total_bri += bri
                on_count += 1
        self._update_sidebar_badge('hue', str(len(all_rooms)), 'default')
    def _rebuild_hue_grid(self):
        for room, w in list(self.hue_room_widgets.items()):
            w['container'].deleteLater()
        self.hue_room_widgets.clear()
        all_rooms = getattr(self, '_cached_all_rooms', {})
        if not all_rooms:
            return
        cols = 2
        for i, (room, data) in enumerate(sorted(all_rooms.items())):
            container = QFrame()
            container.setCursor(Qt.PointingHandCursor)
            container.setProperty('room_name', room)
            container.mousePressEvent = lambda e, r=room: self._on_room_card_clicked(r)
            is_selected = room in self._focus_keep_rooms
            lay = QHBoxLayout(container)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(8)
            lbl = QLabel(room[:10])
            lbl.setStyleSheet("font-size:10px;color:#e6edf3;background:transparent;")
            lbl.setFixedWidth(70)
            bar = SmoothProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            bri = int(data.get('bri', 0) * 100) if data.get('on') else 0
            bar.setValue(bri)
            lay.addWidget(lbl)
            lay.addWidget(bar, 1)
            self.hue_room_widgets[room] = {'container': container, 'label': lbl, 'bar': bar}
            self._update_room_card_style(self.hue_room_widgets[room], data.get('on', False), is_selected)
            row, col = divmod(i, cols)
            self._hue_grid.addWidget(container, row, col)
        self._update_sidebar_badge('hue', str(len(all_rooms)), 'default')
    def _update_room_card_style(self, w: Dict, is_on: bool, is_selected: bool):
        if is_selected:
            w['container'].setStyleSheet("QFrame{background:#1a3a5c;border-radius:4px;}")
            w['label'].setStyleSheet("font-size:10px;color:#58a6ff;background:transparent;border:none;outline:none;")
        else:
            w['container'].setStyleSheet("QFrame{background:#21262d;border-radius:4px;}QFrame:hover{background:#282e36;}")
            w['label'].setStyleSheet("font-size:10px;color:#9198a1;background:transparent;border:none;outline:none;")
        if is_on:
            w['bar'].setStyleSheet("QProgressBar{border:none;border-radius:2px;background:#30363d;}QProgressBar::chunk{background:#00d4aa;border-radius:2px;}")
        else:
            w['bar'].setStyleSheet("QProgressBar{border:none;border-radius:2px;background:#30363d;}QProgressBar::chunk{background:#484f58;border-radius:2px;}")
    def _on_room_card_clicked(self, room: str):
        if room in self._focus_keep_rooms:
            self._focus_keep_rooms.remove(room)
        else:
            self._focus_keep_rooms.append(room)
        if room in self.hue_room_widgets:
            all_rooms = getattr(self, '_cached_all_rooms', {})
            data = all_rooms.get(room, {})
            is_selected = room in self._focus_keep_rooms
            self._update_room_card_style(self.hue_room_widgets[room], data.get('on', False), is_selected)
        if self.ambient_sync:
            self.ambient_sync.set_focus_lighting(getattr(self, '_focus_enabled', False), self._focus_keep_rooms)
        self._auto_save_connection()
    def _update_ac_status(self, room_key: str, data: Dict, switchbot: Dict = None):
        w = self._ac_widgets.get(room_key)
        if not w:
            return
        pwr = data.get('power')
        mode = data.get('mode', 'AUTO')
        temp = data.get('temp')
        switchbot = switchbot or {}
        room_temp = switchbot.get('temperature') or data.get('room_temp')
        humidity = switchbot.get('humidity')
        fan = data.get('fan')
        vane_ud = data.get('vane_ud')
        vane_ud_pos = data.get('vane_ud_pos')
        vane_lr = data.get('vane_lr')
        vane_lr_pos = data.get('vane_lr_pos')
        mode_jp = self._ac_mode_map.get(mode, '--')
        mode_icon = self._ac_mode_icons.get(mode, '⚙')
        if pwr:
            w['status_lbl'].setText(f"{mode_icon} {mode_jp}")
        elif pwr is False:
            w['status_lbl'].setText("Power OFF")
        else:
            w['status_lbl'].setText("--")
        fan_jp = self._ac_fan_map.get(fan, '--') if fan else '--'
        if vane_ud == 'SWING':
            vane_ud_jp = 'Swing'
        elif vane_ud == 'AUTO':
            vane_ud_jp = 'Auto'
        elif vane_ud_pos:
            vane_ud_jp = self._ac_vane_ud_map.get(str(vane_ud_pos), '--')
        else:
            vane_ud_jp = '--'
        if vane_lr == 'SWING':
            vane_lr_jp = 'Swing'
        elif vane_lr_pos:
            vane_lr_jp = self._ac_vane_lr_map.get(vane_lr_pos, '--')
        else:
            vane_lr_jp = '--'
        w['detail_lbl'].setText(f"Fan: {fan_jp} / UD: {vane_ud_jp} / LR: {vane_lr_jp}")
        w['temp_value'].setText(f"{temp}°" if temp is not None else "--°")
        room_temp_str = f"{room_temp}°C" + (f" / {humidity}%" if humidity else "") if room_temp is not None else "--°C"
        w['room_temp'].setText(room_temp_str)
        w['fan'].setText(fan_jp)
        w['vane_ud'].setText(vane_ud_jp)
        w['vane_lr'].setText(vane_lr_jp)
        if mode == 'HEAT':
            w['ac_icon'].setStyleSheet("font-size:20px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #da3633,stop:1 #f85149);border-radius:8px;")
        elif mode == 'COOL':
            w['ac_icon'].setStyleSheet("font-size:20px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1f6feb,stop:1 #58a6ff);border-radius:8px;")
        elif pwr:
            w['ac_icon'].setStyleSheet("font-size:20px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #238636,stop:1 #2ea043);border-radius:8px;")
        else:
            w['ac_icon'].setStyleSheet("font-size:20px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #484f58,stop:1 #3d444d);border-radius:8px;")
        if pwr is not None:
            w['power_state'] = pwr
            self._update_toggle_label(w['power_toggle'], pwr)
        badge_text = f"{temp}°C" if temp is not None else ('OFF' if pwr is False else '--')
        if pwr and mode:
            badge_style = {'HEAT': 'heat', 'COOL': 'cool', 'DRY': 'dry', 'AUTO': 'auto', 'FAN': 'auto'}.get(mode, 'auto')
        elif pwr is False:
            badge_style = 'off'
        else:
            badge_style = 'default'
        self._update_sidebar_badge(room_key, badge_text, badge_style)
    def _update_climate_status(self, living_data: Dict, switchbot: Dict = None):
        switchbot = switchbot or {}
        room_temp = switchbot.get('temperature') or living_data.get('room_temp')
        humidity = switchbot.get('humidity')
        co2 = switchbot.get('co2')
        if co2 and hasattr(self, '_co2_current_lbl'):
            self._co2_current_lbl.setText(f"Current {co2}ppm")
        if room_temp is not None:
            parts = [f"Current {room_temp}°C"]
            if humidity: parts.append(f"{humidity}%")
            if co2: parts.append(f"{co2}ppm")
            self._climate_current_lbl.setText(" / ".join(parts))
            self._update_climate_pin(room_temp)
            diff = room_temp - self._climate_target_temp
            heat_threshold = self._climate_zones[0].get('target_diff', -3)
            cool_threshold = self._climate_zones[2].get('target_diff', 3)
            if diff <= heat_threshold:
                self._climate_diff_lbl.setText(f"▼ {abs(diff):.0f}°C below")
                self._climate_diff_lbl.setStyleSheet("font-size:11px;color:#58a6ff;background:transparent;")
                self._climate_zone_lbl.setText("Boost Mode")
                self._update_zone_card_active(0)
            elif diff >= cool_threshold:
                self._climate_diff_lbl.setText(f"▲ {diff:.0f}°C above")
                self._climate_diff_lbl.setStyleSheet("font-size:11px;color:#f85149;background:transparent;")
                self._climate_zone_lbl.setText("Reduce Mode")
                self._update_zone_card_active(2)
            else:
                self._climate_diff_lbl.setText("✔ On Target")
                self._climate_diff_lbl.setStyleSheet("font-size:11px;color:#3fb950;background:transparent;")
                self._climate_zone_lbl.setText("Maintain Mode")
                self._update_zone_card_active(1)
            now = time.time()
            if now - getattr(self, '_last_temp_log_time', 0) >= 180:
                self._last_temp_log_time = now
                try:
                    db = get_gui_db()
                    db.log_room_temperature(room_temp, self._climate_target_temp, getattr(self, '_climate_mode', 'COOL'))
                except:
                    pass
            if self._climate_enabled and abs(diff) > 1:
                try:
                    db = get_gui_db()
                    rate, _ = db.get_temperature_trend(30)
                    if rate is not None and abs(rate) > 0.01:
                        if (diff < 0 and rate > 0) or (diff > 0 and rate < 0):
                            eta_min = abs(diff) / abs(rate)
                            if eta_min < 120:
                                self._climate_eta_lbl.setText(f"~{eta_min:.0f} min")
                            else:
                                self._climate_eta_lbl.setText(f"~{eta_min/60:.1f} hr")
                        else:
                            self._climate_eta_lbl.setText("--")
                    else:
                        eta = abs(diff) * 5
                        self._climate_eta_lbl.setText(f"~{eta:.0f} min")
                except:
                    eta = abs(diff) * 5
                    self._climate_eta_lbl.setText(f"~{eta:.0f} min")
            else:
                self._climate_eta_lbl.setText("--")
    def _update_climate_pin(self, room_temp: float):
        if not hasattr(self, '_pin_bar_container'):
            return
        bar_width = self._scale_bar.width() if hasattr(self, '_scale_bar') and self._scale_bar.width() > 0 else 300
        pos = max(0, min(1, (room_temp - 16) / 14))
        pixel_pos = int(pos * (bar_width - 20))
        self._climate_pin_target_x = max(0, pixel_pos)
        current_pos = self._climate_pin.pos()
        if abs(current_pos.x() - self._climate_pin_target_x) > 3:
            self._climate_pin_anim.stop()
            self._climate_pin_anim.setStartValue(current_pos)
            self._climate_pin_anim.setEndValue(QPointF(self._climate_pin_target_x, 0))
            self._climate_pin_anim.start()
    def _wobble_climate_pin(self):
        if not hasattr(self, '_climate_pin') or not hasattr(self, '_climate_pin_target_x'):
            return
        if self._climate_pin_anim.state() == QPropertyAnimation.Running:
            return
        import random
        wobble = random.randint(-2, 2)
        new_x = max(0, self._climate_pin_target_x + wobble)
        current_pos = self._climate_pin.pos()
        self._climate_pin_anim.stop()
        self._climate_pin_anim.setDuration(300)
        self._climate_pin_anim.setStartValue(current_pos)
        self._climate_pin_anim.setEndValue(QPointF(new_x, 0))
        self._climate_pin_anim.start()
        QTimer.singleShot(350, lambda: self._climate_pin_anim.setDuration(600))
    def _set_climate_mode(self, mode: str):
        self._climate_mode = mode
        self._climate_cool_btn.setChecked(mode == 'COOL')
        self._climate_heat_btn.setChecked(mode == 'HEAT')
        temp_color = '#58a6ff' if mode == 'COOL' else '#f85149'
        self._climate_temp_lbl.setStyleSheet(f"font-size:18px;font-weight:600;color:{temp_color};background:transparent;")
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['climate_mode'] = mode
        safe_write_json(CONFIG_PATH, config)
        if self._climate_enabled and self.ambient_sync:
            threading.Thread(target=self.ambient_sync.kirigamine.set_state, kwargs={'mode': mode}, daemon=True).start()
    def _update_zone_card_active(self, active_idx: int):
        if hasattr(self, '_current_zone_idx') and self._current_zone_idx != active_idx:
            self._apply_zone_settings(active_idx)
        self._current_zone_idx = active_idx
        for i, zc in enumerate(self._zone_cards):
            zc_id = zc.get('id', f'zone_{i}')
            if i == active_idx:
                zc['frame'].setStyleSheet(f"#{zc_id}{{background:#1a2332;border-radius:6px;border:1px solid #00d4aa;}}")
            else:
                zc['frame'].setStyleSheet(f"#{zc_id}{{background:#21262d;border-radius:6px;border:1px solid transparent;}}#{zc_id}:hover{{border-color:#30363d;}}")
    def _apply_zone_settings(self, zone_idx: int):
        if not self._climate_enabled or not self.ambient_sync:
            return
        zone = self._climate_zones[zone_idx]
        kwargs = {'fan': zone['fan']}
        if zone_idx != 1 and 'temp_offset' in zone:
            kwargs['temp'] = self._climate_target_temp + zone['temp_offset']
        vane_ud = zone['vane_ud']
        if vane_ud in ('SWING', 'AUTO'):
            kwargs['vane_ud'] = vane_ud
        elif vane_ud.isdigit():
            kwargs['vane_ud'] = 'MANUAL'
            kwargs['vane_ud_pos'] = int(vane_ud)
        kwargs['vane_lr'] = zone['vane_lr']
        print(f"\n[ZONE] Applying zone {zone_idx} ({zone['name']}): {kwargs}")
        threading.Thread(target=self.ambient_sync.kirigamine.set_state_with_retry, kwargs=kwargs, daemon=True).start()
    def _edit_zone_settings(self, zone_idx: int):
        zone = self._climate_zones[zone_idx]
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{zone['name']} Mode Settings")
        dialog.setFixedWidth(280)
        dialog.setStyleSheet("QDialog{background:#1e1e1e;border:1px solid #30363d;border-radius:8px;}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = QLabel(f"{zone['icon']} {zone['name']} Mode")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#e6edf3;background:transparent;")
        layout.addWidget(title)
        range_lbl = QLabel(f"Trigger: Target {zone['range']}")
        range_lbl.setStyleSheet("font-size:10px;color:#7d8590;background:transparent;margin-bottom:4px;")
        layout.addWidget(range_lbl)
        temp_combo = None
        if zone_idx != 1:
            temp_row = QHBoxLayout()
            temp_lbl = QLabel("Temp Offset")
            temp_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
            temp_lbl.setFixedWidth(50)
            temp_row.addWidget(temp_lbl)
            temp_combo = QComboBox()
            if zone_idx == 0:
                temp_combo.addItems(['+1°C', '+2°C', '+3°C', '+4°C', '+5°C'])
                current_offset = zone.get('temp_offset', 2)
                temp_combo.setCurrentText(f'+{current_offset}°C')
            else:
                temp_combo.addItems(['-1°C', '-2°C', '-3°C', '-4°C', '-5°C'])
                current_offset = zone.get('temp_offset', -2)
                temp_combo.setCurrentText(f'{current_offset}°C')
            temp_row.addWidget(temp_combo)
            layout.addLayout(temp_row)
        fan_row = QHBoxLayout()
        fan_lbl = QLabel("Fan")
        fan_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        fan_lbl.setFixedWidth(50)
        fan_row.addWidget(fan_lbl)
        fan_combo = QComboBox()
        fan_combo.addItems(['Auto', 'Quiet', '2', '3', '4', 'Max'])
        current_fan = self._ac_fan_map.get(zone['fan'], 'Auto')
        fan_combo.setCurrentText(current_fan)
        fan_row.addWidget(fan_combo)
        layout.addLayout(fan_row)
        vane_ud_row = QHBoxLayout()
        vane_ud_lbl = QLabel("UD Vane")
        vane_ud_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        vane_ud_lbl.setFixedWidth(50)
        vane_ud_row.addWidget(vane_ud_lbl)
        vane_ud_combo = QComboBox()
        vane_ud_combo.addItems(['Swing', 'Auto', '1', '2', '3', '4', '5'])
        current_vane_ud = self._ac_vane_ud_map.get(zone['vane_ud'], 'Swing')
        vane_ud_combo.setCurrentText(current_vane_ud)
        vane_ud_row.addWidget(vane_ud_combo)
        layout.addLayout(vane_ud_row)
        vane_lr_row = QHBoxLayout()
        vane_lr_lbl = QLabel("LR Vane")
        vane_lr_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        vane_lr_lbl.setFixedWidth(50)
        vane_lr_row.addWidget(vane_lr_lbl)
        vane_lr_combo = QComboBox()
        vane_lr_combo.addItems(['Swing', 'N-Left', 'N-Center', 'N-Right', 'M-Left', 'M-Center', 'M-Right', 'W-Left', 'Wide', 'W-Right'])
        current_vane_lr = self._ac_vane_lr_map.get(zone['vane_lr'], 'Wide')
        vane_lr_combo.setCurrentText(current_vane_lr)
        vane_lr_row.addWidget(vane_lr_combo)
        layout.addLayout(vane_lr_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(80, 28)
        cancel_btn.setStyleSheet("QPushButton{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#9198a1;font-size:11px;}QPushButton:hover{background:#30363d;}")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setFixedSize(80, 28)
        save_btn.setStyleSheet("QPushButton{background:#238636;border:none;border-radius:4px;color:white;font-size:11px;font-weight:600;}QPushButton:hover{background:#2ea043;}")
        def save_zone():
            if temp_combo is not None:
                offset_str = temp_combo.currentText().replace('°C', '')
                self._climate_zones[zone_idx]['temp_offset'] = int(offset_str)
            self._climate_zones[zone_idx]['fan'] = self._ac_fan_rev.get(fan_combo.currentText(), 'AUTO')
            self._climate_zones[zone_idx]['vane_ud'] = self._ac_vane_ud_rev.get(vane_ud_combo.currentText(), 'SWING')
            self._climate_zones[zone_idx]['vane_lr'] = self._ac_vane_lr_rev.get(vane_lr_combo.currentText(), 'M-CENTER')
            self._update_zone_card_display(zone_idx)
            self._save_climate_zones()
            if getattr(self, '_current_zone_idx', -1) == zone_idx:
                self._apply_zone_settings(zone_idx)
            dialog.accept()
        save_btn.clicked.connect(save_zone)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        dialog.exec_()
    def _update_zone_card_display(self, zone_idx: int):
        zone = self._climate_zones[zone_idx]
        zc = self._zone_cards[zone_idx]
        fan_jp = self._ac_fan_map.get(zone['fan'], zone['fan'])
        vane_ud_jp = self._ac_vane_ud_map.get(zone['vane_ud'], zone['vane_ud'])
        vane_lr_jp = self._ac_vane_lr_map.get(zone['vane_lr'], zone['vane_lr'])
        if zone_idx != 1:
            offset = zone.get('temp_offset', 0)
            temp_str = f"Offset: {'+' if offset >= 0 else ''}{offset}°C\n"
        else:
            temp_str = ""
        zc['settings'].setText(f"{temp_str}Fan: {fan_jp}\nUD: {vane_ud_jp}\nLR: {vane_lr_jp}")
    def _on_threshold_slider_changed(self):
        heat_val = self._threshold_heat_slider.value()
        cool_val = self._threshold_cool_slider.value()
        self._threshold_heat_val.setText(f"{heat_val}°C")
        self._threshold_cool_val.setText(f"+{cool_val}°C")
        self._climate_zones[0]['target_diff'] = heat_val
        self._climate_zones[0]['range'] = f"{heat_val}°C below"
        self._climate_zones[1]['target_diff'] = max(abs(heat_val), cool_val)
        self._climate_zones[1]['range'] = f"±{max(abs(heat_val)-1, cool_val-1)}°C"
        self._climate_zones[2]['target_diff'] = cool_val
        self._climate_zones[2]['range'] = f"+{cool_val}°C above"
        for i, zone in enumerate(self._climate_zones):
            if i < len(self._zone_cards):
                zc = self._zone_cards[i]
                if 'range' in zc:
                    zc['range'].setText(f"Target {zone['range']}")
        self._save_climate_zones()
    def _save_climate_zones(self):
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['climate_zones'] = self._climate_zones
        safe_write_json(CONFIG_PATH, config)
    def _refresh_zone_ui(self):
        heat_val = self._climate_zones[0].get('target_diff', -3)
        cool_val = self._climate_zones[2].get('target_diff', 3)
        self._threshold_heat_slider.blockSignals(True)
        self._threshold_cool_slider.blockSignals(True)
        self._threshold_heat_slider.setValue(heat_val)
        self._threshold_cool_slider.setValue(cool_val)
        self._threshold_heat_slider.blockSignals(False)
        self._threshold_cool_slider.blockSignals(False)
        self._threshold_heat_val.setText(f"{heat_val}°C")
        self._threshold_cool_val.setText(f"+{cool_val}°C")
        for i, zone in enumerate(self._climate_zones):
            if i < len(self._zone_cards):
                zc = self._zone_cards[i]
                if 'range' in zc:
                    zc['range'].setText(f"Target {zone['range']}")
                if 'settings' in zc:
                    fan_jp = self._ac_fan_map.get(zone.get('fan', 'AUTO'), zone.get('fan', 'AUTO'))
                    vane_ud_jp = self._ac_vane_ud_map.get(zone.get('vane_ud', 'SWING'), zone.get('vane_ud', 'SWING'))
                    vane_lr_jp = self._ac_vane_lr_map.get(zone.get('vane_lr', 'M-CENTER'), zone.get('vane_lr', 'M-CENTER'))
                    if i != 1:
                        offset = zone.get('temp_offset', 0)
                        temp_str = f"Offset: {'+' if offset >= 0 else ''}{offset}°C\n"
                    else:
                        temp_str = ""
                    zc['settings'].setText(f"{temp_str}Fan: {fan_jp}\nUD: {vane_ud_jp}\nLR: {vane_lr_jp}")
    def _toggle_ac_power_room(self, room_key: str):
        if not self.ambient_sync:
            return
        w = self._ac_widgets.get(room_key)
        if not w:
            return
        current = w.get('power_state')
        new_state = not current if current is not None else True
        ctrl = self.ambient_sync.kirigamine if room_key == 'living' else self.ambient_sync.kirigamine_bedroom
        threading.Thread(target=ctrl.set_state_with_retry, kwargs={'power': new_state}, daemon=True).start()
    def _apply_bedroom_ac_settings_to_gui(self):
        w = self._ac_widgets.get('bedroom')
        if not w or not hasattr(self, '_bedroom_ac_settings'):
            return
        s = self._bedroom_ac_settings
        if s.get('temp'):
            w['temp_value'].setText(f"{s['temp']}°")
        if s.get('fan'):
            fan_jp = self._ac_fan_map.get(s['fan'], '--')
            w['fan'].setText(fan_jp)
        if s.get('vane_ud'):
            vane_ud_jp = self._ac_vane_ud_map.get(s['vane_ud'], '--')
            w['vane_ud'].setText(vane_ud_jp)
        elif s.get('vane_ud_pos'):
            w['vane_ud'].setText(self._ac_vane_ud_map.get(str(s['vane_ud_pos']), '--'))
        if s.get('vane_lr'):
            vane_lr_jp = self._ac_vane_lr_map.get(s['vane_lr'], '--')
            w['vane_lr'].setText(vane_lr_jp)
        fan_jp = self._ac_fan_map.get(s.get('fan', ''), '--')
        vane_ud_jp = self._ac_vane_ud_map.get(s.get('vane_ud', ''), self._ac_vane_ud_map.get(str(s.get('vane_ud_pos', '')), '--'))
        vane_lr_jp = self._ac_vane_lr_map.get(s.get('vane_lr', ''), '--')
        w['detail_lbl'].setText(f"Fan: {fan_jp} / UD: {vane_ud_jp} / LR: {vane_lr_jp}")
        if s.get('temp'):
            self._update_sidebar_badge('bedroom', f"{s['temp']}°C", 'default')
    def _save_bedroom_ac_settings(self, **kwargs):
        global config
        if not hasattr(self, '_bedroom_ac_settings'):
            self._bedroom_ac_settings = config.get('home', {}).get('bedroom_ac', {})
        self._bedroom_ac_settings.update(kwargs)
        if 'home' not in config:config['home'] = {}
        config['home']['bedroom_ac'] = self._bedroom_ac_settings
        safe_write_json(CONFIG_PATH, config)
    def _on_ac_temp_menu(self, room_key: str, value: str):
        if not self.ambient_sync or value == '--':
            return
        temp = int(value.replace('°C', ''))
        ctrl = self.ambient_sync.kirigamine if room_key == 'living' else self.ambient_sync.kirigamine_bedroom
        threading.Thread(target=ctrl.set_state_with_retry, kwargs={'temp': temp}, daemon=True).start()
        if room_key == 'bedroom':self._save_bedroom_ac_settings(temp=temp)
    def _on_ac_mode_jp(self, room_key: str, value_jp: str):
        if not self.ambient_sync:
            return
        value = self._ac_mode_rev.get(value_jp, value_jp)
        ctrl = self.ambient_sync.kirigamine if room_key == 'living' else self.ambient_sync.kirigamine_bedroom
        threading.Thread(target=ctrl.set_state_with_retry, kwargs={'mode': value}, daemon=True).start()
        if room_key == 'bedroom':self._save_bedroom_ac_settings(mode=value)
    def _on_ac_fan_jp(self, room_key: str, value_jp: str):
        if not self.ambient_sync:
            return
        value = self._ac_fan_rev.get(value_jp, value_jp)
        ctrl = self.ambient_sync.kirigamine if room_key == 'living' else self.ambient_sync.kirigamine_bedroom
        threading.Thread(target=ctrl.set_state_with_retry, kwargs={'fan': value}, daemon=True).start()
        if room_key == 'bedroom':self._save_bedroom_ac_settings(fan=value)
    def _on_ac_vane_ud_jp(self, room_key: str, value_jp: str):
        if not self.ambient_sync:
            return
        value = self._ac_vane_ud_rev.get(value_jp, value_jp)
        ctrl = self.ambient_sync.kirigamine if room_key == 'living' else self.ambient_sync.kirigamine_bedroom
        if value in ('SWING', 'AUTO'):
            threading.Thread(target=ctrl.set_state_with_retry, kwargs={'vane_ud': value}, daemon=True).start()
            if room_key == 'bedroom':self._save_bedroom_ac_settings(vane_ud=value)
        elif value.isdigit():
            pos = int(value)
            threading.Thread(target=ctrl.set_state_with_retry, kwargs={'vane_ud': 'MANUAL', 'vane_ud_pos': pos}, daemon=True).start()
            if room_key == 'bedroom':self._save_bedroom_ac_settings(vane_ud='MANUAL', vane_ud_pos=pos)
    def _on_ac_vane_lr_jp(self, room_key: str, value_jp: str):
        if not self.ambient_sync:
            return
        value = self._ac_vane_lr_rev.get(value_jp, value_jp)
        ctrl = self.ambient_sync.kirigamine if room_key == 'living' else self.ambient_sync.kirigamine_bedroom
        if value == 'SWING':
            threading.Thread(target=ctrl.set_state_with_retry, kwargs={'vane_lr': 'SWING'}, daemon=True).start()
        else:
            threading.Thread(target=ctrl.set_state_with_retry, kwargs={'vane_lr': value}, daemon=True).start()
        if room_key == 'bedroom':self._save_bedroom_ac_settings(vane_lr=value)
    def _on_sleep_state_changed(self, is_sleeping: bool):
        if is_sleeping and self.ambient_sync:
            if self.ambient_sync.kirigamine:
                threading.Thread(target=self.ambient_sync.kirigamine.set_state_with_retry, kwargs={'power': False}, daemon=True).start()
    def _update_status(self):
        if not self.ambient_sync:
            return
        self.ambient_sync._last_input_time = input_listener.last_input_time
    def _add_volume_profile(self):
        dialog = QInputDialog(self)
        dialog.setWindowTitle("App Volume Settings")
        dialog.setLabelText("App name")
        dialog.setMinimumWidth(350)
        if dialog.exec_() == QDialog.Accepted:
            text = dialog.textValue().strip()
            if text and text not in self.vol_profile_widgets:
                self._create_vol_row(text, False, 20)
                self._save_vol_profiles()
    def _delete_volume_profile(self, app: str):
        if app in self.vol_profile_widgets:
            w = self.vol_profile_widgets.pop(app)
            w['container'].deleteLater()
            self._save_vol_profiles()
    def _save_vol_profiles(self):
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['volume_profiles'] = {app: {'enabled': w['toggle'].text() == 'ON', 'volume': w['spin'].value()} for app, w in self.vol_profile_widgets.items()}
        safe_write_json(CONFIG_PATH, config)
        if self.ambient_sync:
            self.ambient_sync.set_volume_profiles(config['home']['volume_profiles'])
    def _toggle_desktop_organizer(self):
        enabled = self._desktop_org_toggle.text() == "OFF"
        self._update_toggle_label(self._desktop_org_toggle, enabled)
        if self.desktop_organizer:
            self.desktop_organizer.set_enabled(enabled)
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['desktop_organizer_enabled'] = enabled
        safe_write_json(CONFIG_PATH, config)
    def _on_desktop_path_changed(self, text: str):
        global config
        if 'home' not in config: config['home'] = {}
        config['home']['desktop_path'] = text
        safe_write_json(CONFIG_PATH, config)
        if self.desktop_organizer and text: self.desktop_organizer.set_desktop_path(text)
    def _fetch_switchbot_devices(self):
        from core.home import SwitchbotController
        token = self.switchbot_token_input.text().strip()
        if not token:
            self._switchbot_devices_list.setText("(Enter token)")
            self._switchbot_devices_list.setStyleSheet("font-size:9px;color:#f85149;background:#161b22;border-radius:4px;padding:8px;")
            return
        try:
            devices, ir_devices = SwitchbotController.fetch_devices(token)
        except Exception as e:
            self._switchbot_devices_list.setText(f"(Error: {e})")
            self._switchbot_devices_list.setStyleSheet("font-size:9px;color:#f85149;background:#161b22;border-radius:4px;padding:8px;")
            return
        if not devices and not ir_devices:
            self._switchbot_devices_list.setText("(No devices found)")
            return
        self._switchbot_devices_cache = devices
        self._switchbot_ir_cache = ir_devices
        sensors = [d for d in devices if d.get('deviceType') in SwitchbotController.SENSOR_TYPES]
        bots = [d for d in devices if d.get('deviceType') in SwitchbotController.BOT_TYPES]
        all_controllable = bots + ir_devices
        for key, combo in self._switchbot_combos.items():
            current_id = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("-- Not Set --", "")
            src = sensors if key in ['living', 'bedroom', 'co2'] else all_controllable
            for dev in src:
                name = dev.get('deviceName', '?')
                dtype = dev.get('deviceType', dev.get('remoteType', ''))
                dev_id = dev.get('deviceId', '')
                display = f"{name}  [{dtype}]"
                combo.addItem(display, dev_id)
            if current_id:
                for i in range(combo.count()):
                    if combo.itemData(i) == current_id:
                        combo.setCurrentIndex(i)
                        break
            combo.blockSignals(False)
        lines = []
        if sensors:
            lines.append(f"📊 Sensors: {len(sensors)}")
            for d in sensors:
                lines.append(f"  • {d.get('deviceName')} [{d.get('deviceType')}]")
        if bots:
            lines.append(f"🎛️ Bot/Plug: {len(bots)}")
            for d in bots:
                lines.append(f"  • {d.get('deviceName')} [{d.get('deviceType')}]")
        if ir_devices:
            lines.append(f"📡 IR Remotes: {len(ir_devices)}")
            for d in ir_devices:
                lines.append(f"  • {d.get('deviceName')} [{d.get('remoteType')}]")
        self._switchbot_devices_list.setText("\n".join(lines))
        self._switchbot_devices_list.setStyleSheet("font-size:9px;color:#9198a1;background:#161b22;border-radius:4px;padding:8px;")
        self._auto_save_connection()
    def _get_switchbot_devices(self) -> Dict[str, Dict]:
        result = {}
        if not hasattr(self, '_switchbot_combos'):
            return result
        for key, combo in self._switchbot_combos.items():
            dev_id = combo.currentData()
            if dev_id:
                idx = combo.currentIndex()
                text = combo.itemText(idx)
                result[key] = {'id': dev_id, 'name': text}
        return result
    def _toggle_ac_preset(self, idx: int):
        if idx >= len(self._ac_presets):
            return
        preset = self._ac_presets[idx]
        preset['enabled'] = not preset.get('enabled', False)
        w = self._preset_widgets[idx]
        self._update_toggle_btn_style(w['toggle'], preset['enabled'])
        self._save_ac_presets()
    def _edit_ac_preset(self, idx: int):
        if idx >= len(self._ac_presets):
            return
        preset = self._ac_presets[idx]
        settings = preset.get('settings', {})
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{preset.get('icon', '⚙️')} {preset.get('name', 'Preset')} Settings")
        dialog.setFixedWidth(280)
        dialog.setStyleSheet("QDialog{background:#1e1e1e;border:1px solid #30363d;border-radius:8px;}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = QLabel(f"{preset.get('icon', '⚙️')} {preset.get('name', 'Preset')}")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#e6edf3;background:transparent;")
        layout.addWidget(title)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Name")
        name_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        name_lbl.setFixedWidth(50)
        name_row.addWidget(name_lbl)
        name_edit = QLineEdit(preset.get('name', ''))
        name_edit.setStyleSheet("QLineEdit{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:6px;color:#e6edf3;font-size:11px;}")
        name_row.addWidget(name_edit)
        layout.addLayout(name_row)
        icon_row = QHBoxLayout()
        icon_lbl = QLabel("Icon")
        icon_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        icon_lbl.setFixedWidth(50)
        icon_row.addWidget(icon_lbl)
        icon_edit = QLineEdit(preset.get('icon', '⚙️'))
        icon_edit.setStyleSheet("QLineEdit{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:6px;color:#e6edf3;font-size:11px;}")
        icon_row.addWidget(icon_edit)
        layout.addLayout(icon_row)
        temp_row = QHBoxLayout()
        temp_lbl = QLabel("Temp")
        temp_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        temp_lbl.setFixedWidth(50)
        temp_row.addWidget(temp_lbl)
        temp_combo = QComboBox()
        temp_combo.addItem("--")
        for t in range(16, 32):
            temp_combo.addItem(f"{t}°C")
        if settings.get('temp'):
            temp_combo.setCurrentText(f"{settings['temp']}°C")
        temp_row.addWidget(temp_combo)
        layout.addLayout(temp_row)
        fan_row = QHBoxLayout()
        fan_lbl = QLabel("Fan")
        fan_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        fan_lbl.setFixedWidth(50)
        fan_row.addWidget(fan_lbl)
        fan_combo = QComboBox()
        fan_combo.addItems(['--', 'Auto', 'Quiet', '2', '3', '4', 'Max'])
        if settings.get('fan'):
            fan_combo.setCurrentText(self._ac_fan_map.get(settings['fan'], '--'))
        fan_row.addWidget(fan_combo)
        layout.addLayout(fan_row)
        vane_ud_row = QHBoxLayout()
        vane_ud_lbl = QLabel("UD Vane")
        vane_ud_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        vane_ud_lbl.setFixedWidth(50)
        vane_ud_row.addWidget(vane_ud_lbl)
        vane_ud_combo = QComboBox()
        vane_ud_combo.addItems(['--', 'Swing', 'Auto', '1', '2', '3', '4', '5'])
        if settings.get('vane_ud'):
            vane_ud_combo.setCurrentText(self._ac_vane_ud_map.get(settings['vane_ud'], '--'))
        vane_ud_row.addWidget(vane_ud_combo)
        layout.addLayout(vane_ud_row)
        vane_lr_row = QHBoxLayout()
        vane_lr_lbl = QLabel("LR Vane")
        vane_lr_lbl.setStyleSheet("font-size:11px;color:#9198a1;background:transparent;")
        vane_lr_lbl.setFixedWidth(50)
        vane_lr_row.addWidget(vane_lr_lbl)
        vane_lr_combo = QComboBox()
        vane_lr_combo.addItems(['--', 'Swing', 'N-Left', 'N-Center', 'N-Right', 'M-Left', 'M-Center', 'M-Right', 'W-Left', 'Wide', 'W-Right'])
        if settings.get('vane_lr'):
            vane_lr_combo.setCurrentText(self._ac_vane_lr_map.get(settings['vane_lr'], '--'))
        vane_lr_row.addWidget(vane_lr_combo)
        layout.addLayout(vane_lr_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(80, 28)
        cancel_btn.setStyleSheet("QPushButton{background:#21262d;border:1px solid #30363d;border-radius:4px;color:#9198a1;font-size:11px;}QPushButton:hover{background:#30363d;}")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setFixedSize(80, 28)
        save_btn.setStyleSheet("QPushButton{background:#238636;border:none;border-radius:4px;color:white;font-size:11px;font-weight:600;}QPushButton:hover{background:#2ea043;}")
        def save_preset():
            preset['name'] = name_edit.text()
            preset['icon'] = icon_edit.text()
            settings['temp'] = int(temp_combo.currentText().replace('°C', '')) if temp_combo.currentText() != '--' else None
            settings['fan'] = self._ac_fan_rev.get(fan_combo.currentText()) if fan_combo.currentText() != '--' else None
            settings['vane_ud'] = self._ac_vane_ud_rev.get(vane_ud_combo.currentText()) if vane_ud_combo.currentText() != '--' else None
            settings['vane_lr'] = self._ac_vane_lr_rev.get(vane_lr_combo.currentText()) if vane_lr_combo.currentText() != '--' else None
            preset['settings'] = settings
            w = self._preset_widgets[idx]
            w['icon'].setText(preset['icon'])
            w['name'].setText(preset['name'])
            fan_jp = self._ac_fan_map.get(settings.get('fan', ''), '')
            vane_ud_jp = self._ac_vane_ud_map.get(settings.get('vane_ud', ''), '')
            vane_lr_jp = self._ac_vane_lr_map.get(settings.get('vane_lr', ''), '')
            desc_parts = [f"{settings['temp']}°C" if settings.get('temp') else '', f"Fan:{fan_jp}" if fan_jp else '', f"UD:{vane_ud_jp}" if vane_ud_jp else '', f"LR:{vane_lr_jp}" if vane_lr_jp else '']
            w['desc'].setText(' / '.join([p for p in desc_parts if p]) or '--')
            self._save_ac_presets()
            dialog.accept()
        save_btn.clicked.connect(save_preset)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        dialog.exec_()
    def _save_ac_presets(self):
        global config
        if 'home' not in config:
            config['home'] = {}
        config['home']['ac_presets'] = self._ac_presets
        safe_write_json(CONFIG_PATH, config)
    def apply_ac_preset_by_trigger(self, trigger: str):
        for preset in self._ac_presets:
            if preset.get('trigger') == trigger and preset.get('enabled', False):
                self._active_preset_trigger = trigger
                settings = preset.get('settings', {})
                if self.ambient_sync and self.ambient_sync.kirigamine:
                    kwargs = {}
                    if settings.get('temp'):
                        kwargs['temp'] = settings['temp']
                    if settings.get('fan'):
                        kwargs['fan'] = settings['fan']
                    if settings.get('vane_ud'):
                        if settings['vane_ud'] in ('SWING', 'AUTO'):
                            kwargs['vane_ud'] = settings['vane_ud']
                        else:
                            kwargs['vane_ud'] = 'MANUAL'
                            kwargs['vane_ud_pos'] = int(settings['vane_ud'])
                    if settings.get('vane_lr'):
                        kwargs['vane_lr'] = settings['vane_lr']
                    if kwargs:
                        threading.Thread(target=self.ambient_sync.kirigamine.set_state_with_retry, kwargs=kwargs, daemon=True).start()
                break
    def deactivate_ac_preset_by_trigger(self, trigger: str):
        if getattr(self, '_active_preset_trigger', None) != trigger:
            return
        self._active_preset_trigger = None
        if not self._climate_enabled:
            return
        for zone in self._climate_zones:
            if zone.get('selected', False):
                settings = zone.get('settings', {})
                if self.ambient_sync and self.ambient_sync.kirigamine:
                    kwargs = {}
                    if settings.get('fan'):
                        kwargs['fan'] = settings['fan']
                    if settings.get('vane_ud'):
                        if settings['vane_ud'] in ('SWING', 'AUTO'):
                            kwargs['vane_ud'] = settings['vane_ud']
                        else:
                            kwargs['vane_ud'] = 'MANUAL'
                            kwargs['vane_ud_pos'] = int(settings['vane_ud'])
                    if settings.get('vane_lr'):
                        kwargs['vane_lr'] = settings['vane_lr']
                    if kwargs:
                        threading.Thread(target=self.ambient_sync.kirigamine.set_state_with_retry, kwargs=kwargs, daemon=True).start()
                break
class LogTab(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.load_log)
        self.timer.start(2000)

    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)

        title = QLabel("📋 Logs")
        title.setFont(Fonts.number(16, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        layout.addWidget(title)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {Colors.BG_CARD};
                color: #00FF00;
                font-family: {Fonts.FAMILY_NUMBER};
                font-size: 9pt;
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
            }}
        """)
        layout.addWidget(self.log_text)

        self.setLayout(layout)
        self.load_log()

    def load_log(self):
        try:
            log_path = ROOT_PATH / "logs" / "daemon.log"
            if log_path.exists():
                content = log_path.read_text(encoding='utf-8', errors='ignore')
                lines = content.split('\n')[-100:]
                self.log_text.setPlainText('\n'.join(lines))
                self.log_text.verticalScrollBar().setValue(
                    self.log_text.verticalScrollBar().maximum()
                )
        except:
            pass

class LifeOSGUI(QMainWindow):

    def __init__(self):
        super().__init__()
        self.daemon_process = None

        self._init_database()

        self.initUI()
        self._auto_start_daemon()

        atexit.register(self._cleanup_daemon)

    def _init_database(self):
        try:
            from core.database import LifeOSDatabase
            db_path = ROOT_PATH / "Data" / "life_os.db"
            self.database = LifeOSDatabase(str(db_path))
        except Exception as e:
            print(f"v3.7 Database init error: {e}")
            self.database = None

    def initUI(self):
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.setWindowTitle('LifeOS v6.2.0')
        self.setMinimumSize(950, 750)

        stylesheet = load_stylesheet()
        if stylesheet:
            self.setStyleSheet(stylesheet)
        else:
            self.setStyleSheet(f"background-color: {Colors.BG_DARK};")

        self.setWindowFlags(Qt.FramelessWindowHint)

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet(f"background-color: {Colors.BG_PANEL};")

        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)

        btn_configs = [
            ('#FF5F57', 'closeButton', self.close),
            ('#FEBC2E', 'minimizeButton', self.showMinimized),
            ('#28C840', 'maximizeButton', self._toggle_max)
        ]
        for color, obj_name, action in btn_configs:
            btn = QPushButton()
            btn.setObjectName(obj_name)
            btn.setFixedSize(12, 12)
            btn.setStyleSheet(f"QPushButton {{ background-color: {color}; border: none; border-radius: 6px; }}")
            btn.clicked.connect(action)
            title_layout.addWidget(btn)

        title_layout.addSpacing(15)

        title = QLabel("LifeOS v6.2.0")
        title.setObjectName("windowTitle")
        title.setFont(Fonts.label(11, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        title_layout.addWidget(title)

        title_layout.addStretch()

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {Colors.CYAN};")
        title_layout.addWidget(self.status_dot)

        main_layout.addWidget(title_bar)

        self.tabs = QTabWidget()
        self._lazy_tabs = {}
        self.dashboard_tab = DashboardTab()
        self.tabs.addTab(self.dashboard_tab, "🔄 Dashboard")
        self._lazy_tabs[1] = ('analysis', QWidget())
        self.tabs.addTab(self._lazy_tabs[1][1], "📊 Analytics")
        self.sequence_tab = SequenceTab(database=self.database)
        self.tabs.addTab(self.sequence_tab, "🌿 Shisha")
        self.home_tab = HomeTab()
        self.tabs.addTab(self.home_tab, "🏠 Home")
        neuro_sound = getattr(self.dashboard_tab, 'neuro_sound', None)
        self.settings_tab = SettingsTab(neuro_sound=neuro_sound)
        self.tabs.addTab(self.settings_tab, "⚙️ Settings")
        self._lazy_tabs[5] = ('logs', QWidget())
        self.tabs.addTab(self._lazy_tabs[5][1], "📋 Logs")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tabs)
        central.setLayout(main_layout)

        self._drag_pos = None
        title_bar.mousePressEvent = self._title_press
        title_bar.mouseMoveEvent = self._title_move

        self._center()
    def _on_tab_changed(self, index: int):
        if index in self._lazy_tabs:
            tab_type, placeholder = self._lazy_tabs[index]
            self.tabs.blockSignals(True)
            if tab_type == 'analysis':
                real_tab = AnalysisTab()
                self.tabs.removeTab(index)
                self.tabs.insertTab(index, real_tab, "📊 Analytics")
                self.tabs.setCurrentIndex(index)
            elif tab_type == 'logs':
                real_tab = LogTab()
                self.tabs.removeTab(index)
                self.tabs.insertTab(index, real_tab, "📋 Logs")
                self.tabs.setCurrentIndex(index)
            del self._lazy_tabs[index]
            self.tabs.blockSignals(False)
        for i, w in [(1, self.tabs.widget(1))]:
            if hasattr(w, 'graph') and hasattr(w.graph, 'canvas'):
                canvas = w.graph.canvas
                if i == index:
                    canvas._buffer_valid = False
                    canvas.update()
                elif canvas._buffer is not None:
                    canvas._buffer = None
                    canvas._buffer_valid = False

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _title_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _title_move(self, event):
        if self._drag_pos and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)

    def _center(self):
        frame = self.frameGeometry()
        center = QDesktopWidget().availableGeometry().center()
        frame.moveCenter(center)
        self.move(frame.topLeft())
        self.setAttribute(Qt.WA_DontShowOnScreen, False)

    def _auto_start_daemon(self):
        daemon = ROOT_PATH / "core" / "daemon.py"

        if not daemon.exists():
            self.status_dot.setStyleSheet(f"color: {Colors.RED};")
            return

        if PID_PATH.exists():
            try:
                existing_pid = int(PID_PATH.read_text().strip())
                if sys.platform == 'win32':
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x1000, False, existing_pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        print(f"Daemon already running (PID: {existing_pid})")
                        self.status_dot.setStyleSheet(f"color: {Colors.CYAN};")
                        return
                else:
                    os.kill(existing_pid, 0)
                    print(f"Daemon already running (PID: {existing_pid})")
                    self.status_dot.setStyleSheet(f"color: {Colors.CYAN};")
                    return
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                PID_PATH.unlink(missing_ok=True)
        try:
            gui_push_command('SET_GUI_RUNNING', True)
            self.daemon_process = subprocess.Popen(
                [sys.executable, str(daemon)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(ROOT_PATH)
            )
            self.status_dot.setStyleSheet(f"color: {Colors.CYAN};")
        except Exception as e:
            self.status_dot.setStyleSheet(f"color: {Colors.RED};")

    def _cleanup_daemon(self):
        if self.daemon_process and self.daemon_process.poll() is None:
            print("Terminating daemon...")
            self.daemon_process.terminate()
            try:
                self.daemon_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.daemon_process.kill()

    def closeEvent(self, event):
        try:
            if hasattr(self, 'sequence_tab') and self.sequence_tab is not None:
                self.sequence_tab.force_stop_for_shutdown()
        except Exception as e:
            print(f"v3.7 Shisha Shutdown Error: {e}")

        try:
            if hasattr(self, 'dashboard_tab') and self.dashboard_tab is not None:
                if hasattr(self.dashboard_tab, 'neuro_sound') and self.dashboard_tab.neuro_sound:
                    self.dashboard_tab.neuro_sound.cleanup()
                    print("v6.0.1: NeuroSoundEngine cleanup complete")
        except Exception as e:
            print(f"v6.0.1 Audio Cleanup Error: {e}")
        gui_push_command('SET_GUI_RUNNING', False)
        self._cleanup_daemon()
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create('Fusion'))

    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    window = LifeOSGUI()
    window.show()

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
