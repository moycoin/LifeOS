#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import time
import math
import signal
import atexit
import random
import logging
import threading
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from typing import Dict, List, Optional, Tuple
from collections import namedtuple
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    print("[daemon] pygame not available - audio disabled")
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[daemon] requests not available - Oura API disabled")
try:
    from pynput import mouse, keyboard as pynput_keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    print("[daemon] pynput not available - input monitoring disabled")
def get_root_path() -> Path:
    return Path(__file__).parent.parent.resolve()
ROOT_PATH = get_root_path()
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))
from core.types import (
    __version__,
    JST,
    now_jst,
    Command,
    CommandType,
    CommandQueue,
    safe_read_json,
    safe_write_json,
    COMMAND_QUEUE_FILENAME,
)
try:
    from core import LifeOSDatabase, DATABASE_AVAILABLE
except ImportError:
    from core.database import LifeOSDatabase
    DATABASE_AVAILABLE = True
try:
    from core import BioEngine, ENGINE_AVAILABLE
except ImportError:
    try:
        from core.engine import BioEngine
        ENGINE_AVAILABLE = True
    except ImportError:
        ENGINE_AVAILABLE = False
        BioEngine = None
try:
    from core.engine import ShadowHeartrate
    SHADOW_HR_AVAILABLE = True
except ImportError:
    SHADOW_HR_AVAILABLE = False
    ShadowHeartrate = None
UTC = timezone.utc
POLLING_INTERVAL_SECONDS = 300
OURA_DAY_BOUNDARY_HOUR = 4
LISTENER_RESTART_DELAY = 5.0
MAX_LISTENER_RESTARTS = 10
HeartRatePoint = namedtuple('HeartRatePoint', ['timestamp', 'bpm', 'source'])
NapSegment = namedtuple('NapSegment', ['start', 'end', 'avg_bpm', 'duration_minutes'])
def get_config_path() -> Path:
    return ROOT_PATH / "config.json"
def get_state_path() -> Path:
    return ROOT_PATH / "logs" / "daemon_state.json"
def get_command_queue_path() -> Path:
    return ROOT_PATH / "logs" / COMMAND_QUEUE_FILENAME
def get_log_dir() -> Path:
    return ROOT_PATH / "logs"
def get_pid_path() -> Path:
    return ROOT_PATH / "logs" / "daemon.pid"
def get_voice_assets_path() -> Path:
    return ROOT_PATH / "Data" / "sounds"
def get_db_path() -> Path:
    return ROOT_PATH / "Data" / "life_os.db"


# ==============================================================================
# PID Management
# ==============================================================================
def write_pid_file(logger: logging.Logger = None) -> bool:
    """Create PID file for single-instance enforcement"""
    try:
        pid_path = get_pid_path()
        pid_path.parent.mkdir(exist_ok=True)
        pid_path.write_text(str(os.getpid()))
        if logger:
            logger.info(f"PID file created: {pid_path} (PID: {os.getpid()})")
        return True
    except Exception as e:
        if logger:
            logger.error(f"Failed to create PID file: {e}")
        return False


def remove_pid_file(logger: logging.Logger = None):
    """Remove PID file on shutdown"""
    try:
        pid_path = get_pid_path()
        if pid_path.exists():
            pid_path.unlink()
            if logger:
                logger.info(f"PID file removed: {pid_path}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to remove PID file: {e}")


def is_daemon_running() -> Tuple[bool, Optional[int]]:
    """
    Check if daemon is already running.
    Returns: (is_running, pid)
    """
    pid_path = get_pid_path()
    
    if not pid_path.exists():
        return False, None
    
    try:
        pid = int(pid_path.read_text().strip())
        
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True, pid
            return False, None
        else:
            os.kill(pid, 0)
            return True, pid
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        remove_pid_file()
        return False, None


def get_oura_effective_date() -> date:
    now = datetime.now()
    if now.hour < OURA_DAY_BOUNDARY_HOUR:
        return (now - timedelta(days=1)).date()
    return now.date()


def is_data_from_effective_today(day_str: str) -> bool:
    """Tolerant date validation - accept today or yesterday"""
    if not day_str:
        return False
    try:
        data_date = datetime.strptime(day_str, '%Y-%m-%d').date()
        effective_today = get_oura_effective_date()
        yesterday = effective_today - timedelta(days=1)
        return data_date == effective_today or data_date == yesterday
    except Exception:
        return False


# ==============================================================================
# Logger Setup
# ==============================================================================
def setup_logger() -> logging.Logger:
    """Setup rotating logger with fixed filename"""
    log_dir = get_log_dir()
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / "daemon.log"
    
    # Rotate if too large (5MB)
    try:
        if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
            backup = log_dir / "daemon.log.old"
            if backup.exists():
                backup.unlink()
            log_file.rename(backup)
    except Exception:
        pass
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8', mode='a'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    return logging.getLogger('LifeOSDaemon')


# ==============================================================================
# Activity Monitor (Basic Presence Detection)
# ==============================================================================
class ActivityMonitor:
    """Basic user presence monitoring via pynput"""
    
    def __init__(self, idle_threshold_minutes: int = 10, logger: logging.Logger = None):
        self.idle_threshold = idle_threshold_minutes * 60
        self.last_activity = datetime.now()
        self.momentum_start = datetime.now()
        self.is_active = True
        self.logger = logger
        self._running = True
        
        self._mouse_listener = None
        self._keyboard_listener = None
        
        if PYNPUT_AVAILABLE:
            self._start_listeners()
    
    def _start_listeners(self):
        """Start pynput listeners with error handling"""
        try:
            self._mouse_listener = mouse.Listener(on_move=self._on_activity)
            self._keyboard_listener = pynput_keyboard.Listener(on_press=self._on_activity)
            
            self._mouse_listener.start()
            self._keyboard_listener.start()
            
            if self.logger:
                self.logger.debug("ActivityMonitor listeners started")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"ActivityMonitor listener start failed: {e}")
    
    def _on_activity(self, *args):
        """Activity callback"""
        try:
            now = datetime.now()
            
            # Reset momentum if idle for 5+ minutes
            idle_seconds = (now - self.last_activity).total_seconds()
            if idle_seconds > 300:
                self.momentum_start = now
            
            self.last_activity = now
            self.is_active = True
        except Exception:
            pass
    
    def is_user_present(self) -> bool:
        idle_seconds = (datetime.now() - self.last_activity).total_seconds()
        return idle_seconds < self.idle_threshold
    
    def get_idle_time(self) -> int:
        return int((datetime.now() - self.last_activity).total_seconds())
    
    def get_momentum_minutes(self) -> int:
        """Get continuous work minutes"""
        idle_seconds = self.get_idle_time()
        if idle_seconds > 300:
            return 0
        return int((datetime.now() - self.momentum_start).total_seconds() / 60)
    
    def stop(self):
        """Stop listeners gracefully"""
        self._running = False
        try:
            if self._mouse_listener:
                self._mouse_listener.stop()
            if self._keyboard_listener:
                self._keyboard_listener.stop()
        except Exception:
            pass


