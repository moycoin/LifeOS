#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LifeOS GUI - Version from core.types.__version__
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
from PyQt5.QtCore import QTimer, Qt, QRectF, QPointF, pyqtSignal, QObject, QFile, QTextStream
from PyQt5.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDesktopWidget,
    QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QProgressBar, QPushButton, QScrollArea, QScrollBar, QSizePolicy,
    QSlider, QSpinBox, QStyleFactory, QTabWidget, QTextEdit, QVBoxLayout, QWidget
)
try:
    from pynput import keyboard, mouse
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
try:
    from core.home import AmbientSync, PHUE_AVAILABLE, REQUESTS_AVAILABLE as HOME_REQUESTS_AVAILABLE
    HOME_AVAILABLE = True
except ImportError:
    HOME_AVAILABLE = False
    PHUE_AVAILABLE = False
    HOME_REQUESTS_AVAILABLE = False

# types.pyから共通定義をインポート
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
            return ("INITIALIZING", "システム起動中。安定稼働を目指せ。")
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
    """GUI用コマンドプッシュ（DBベース）"""
    db = get_gui_db()
    if db:
        db.push_command(cmd, value)
def get_state_from_db() -> Dict:
    """DB完全依存: daemon_state + oura_cache + brain_metricsを結合"""
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


# ==================== Input Listener ====================
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
        db_path = ROOT_PATH / "Data" / "life_os.db"
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
        self._timer.start(30)
    def setValue(self, value: int):
        self._target = max(self.minimum(), min(self.maximum(), value))
    def _animate(self):
        if abs(self._current - self._target) < 0.5:
            self._current = float(self._target)
        else:
            self._current += (self._target - self._current) * 0.15
        super().setValue(int(round(self._current)))
    def setValueImmediate(self, value: int):
        self._target = self._current = float(max(self.minimum(), min(self.maximum(), value)))
        super().setValue(value)

# ==================== Trinity Circle Widget (v2.6 Style) ====================
class TrinityCircleWidget(QWidget):
    """
    v6.0.1 Trinity Circle
    - Outer: Readiness (Cyan)
    - Middle: FP (Orange)  
    - Inner: Cognitive Load (Red)
    
    v6.0.1: ラベル位置を cy + size * 0.08 に設定
    """
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
        self.anim_timer.start(30)
    
    def set_data(self, readiness: int, fp: float, load: float):
        self.target_readiness = readiness
        self.target_fp = fp
        self.target_load = load
    
    def _animate(self):
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
        
        # Ring parameters
        outer_radius = size * 0.42
        middle_radius = size * 0.34
        inner_radius = size * 0.26
        ring_width = size * 0.045
        
        # ===== Outer Ring: Readiness =====
        self._draw_ring(painter, cx, cy, outer_radius, ring_width,
                       self.readiness / 100, Colors.RING_READINESS)
        
        # ===== Middle Ring: FP =====
        self._draw_ring(painter, cx, cy, middle_radius, ring_width,
                       min(1.0, self.fp / 100), Colors.RING_FP)
        
        # ===== Inner Ring: Cognitive Load =====
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
        # Background track
        pen = QPen(QColor(Colors.BG_ELEVATED), int(width))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawArc(rect, 0, 360 * 16)
        
        # Progress arc
        if progress > 0:
            pen = QPen(QColor(color), int(width))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            
            angle = int(progress * 360 * 16)
            painter.drawArc(rect, 90 * 16, -angle)


# ==================== Resource Timeline Widget (v2.6 Style - Legacy) ====================
class ResourceTimelineWidget(QWidget):
    """
    v2.6 Classic Resource Timeline (横棒グラフ) - Legacy
    Now, +1h, +2h, +4h, +8h の予測
    """
    def __init__(self):
        super().__init__()
        self.bars = []
        self.setMinimumSize(300, 180)
    
    def set_data(self, bars: List[Dict]):
        """bars: [{'label': 'Now', 'fp': 17, 'color': '#00D4AA'}, ...]"""
        self.bars = bars
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # Background panel
        painter.fillRect(0, 0, width, height, QColor(Colors.BG_CARD))
        
        # Border
        painter.setPen(QPen(QColor(Colors.BORDER), 1))
        painter.drawRect(0, 0, width - 1, height - 1)
        
        # Title
        painter.setPen(QColor(Colors.ORANGE))
        painter.setFont(Fonts.label(11, True))
        painter.drawText(15, 22, "🔥 Resource Timeline")
        
        if not self.bars:
            return
        
        # Bars
        margin_left = 55
        margin_right = 60
        bar_area_width = width - margin_left - margin_right
        bar_height = 18
        bar_spacing = 8
        start_y = 45
        
        for i, bar in enumerate(self.bars[:5]):
            y = start_y + i * (bar_height + bar_spacing)
            
            # Label
            painter.setPen(QColor(Colors.TEXT_SECONDARY))
            painter.setFont(Fonts.label(9))
            painter.drawText(10, y + 14, bar['label'])
            
            # Background bar
            painter.fillRect(int(margin_left), int(y), int(bar_area_width), int(bar_height),
                           QColor(Colors.BG_ELEVATED))
            
            # Progress bar
            fp = bar.get('fp', 0)
            progress = min(1.0, max(0, fp / 100))
            bar_width = int(bar_area_width * progress)
            
            if bar_width > 0:
                color = bar.get('color', Colors.CYAN)
                painter.fillRect(int(margin_left), int(y), bar_width, int(bar_height),
                               QColor(color))
            
            # Value
            painter.setPen(QColor(Colors.TEXT_PRIMARY))
            painter.setFont(Fonts.number(10))
            painter.drawText(int(margin_left + bar_area_width + 8), int(y + 14), f"{int(fp)} FP")