# ==============================================================================
# Input Telemetry (Robust Version with Auto-Restart)
# ==============================================================================
class InputTelemetry:
    """
    Robust input telemetry with auto-restart capability.
    
    Key improvements:
    - Listener auto-restart on failure
    - Thread-safe counter access
    - Guaranteed state persistence
    - Exception isolation (listeners never crash daemon)
    """
    
    # State thresholds
    CORRECTION_THRESHOLDS = {
        'CLEAR': 0.05,
        'HESITATION': 0.15
    }
    
    NEURO_THRESHOLDS = {
        'deep_dive_apm': 60,
        'deep_dive_mouse_max': 1500,
        'scavenging_mouse_min': 1500,
        'idle_apm': 5,
        'idle_mouse': 100
    }
    
    PHANTOM_NAP_THRESHOLD_MINUTES = 15
    PHANTOM_RECOVERY_RATE = 0.5
    
    AGGREGATE_INTERVAL_SECONDS = 60
    STATE_UPDATE_INTERVAL_SECONDS = 1
    
    def __init__(self, db, logger: logging.Logger):
        self.db = db
        self.logger = logger
        
        # Thread-safe lock for counters
        self._counter_lock = threading.Lock()
        
        # BioEngine for FP calculation
        self._telemetry_engine = None
        if ENGINE_AVAILABLE and BioEngine is not None:
            try:
                db_path = get_root_path() / "Data"
                self._telemetry_engine = BioEngine(db_path=db_path)
                self.logger.info("BioEngine initialized for Telemetry FP")
            except Exception as e:
                self.logger.warning(f"BioEngine init failed: {e}")
        
        # Counters (1-minute aggregation, reset each cycle)
        self._key_count = 0
        self._click_count = 0
        self._backspace_count = 0
        self._ctrl_pressed = False
        self._mouse_distance = 0.0
        self._last_mouse_pos: Optional[Tuple[int, int]] = None
        self._scroll_steps = 0
        
        # Session cumulative (never reset during daemon lifetime)
        self._session_mouse_total = 0.0
        self._session_backspace_total = 0
        self._session_key_total = 0
        self._session_click_total = 0
        self._session_scroll_total = 0
        
        # Timing
        self._last_aggregate_time = datetime.now()
        self._last_state_update_time = datetime.now()
        self._idle_start: Optional[datetime] = None
        self._phantom_recovery_accumulated = 0.0
        self._phantom_recovery_sum = 0.0
        self._running = True
        self._mouse_listener = None
        self._keyboard_listener = None
        self._listener_restart_count = 0
        self._listener_lock = threading.Lock()
        initial_brain = self._calculate_full_brain_state()
        self.current_state = {'state_label': 'IDLE', 'cognitive_friction': 'CLEAR', 'apm': 0, 'mouse_pixels': 0, 'correction_rate': 0.0, 'fp_multiplier': 1.0, 'mouse_pixels_cumulative': 0.0, 'backspace_count_cumulative': 0, 'key_count_cumulative': 0, 'phantom_recovery': 0.0, 'phantom_recovery_sum': 0.0, 'scroll_steps_cumulative': 0, 'effective_fp': initial_brain.get('effective_fp', 75.0), 'current_load': initial_brain.get('current_load', 0.0), 'estimated_readiness': initial_brain.get('estimated_readiness', 75.0), 'activity_state': initial_brain.get('activity_state', 'IDLE'), 'boost_fp': initial_brain.get('boost_fp', 0.0), 'base_fp': initial_brain.get('base_fp', 75.0), 'debt': initial_brain.get('debt', 0.0), 'continuous_work_hours': initial_brain.get('continuous_work_hours', 0.0), 'status_code': initial_brain.get('status_code', 'INITIALIZING'), 'status_sub': initial_brain.get('status_sub', ''), 'recommended_break_iso': initial_brain.get('recommended_break_iso'), 'exhaustion_iso': initial_brain.get('exhaustion_iso'), 'recovery_ceiling': initial_brain.get('recovery_ceiling', 100.0), 'stress_index': initial_brain.get('stress_index', 0.0), 'recovery_efficiency': initial_brain.get('recovery_efficiency', 1.0), 'decay_multiplier': initial_brain.get('decay_multiplier', 1.0), 'hours_since_wake': initial_brain.get('hours_since_wake', 0.0), 'boost_efficiency': initial_brain.get('boost_efficiency', 1.0), 'correction_factor': initial_brain.get('correction_factor', 1.0), 'current_hr': initial_brain.get('current_hr'), 'hr_stress_factor': initial_brain.get('hr_stress_factor', 1.0), 'current_mouse_speed': initial_brain.get('current_mouse_speed', 0.0), 'recent_correction_rate': initial_brain.get('recent_correction_rate', 0.0), 'is_shisha_active': initial_brain.get('is_shisha_active', False), 'prediction': initial_brain.get('prediction', {'continue': [], 'rest': []})}
        self._start_listeners()
        self._aggregate_thread = threading.Thread(
            target=self._aggregate_loop, 
            daemon=True,
            name="Telemetry-Aggregator"
        )
        self._aggregate_thread.start()
        self.logger.info("InputTelemetry initialized (Robust + Auto-Restart)")
    
    def _start_listeners(self):
        """Start pynput listeners with error handling"""
        if not PYNPUT_AVAILABLE:
            self.logger.warning("pynput not available - input monitoring disabled")
            return
        
        with self._listener_lock:
            try:
                # Stop existing listeners if any
                self._stop_listeners_internal()
                
                # Create new listeners
                self._mouse_listener = mouse.Listener(
                    on_move=self._on_mouse_move,
                    on_click=self._on_mouse_click,
                    on_scroll=self._on_scroll
                )
                self._keyboard_listener = pynput_keyboard.Listener(
                    on_press=self._on_key_press,
                    on_release=self._on_key_release
                )
                
                self._mouse_listener.start()
                self._keyboard_listener.start()
                
                self.logger.info("pynput listeners started successfully")
                self._listener_restart_count = 0
                
            except Exception as e:
                self.logger.error(f"Failed to start pynput listeners: {e}")
                self._schedule_listener_restart()
    
    def _stop_listeners_internal(self):
        """Stop listeners without lock (internal use)"""
        try:
            if self._mouse_listener:
                self._mouse_listener.stop()
                self._mouse_listener = None
        except Exception:
            pass
        
        try:
            if self._keyboard_listener:
                self._keyboard_listener.stop()
                self._keyboard_listener = None
        except Exception:
            pass
    
    def _schedule_listener_restart(self):
        """Schedule listener restart after delay"""
        if self._listener_restart_count >= MAX_LISTENER_RESTARTS:
            self.logger.error(f"Max listener restarts ({MAX_LISTENER_RESTARTS}) reached")
            return
        
        self._listener_restart_count += 1
        self.logger.info(f"Scheduling listener restart ({self._listener_restart_count}/{MAX_LISTENER_RESTARTS})")
        
        def restart_delayed():
            time.sleep(LISTENER_RESTART_DELAY)
            if self._running:
                self._start_listeners()
        
        threading.Thread(
            target=restart_delayed, 
            daemon=True, 
            name="Listener-Restarter"
        ).start()
    
    def _check_listener_health(self):
        """Check if listeners are alive and restart if needed"""
        try:
            mouse_alive = self._mouse_listener and self._mouse_listener.is_alive()
            keyboard_alive = self._keyboard_listener and self._keyboard_listener.is_alive()
            
            if not mouse_alive or not keyboard_alive:
                self.logger.warning(f"Listener health check failed (mouse={mouse_alive}, keyboard={keyboard_alive})")
                self._start_listeners()
        except Exception as e:
            self.logger.debug(f"Listener health check error: {e}")
    
    # ========== Input Callbacks (Exception-Isolated) ==========
    
    def _on_mouse_move(self, x: int, y: int):
        """Mouse move callback - thread-safe"""
        try:
            with self._counter_lock:
                if self._last_mouse_pos is not None:
                    dx = x - self._last_mouse_pos[0]
                    dy = y - self._last_mouse_pos[1]
                    dist = math.hypot(dx, dy)
                    self._mouse_distance += dist
                    self._session_mouse_total += dist
                self._last_mouse_pos = (x, y)
        except Exception:
            pass
    
    def _on_mouse_click(self, x, y, button, pressed):
        """Mouse click callback - thread-safe"""
        try:
            if pressed:
                with self._counter_lock:
                    self._click_count += 1
                    self._session_click_total += 1
        except Exception:
            pass
    
    def _on_scroll(self, x, y, dx, dy):
        """Scroll callback - thread-safe"""
        try:
            with self._counter_lock:
                steps = abs(dx) + abs(dy)
                self._scroll_steps += steps
                self._session_scroll_total += steps
        except Exception:
            pass
    
    def _on_key_press(self, key):
        """Key press callback - thread-safe"""
        try:
            with self._counter_lock:
                self._key_count += 1
                self._session_key_total += 1
                
                # Ctrl tracking
                if key == pynput_keyboard.Key.ctrl_l or key == pynput_keyboard.Key.ctrl_r:
                    self._ctrl_pressed = True
                
                # Backspace/Delete
                if key == pynput_keyboard.Key.backspace or key == pynput_keyboard.Key.delete:
                    self._backspace_count += 1
                    self._session_backspace_total += 1
                
                # Ctrl+Z (Undo)
                if self._ctrl_pressed:
                    try:
                        if hasattr(key, 'char') and key.char == 'z':
                            self._backspace_count += 1
                            self._session_backspace_total += 1
                    except Exception:
                        pass
        except Exception:
            pass
    
    def _on_key_release(self, key):
        """Key release callback - thread-safe"""
        try:
            if key == pynput_keyboard.Key.ctrl_l or key == pynput_keyboard.Key.ctrl_r:
                with self._counter_lock:
                    self._ctrl_pressed = False
        except Exception:
            pass
    
    # ========== Aggregation Loop ==========
    
    def _aggregate_loop(self):
        """Main aggregation loop with health checks"""
        health_check_counter = 0
        
        while self._running:
            try:
                time.sleep(1)
                
                now = datetime.now()
                
                # Update current state every second
                self._update_current_state()
                
                # Health check every 30 seconds
                health_check_counter += 1
                if health_check_counter >= 30:
                    self._check_listener_health()
                    health_check_counter = 0
                
                # Aggregate every 60 seconds
                aggregate_elapsed = (now - self._last_aggregate_time).total_seconds()
                if aggregate_elapsed >= self.AGGREGATE_INTERVAL_SECONDS:
                    self._perform_aggregation()
                    self._last_aggregate_time = now
            
            except Exception as e:
                self.logger.error(f"Aggregation loop error: {e}")
                traceback.print_exc()
    
    def _update_current_state(self):
        """Update current_state (called every second)"""
        try:
            with self._counter_lock:
                recent_apm = self._key_count + self._click_count
                recent_mouse = int(self._mouse_distance)
                recent_scroll = self._scroll_steps
                correction_rate = self._backspace_count / self._key_count if self._key_count > 0 else 0.0
                current_mouse_speed = recent_mouse
                recent_correction_rate = correction_rate
            cognitive_friction = 'CLEAR' if correction_rate < self.CORRECTION_THRESHOLDS['CLEAR'] else ('HESITATION' if correction_rate < self.CORRECTION_THRESHOLDS['HESITATION'] else 'GRIDLOCK')
            state_label, fp_multiplier = self._determine_state_with_scroll(recent_apm, recent_mouse, recent_scroll)
            self._handle_phantom_recovery(state_label)
            prev = self.current_state
            self.current_state = {'state_label': state_label, 'cognitive_friction': cognitive_friction, 'apm': recent_apm, 'mouse_pixels': recent_mouse, 'correction_rate': round(correction_rate, 4), 'fp_multiplier': fp_multiplier, 'mouse_pixels_cumulative': round(self._session_mouse_total, 1), 'backspace_count_cumulative': self._session_backspace_total, 'key_count_cumulative': self._session_key_total, 'phantom_recovery': round(self._phantom_recovery_accumulated, 2), 'phantom_recovery_sum': round(self._phantom_recovery_sum, 2), 'scroll_steps_cumulative': self._session_scroll_total, 'effective_fp': prev.get('effective_fp', 75.0), 'current_load': prev.get('current_load', 0.0), 'estimated_readiness': prev.get('estimated_readiness', 75.0), 'activity_state': prev.get('activity_state', 'IDLE'), 'boost_fp': prev.get('boost_fp', 0.0), 'base_fp': prev.get('base_fp', 75.0), 'debt': prev.get('debt', 0.0), 'continuous_work_hours': prev.get('continuous_work_hours', 0.0), 'status_code': prev.get('status_code', 'INITIALIZING'), 'status_sub': prev.get('status_sub', ''), 'recommended_break_iso': prev.get('recommended_break_iso'), 'exhaustion_iso': prev.get('exhaustion_iso'), 'recovery_ceiling': prev.get('recovery_ceiling', 100.0), 'stress_index': prev.get('stress_index', 0.0), 'recovery_efficiency': prev.get('recovery_efficiency', 1.0), 'decay_multiplier': prev.get('decay_multiplier', 1.0), 'hours_since_wake': prev.get('hours_since_wake', 0.0), 'boost_efficiency': prev.get('boost_efficiency', 1.0), 'correction_factor': prev.get('correction_factor', 1.0), 'current_hr': prev.get('current_hr'), 'hr_stress_factor': prev.get('hr_stress_factor', 1.0), 'current_mouse_speed': current_mouse_speed, 'recent_correction_rate': recent_correction_rate, 'is_shisha_active': prev.get('is_shisha_active', False), 'prediction': prev.get('prediction', {'continue': [], 'rest': []})}
        except Exception as e:
            self.logger.debug(f"State update error: {e}")
    
    def _perform_aggregation(self):
        """Perform 60-second aggregation and persist to DB"""
        try:
            with self._counter_lock:
                key_count = self._key_count
                click_count = self._click_count
                backspace_count = self._backspace_count
                mouse_distance = int(self._mouse_distance)
                scroll_steps = self._scroll_steps
                self._key_count = 0
                self._click_count = 0
                self._backspace_count = 0
                self._mouse_distance = 0.0
                self._scroll_steps = 0
            apm = key_count + click_count
            correction_rate = backspace_count / key_count if key_count > 0 else 0.0
            cognitive_friction = 'CLEAR' if correction_rate < self.CORRECTION_THRESHOLDS['CLEAR'] else ('HESITATION' if correction_rate < self.CORRECTION_THRESHOLDS['HESITATION'] else 'GRIDLOCK')
            state_label, fp_multiplier = self._determine_state(apm, mouse_distance)
            self._handle_phantom_recovery(state_label)
            full_brain = self._calculate_full_brain_state()
            effective_fp = full_brain.get('effective_fp')
            self.current_state = {'state_label': state_label, 'cognitive_friction': cognitive_friction, 'apm': apm, 'mouse_pixels': mouse_distance, 'correction_rate': round(correction_rate, 4), 'fp_multiplier': fp_multiplier, 'phantom_recovery': round(self._phantom_recovery_accumulated, 2), 'phantom_recovery_sum': round(self._phantom_recovery_sum, 2), 'mouse_pixels_cumulative': round(self._session_mouse_total, 1), 'backspace_count_cumulative': self._session_backspace_total, 'key_count_cumulative': self._session_key_total, 'scroll_steps_cumulative': self._session_scroll_total, 'effective_fp': effective_fp, 'current_load': full_brain.get('current_load', 0.0), 'estimated_readiness': full_brain.get('estimated_readiness', 75.0), 'activity_state': full_brain.get('activity_state', 'IDLE'), 'boost_fp': full_brain.get('boost_fp', 0.0), 'base_fp': full_brain.get('base_fp', 75.0), 'debt': full_brain.get('debt', 0.0), 'continuous_work_hours': full_brain.get('continuous_work_hours', 0.0), 'status_code': full_brain.get('status_code', 'INITIALIZING'), 'status_sub': full_brain.get('status_sub', ''), 'recommended_break_iso': full_brain.get('recommended_break_iso'), 'exhaustion_iso': full_brain.get('exhaustion_iso'), 'recovery_ceiling': full_brain.get('recovery_ceiling', 100.0), 'stress_index': full_brain.get('stress_index', 0.0), 'recovery_efficiency': full_brain.get('recovery_efficiency', 1.0), 'decay_multiplier': full_brain.get('decay_multiplier', 1.0), 'hours_since_wake': full_brain.get('hours_since_wake', 0.0), 'boost_efficiency': full_brain.get('boost_efficiency', 1.0), 'correction_factor': full_brain.get('correction_factor', 1.0), 'current_hr': full_brain.get('current_hr'), 'hr_stress_factor': full_brain.get('hr_stress_factor', 1.0), 'current_mouse_speed': full_brain.get('current_mouse_speed', 0.0), 'recent_correction_rate': full_brain.get('recent_correction_rate', 0.0), 'is_shisha_active': full_brain.get('is_shisha_active', False), 'prediction': full_brain.get('prediction', {'continue': [], 'rest': []})}
            if self.db:
                try:
                    self.db.log_tactile_data({'timestamp': datetime.now().isoformat(), 'apm': apm, 'mouse_pixels': mouse_distance, 'correction_rate': correction_rate, 'state_label': state_label, 'cognitive_friction': cognitive_friction, 'key_count': key_count, 'click_count': click_count, 'backspace_count': backspace_count, 'effective_fp': effective_fp})
                except Exception as db_err:
                    self.logger.warning(f"DB log failed: {db_err}")
            fp_str = f"{effective_fp:.1f}" if effective_fp is not None else "N/A"
            self.logger.info(f"Tactile: APM={apm}, Mouse={mouse_distance}px, State={state_label}, FP={fp_str}")
        except Exception as e:
            self.logger.error(f"Aggregation failed: {e}")
            traceback.print_exc()
    
    def _calculate_fp_via_engine(self) -> Optional[float]:
        result = self._calculate_full_brain_state()
        return result.get('effective_fp') if result else None
    def _calculate_full_brain_state(self) -> Dict:
        """Calculate full brain state using BioEngine (DB-centric SSOT)"""
        result = {'effective_fp': 75.0, 'current_load': 0.0, 'estimated_readiness': 75.0, 'activity_state': 'IDLE', 'boost_fp': 0.0, 'base_fp': 75.0, 'debt': 0.0, 'continuous_work_hours': 0.0, 'status_code': 'INITIALIZING', 'status_sub': '', 'recommended_break_iso': None, 'exhaustion_iso': None, 'recovery_ceiling': 100.0, 'stress_index': 0.0, 'recovery_efficiency': 1.0, 'decay_multiplier': 1.0, 'hours_since_wake': 0.0, 'boost_efficiency': 1.0, 'correction_factor': 1.0, 'current_hr': None, 'hr_stress_factor': 1.0, 'prediction': {'continue': [], 'rest': []}}
        if self._telemetry_engine is None:
            return result
        try:
            state_data = self.db.get_combined_state() if self.db else {}
            oura_details = state_data.get('oura_details', {})
            readiness = state_data.get('last_oura_score') or 75
            self._telemetry_engine.set_readiness(readiness)
            sleep_score = oura_details.get('sleep_score')
            if sleep_score:
                self._telemetry_engine.set_sleep_score(sleep_score)
            rhr = oura_details.get('true_rhr')
            if rhr:
                self._telemetry_engine.set_baseline_hr(rhr)
            wake_anchor_iso = oura_details.get('wake_anchor_iso')
            if wake_anchor_iso:
                try:
                    wake_time = datetime.fromisoformat(wake_anchor_iso)
                    if wake_time.tzinfo is None:
                        wake_time = wake_time.replace(tzinfo=JST)
                    self._telemetry_engine.set_wake_time(wake_time)
                except:
                    pass
            main_sleep = oura_details.get('main_sleep_seconds')
            if main_sleep:
                self._telemetry_engine.set_main_sleep_seconds(main_sleep)
            hr_stream = oura_details.get('hr_stream', [])
            current_hr = oura_details.get('current_hr')
            total_nap_minutes = oura_details.get('total_nap_minutes', 0.0) or 0.0
            is_shisha_active = state_data.get('is_shisha_active', False)
            self._telemetry_engine.update(apm=self.current_state.get('apm', 0), cumulative_mouse_pixels=self._session_mouse_total, cumulative_backspace_count=self._session_backspace_total, cumulative_key_count=self._session_key_total, cumulative_scroll_steps=self._session_scroll_total, phantom_recovery_sum=self._phantom_recovery_sum, hr=current_hr, hr_stream=hr_stream, total_nap_minutes=total_nap_minutes, dt_seconds=60.0, is_shisha_active=is_shisha_active, is_hr_estimated=False)
            metrics = self._telemetry_engine.get_health_metrics()
            status_code, status_sub = self._telemetry_engine.get_status_code()
            recommended_break = self._telemetry_engine.get_recommended_break_time()
            exhaustion_time = self._telemetry_engine.get_exhaustion_time()
            prediction_raw = self._telemetry_engine.predict_trajectory(240)
            prediction = {'continue': [{'minutes': i * 5, 'fp': p.fp} for i, p in enumerate(prediction_raw.get('continue', []))], 'rest': [{'minutes': i * 5, 'fp': p.fp} for i, p in enumerate(prediction_raw.get('rest', []))]}
            result.update({'effective_fp': metrics.get('effective_fp', 75.0), 'current_load': metrics.get('current_load', 0.0), 'estimated_readiness': metrics.get('estimated_readiness', 75.0), 'activity_state': metrics.get('activity_state', 'IDLE'), 'boost_fp': metrics.get('boost_fp', 0.0), 'base_fp': metrics.get('base_fp', 75.0), 'debt': metrics.get('debt', 0.0), 'continuous_work_hours': metrics.get('continuous_work_hours', 0.0), 'status_code': status_code, 'status_sub': status_sub, 'recommended_break_iso': recommended_break.isoformat() if recommended_break else None, 'exhaustion_iso': exhaustion_time.isoformat() if exhaustion_time else None, 'recovery_ceiling': metrics.get('recovery_ceiling', 100.0), 'stress_index': metrics.get('stress_index', 0.0), 'recovery_efficiency': metrics.get('recovery_efficiency', 1.0), 'decay_multiplier': metrics.get('decay_multiplier', 1.0), 'hours_since_wake': metrics.get('hours_since_wake', 0.0), 'boost_efficiency': metrics.get('boost_efficiency', 1.0), 'correction_factor': metrics.get('correction_factor', 1.0), 'current_hr': metrics.get('current_hr'), 'hr_stress_factor': metrics.get('hr_stress_factor', 1.0), 'current_mouse_speed': metrics.get('current_mouse_speed', 0.0), 'recent_correction_rate': metrics.get('recent_correction_rate', 0.0), 'is_shisha_active': is_shisha_active, 'prediction': prediction})
        except Exception as e:
            self.logger.debug(f"BioEngine full calc error: {e}")
        return result
    
    def _determine_state(self, apm: int, mouse_distance: int) -> Tuple[str, float]:
        """Determine activity state"""
        thresholds = self.NEURO_THRESHOLDS
        
        if apm < thresholds['idle_apm'] and mouse_distance < thresholds['idle_mouse']:
            return ('IDLE', 0.0)
        
        if apm > thresholds['deep_dive_apm'] and mouse_distance < thresholds['deep_dive_mouse_max']:
            return ('DEEP_DIVE', 1.5)
        
        if mouse_distance > thresholds['scavenging_mouse_min']:
            return ('SCAVENGING', 0.8)
        
        return ('CRUISING', 1.0)
    
    def _determine_state_with_scroll(self, apm: int, mouse_distance: int, 
                                     scroll_steps: int) -> Tuple[str, float]:
        """Determine activity state with scroll consideration"""
        thresholds = self.NEURO_THRESHOLDS
        SCROLL_ACTIVE_THRESHOLD = 3
        
        if apm < thresholds['idle_apm'] and mouse_distance < thresholds['idle_mouse']:
            if scroll_steps >= SCROLL_ACTIVE_THRESHOLD:
                return ('SCAVENGING', 0.8)
            return ('IDLE', 0.0)
        
        if apm > thresholds['deep_dive_apm'] and mouse_distance < thresholds['deep_dive_mouse_max']:
            return ('DEEP_DIVE', 1.5)
        
        if mouse_distance > thresholds['scavenging_mouse_min'] or scroll_steps >= SCROLL_ACTIVE_THRESHOLD:
            return ('SCAVENGING', 0.8)
        
        return ('CRUISING', 1.0)
    
    def _handle_phantom_recovery(self, state_label: str):
        """Handle phantom recovery for long idle periods"""
        now = datetime.now()
        
        if state_label == 'IDLE':
            if self._idle_start is None:
                self._idle_start = now
            else:
                idle_minutes = (now - self._idle_start).total_seconds() / 60
                if idle_minutes >= self.PHANTOM_NAP_THRESHOLD_MINUTES:
                    recovery = (idle_minutes - self.PHANTOM_NAP_THRESHOLD_MINUTES) * self.PHANTOM_RECOVERY_RATE
                    self._phantom_recovery_accumulated = recovery
                    self._phantom_recovery_sum = max(self._phantom_recovery_sum, recovery)
        else:
            self._idle_start = None
            self._phantom_recovery_accumulated = 0.0
    
    def get_current_state(self) -> Dict:
        """Get current state (thread-safe copy)"""
        return self.current_state.copy()
    
    def stop(self):
        """Stop telemetry gracefully"""
        self._running = False
        with self._listener_lock:
            self._stop_listeners_internal()
        self.logger.info("InputTelemetry stopped")