# ==================== Resource Curve Widget (v6.0.2 SSOT) ====================
class ResourceCurveWidget(QWidget):
    """v6.0.2 Resource Curve Widget - SSOT対応（辞書形式予測データ）"""
    COLOR_CONTINUE = '#FF6B00'
    COLOR_REST = '#00D4AA'
    def __init__(self):
        super().__init__()
        self.continue_points = []
        self.rest_points = []
        self.setMinimumSize(300, 140)
    def set_data(self, prediction: Dict):
        """v6.0.2: daemon_state.jsonからの辞書形式予測データに対応"""
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
        
        # Background panel
        painter.fillRect(0, 0, width, height, QColor(Colors.BG_CARD))
        
        # Border
        painter.setPen(QPen(QColor(Colors.BORDER), 1))
        painter.drawRect(0, 0, width - 1, height - 1)
        
        # Title
        painter.setPen(QColor(Colors.ORANGE))
        painter.setFont(Fonts.label(11, True))
        painter.drawText(15, 22, "📈 Resource Trajectory")
        
        # Graph area
        margin_left = 45
        margin_right = 15
        margin_top = 38
        margin_bottom = 30
        
        graph_width = width - margin_left - margin_right
        graph_height = height - margin_top - margin_bottom
        
        if graph_width <= 0 or graph_height <= 0:
            return
        
        # Y-axis grid and labels (0, 25, 50, 75, 100)
        painter.setPen(QPen(QColor(Colors.BG_ELEVATED), 1))
        painter.setFont(Fonts.label(8))
        
        for fp_val in [0, 25, 50, 75, 100]:
            y = margin_top + graph_height * (1 - fp_val / 100)
            
            # Grid line
            painter.setPen(QPen(QColor(Colors.BG_ELEVATED), 1, Qt.DotLine))
            painter.drawLine(int(margin_left), int(y), int(width - margin_right), int(y))
            
            # Label
            painter.setPen(QColor(Colors.TEXT_DIM))
            painter.drawText(5, int(y + 4), f"{fp_val}")
        
        # X-axis labels (+0h, +1h, +2h, +4h)
        x_labels = [(0, '+0h'), (60, '+1h'), (120, '+2h'), (240, '+4h')]
        max_minutes = 240
        
        painter.setFont(Fonts.label(8))
        painter.setPen(QColor(Colors.TEXT_DIM))
        
        for minutes, label in x_labels:
            x = margin_left + (minutes / max_minutes) * graph_width
            painter.drawText(int(x - 10), int(height - 8), label)
        
        # Draw curves
        if not self.continue_points and not self.rest_points:
            # No data
            painter.setPen(QColor(Colors.TEXT_DIM))
            painter.setFont(Fonts.label(10))
            painter.drawText(QRectF(margin_left, margin_top, graph_width, graph_height),
                           Qt.AlignCenter, "No prediction data")
            return
        
        # Get base time
        base_time = self.continue_points[0].timestamp if self.continue_points else now_jst()
        
        # Draw Rest curve (dashed, behind)
        if self.rest_points:
            self._draw_curve(painter, self.rest_points, base_time, max_minutes,
                           margin_left, margin_top, graph_width, graph_height,
                           self.COLOR_REST, dashed=True)
        
        # Draw Continue curve (solid, front)
        if self.continue_points:
            self._draw_curve(painter, self.continue_points, base_time, max_minutes,
                           margin_left, margin_top, graph_width, graph_height,
                           self.COLOR_CONTINUE, dashed=False)
        
        # Legend
        legend_y = margin_top + 5
        
        # Continue legend
        painter.setPen(QPen(QColor(self.COLOR_CONTINUE), 2))
        painter.drawLine(int(margin_left + graph_width - 100), int(legend_y),
                        int(margin_left + graph_width - 80), int(legend_y))
        painter.setPen(QColor(Colors.TEXT_SECONDARY))
        painter.setFont(Fonts.label(8))
        painter.drawText(int(margin_left + graph_width - 75), int(legend_y + 4), "Continue")
        
        # Rest legend
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
        """
        v6.0.1: 曲線を描画（FP=10区間は赤色で強調）
        """
        if not points or len(points) < 2:
            return
        
        # v6.0.1: 通常区間と枯渇区間を分けて描画
        DEPLETED_THRESHOLD = 12  # FP < 12 を枯渇とみなす
        DEPLETED_COLOR = Colors.RED
        
        # セグメントごとに描画（色を変えるため）
        current_path = QPainterPath()
        current_depleted = None
        first = True
        prev_x, prev_y = 0, 0
        
        for point in points:
            # X座標: 時間差
            dt_minutes = (point.timestamp - base_time).total_seconds() / 60
            if dt_minutes > max_minutes:
                break
            
            x = margin_left + (dt_minutes / max_minutes) * graph_width
            
            # Y座標: FP値
            fp = max(0, min(100, point.fp))
            y = margin_top + graph_height * (1 - fp / 100)
            
            # 枯渇状態かどうか
            is_depleted = fp < DEPLETED_THRESHOLD
            
            if first:
                current_path.moveTo(x, y)
                current_depleted = is_depleted
                first = False
            else:
                # 状態が変わったら、現在のパスを描画して新しいパスを開始
                if is_depleted != current_depleted:
                    # 現在のパスを描画
                    self._draw_path_segment(painter, current_path, 
                                           DEPLETED_COLOR if current_depleted else color,
                                           dashed)
                    # 新しいパスを開始（前の点から）
                    current_path = QPainterPath()
                    current_path.moveTo(prev_x, prev_y)
                    current_path.lineTo(x, y)
                    current_depleted = is_depleted
                else:
                    current_path.lineTo(x, y)
            
            prev_x, prev_y = x, y
        
        # 最後のパスを描画
        if not first:
            self._draw_path_segment(painter, current_path,
                                   DEPLETED_COLOR if current_depleted else color,
                                   dashed)
    
    def _draw_path_segment(self, painter, path, color, dashed=False):
        """v6.0.1: パスセグメントを描画"""
        if dashed:
            pen = QPen(QColor(color), 2, Qt.DashLine)
        else:
            pen = QPen(QColor(color), 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


# ==================== Info Card Widget (v2.6 Style) ====================
class InfoCardWidget(QWidget):
    """
    v2.6 Classic Info Card (枠線付き)
    """
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


# ==================== Telemetry Strip (v2.6 Style) ====================
class TelemetryStripWidget(QWidget):
    """
    v6.0.1 Telemetry Strip - 細いライン (高さ4px)
    入力時に発光（輝度抑制版）
    """
    def __init__(self):
        super().__init__()
        self.glow_intensity = 0.0
        self.pulses: List[Dict] = []
        
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(30)
        
        if PYNPUT_AVAILABLE:
            input_listener.signals.key_pressed.connect(self._on_input)
            input_listener.signals.mouse_clicked.connect(self._on_input)
    
    def _on_input(self):
        self.glow_intensity = 1.0
        self.pulses.append({'x': 0.5, 'intensity': 1.0})
        self._check_shisha_resume()
        self._check_sleep_wake()
    def _check_sleep_wake(self):
        """v6.1.0: 就寝中にPC操作検出でモニタ復帰（DB参照）"""
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
        """v6.1.0: シーシャ復帰チェック（NeuroSound内部状態で管理）"""
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
        
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        
        # Base line
        base_color = QColor(Colors.BORDER)
        painter.fillRect(0, 0, width, height, base_color)
        
        # v6.0.1: Glow（輝度抑制: 200→100）
        if self.glow_intensity > 0.05:
            glow = QColor(Colors.CYAN)
            glow.setAlpha(int(self.glow_intensity * 100))  # 200→100
            painter.fillRect(0, 0, width, height, glow)


# ==================== Dashboard Tab (v6.0.2 SSOT Pure Reader) ====================
class DashboardTab(QWidget):
    """v6.0.2 SSOT Pure Reader Dashboard - No local BioEngine"""
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
        for key, label, default in [('next_break', '☕ Next Break:', 'SAFE'), ('bedtime', '⚡ Bedtime:', 'SAFE'), ('recovery', '🔋 Recovery:', '+0.0'), ('sleep', '💤 Sleep:', '--')]:
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
            db_path = ROOT_PATH / "Data" / "life_os.db"
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
        """v6.2.7 SSOT: 高速更新 (100ms) - brain_stateのキャッシュから読み取り"""
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
        """v6.1.0 Hybrid Mode: DB優先 + JSONフォールバック"""
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
        """v6.1.0: Mute連動（DB経由）"""
        db = get_gui_db()
        if not db: return
        state = db.get_daemon_state()
        is_muted = not state.get('is_muted', False)
        db.update_daemon_state(is_muted=is_muted)
        if self.neuro_sound:
            self.neuro_sound.set_enabled(not is_muted)


# ==================== Sequence Tab (Shisha Timer) ====================
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
        """
        v3.7: 起動時に未完了のシーシャセッションを自動クローズ
        前回の押し忘れや異常終了を救済
        """
        if self.database is None:
            return
        
        try:
            incomplete = self.database.get_incomplete_shisha_session()
            if incomplete:
                session_id = incomplete['id']
                start_time_str = incomplete['start_time']
                
                # 未終了セッションを現在時刻でクローズ（completed=False）
                now = now_jst()
                self.database.end_shisha_session(session_id, now, completed=False)
                
                print(f"v3.7 Shisha Recovery: Auto-closed incomplete session "
                      f"(id={session_id}, started={start_time_str})")
        except Exception as e:
            print(f"v3.7 Shisha Recovery: Could not handle incomplete session ({e})")
    
    def _force_reset_shisha_state(self):
        """v6.1.0: 起動時にis_shisha_activeを強制的にFalseにリセット（DB経由）"""
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
        
        # Title
        title = QLabel("🌿 Shisha Sequence")
        title.setFont(Fonts.number(20, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)
        
        # Circle Progress
        self.circle_widget = ShishaCircleWidget()
        self.circle_widget.setFixedSize(280, 280)
        
        circle_container = QHBoxLayout()
        circle_container.addStretch()
        circle_container.addWidget(self.circle_widget)
        circle_container.addStretch()
        main_layout.addLayout(circle_container)
        
        # Stage Indicators
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
        
        # Controls
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
        
        self.stop_btn = QPushButton("■ STOP")
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
        """v4.0: シーシャセッション開始（DB記録 + Audio Away Mode）"""
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
        
        # v3.7: DBにセッション開始を記録
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
        self.timer.start(1000)
        self._update_display()
    
    def _stop(self, completed: bool = True):
        """
        v4.0: シーシャセッション停止（DB記録 + Audio復帰）
        
        Args:
            completed: 正常終了かどうか（押し忘れ救済時はFalse）
        """
        self.timer.stop()
        self.is_running = False
        self.current_stage = 0
        self.remaining = 0
        
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        # v3.7: DBにセッション終了を記録
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
        self._update_display()
    
    def force_stop_for_shutdown(self):
        """
        v3.7: アプリ終了時の強制停止（押し忘れ救済）
        
        シーシャがActiveのまま終了する場合、
        現在時刻までをセッションとしてDBに記録する。
        """
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
        
        # Update stage indicators
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
        """v6.0.1: NeuroSoundEngineへの参照を取得"""
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
    """
    v6.0.1 シーシャ用円形プログレス
    ラベル位置を cy + size * 0.08 に設定
    """
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
        
        # Background
        pen = QPen(QColor(Colors.BG_ELEVATED), int(ring_width))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawArc(rect, 0, 360 * 16)
        
        # Progress
        if self.is_running and self.progress > 0:
            pen = QPen(QColor(self.color), int(ring_width))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            angle = int(self.progress * 360 * 16)
            painter.drawArc(rect, 90 * 16, -angle)
        
        # Time
        minutes = self.remaining // 60
        seconds = self.remaining % 60
        
        painter.setPen(QColor(Colors.TEXT_PRIMARY))
        painter.setFont(Fonts.number(int(size * 0.14), True))
        time_text = f"{minutes:02d}:{seconds:02d}"
        text_rect = QRectF(0, cy - size * 0.08, width, size * 0.14)
        painter.drawText(text_rect, Qt.AlignCenter, time_text)
        
        # v6.0.1: ラベルを cy + size * 0.08 に配置
        painter.setPen(QColor(self.color if self.is_running else Colors.TEXT_DIM))
        painter.setFont(Fonts.label(int(size * 0.05)))
        label = "ACTIVE" if self.is_running else "STANDBY"
        label_rect = QRectF(0, cy + size * 0.08, width, size * 0.08)
        painter.drawText(label_rect, Qt.AlignCenter, label)



# ==================== Analysis Tab ====================
class TimelineGraphCanvas(QWidget):
    """v6.2.7 Dual-Layer Architecture - Static Graph Layer (Bottom)"""
    VIEW_WINDOW_HOURS = 12
    CACHE_HOURS = 72
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
        self.scroll_offset_hours = max(0, min(60, hours))
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
            db_path = ROOT_PATH / "Data" / "life_os.db"
            if not db_path.exists(): return
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            now = now_jst()
            start = now - timedelta(hours=self.CACHE_HOURS)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='brain_metrics'")
            if cursor.fetchone():
                cursor.execute('SELECT timestamp, effective_fp FROM brain_metrics WHERE timestamp >= ? ORDER BY timestamp ASC', (start.isoformat(),))
                self.cached_tactile = [dict(r) for r in cursor.fetchall()]
            else:
                self.cached_tactile = []
            if not self.cached_tactile:
                cursor.execute('SELECT timestamp, effective_fp FROM tactile_logs WHERE timestamp >= ? AND effective_fp IS NOT NULL ORDER BY timestamp ASC', (start.isoformat(),))
                self.cached_tactile = [dict(r) for r in cursor.fetchall()]
            cursor.execute('SELECT id, start_time, end_time FROM shisha_logs WHERE start_time >= ? OR end_time >= ? OR end_time IS NULL ORDER BY start_time', (start.isoformat(), start.isoformat()))
            self.cached_shisha = []
            for r in cursor.fetchall():
                st = datetime.fromisoformat(r['start_time']).replace(tzinfo=JST) if r['start_time'] else None
                et = datetime.fromisoformat(r['end_time']).replace(tzinfo=JST) if r['end_time'] else None
                if st: self.cached_shisha.append({'start': st, 'end': et})
            cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? ORDER BY timestamp ASC', (start.isoformat(),))
            self.hr_stream = [{'timestamp': r['timestamp'], 'bpm': r['bpm'], 'source': r['source']} for r in cursor.fetchall()]
            conn.close()
            self._cache_loaded = True
        except Exception as e:
            print(f"[Timeline] Cache load error: {e}")
    def _draw_context_bg(self, p, vs, ve, m, gw, gh):
        vh = self.VIEW_WINDOW_HOURS
        sc = QColor(self.COLOR_SHISHA_BG)
        sc.setAlpha(40)
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
        lc = QColor(Colors.ORANGE)
        lc.setAlpha(40)
        pen = QPen(lc, 2.0)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        for e in self.cached_tactile:
            try:
                ts = datetime.fromisoformat(e['timestamp'])
                if ts.tzinfo is None: ts = ts.replace(tzinfo=JST)
                fp = e.get('effective_fp')
                if fp is None or ts < vs or ts > ve: continue
                ratio = (ts - vs).total_seconds() / (vh * 3600)
                x = m['left'] + ratio * gw
                bh = max(0, min(1, fp / 100)) * fp_max
                p.drawLine(QPointF(x, fp_bottom), QPointF(x, fp_bottom - bh))
            except: continue
        if self.current_fp and self.current_fp > 0:
            now = now_jst()
            ratio = (now - vs).total_seconds() / (vh * 3600)
            x = m['left'] + ratio * gw
            if m['left'] <= x <= m['left'] + gw:
                cc = QColor(Colors.ORANGE)
                cc.setAlpha(100)
                cp = QPen(cc, 4.0)
                cp.setCapStyle(Qt.FlatCap)
                p.setPen(cp)
                bh = max(0, min(1, self.current_fp / 100)) * fp_max
                p.drawLine(QPointF(x, fp_bottom), QPointF(x, fp_bottom - bh))
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
    """v5.4.0 Dual-Layer Architecture - Interactive Overlay Layer (Top)"""
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
    """v6.2.14 Dual-Layer Architecture - Container with ScrollBar (3 days)"""
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
    """v6.1.0 DB-centric SSOT Analysis Tab"""
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
            db_path = ROOT_PATH / "Data" / "life_os.db"
            if not db_path.exists(): return
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            now = now_jst()
            start = now - timedelta(hours=72)
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
        title = QLabel("📊 Analysis - 12h Timeline (Drag for 7 days)")
        title.setFont(Fonts.number(16, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        tr.addWidget(title)
        tr.addStretch()
        desc = QLabel("マウスホバーで詳細表示 | ドラッグで過去を表示")
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
                    start = now - timedelta(hours=72)
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

# ==================== Settings Tab ====================
class SettingsTab(QWidget):
    """v6.0.1: 設定タブ（Audio Engine + Multi-Slot Ambient）"""
    
    # v6.0.1: 環境音ソース一覧（Rain/Fireのみ）
    AMBIENT_SOURCES = ['Rain', 'Fire']
    
    def __init__(self, neuro_sound=None):
        super().__init__()
        self.neuro_sound = neuro_sound
        
        # v4.0: 3スロット分のUI要素を保持
        self.ambient_slot_checks = []
        self.ambient_slot_combos = []
        self.ambient_slot_sliders = []
        self.ambient_slot_labels = []
        
        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel("⚙ Settings")
        title.setFont(Fonts.number(16, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        layout.addWidget(title)
        
        # Oura Ring
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
        
        # Master Enable (グリッド外に配置)
        self.audio_enabled_check = QCheckBox("Enable Audio Engine")
        self.audio_enabled_check.setChecked(audio_cfg.get('enabled', True))
        self.audio_enabled_check.stateChanged.connect(self._on_audio_enabled_changed)
        audio_main_layout.addWidget(self.audio_enabled_check)
        
        # v6.0.1: QGridLayout でボリュームコントロールを整列
        volume_grid = QGridLayout()
        volume_grid.setColumnStretch(1, 1)  # スライダー列を伸縮
        volume_grid.setColumnMinimumWidth(0, 70)   # ラベル列
        volume_grid.setColumnMinimumWidth(2, 45)   # 数値列
        volume_grid.setColumnMinimumWidth(3, 60)   # スイッチ列
        volume_grid.setHorizontalSpacing(10)
        volume_grid.setVerticalSpacing(8)
        
        row = 0
        
        # ─── Volume Controls ───
        vol_header = QLabel("─── Volume Controls ───")
        vol_header.setAlignment(Qt.AlignCenter)
        volume_grid.addWidget(vol_header, row, 0, 1, 4)
        row += 1
        
        # Master Volume
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
        
        # Master は常に有効なので空欄
        volume_grid.addWidget(QLabel(""), row, 3)
        row += 1
        
        # BGM
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
        
        # v6.0.1: Ambient Slots を QGridLayout で配置
        ambient_grid = QGridLayout()
        ambient_grid.setColumnStretch(2, 1)  # スライダー列を伸縮
        ambient_grid.setColumnMinimumWidth(0, 55)   # チェック列
        ambient_grid.setColumnMinimumWidth(1, 70)   # コンボ列
        ambient_grid.setColumnMinimumWidth(3, 45)   # 数値列
        ambient_grid.setHorizontalSpacing(8)
        ambient_grid.setVerticalSpacing(6)
        
        # Configから ambient_slots を取得
        ambient_slots_cfg = audio_cfg.get('ambient_slots', [
            {'source': 'Rain', 'volume': 0.15, 'enabled': False},
            {'source': 'Fire', 'volume': 0.15, 'enabled': False},
            {'source': 'Rain', 'volume': 0.15, 'enabled': False},
        ])
        
        # 3スロット保証
        while len(ambient_slots_cfg) < 3:
            ambient_slots_cfg.append({'source': 'Rain', 'volume': 0.15, 'enabled': False})
        
        for i in range(3):
            slot_cfg = ambient_slots_cfg[i]
            
            # Col 0: Enable checkbox
            check = QCheckBox(f"Slot{i+1}")
            check.setChecked(slot_cfg.get('enabled', False))
            check.stateChanged.connect(lambda state, idx=i: self._on_ambient_slot_enabled_changed(idx, state))
            ambient_grid.addWidget(check, i, 0)
            self.ambient_slot_checks.append(check)
            
            # Col 1: Source combo
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
            
            # Col 2: Volume slider
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(slot_cfg.get('volume', 0.15) * 100))
            slider.valueChanged.connect(lambda val, idx=i: self._on_ambient_slot_volume_changed(idx, val))
            ambient_grid.addWidget(slider, i, 2)
            self.ambient_slot_sliders.append(slider)
            
            # Col 3: Volume label
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
        self.headphone_check.setToolTip("ヘッドホン使用時はBinaural Beat、スピーカー時はIsochronic Tone")
        self.headphone_check.stateChanged.connect(self._on_headphone_mode_changed)
        neuro_layout.addWidget(self.headphone_check)
        self.bas_check = QCheckBox("BAS (Left-Right Stim)")
        self.bas_check.setChecked(audio_cfg.get('bas_enabled', False))
        self.bas_check.setToolTip("Bilateral Alternating Stimulation - 左右交互のパンニング")
        self.bas_check.stateChanged.connect(self._on_bas_enabled_changed)
        neuro_layout.addWidget(self.bas_check)
        neuro_layout.addStretch()
        audio_main_layout.addLayout(neuro_layout)
        audio_info = QLabel("BGM: Binaural Beat (8%推奨) | Ambient: Rain / Fire")
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
        """v4.0: 設定保存（新しいconfig構造）"""
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
            
            # v4.0: Neuro Settings
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
    GROUP_STYLE = f"QGroupBox{{font-size:11pt;color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:6px;margin-top:12px;padding-top:12px;}}QGroupBox::title{{subcontrol-origin:margin;left:10px;}}"
    INPUT_STYLE = f"QLineEdit{{background-color:{Colors.BG_ELEVATED};color:{Colors.TEXT_PRIMARY};border:1px solid {Colors.BORDER};border-radius:4px;padding:8px;font-family:{Fonts.FAMILY_NUMBER};}}"
    SPIN_STYLE = f"QSpinBox{{background-color:{Colors.BG_ELEVATED};color:{Colors.CYAN};border:1px solid {Colors.BORDER};border-radius:4px;padding:5px 8px;}}"
    def __init__(self):
        super().__init__()
        self.ambient_sync: Optional[AmbientSync] = None
        self.initUI()
        self._load_settings()
        self._init_home_system()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_status)
        self.timer.start(3000)
    def _load_settings(self):
        home_cfg = config.get('home', {})
        self.hue_ip_input.setText(home_cfg.get('hue_ip', ''))
        self.hue_room_input.setText(home_cfg.get('hue_room', 'リビングルーム'))
        self.bravia_ip_input.setText(home_cfg.get('bravia_ip', ''))
        self.bravia_psk_input.setText(home_cfg.get('bravia_psk', ''))
        thresholds = home_cfg.get('thresholds', {'off': 60, 'low': 20, 'high': 1})
        self.threshold_off_spin.setValue(thresholds.get('off', 60))
        self.threshold_low_spin.setValue(thresholds.get('low', 20))
        self.threshold_high_spin.setValue(thresholds.get('high', 1))
        if hasattr(self, 'sleep_toggle'):
            self.sleep_toggle.setChecked(home_cfg.get('sleep_detection_enabled', False))
        if hasattr(self, 'sleep_delay_spin'):
            self.sleep_delay_spin.setValue(home_cfg.get('sleep_detection_minutes', 1.0))
        self._update_toggle_styles()
        vol_profiles = home_cfg.get('volume_profiles', {'Spotify': {'enabled': False, 'volume': 20}, 'Netflix': {'enabled': False, 'volume': 20}, 'YouTube': {'enabled': False, 'volume': 20}, 'Prime Video': {'enabled': False, 'volume': 20}})
        self._rebuild_vol_profiles(vol_profiles)
    def _rebuild_vol_profiles(self, profiles: Dict):
        while self._vol_profiles_container.count():
            item = self._vol_profiles_container.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout(): self._clear_layout(item.layout())
        self.vol_profile_widgets = {}
        for app, data in profiles.items():
            self._create_vol_row(app, data.get('enabled', False), data.get('volume', 20))
    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout(): self._clear_layout(item.layout())
    def _create_vol_row(self, app: str, enabled: bool = False, volume: int = 20):
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(12, 6, 8, 6)
        lbl = QLabel(app)
        lbl.setMinimumWidth(80)
        lbl.setStyleSheet(f"color:{Colors.TEXT_PRIMARY};")
        row.addWidget(lbl, 2, Qt.AlignVCenter)
        chk = QCheckBox()
        chk.setChecked(enabled)
        chk.setStyleSheet("QCheckBox::indicator{width:16px;height:16px;}")
        chk.stateChanged.connect(self._save_vol_profiles)
        row.addWidget(chk, 1, Qt.AlignCenter)
        spin = NoScrollSpinBox()
        spin.setRange(0, 100)
        spin.setValue(volume)
        spin.setFixedWidth(70)
        spin.setStyleSheet(self.SPIN_STYLE)
        spin.valueChanged.connect(self._save_vol_profiles)
        row.addWidget(spin, 1, Qt.AlignVCenter)
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{Colors.TEXT_DIM};border:none;font-size:11pt;}}QPushButton:hover{{color:{Colors.RED};}}")
        del_btn.clicked.connect(lambda _, a=app: self._delete_volume_profile(a))
        row.addWidget(del_btn, 0, Qt.AlignVCenter)
        container = QWidget()
        container.setLayout(row)
        container.setStyleSheet(f"QWidget{{background-color:{Colors.BG_ELEVATED};border-radius:6px;}}")
        self._vol_profiles_container.addWidget(container)
        self.vol_profile_widgets[app] = {'check': chk, 'spin': spin, 'container': container}
    def _add_volume_profile(self):
        from PyQt5.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Add App Profile", "App name:")
        if ok and text.strip() and text.strip() not in self.vol_profile_widgets:
            self._create_vol_row(text.strip(), False, 20)
            self._save_vol_profiles()
    def _delete_volume_profile(self, app: str):
        if app in self.vol_profile_widgets:
            w = self.vol_profile_widgets.pop(app)
            w['container'].deleteLater()
            self._save_vol_profiles()
    def _save_vol_profiles(self):
        global config
        if 'home' not in config: config['home'] = {}
        config['home']['volume_profiles'] = {app: {'enabled': w['check'].isChecked(), 'volume': w['spin'].value()} for app, w in self.vol_profile_widgets.items()}
        safe_write_json(CONFIG_PATH, config)
    def _init_home_system(self):
        if not HOME_AVAILABLE: return
        home_cfg = config.get('home', {})
        if home_cfg.get('hue_ip'):
            self.ambient_sync = AmbientSync(home_cfg)
            self.ambient_sync.set_status_callback(self._on_status_update)
            self.ambient_sync.set_sleep_callback(self._on_sleep_state_changed)
            if home_cfg.get('auto_start', False):
                self.ambient_sync.start()
                self.ambient_sync.set_enabled(True)
                self.sync_toggle.setChecked(True)
            if home_cfg.get('focus_lighting', False):
                if not self.ambient_sync.is_running():
                    self.ambient_sync.start()
                self.ambient_sync.set_focus_lighting(True)
                self.focus_toggle.setChecked(True)
            if home_cfg.get('sleep_detection_enabled', False):
                if not self.ambient_sync.is_running():
                    self.ambient_sync.start()
                self.ambient_sync.set_sleep_detection(True, home_cfg.get('sleep_detection_minutes', 1.0))
                self.sleep_toggle.setChecked(True)
            self._update_toggle_styles()
    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea{{border:none;background:{Colors.BG_DARK};}}")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("🏠 Home Cybernetics")
        title.setFont(Fonts.number(16, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        layout.addWidget(title)
        if not HOME_AVAILABLE:
            warn = QLabel("⚠ Home module not available. Install: pip install phue requests")
            warn.setStyleSheet(f"color: {Colors.ORANGE};")
            layout.addWidget(warn)
            layout.addStretch()
            scroll.setWidget(content)
            main_layout.addWidget(scroll)
            self.setLayout(main_layout)
            return
        status_row = QHBoxLayout()
        tv_group = QGroupBox("📺 BRAVIA")
        tv_group.setStyleSheet(self.GROUP_STYLE)
        tv_layout = QGridLayout()
        tv_layout.setSpacing(8)
        self.tv_power_label = QLabel("Power: --")
        self.tv_app_label = QLabel("App: --")
        self.tv_volume_label = QLabel("Volume: --")
        self.tv_saving_label = QLabel("Saving: --")
        for i, lbl in enumerate([self.tv_power_label, self.tv_app_label, self.tv_volume_label, self.tv_saving_label]):
            lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
            tv_layout.addWidget(lbl, i // 2, i % 2)
        tv_group.setLayout(tv_layout)
        status_row.addWidget(tv_group)
        hue_group = QGroupBox("💡 Hue Rooms")
        hue_group.setStyleSheet(self.GROUP_STYLE)
        hue_layout = QGridLayout()
        hue_layout.setSpacing(6)
        self.hue_room_widgets = {}
        hue_group.setLayout(hue_layout)
        self._hue_layout = hue_layout
        status_row.addWidget(hue_group)
        layout.addLayout(status_row)
        logic_group = QGroupBox("⚙ Sync Logic Tuning")
        logic_group.setStyleSheet(self.GROUP_STYLE)
        logic_layout = QGridLayout()
        logic_layout.setSpacing(8)
        logic_layout.addWidget(QLabel("Hue >"), 0, 0)
        self.threshold_off_spin = NoScrollSpinBox()
        self.threshold_off_spin.setRange(0, 100)
        self.threshold_off_spin.setSuffix("%")
        self.threshold_off_spin.setStyleSheet(self.SPIN_STYLE)
        logic_layout.addWidget(self.threshold_off_spin, 0, 1)
        lbl_off = QLabel("🔆")
        lbl_off.setStyleSheet("font-size:12pt;")
        logic_layout.addWidget(lbl_off, 0, 2)
        logic_layout.addWidget(QLabel("Hue >"), 1, 0)
        self.threshold_low_spin = NoScrollSpinBox()
        self.threshold_low_spin.setRange(0, 100)
        self.threshold_low_spin.setSuffix("%")
        self.threshold_low_spin.setStyleSheet(self.SPIN_STYLE)
        logic_layout.addWidget(self.threshold_low_spin, 1, 1)
        lbl_low = QLabel("🌿🌿")
        lbl_low.setStyleSheet("font-size:12pt;")
        logic_layout.addWidget(lbl_low, 1, 2)
        logic_layout.addWidget(QLabel("Hue ≤"), 2, 0)
        self.threshold_high_spin = NoScrollSpinBox()
        self.threshold_high_spin.setRange(0, 100)
        self.threshold_high_spin.setSuffix("%")
        self.threshold_high_spin.setStyleSheet(self.SPIN_STYLE)
        logic_layout.addWidget(self.threshold_high_spin, 2, 1)
        lbl_high = QLabel("🌿")
        lbl_high.setStyleSheet("font-size:12pt;")
        logic_layout.addWidget(lbl_high, 2, 2)
        logic_group.setLayout(logic_layout)
        layout.addWidget(logic_group)
        vol_group = QGroupBox("🔊 App Volume Profiles")
        vol_group.setStyleSheet(self.GROUP_STYLE)
        self._vol_layout = QVBoxLayout()
        self._vol_layout.setSpacing(4)
        self._vol_layout.setContentsMargins(10, 8, 10, 8)
        self._vol_profiles_container = QVBoxLayout()
        self._vol_profiles_container.setSpacing(4)
        self._vol_layout.addLayout(self._vol_profiles_container)
        self.vol_profile_widgets = {}
        add_btn = QPushButton("➕ Add App Profile")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"QPushButton{{background:{Colors.BG_ELEVATED};color:{Colors.TEXT_SECONDARY};border:1px dashed {Colors.BORDER};border-radius:4px;padding:6px;}}QPushButton:hover{{color:{Colors.CYAN};border-color:{Colors.CYAN};}}")
        add_btn.clicked.connect(self._add_volume_profile)
        self._vol_layout.addWidget(add_btn)
        vol_group.setLayout(self._vol_layout)
        layout.addWidget(vol_group)
        control_layout = QHBoxLayout()
        control_layout.setSpacing(15)
        self.sync_toggle = QPushButton("🔄")
        self.sync_toggle.setCheckable(True)
        self.sync_toggle.setFixedSize(50, 50)
        self.sync_toggle.setCursor(Qt.PointingHandCursor)
        self.sync_toggle.clicked.connect(self._toggle_sync)
        control_layout.addWidget(self.sync_toggle)
        self.focus_toggle = QPushButton("💡")
        self.focus_toggle.setCheckable(True)
        self.focus_toggle.setFixedSize(50, 50)
        self.focus_toggle.setCursor(Qt.PointingHandCursor)
        self.focus_toggle.clicked.connect(self._toggle_focus)
        control_layout.addWidget(self.focus_toggle)
        self.sleep_toggle = QPushButton("🌙")
        self.sleep_toggle.setCheckable(True)
        self.sleep_toggle.setFixedSize(50, 50)
        self.sleep_toggle.setCursor(Qt.PointingHandCursor)
        self.sleep_toggle.clicked.connect(self._toggle_sleep_detection)
        control_layout.addWidget(self.sleep_toggle)
        self._update_toggle_styles()
        self.apply_btn = QPushButton("💾 Apply && Connect")
        self.apply_btn.setMinimumHeight(50)
        self.apply_btn.setFont(Fonts.label(11, True))
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.setStyleSheet(f"QPushButton{{background:{Colors.CYAN};color:{Colors.BG_DARK};border:none;border-radius:6px;padding:10px 20px;}}QPushButton:hover{{background:{Colors.BLUE};}}")
        self.apply_btn.clicked.connect(self._apply_and_connect)
        control_layout.addWidget(self.apply_btn, 1)
        layout.addLayout(control_layout)
        sleep_group = QGroupBox("🌙 Sleep Detection")
        sleep_group.setStyleSheet(self.GROUP_STYLE)
        sleep_layout = QHBoxLayout()
        sleep_layout.setSpacing(12)
        sleep_layout.addWidget(QLabel("Lights off delay:"))
        self.sleep_delay_spin = NoScrollDoubleSpinBox()
        self.sleep_delay_spin.setRange(0.5, 30.0)
        self.sleep_delay_spin.setSingleStep(0.5)
        self.sleep_delay_spin.setSuffix(" min")
        self.sleep_delay_spin.setValue(config.get('home', {}).get('sleep_detection_minutes', 1.0))
        self.sleep_delay_spin.setStyleSheet(self.SPIN_STYLE.replace("QSpinBox", "QDoubleSpinBox"))
        sleep_layout.addWidget(self.sleep_delay_spin)
        sleep_layout.addStretch()
        sleep_info = QLabel("TV+Monitors off when Hue Room lights off")
        sleep_info.setStyleSheet(f"color:{Colors.TEXT_DIM};font-size:9pt;")
        sleep_layout.addWidget(sleep_info)
        sleep_group.setLayout(sleep_layout)
        layout.addWidget(sleep_group)
        conn_group = QGroupBox("🔌 Connection Settings")
        conn_group.setStyleSheet(self.GROUP_STYLE)
        conn_layout = QGridLayout()
        conn_layout.setSpacing(8)
        conn_layout.addWidget(QLabel("Hue Bridge IP:"), 0, 0)
        self.hue_ip_input = QLineEdit()
        self.hue_ip_input.setPlaceholderText("192.168.x.x")
        self.hue_ip_input.setStyleSheet(self.INPUT_STYLE)
        conn_layout.addWidget(self.hue_ip_input, 0, 1)
        conn_layout.addWidget(QLabel("Hue Room:"), 1, 0)
        self.hue_room_input = QLineEdit()
        self.hue_room_input.setStyleSheet(self.INPUT_STYLE)
        conn_layout.addWidget(self.hue_room_input, 1, 1)
        conn_layout.addWidget(QLabel("BRAVIA IP:"), 2, 0)
        self.bravia_ip_input = QLineEdit()
        self.bravia_ip_input.setPlaceholderText("192.168.x.x")
        self.bravia_ip_input.setStyleSheet(self.INPUT_STYLE)
        conn_layout.addWidget(self.bravia_ip_input, 2, 1)
        conn_layout.addWidget(QLabel("BRAVIA PSK:"), 3, 0)
        self.bravia_psk_input = QLineEdit()
        self.bravia_psk_input.setEchoMode(QLineEdit.Password)
        self.bravia_psk_input.setStyleSheet(self.INPUT_STYLE)
        conn_layout.addWidget(self.bravia_psk_input, 3, 1)
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)
        layout.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll)
        self.setLayout(main_layout)
    def _update_toggle_styles(self):
        on_style = f"QPushButton{{background:{Colors.CYAN};color:{Colors.BG_DARK};border:none;border-radius:6px;font-size:16px;}}QPushButton:hover{{background:{Colors.BLUE};}}"
        off_style = f"QPushButton{{background:{Colors.BG_ELEVATED};color:{Colors.TEXT_DIM};border:1px solid {Colors.BORDER};border-radius:6px;font-size:16px;}}QPushButton:hover{{background:#3a3a3a;}}"
        self.sync_toggle.setStyleSheet(on_style if self.sync_toggle.isChecked() else off_style)
        self.focus_toggle.setStyleSheet(on_style if self.focus_toggle.isChecked() else off_style)
        self.sleep_toggle.setStyleSheet(on_style if self.sleep_toggle.isChecked() else off_style)
    def _toggle_focus(self):
        self._update_toggle_styles()
        enabled = self.focus_toggle.isChecked()
        if not self.ambient_sync:
            if not self._create_ambient_sync():
                self.focus_toggle.setChecked(False)
                self._update_toggle_styles()
                return
        if not self.ambient_sync.is_running():
            if not self.ambient_sync.start():
                self.focus_toggle.setChecked(False)
                self._update_toggle_styles()
                return
        self.ambient_sync.set_focus_lighting(enabled)
        global config
        if 'home' not in config: config['home'] = {}
        config['home']['focus_lighting'] = enabled
        safe_write_json(CONFIG_PATH, config)
    def _toggle_sleep_detection(self):
        self._update_toggle_styles()
        enabled = self.sleep_toggle.isChecked()
        delay = self.sleep_delay_spin.value()
        if not self.ambient_sync:
            if not self._create_ambient_sync():
                self.sleep_toggle.setChecked(False)
                self._update_toggle_styles()
                return
        if not self.ambient_sync.is_running():
            if not self.ambient_sync.start():
                self.sleep_toggle.setChecked(False)
                self._update_toggle_styles()
                return
        self.ambient_sync.set_sleep_detection(enabled, delay)
        self.ambient_sync.set_sleep_callback(self._on_sleep_state_changed)
        global config
        if 'home' not in config: config['home'] = {}
        config['home']['sleep_detection_enabled'] = enabled
        config['home']['sleep_detection_minutes'] = delay
        safe_write_json(CONFIG_PATH, config)
    def _on_sleep_state_changed(self, is_sleeping: bool):
        gui_push_command('SLEEP_DETECTED' if is_sleeping else 'WAKE_MONITORS')
    def _toggle_sync(self):
        self._update_toggle_styles()
        if not self.ambient_sync:
            if not self._create_ambient_sync(): return
        enabled = self.sync_toggle.isChecked()
        if enabled and not self.ambient_sync.is_running():
            self.ambient_sync.start()
        self.ambient_sync.set_enabled(enabled)
    def _create_ambient_sync(self) -> bool:
        if not HOME_AVAILABLE: return False
        home_cfg = self._build_config()
        if not home_cfg.get('hue_ip'):
            self.sync_toggle.setChecked(False)
            self.focus_toggle.setChecked(False)
            self._update_toggle_styles()
            return False
        self.ambient_sync = AmbientSync(home_cfg)
        self.ambient_sync.set_status_callback(self._on_status_update)
        return True
    def _build_config(self) -> Dict:
        vol_profiles = {}
        for app, widgets in self.vol_profile_widgets.items():
            vol_profiles[app] = {
                'enabled': widgets['check'].isChecked(),
                'volume': widgets['spin'].value()
            }
        return {
            'hue_ip': self.hue_ip_input.text(),
            'hue_room': self.hue_room_input.text() or 'リビングルーム',
            'bravia_ip': self.bravia_ip_input.text(),
            'bravia_psk': self.bravia_psk_input.text(),
            'thresholds': {
                'off': self.threshold_off_spin.value(),
                'low': self.threshold_low_spin.value(),
                'high': self.threshold_high_spin.value()
            },
            'volume_profiles': vol_profiles,
            'auto_start': self.sync_toggle.isChecked(),
            'focus_lighting': self.focus_toggle.isChecked(),
            'sleep_detection_enabled': self.sleep_toggle.isChecked() if hasattr(self, 'sleep_toggle') else False,
            'sleep_detection_minutes': self.sleep_delay_spin.value() if hasattr(self, 'sleep_delay_spin') else 1.0
        }
    def _apply_and_connect(self):
        global config
        home_cfg = self._build_config()
        config['home'] = home_cfg
        safe_write_json(CONFIG_PATH, config)
        print("[Home] Settings saved")
        was_enabled = self.sync_toggle.isChecked()
        if self.ambient_sync and self.ambient_sync.is_running():
            self.ambient_sync.stop()
        self.ambient_sync = None
        if home_cfg.get('hue_ip') and home_cfg.get('bravia_ip'):
            self.ambient_sync = AmbientSync(home_cfg)
            self.ambient_sync.set_status_callback(self._on_status_update)
            if was_enabled:
                self.ambient_sync.start()
                self.ambient_sync.set_enabled(True)
            print("[Home] Reconnected with new settings")
    def _on_status_update(self, hue_status: Dict, bravia_status: Dict):
        pass
    def _update_hue_rooms(self, all_rooms: Dict):
        bar_style = f"QProgressBar{{border:none;border-radius:2px;background:{Colors.BG_ELEVATED};height:8px;}}QProgressBar::chunk{{background:{Colors.CYAN};border-radius:2px;}}"
        bar_style_off = f"QProgressBar{{border:none;border-radius:2px;background:{Colors.BG_ELEVATED};height:8px;}}QProgressBar::chunk{{background:{Colors.TEXT_DIM};border-radius:2px;}}"
        existing = set(self.hue_room_widgets.keys())
        current = set(all_rooms.keys())
        for room in existing - current:
            w = self.hue_room_widgets.pop(room)
            w['label'].deleteLater()
            w['bar'].deleteLater()
        cols = 2
        for i, (room, data) in enumerate(sorted(all_rooms.items())):
            if room not in self.hue_room_widgets:
                lbl = QLabel(room[:8])
                lbl.setStyleSheet(f"color:{Colors.TEXT_SECONDARY};font-size:9pt;")
                bar = SmoothProgressBar()
                bar.setRange(0, 100)
                bar.setTextVisible(False)
                bar.setFixedHeight(8)
                self.hue_room_widgets[room] = {'label': lbl, 'bar': bar}
                row, col = divmod(i, cols)
                self._hue_layout.addWidget(lbl, row, col * 2)
                self._hue_layout.addWidget(bar, row, col * 2 + 1)
            w = self.hue_room_widgets[room]
            bri = int(data.get('bri', 0) * 100) if data.get('on') else 0
            w['bar'].setValue(bri)
            w['bar'].setStyleSheet(bar_style if data.get('on') else bar_style_off)
    def _update_status(self):
        if not self.ambient_sync: return
        self.ambient_sync._last_input_time = input_listener.last_input_time
        hue = self.ambient_sync.get_hue_status()
        bravia = self.ambient_sync.get_bravia_status()
        self.tv_power_label.setText(f"Power: {'ON' if bravia.get('power') else 'OFF'}")
        self.tv_app_label.setText(f"App: {bravia.get('app', '--')[:12]}")
        self.tv_volume_label.setText(f"Vol: {bravia.get('volume', '--')}")
        self.tv_saving_label.setText(f"Save: {bravia.get('power_saving', '--')}")
        all_rooms = hue.get('all_rooms', {})
        if all_rooms:
            self._update_hue_rooms(all_rooms)
    def shutdown(self):
        if self.ambient_sync and self.ambient_sync.is_running(): self.ambient_sync.stop()
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


# ==================== Main Window ====================
class LifeOSGUI(QMainWindow):
    """
    v6.0.1 - Perfect Timeline
    グラフループ修正 + スクロールUX改善 + 日付表示
    """
    
    def __init__(self):
        super().__init__()
        self.daemon_process = None
        
        # v3.7: データベース初期化
        self._init_database()
        
        self.initUI()
        self._auto_start_daemon()
        
        # v6.0.1: 終了時のクリーンアップを登録
        atexit.register(self._cleanup_daemon)
    
    def _init_database(self):
        """v3.7: データベース初期化"""
        try:
            from core.database import LifeOSDatabase
            db_path = ROOT_PATH / "Data" / "life_os.db"
            self.database = LifeOSDatabase(str(db_path))
        except Exception as e:
            print(f"v3.7 Database init error: {e}")
            self.database = None
    
    def initUI(self):
        self.setWindowTitle('LifeOS v5.3.0')
        self.setMinimumSize(950, 750)
        
        # v6.0.1: QSSを読み込み（基本スタイルはQSSから）
        stylesheet = load_stylesheet()
        if stylesheet:
            self.setStyleSheet(stylesheet)
        else:
            # フォールバック
            self.setStyleSheet(f"background-color: {Colors.BG_DARK};")
        
        self.setWindowFlags(Qt.FramelessWindowHint)
        
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Title bar
        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet(f"background-color: {Colors.BG_PANEL};")
        
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        
        # v6.0.1: Traffic lights with objectName
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
        
        title = QLabel("LifeOS v6.0.1")
        title.setObjectName("windowTitle")
        title.setFont(Fonts.label(11, True))
        title.setStyleSheet(f"color: {Colors.CYAN};")
        title_layout.addWidget(title)
        
        title_layout.addStretch()
        
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {Colors.CYAN};")
        title_layout.addWidget(self.status_dot)
        
        main_layout.addWidget(title_bar)
        
        # v6.0.1: Tabs (スタイルはstyle.qssに委譲)
        tabs = QTabWidget()
        
        # v3.0: DashboardTabを保持してneuro_sound参照を取得
        self.dashboard_tab = DashboardTab()
        tabs.addTab(self.dashboard_tab, "🔄 Dashboard")
        tabs.addTab(AnalysisTab(), "📊 Analysis")
        
        # v3.7: SequenceTabにデータベースを渡す
        self.sequence_tab = SequenceTab(database=self.database)
        tabs.addTab(self.sequence_tab, "🌿 Shisha")
        self.home_tab = HomeTab()
        tabs.addTab(self.home_tab, "🏠 Home")
        neuro_sound = getattr(self.dashboard_tab, 'neuro_sound', None)
        self.settings_tab = SettingsTab(neuro_sound=neuro_sound)
        tabs.addTab(self.settings_tab, "⚙ Settings")
        tabs.addTab(LogTab(), "📋 Logs")
        
        main_layout.addWidget(tabs)
        central.setLayout(main_layout)
        
        self._drag_pos = None
        title_bar.mousePressEvent = self._title_press
        title_bar.mouseMoveEvent = self._title_move
        
        self._center()
    
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
    
    def _auto_start_daemon(self):
        """v6.0.1: デーモンを自動起動（PIDファイルで重複起動を防止）"""
        daemon = ROOT_PATH / "core" / "daemon.py"
        
        if not daemon.exists():
            self.status_dot.setStyleSheet(f"color: {Colors.RED};")
            return
        
        # v6.0.1: 既存プロセスの確認
        if PID_PATH.exists():
            try:
                existing_pid = int(PID_PATH.read_text().strip())
                # プロセスが存在するか確認
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
        """v6.0.1: デーモンのクリーンアップ"""
        if self.daemon_process and self.daemon_process.poll() is None:
            print("Terminating daemon...")
            self.daemon_process.terminate()
            try:
                self.daemon_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.daemon_process.kill()
    
    def closeEvent(self, event):
        """
        v6.0.1: GUI終了時の処理（押し忘れ救済対応 + Audio cleanup）
        
        シーシャがActiveのまま終了する場合、
        現在時刻までをセッションとしてDBに記録する。
        """
        try:
            # v3.7: シーシャ押し忘れ救済
            if hasattr(self, 'sequence_tab') and self.sequence_tab is not None:
                self.sequence_tab.force_stop_for_shutdown()
        except Exception as e:
            # 終了を阻害しない
            print(f"v3.7 Shisha Shutdown Error: {e}")
        
        # v6.0.1: NeuroSoundEngine cleanup
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


# ==================== Entry Point ====================
def main():
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create('Fusion'))
    
    # v6.0.1: QSSを読み込み
    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)
    
    window = LifeOSGUI()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