# ==============================================================================
# Oura API Client
# ==============================================================================
class OuraAPIClient:
    """Oura Ring API client with robust error handling"""
    
    BASE_URL = "https://api.ouraring.com/v2/usercollection"
    
    def __init__(self, api_token: str, logger: logging.Logger):
        self.api_token = api_token
        self.logger = logger
        self.headers = {
            'Authorization': f'Bearer {api_token}'
        }
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make API request with error handling"""
        if not REQUESTS_AVAILABLE:
            self.logger.warning("requests library not available")
            return None
        
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            self.logger.warning(f"Oura API timeout ({endpoint})")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Oura API request failed ({endpoint}): {e}")
            return None
        except Exception as e:
            self.logger.error(f"Oura API unexpected error ({endpoint}): {e}")
            return None
    
    @staticmethod
    def parse_utc_timestamp(ts_str: str) -> Optional[datetime]:
        """Parse UTC timestamp to JST datetime"""
        if not ts_str:
            return None
        
        try:
            ts_str = ts_str.replace('Z', '+00:00')
            
            if '.' in ts_str:
                base, rest = ts_str.rsplit('.', 1)
                if '+' in rest:
                    ms, tz = rest.split('+', 1)
                    ts_str = f"{base}.{ms[:3]}+{tz}"
                elif '-' in rest and len(rest) > 6:
                    ms, tz = rest.rsplit('-', 1)
                    ts_str = f"{base}.{ms[:3]}-{tz}"
            
            dt_utc = datetime.fromisoformat(ts_str)
            dt_jst = dt_utc.astimezone(JST)
            return dt_jst
        
        except Exception:
            return None
    
    def get_daily_readiness(self) -> Tuple[Optional[int], bool]:
        """Get readiness score with tolerant date logic"""
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            data = self._make_request("daily_readiness", {"start_date": start_date, "end_date": end_date})
            if data and 'data' in data and len(data['data']) > 0:
                latest = data['data'][-1]
                score = latest.get('score')
                day = latest.get('day', '')
                is_valid = is_data_from_effective_today(day)
                if day:
                    self.logger.info(f"Oura readiness: {score} from {day} (valid={is_valid})")
                return (score, is_valid)
            return (None, False)
        except Exception as e:
            self.logger.error(f"get_daily_readiness failed: {e}")
            return (None, False)
    
    def analyze_heartrate_stream(self) -> Dict:
        """Analyze heartrate stream with datetime query"""
        result = {
            'true_rhr': None,
            'true_rhr_time': None,
            'current_hr': None,
            'current_hr_time': None,
            'wake_anchor': None,
            'nap_segments': [],
            'total_nap_minutes': 0.0,
            'recovery_score': 0.0,
            'hr_stream': [],
            'min_bpm': None,
            'max_bpm': None,
            'main_sleep_seconds': None,
            'max_continuous_rest_seconds': None
        }
        
        try:
            now_utc = datetime.now(UTC)
            end_datetime = now_utc
            start_datetime = now_utc - timedelta(days=30)
            
            params_dt = {
                "start_datetime": start_datetime.isoformat(),
                "end_datetime": end_datetime.isoformat()
            }
            
            hr_data = self._make_request("heartrate", params_dt)
            
            if not hr_data or 'data' not in hr_data or len(hr_data['data']) == 0:
                self.logger.debug("No heartrate data available")
                return result
            
            self.logger.info(f"Heartrate data points: {len(hr_data['data'])}")
            
            hr_points: List[HeartRatePoint] = []
            all_bpms = []
            
            for entry in hr_data['data']:
                ts = self.parse_utc_timestamp(entry.get('timestamp'))
                bpm = entry.get('bpm')
                source = entry.get('source', 'awake')
                if ts and bpm:
                    hr_points.append(HeartRatePoint(ts, bpm, source))
                    all_bpms.append(bpm)
            
            hr_points.sort(key=lambda x: x.timestamp)
            result['hr_stream'] = hr_points
            
            if not all_bpms:
                return result
            
            min_bpm = min(all_bpms)
            result['true_rhr'] = min_bpm
            result['min_bpm'] = min_bpm
            result['max_bpm'] = max(all_bpms)
            
            for point in hr_points:
                if point.bpm == min_bpm:
                    result['true_rhr_time'] = point.timestamp.strftime('%H:%M')
                    break
            
            if hr_points:
                latest = hr_points[-1]
                result['current_hr'] = latest.bpm
                result['current_hr_time'] = latest.timestamp.strftime('%H:%M')
            
            # Wake anchor detection
            wake_anchor = self._detect_wake_anchor(hr_points)
            result['wake_anchor'] = wake_anchor
            
            # Main sleep calculation
            if wake_anchor:
                main_sleep = self._calculate_main_sleep(hr_points, wake_anchor)
                result['main_sleep_seconds'] = main_sleep
            
            # Nap analysis
            if wake_anchor:
                nap_result = self._analyze_naps(hr_points, wake_anchor, min_bpm)
                result['nap_segments'] = nap_result['segments']
                result['total_nap_minutes'] = nap_result['total_minutes']
                result['recovery_score'] = nap_result['recovery_score']
            
            # Max continuous rest
            max_rest = self._calculate_max_continuous_rest(hr_points)
            result['max_continuous_rest_seconds'] = max_rest
            
            return result
        
        except Exception as e:
            self.logger.error(f"Heartrate analysis failed: {e}")
            traceback.print_exc()
            return result
    
    def _detect_wake_anchor(self, hr_points: List[HeartRatePoint]) -> Optional[datetime]:
        """Detect wake time from HR data - prioritize HR stream detection"""
        try:
            now = datetime.now(UTC)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_noon = today_start - timedelta(hours=12)
            recent_hr = [p for p in hr_points if p.timestamp > yesterday_noon]
            if not recent_hr:
                recent_hr = hr_points[-500:] if len(hr_points) > 500 else hr_points
            rest_periods = []
            current_rest_start = None
            for i, point in enumerate(recent_hr):
                if point.source == 'rest':
                    if current_rest_start is None:
                        current_rest_start = point.timestamp
                else:
                    if current_rest_start is not None:
                        rest_periods.append({'start': current_rest_start, 'end': point.timestamp, 'duration': (point.timestamp - current_rest_start).total_seconds()})
                        current_rest_start = None
            if current_rest_start is not None and recent_hr:
                rest_periods.append({'start': current_rest_start, 'end': recent_hr[-1].timestamp, 'duration': (recent_hr[-1].timestamp - current_rest_start).total_seconds()})
            main_sleep_candidates = [r for r in rest_periods if r['duration'] >= 3600]
            if main_sleep_candidates:
                main_sleep = max(main_sleep_candidates, key=lambda r: r['duration'])
                wake_time = main_sleep['end']
                if wake_time.tzinfo is None:
                    wake_time = wake_time.replace(tzinfo=UTC)
                wake_jst = wake_time.astimezone(JST)
                self.logger.info(f"Wake anchor detected from HR stream: {wake_jst.strftime('%Y-%m-%d %H:%M')}")
                return wake_time
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            sleep_data = self._make_request("daily_sleep", {"start_date": start_date, "end_date": end_date})
            if sleep_data and 'data' in sleep_data and len(sleep_data['data']) > 0:
                sorted_data = sorted(sleep_data['data'], key=lambda x: x.get('day', ''), reverse=True)
                latest = sorted_data[0]
                bedtime_end = latest.get('bedtime_end')
                if bedtime_end:
                    api_wake = self.parse_utc_timestamp(bedtime_end)
                    if api_wake and api_wake > yesterday_noon:
                        self.logger.info(f"Wake anchor from API: {api_wake}")
                        return api_wake
            return None
        except Exception as e:
            self.logger.error(f"Wake anchor detection failed: {e}")
            return None
    
    def _calculate_main_sleep(self, hr_points: List[HeartRatePoint], 
                              wake_anchor: datetime) -> Optional[int]:
        """Calculate main sleep duration"""
        try:
            pre_wake = [p for p in hr_points if p.timestamp < wake_anchor]
            
            if len(pre_wake) < 10:
                return None
            
            reversed_points = list(reversed(pre_wake))
            
            sleep_start = None
            consecutive_awake = 0
            last_time = None
            
            for point in reversed_points:
                if last_time is None:
                    last_time = point.timestamp
                    continue
                
                interval = (last_time - point.timestamp).total_seconds() / 60
                
                if point.source == 'awake':
                    consecutive_awake += interval
                    if consecutive_awake >= 120:
                        sleep_start = last_time
                        break
                else:
                    consecutive_awake = 0
                
                last_time = point.timestamp
            
            if sleep_start is None:
                sleep_start = pre_wake[0].timestamp
            
            rest_points = [
                p for p in pre_wake
                if sleep_start <= p.timestamp < wake_anchor and p.source == 'rest'
            ]
            
            if not rest_points:
                return None
            
            return len(rest_points) * 300
        
        except Exception:
            return None
    
    def _analyze_naps(self, hr_points: List[HeartRatePoint], wake_anchor: datetime,
                      true_rhr: int) -> Dict:
        """Analyze post-wake rest periods (naps)"""
        result = {
            'segments': [],
            'total_minutes': 0.0,
            'recovery_score': 0.0
        }
        
        try:
            post_wake_rest = [
                p for p in hr_points
                if p.timestamp > wake_anchor and p.source == 'rest'
            ]
            
            if not post_wake_rest:
                return result
            
            segments = []
            seg_start = post_wake_rest[0].timestamp
            seg_bpms = [post_wake_rest[0].bpm]
            
            for i in range(1, len(post_wake_rest)):
                prev = post_wake_rest[i - 1]
                curr = post_wake_rest[i]
                
                gap = (curr.timestamp - prev.timestamp).total_seconds() / 60
                
                if gap > 10:
                    duration = (prev.timestamp - seg_start).total_seconds() / 60
                    if duration >= 5:
                        segments.append(NapSegment(
                            seg_start, prev.timestamp,
                            sum(seg_bpms) / len(seg_bpms), duration
                        ))
                    seg_start = curr.timestamp
                    seg_bpms = [curr.bpm]
                else:
                    seg_bpms.append(curr.bpm)
            
            # Final segment
            if seg_bpms:
                duration = (post_wake_rest[-1].timestamp - seg_start).total_seconds() / 60
                if duration >= 5:
                    segments.append(NapSegment(
                        seg_start, post_wake_rest[-1].timestamp,
                        sum(seg_bpms) / len(seg_bpms), duration
                    ))
            
            result['segments'] = segments
            result['total_minutes'] = sum(s.duration_minutes for s in segments)
            
            # Recovery score
            for seg in segments:
                if seg.avg_bpm > 0 and true_rhr > 0:
                    ratio = true_rhr / seg.avg_bpm
                    result['recovery_score'] += seg.duration_minutes * 0.1 * (ratio ** 2)
            
            return result
        
        except Exception:
            return result
    
    def _calculate_max_continuous_rest(self, hr_points: List[HeartRatePoint]) -> Optional[int]:
        """Calculate max continuous rest period (5min awake gaps merged)"""
        try:
            if not hr_points:
                return None
            
            sorted_points = sorted(hr_points, key=lambda x: x.timestamp)
            
            segments = []
            seg_start = None
            seg_end = None
            awake_gap_start = None
            GAP_THRESHOLD = 5  # minutes
            
            for point in sorted_points:
                if point.source == 'rest':
                    if seg_start is None:
                        seg_start = point.timestamp
                        seg_end = point.timestamp
                    else:
                        if awake_gap_start is not None:
                            gap = (point.timestamp - awake_gap_start).total_seconds() / 60
                            if gap < GAP_THRESHOLD:
                                seg_end = point.timestamp
                            else:
                                if seg_start and seg_end:
                                    duration = (seg_end - seg_start).total_seconds()
                                    if duration > 0:
                                        segments.append(duration)
                                seg_start = point.timestamp
                                seg_end = point.timestamp
                            awake_gap_start = None
                        else:
                            seg_end = point.timestamp
                else:
                    if seg_start is not None and awake_gap_start is None:
                        awake_gap_start = point.timestamp
            
            # Final segment
            if seg_start and seg_end:
                duration = (seg_end - seg_start).total_seconds()
                if duration > 0:
                    segments.append(duration)
            
            if not segments:
                return None
            
            return int(max(segments))
        
        except Exception:
            return None
    
    def get_detailed_data(self) -> Tuple[Dict, bool]:
        """Get comprehensive Oura data"""
        details = {
            'temperature_deviation': 0.0,
            'sleep_score': 0,
            'stress_high': 0,
            'recovery_high': 0,
            'true_rhr': None,
            'true_rhr_time': None,
            'current_hr': None,
            'current_hr_time': None,
            'wake_anchor_iso': None,
            'nap_segments': [],
            'total_nap_minutes': 0.0,
            'recovery_score': 0.0,
            'hr_stream': [],
            'min_bpm': None,
            'max_bpm': None,
            'main_sleep_seconds': None,
            'max_continuous_rest_seconds': None,
            'contributors': {},
            'data_date': None,
            'is_effective_today': True
        }
        
        is_today = True
        
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            params = {"start_date": start_date, "end_date": end_date}
            
            # Readiness
            r_data = self._make_request("daily_readiness", params)
            if r_data and r_data.get('data'):
                latest = r_data['data'][-1]
                details['temperature_deviation'] = float(latest.get('temperature_deviation', 0.0) or 0.0)
                
                data_day = latest.get('day', '')
                details['data_date'] = data_day
                if not is_data_from_effective_today(data_day):
                    details['is_effective_today'] = False
                    is_today = False
            
            # Sleep
            s_data = self._make_request("daily_sleep", params)
            if s_data and s_data.get('data'):
                latest = s_data['data'][-1]
                details['sleep_score'] = latest.get('score', 0)
                
                contributors = latest.get('contributors', {})
                details['contributors'] = {
                    'efficiency': contributors.get('efficiency'),
                    'restfulness': contributors.get('restfulness'),
                    'deep_sleep': contributors.get('deep_sleep'),
                    'rem_sleep': contributors.get('rem_sleep'),
                    'latency': contributors.get('latency'),
                    'timing': contributors.get('timing'),
                    'total_sleep': contributors.get('total_sleep'),
                }
            
            # Stress
            st_data = self._make_request("daily_stress", params)
            if st_data and st_data.get('data'):
                for record in reversed(st_data['data']):
                    sh = record.get('stress_high', 0) or 0
                    rh = record.get('recovery_high', 0) or 0
                    if sh + rh > 0:
                        details['stress_high'] = sh
                        details['recovery_high'] = rh
                        break
            
            # Heartrate stream
            stream = self.analyze_heartrate_stream()
            
            details['true_rhr'] = stream['true_rhr']
            details['true_rhr_time'] = stream['true_rhr_time']
            details['current_hr'] = stream['current_hr']
            details['current_hr_time'] = stream['current_hr_time']
            details['min_bpm'] = stream['min_bpm']
            details['max_bpm'] = stream['max_bpm']
            details['main_sleep_seconds'] = stream['main_sleep_seconds']
            details['max_continuous_rest_seconds'] = stream['max_continuous_rest_seconds']
            
            details['nap_segments'] = [
                {
                    'start': seg.start.isoformat(),
                    'end': seg.end.isoformat(),
                    'avg_bpm': seg.avg_bpm,
                    'duration_minutes': seg.duration_minutes
                }
                for seg in stream['nap_segments']
            ]
            details['total_nap_minutes'] = stream['total_nap_minutes']
            details['recovery_score'] = stream['recovery_score']
            
            if stream['wake_anchor']:
                details['wake_anchor_iso'] = stream['wake_anchor'].isoformat()
            
            full_hr_stream = stream['hr_stream'] if stream['hr_stream'] else []
            details['hr_stream_full'] = [{'timestamp': p.timestamp.isoformat(), 'bpm': p.bpm, 'source': p.source} for p in full_hr_stream]
            details['hr_stream'] = [{'timestamp': p.timestamp.isoformat(), 'bpm': p.bpm, 'source': p.source} for p in full_hr_stream[-200:]]
            
            return (details, is_today)
        except Exception as e:
            self.logger.error(f"get_detailed_data failed: {e}")
            traceback.print_exc()
            return (details, False)
    def fetch_historical_sleep(self, days: int = 30) -> List[Dict]:
        """Fetch historical sleep data for sleep debt calculation"""
        result = []
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            params = {"start_date": start_date, "end_date": end_date}
            s_data = self._make_request("daily_sleep", params)
            r_data = self._make_request("daily_readiness", params)
            readiness_map = {}
            if r_data and r_data.get('data'):
                for r in r_data['data']:
                    day = r.get('day')
                    if day:
                        readiness_map[day] = r.get('score')
            if s_data and s_data.get('data'):
                for record in s_data['data']:
                    day = record.get('day')
                    if not day:
                        continue
                    contributors = record.get('contributors', {})
                    result.append({'date': day, 'sleep_score': record.get('score'), 'main_sleep_seconds': record.get('total_sleep_duration'), 'readiness_score': readiness_map.get(day), 'sleep_efficiency': contributors.get('efficiency'), 'restfulness': contributors.get('restfulness'), 'deep_sleep': contributors.get('deep_sleep'), 'rem_sleep': contributors.get('rem_sleep')})
            self.logger.info(f"Fetched {len(result)} days of historical sleep data")
            return result
        except Exception as e:
            self.logger.error(f"fetch_historical_sleep failed: {e}")
            return result


# ==============================================================================
# Voice Notifier
# ==============================================================================
class VoiceNotifier:
    """Voice notification system"""
    
    def __init__(self, assets_path: Path, volume: float, logger: logging.Logger):
        self.assets_path = assets_path
        self.volume = volume
        self.logger = logger
        self.is_muted = False
        
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.init()
            except Exception:
                pass
        
        self.voice_files = self._scan()
    
    def _scan(self) -> Dict:
        """Scan voice asset directories"""
        cats = {}
        if not self.assets_path.exists():
            return cats
        
        try:
            for d in self.assets_path.iterdir():
                if d.is_dir():
                    cats[d.name] = {}
                    for f in d.glob("*.mp3"):
                        tag = f.stem.rsplit('_', 1)[0]
                        if tag not in cats[d.name]:
                            cats[d.name][tag] = []
                        cats[d.name][tag].append(f)
        except Exception:
            pass
        
        return cats
    
    def play(self, category: str, tag: str, force: bool = False) -> bool:
        """Play voice file"""
        if self.is_muted and not force:
            return False
        
        if not PYGAME_AVAILABLE:
            return False
        
        try:
            if category in self.voice_files and tag in self.voice_files[category]:
                pygame.mixer.music.load(str(random.choice(self.voice_files[category][tag])))
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
                return True
        except Exception:
            pass
        
        return False
    
    def set_mute(self, muted: bool):
        self.is_muted = muted


# ==============================================================================
# Bio Feedback Scheduler
# ==============================================================================
class BioFeedbackScheduler:
    """Schedule bio feedback notifications"""
    
    def __init__(self, config: Dict, notifier: VoiceNotifier, logger: logging.Logger):
        self.config = config
        self.notifier = notifier
        self.logger = logger
        self.next_exec: Dict[str, datetime] = {}
        
        bio_config = config.get('bio_feedback', {})
        if bio_config.get('break', {}).get('enabled', True):
            self._schedule('break')
    
    def _schedule(self, key: str):
        """Schedule next execution"""
        bio_config = self.config.get('bio_feedback', {})
        settings = bio_config.get(key, {})
        minutes = random.randint(
            settings.get('min_interval_minutes', 45),
            settings.get('max_interval_minutes', 90)
        )
        self.next_exec[key] = datetime.now() + timedelta(minutes=minutes)
    
    def check_and_execute(self, present: bool):
        """Check and execute scheduled notifications"""
        if not present:
            return
        
        now = datetime.now()
        for key, scheduled_time in list(self.next_exec.items()):
            if now >= scheduled_time:
                self.notifier.play('health', key)
                self._schedule(key)


# ==============================================================================
# State Manager
# ==============================================================================
class StateManager:
    """v6.1.0: DB-centric State Manager (SSOT)"""
    def __init__(self, db: 'LifeOSDatabase', logger: logging.Logger):
        self.db = db
        self.logger = logger
        self._lock = threading.Lock()
        self._cache = {}
    def load(self):
        """Load state from DB"""
        with self._lock:
            self._cache = self.db.get_daemon_state()
    def save(self):
        """Compatibility - no-op"""
        pass
    def update(self, **kwargs):
        """Update daemon_state in DB (filters out complex objects)"""
        with self._lock:
            oura_details = kwargs.pop('oura_details', None)
            if oura_details:
                self.db.update_oura_cache(oura_details)
            db_kwargs = {k: v for k, v in kwargs.items() if k in ('daemon_running', 'daemon_pid', 'gui_running', 'is_muted', 'is_shisha_active', 'is_sleeping', 'user_present', 'idle_seconds', 'momentum_minutes', 'current_mode', 'last_oura_score', 'is_data_effective_today', 'current_shisha_session_id')}
            if db_kwargs:
                self.db.update_daemon_state(**db_kwargs)
            self._cache.update(kwargs)
            if oura_details:
                self._cache['oura_details'] = oura_details
    def update_brain_state(self, brain_state: Dict):
        """Save brain metrics to DB"""
        self.db.save_brain_metrics(brain_state)
        with self._lock:
            self._cache['brain_state'] = brain_state
    def update_oura_cache(self, oura_details: Dict):
        """Save Oura cache to DB"""
        self.db.update_oura_cache(oura_details)
    @property
    def state(self) -> Dict:
        """Get current state (for backward compatibility)"""
        return self._cache


# ==============================================================================
# Main Daemon
# ==============================================================================
class LifeOSDaemon:
    """Main daemon process - DB-centric SSOT Writer"""
    VERSION = "6.2.8"
    SHADOW_HR_SAVE_INTERVAL = 60
    def __init__(self):
        config_path = get_config_path()
        self.config = safe_read_json(config_path, {}, None)
        if not self.config:
            print(f"ERROR: Failed to load config from {config_path}")
            sys.exit(1)
        self.logger = setup_logger()
        self.logger.info("=" * 60)
        self.logger.info(f"Life OS Daemon v{self.VERSION} Starting...")
        self.logger.info(f"Root Path: {ROOT_PATH}")
        self.logger.info(f"DB Path: {get_db_path()}")
        self.logger.info("DB-centric SSOT Architecture")
        self.logger.info("=" * 60)
        try:
            self.db = LifeOSDatabase(str(get_db_path()), self.logger)
        except Exception as e:
            self.logger.error(f"Database init failed: {e}")
            self.db = None
            sys.exit(1)
        self.state = StateManager(self.db, self.logger)
        self.state.load()
        system_config = self.config.get('system', {})
        idle_threshold = system_config.get('idle_threshold_minutes', 10)
        volume = system_config.get('volume', 1.0)
        self.monitor = ActivityMonitor(idle_threshold, self.logger)
        oura_config = self.config.get('oura', {})
        api_token = oura_config.get('api_token', '')
        self.oura = OuraAPIClient(api_token, self.logger) if api_token else None
        self.voice = VoiceNotifier(get_voice_assets_path(), volume, self.logger)
        self.sched = BioFeedbackScheduler(self.config, self.voice, self.logger)
        self.telemetry = InputTelemetry(self.db, self.logger)
        self.shadow_hr = ShadowHeartrate(get_state_path()) if SHADOW_HR_AVAILABLE else None
        self._last_shadow_hr_save: Optional[datetime] = None
        self._base_hr = oura_config.get('rhr', 50)
    def _process_command_queue(self):
        """Process commands from DB command_queue"""
        if not self.db:
            return
        commands = self.db.pop_commands()
        if not commands:
            return
        for cmd in commands:
            try:
                cmd_type = cmd.get('cmd', '')
                ts_str = cmd.get('timestamp')
                ts = datetime.fromisoformat(ts_str) if ts_str else now_jst()
                value = cmd.get('value')
                if cmd_type == CommandType.SHISHA_START.value or cmd_type == 'SHISHA_START':
                    session_id = self.db.start_shisha_session(ts)
                    self.state.update(current_shisha_session_id=session_id, is_shisha_active=True)
                    self.logger.info(f"SHISHA_START: session_id={session_id}")
                elif cmd_type == CommandType.SHISHA_STOP.value or cmd_type == 'SHISHA_END':
                    session_id = self.state.state.get('current_shisha_session_id')
                    completed = value if value is not None else True
                    if session_id:
                        self.db.end_shisha_session(session_id, ts, completed)
                        self.logger.info(f"SHISHA_STOP: session_id={session_id}, completed={completed}")
                    self.state.update(current_shisha_session_id=None, is_shisha_active=False)
                elif cmd_type == 'SHISHA_RECOVER':
                    incomplete = self.db.get_incomplete_shisha_session()
                    if incomplete:
                        self.db.end_shisha_session(incomplete['id'], ts, completed=False)
                        self.logger.info(f"SHISHA_RECOVER: closed session {incomplete['id']}")
                elif cmd_type == CommandType.WAKE_MONITORS.value or cmd_type == 'WAKE_MONITORS':
                    self.state.update(is_sleeping=False)
                    self.logger.info("WAKE_MONITORS: is_sleeping=False")
                elif cmd_type == CommandType.SLEEP_DETECTED.value or cmd_type == 'SLEEP_DETECTED':
                    self.state.update(is_sleeping=True)
                    self.logger.info("SLEEP_DETECTED: is_sleeping=True")
                elif cmd_type == CommandType.SET_GUI_RUNNING.value or cmd_type == 'SET_GUI_RUNNING':
                    self.state.update(gui_running=bool(value))
                    self.logger.info(f"SET_GUI_RUNNING: {value}")
                elif cmd_type == CommandType.SET_MUTE.value or cmd_type == 'SET_MUTE':
                    self.state.update(is_muted=bool(value))
                    self.logger.info(f"SET_MUTE: {value}")
                elif cmd_type == CommandType.FORCE_OURA_REFRESH.value or cmd_type == 'FORCE_OURA_REFRESH':
                    self.logger.info("FORCE_OURA_REFRESH: triggering immediate update")
            except Exception as e:
                self.logger.error(f"Command processing error: {cmd_type} - {e}")
    def _update_shadow_hr(self, brain_state: Dict) -> Dict:
        """Calculate Shadow HR prediction and persist to DB (60s interval)"""
        if not self.shadow_hr:
            return brain_state
        try:
            apm = brain_state.get('apm', 0)
            mouse_px = brain_state.get('mouse_pixels', 0)
            momentum = self.state.state.get('momentum_minutes', 0)
            work_hours = momentum / 60.0
            oura_details = self.state.state.get('oura_details', {})
            current_hr = oura_details.get('current_hr')
            hr_stream = oura_details.get('hr_stream', [])
            last_hr_ts = None
            if hr_stream:
                last_entry = hr_stream[-1] if hr_stream else None
                if last_entry and last_entry.get('source') == 'oura':
                    try:
                        last_hr_ts = datetime.fromisoformat(last_entry['timestamp'])
                    except:
                        pass
            now = datetime.now(JST)
            is_hr_stale = last_hr_ts is None or (now - last_hr_ts).total_seconds() > 300
            if is_hr_stale:
                estimated_hr = self.shadow_hr.predict(self._base_hr, apm, mouse_px / 60.0, work_hours)
                brain_state['estimated_hr'] = estimated_hr
                brain_state['is_hr_estimated'] = True
                if self.db:
                    should_save = (self._last_shadow_hr_save is None or (now - self._last_shadow_hr_save).total_seconds() >= 60)
                    if should_save:
                        self.db.log_shadow_hr(now, int(estimated_hr))
                        self._last_shadow_hr_save = now
                        self.logger.debug(f"Shadow HR persisted: {estimated_hr} bpm")
            else:
                brain_state['estimated_hr'] = current_hr
                brain_state['is_hr_estimated'] = False
        except Exception as e:
            self.logger.debug(f"Shadow HR update error: {e}")
        return brain_state
    
    def _determine_mode(self, score: int) -> str:
        if score >= 85:
            return 'high'
        elif score >= 60:
            return 'mid'
        elif score >= 31:
            return 'low'
        else:
            return 'critical'
    
    def _set_next_break(self, mode: str):
        intervals = {'high': 25, 'mid': 50, 'low': 30, 'critical': 15}
        minutes = intervals.get(mode, 50)
        next_break = datetime.now() + timedelta(minutes=minutes)
        self.state.state['next_break_timestamp'] = next_break.isoformat()
    
    def _save_to_database(self, score: int, details: Dict):
        """Save data to database with sleep correction"""
        if not self.db:
            return
        
        try:
            stress_high = details.get('stress_high', 0) or 0
            recovery_high = details.get('recovery_high', 0) or 0
            stress_ratio = stress_high / (stress_high + recovery_high) if (stress_high + recovery_high) > 0 else 0.0
            
            effective_date = get_oura_effective_date()
            contributors = details.get('contributors', {})
            
            # Sleep correction
            main_sleep = details.get('main_sleep_seconds') or 0
            max_rest = details.get('max_continuous_rest_seconds') or 0
            total_nap = details.get('total_nap_minutes', 0.0) or 0.0
            
            if main_sleep < 1800:  # < 30 min
                if max_rest > main_sleep:
                    self.logger.info(f"Sleep correction: {main_sleep}s -> {max_rest}s (max_rest)")
                    main_sleep = max_rest
                    details['main_sleep_seconds'] = main_sleep
                elif total_nap > 0:
                    corrected = int(total_nap * 60)
                    if corrected > main_sleep:
                        self.logger.info(f"Sleep correction: {main_sleep}s -> {corrected}s (nap)")
                        main_sleep = corrected
                        details['main_sleep_seconds'] = main_sleep
            
            db_data = {
                'date': effective_date.isoformat(),
                'readiness_score': score,
                'sleep_score': details.get('sleep_score'),
                'main_sleep_seconds': main_sleep,
                'true_rhr': details.get('true_rhr'),
                'total_nap_minutes': total_nap,
                'stress_ratio': stress_ratio,
                'recovery_score': details.get('recovery_score'),
                'sleep_efficiency': contributors.get('efficiency'),
                'restfulness': contributors.get('restfulness'),
                'deep_sleep': contributors.get('deep_sleep'),
                'rem_sleep': contributors.get('rem_sleep'),
            }
            
            self.db.upsert_daily_log(db_data)
            
            hr_stream = details.get('hr_stream_full') or details.get('hr_stream', [])
            if hr_stream:
                try:
                    saved = self.db.log_heartrate_stream(hr_stream, auto_purge_shadow=True)
                    if saved > 0:
                        self.logger.debug(f"Persisted {saved} HR records (shadow auto-purged)")
                except Exception as e:
                    self.logger.warning(f"HR stream persist failed: {e}")
        
        except Exception as e:
            self.logger.error(f"Database save failed: {e}")
    
    def run(self):
        """Main daemon loop"""
        write_pid_file(self.logger)
        
        def cleanup():
            self.logger.info("Cleanup: Removing PID file...")
            self.state.update(daemon_running=False, daemon_pid=None)
            remove_pid_file(self.logger)
            self.monitor.stop()
            self.telemetry.stop()
        
        atexit.register(cleanup)
        
        # Signal handlers (Unix)
        if sys.platform != 'win32':
            def signal_handler(signum, frame):
                self.logger.info(f"Received signal {signum}")
                raise SystemExit(0)
            
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
        
        self.logger.info("Daemon running...")
        last_oura_update = datetime.min
        
        # Initial data fetch
        score = 70
        is_today = True
        details = {}
        
        if self.oura:
            try:
                score_result = self.oura.get_daily_readiness()
                score = score_result[0] if score_result[0] else 70
                is_today = score_result[1] if len(score_result) > 1 else True
                details, _ = self.oura.get_detailed_data()
                self._save_to_database(score, details)
                historical = self.oura.fetch_historical_sleep(30)
                for record in historical:
                    try:
                        self.db.upsert_daily_log(record)
                    except Exception:
                        pass
            except Exception as e:
                self.logger.error(f"Initial Oura fetch failed: {e}")
        
        mode = self._determine_mode(score)
        self._set_next_break(mode)
        self.state.update(
            daemon_running=True,
            daemon_pid=os.getpid(),
            last_oura_score=score,
            oura_details=details,
            current_mode=mode,
            is_data_effective_today=is_today
        )
        initial_brain_state = self.telemetry.get_current_state()
        self.state.update_brain_state(initial_brain_state)
        self._process_command_queue()
        last_oura_update = datetime.now()
        try:
            while True:
                loop_start = datetime.now()
                self._process_command_queue()
                self.state.load()
                if not self.state.state.get('gui_running', True):
                    self.logger.info("GUI closed. Shutting down...")
                    break
                momentum = self.monitor.get_momentum_minutes()
                brain_state = self.telemetry.get_current_state()
                brain_state = self._update_shadow_hr(brain_state)
                self.state.update_brain_state(brain_state)
                self.voice.set_mute(self.state.state.get('is_muted', False))
                present = self.monitor.is_user_present()
                self.sched.check_and_execute(present)
                nb_ts = self.state.state.get('next_break_timestamp')
                if nb_ts:
                    try:
                        if datetime.now() >= datetime.fromisoformat(nb_ts):
                            current_mode = self.state.state.get('current_mode', 'mid')
                            self._set_next_break(current_mode)
                            self.state.save()
                    except Exception:
                        pass
                
                # 5-minute Oura poll
                if self.oura and (datetime.now() - last_oura_update).total_seconds() >= POLLING_INTERVAL_SECONDS:
                    self.logger.info("Updating Oura data...")
                    
                    try:
                        score_result = self.oura.get_daily_readiness()
                        if score_result[0]:
                            score = score_result[0]
                            is_today = score_result[1]
                            
                            details, _ = self.oura.get_detailed_data()
                            new_mode = self._determine_mode(score)
                            
                            if new_mode != self.state.state.get('current_mode'):
                                self._set_next_break(new_mode)
                            
                            self._save_to_database(score, details)
                            
                            self.state.update(
                                last_oura_score=score,
                                oura_details=details,
                                current_mode=new_mode,
                                is_data_effective_today=is_today
                            )
                    except Exception as e:
                        self.logger.error(f"Oura update failed: {e}")
                    last_oura_update = datetime.now()
                last_activity_iso = self.monitor.last_activity.replace(tzinfo=JST).isoformat()
                self.state.update(
                    user_present=present,
                    idle_seconds=self.monitor.get_idle_time(),
                    momentum_minutes=momentum,
                    last_activity_iso=last_activity_iso
                )
                elapsed = (datetime.now() - loop_start).total_seconds()
                sleep_time = max(0.1, 1 - elapsed)
                time.sleep(sleep_time)
        
        except KeyboardInterrupt:
            self.logger.info("Stopped by user")
        
        except SystemExit:
            self.logger.info("Exit signal received")
        
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            traceback.print_exc()
        
        finally:
            self.logger.info("Performing cleanup...")
            self.state.update(daemon_running=False, daemon_pid=None)
            remove_pid_file(self.logger)
            self.monitor.stop()
            self.telemetry.stop()
            self.logger.info("Daemon terminated")


# ==============================================================================
# Entry Point
# ==============================================================================
if __name__ == "__main__":
    is_running, existing_pid = is_daemon_running()
    if is_running:
        print(f"Daemon already running (PID: {existing_pid})")
        sys.exit(1)
    
    LifeOSDaemon().run()
