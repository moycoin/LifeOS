#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Life OS Daemon v5.4.1 - Extended Time Horizon (7-Day Fetch)
Location: core/daemon.py
v5.4.1: Oura API取得範囲を7日間に拡大、日付判定緩和
"""

import os
import sys
import json
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

# External dependencies
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


# ==============================================================================
# Path Resolution
# ==============================================================================
def get_root_path() -> Path:
    """Get project root path (parent of core/)"""
    return Path(__file__).parent.parent.resolve()

ROOT_PATH = get_root_path()

if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

# Core module imports (from refactored __init__.py)
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


# ==============================================================================
# Timezone
# ==============================================================================
JST = timezone(timedelta(hours=9))
UTC = timezone.utc


# ==============================================================================
# Constants
# ==============================================================================
POLLING_INTERVAL_SECONDS = 300  # 5 minutes
OURA_DAY_BOUNDARY_HOUR = 4      # Oura day boundary at 4 AM
LISTENER_RESTART_DELAY = 5.0    # Delay before restarting failed listeners
MAX_LISTENER_RESTARTS = 10      # Maximum listener restart attempts


# ==============================================================================
# Data Structures
# ==============================================================================
HeartRatePoint = namedtuple('HeartRatePoint', ['timestamp', 'bpm', 'source'])
NapSegment = namedtuple('NapSegment', ['start', 'end', 'avg_bpm', 'duration_minutes'])


# ==============================================================================
# Path Helpers
# ==============================================================================
def get_config_path() -> Path:
    return ROOT_PATH / "config.json"

def get_state_path() -> Path:
    return ROOT_PATH / "logs" / "daemon_state.json"

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


# ==============================================================================
# Robust JSON Operations (Retry Logic)
# ==============================================================================
def safe_read_json(path: Path, default: Dict = None, logger: logging.Logger = None, 
                   max_retries: int = 3) -> Dict:
    """
    Read JSON with retry logic for permission/access errors.
    """
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
                logger.debug(f"Retry {attempt + 1}/{max_retries} reading {path}: {e}")
            time.sleep(0.1)
        
        except Exception as e:
            if logger:
                logger.error(f"Failed to read {path}: {e}")
            return default.copy()
    
    if logger:
        logger.error(f"All retries failed for {path}: {last_error}")
    return default.copy()


def safe_write_json(path: Path, data: Dict, logger: logging.Logger = None, 
                    max_retries: int = 3) -> bool:
    """
    Write JSON with retry logic and atomic write via temp file.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic write: write to temp file then rename
            temp_path = path.with_suffix('.tmp')
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), 
                encoding='utf-8'
            )
            temp_path.replace(path)
            return True
        
        except (PermissionError, OSError) as e:
            last_error = e
            if logger and attempt < max_retries - 1:
                logger.debug(f"Retry {attempt + 1}/{max_retries} writing {path}: {e}")
            time.sleep(0.1)
        
        except Exception as e:
            if logger:
                logger.error(f"Failed to write {path}: {e}")
            return False
    
    if logger:
        logger.error(f"All retries failed for {path}: {last_error}")
    return False


# ==============================================================================
# 4AM Day Boundary Logic
# ==============================================================================
def get_oura_effective_date() -> date:
    """Get Oura-effective date (before 4AM = previous day)"""
    now = datetime.now()
    if now.hour < OURA_DAY_BOUNDARY_HOUR:
        return (now - timedelta(days=1)).date()
    return now.date()


def is_data_from_effective_today(day_str: str) -> bool:
    """v5.3.1: Tolerant date validation - accept today or yesterday"""
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
    v4.3.0: Robust input telemetry with auto-restart capability.
    
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
                self.logger.info("v4.3.0: BioEngine initialized for Telemetry FP")
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
        
        # Current state (published to daemon_state.json)
        self.current_state = {
            'state_label': 'IDLE',
            'cognitive_friction': 'CLEAR',
            'apm': 0,
            'mouse_pixels': 0,
            'correction_rate': 0.0,
            'fp_multiplier': 1.0,
            'mouse_pixels_cumulative': 0.0,
            'backspace_count_cumulative': 0,
            'key_count_cumulative': 0,
            'phantom_recovery': 0.0,
            'phantom_recovery_sum': 0.0,
            'scroll_steps_cumulative': 0,
            'effective_fp': None
        }
        
        # Phantom Recovery tracking
        self._idle_start: Optional[datetime] = None
        self._phantom_recovery_accumulated = 0.0
        self._phantom_recovery_sum = 0.0
        
        # Listener management
        self._running = True
        self._mouse_listener = None
        self._keyboard_listener = None
        self._listener_restart_count = 0
        self._listener_lock = threading.Lock()
        
        # Start listeners
        self._start_listeners()
        
        # Start aggregation thread
        self._aggregate_thread = threading.Thread(
            target=self._aggregate_loop, 
            daemon=True,
            name="Telemetry-Aggregator"
        )
        self._aggregate_thread.start()
        
        self.logger.info("v4.3.0 InputTelemetry initialized (Robust + Auto-Restart)")
    
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
                
                # Correction rate
                correction_rate = 0.0
                if self._key_count > 0:
                    correction_rate = self._backspace_count / self._key_count
            
            # Cognitive friction
            if correction_rate < self.CORRECTION_THRESHOLDS['CLEAR']:
                cognitive_friction = 'CLEAR'
            elif correction_rate < self.CORRECTION_THRESHOLDS['HESITATION']:
                cognitive_friction = 'HESITATION'
            else:
                cognitive_friction = 'GRIDLOCK'
            
            # State determination
            state_label, fp_multiplier = self._determine_state_with_scroll(
                recent_apm, recent_mouse, recent_scroll
            )
            
            # Phantom recovery
            self._handle_phantom_recovery(state_label)
            
            # Update state dict
            self.current_state = {
                'state_label': state_label,
                'cognitive_friction': cognitive_friction,
                'apm': recent_apm,
                'mouse_pixels': recent_mouse,
                'correction_rate': round(correction_rate, 4),
                'fp_multiplier': fp_multiplier,
                'mouse_pixels_cumulative': round(self._session_mouse_total, 1),
                'backspace_count_cumulative': self._session_backspace_total,
                'key_count_cumulative': self._session_key_total,
                'phantom_recovery': round(self._phantom_recovery_accumulated, 2),
                'phantom_recovery_sum': round(self._phantom_recovery_sum, 2),
                'scroll_steps_cumulative': self._session_scroll_total,
                'effective_fp': self.current_state.get('effective_fp')
            }
            
        except Exception as e:
            self.logger.debug(f"State update error: {e}")
    
    def _perform_aggregation(self):
        """Perform 60-second aggregation and persist to DB"""
        try:
            # Snapshot and reset counters atomically
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
            
            # Calculate metrics
            apm = key_count + click_count
            
            correction_rate = 0.0
            if key_count > 0:
                correction_rate = backspace_count / key_count
            
            # Cognitive friction
            if correction_rate < self.CORRECTION_THRESHOLDS['CLEAR']:
                cognitive_friction = 'CLEAR'
            elif correction_rate < self.CORRECTION_THRESHOLDS['HESITATION']:
                cognitive_friction = 'HESITATION'
            else:
                cognitive_friction = 'GRIDLOCK'
            
            # State
            state_label, fp_multiplier = self._determine_state(apm, mouse_distance)
            
            # Phantom recovery
            self._handle_phantom_recovery(state_label)
            
            # Calculate FP via BioEngine
            effective_fp = self._calculate_fp_via_engine()
            
            # Update current state
            self.current_state = {
                'state_label': state_label,
                'cognitive_friction': cognitive_friction,
                'apm': apm,
                'mouse_pixels': mouse_distance,
                'correction_rate': round(correction_rate, 4),
                'fp_multiplier': fp_multiplier,
                'phantom_recovery': round(self._phantom_recovery_accumulated, 2),
                'phantom_recovery_sum': round(self._phantom_recovery_sum, 2),
                'mouse_pixels_cumulative': round(self._session_mouse_total, 1),
                'backspace_count_cumulative': self._session_backspace_total,
                'key_count_cumulative': self._session_key_total,
                'scroll_steps_cumulative': self._session_scroll_total,
                'effective_fp': effective_fp
            }
            
            # Persist to DB
            if self.db:
                try:
                    self.db.log_tactile_data({
                        'timestamp': datetime.now().isoformat(),
                        'apm': apm,
                        'mouse_pixels': mouse_distance,
                        'correction_rate': correction_rate,
                        'state_label': state_label,
                        'cognitive_friction': cognitive_friction,
                        'key_count': key_count,
                        'click_count': click_count,
                        'backspace_count': backspace_count,
                        'effective_fp': effective_fp
                    })
                except Exception as db_err:
                    self.logger.warning(f"DB log failed: {db_err}")
            
            fp_str = f"{effective_fp:.1f}" if effective_fp is not None else "N/A"
            self.logger.info(
                f"Tactile: APM={apm}, Mouse={mouse_distance}px, "
                f"State={state_label}, FP={fp_str}"
            )
        
        except Exception as e:
            self.logger.error(f"Aggregation failed: {e}")
            traceback.print_exc()
    
    def _calculate_fp_via_engine(self) -> Optional[float]:
        """Calculate FP using BioEngine"""
        effective_fp = None
        
        if self._telemetry_engine is not None:
            try:
                # Load state for Oura data
                state_path = get_state_path()
                state_data = safe_read_json(state_path, {}, self.logger)
                oura_details = state_data.get('oura_details', {})
                readiness = state_data.get('last_oura_score', 75)
                
                # Configure BioEngine
                self._telemetry_engine.set_readiness(readiness)
                
                sleep_score = oura_details.get('sleep_score')
                if sleep_score:
                    self._telemetry_engine.set_sleep_score(sleep_score)
                
                rhr = oura_details.get('true_rhr')
                if rhr:
                    self._telemetry_engine.set_baseline_hr(rhr)
                
                # Get HR data
                hr_stream = oura_details.get('hr_stream', [])
                current_hr = oura_details.get('current_hr')
                total_nap_minutes = oura_details.get('total_nap_minutes', 0.0) or 0.0
                is_shisha_active = state_data.get('is_shisha_active', False)
                
                # Update BioEngine
                self._telemetry_engine.update(
                    apm=self.current_state.get('apm', 0),
                    cumulative_mouse_pixels=self._session_mouse_total,
                    cumulative_backspace_count=self._session_backspace_total,
                    cumulative_key_count=self._session_key_total,
                    cumulative_scroll_steps=self._session_scroll_total,
                    phantom_recovery_sum=self._phantom_recovery_sum,
                    hr=current_hr,
                    hr_stream=hr_stream,
                    total_nap_minutes=total_nap_minutes,
                    dt_seconds=60.0,
                    is_shisha_active=is_shisha_active,
                    is_hr_estimated=False
                )
                
                metrics = self._telemetry_engine.get_health_metrics()
                effective_fp = metrics.get('effective_fp')
                
            except Exception as e:
                self.logger.debug(f"BioEngine FP calc error: {e}")
        
        # Fallback
        if effective_fp is None:
            try:
                state_path = get_state_path()
                state_data = safe_read_json(state_path, {}, self.logger)
                brain_state = state_data.get('brain_state', {})
                effective_fp = brain_state.get('effective_fp')
                
                if effective_fp is None:
                    readiness = state_data.get('last_oura_score', 75)
                    effective_fp = float(readiness) if readiness else 75.0
            except Exception:
                effective_fp = 75.0
        
        return effective_fp
    
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
        """v5.3.1: Get readiness score with tolerant date logic"""
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
            start_datetime = now_utc - timedelta(days=7)
            
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
                source = 'oura'
                
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
        """Detect wake time from HR data"""
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            
            sleep_data = self._make_request("daily_sleep", {
                "start_date": start_date,
                "end_date": end_date
            })
            
            if sleep_data and 'data' in sleep_data and len(sleep_data['data']) > 0:
                latest = sleep_data['data'][-1]
                bedtime_end = latest.get('bedtime_end')
                if bedtime_end:
                    return self.parse_utc_timestamp(bedtime_end)
            
            # Fallback: detect rest->awake transition
            for i in range(1, len(hr_points)):
                prev = hr_points[i - 1]
                curr = hr_points[i]
                if prev.source == 'rest' and curr.source != 'rest':
                    return curr.timestamp
            
            return None
        
        except Exception:
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
            
            hr_stream = stream['hr_stream'][-200:] if stream['hr_stream'] else []
            details['hr_stream'] = [
                {
                    'timestamp': p.timestamp.isoformat(),
                    'bpm': p.bpm,
                    'source': p.source
                }
                for p in hr_stream
            ]
            
            return (details, is_today)
        
        except Exception as e:
            self.logger.error(f"get_detailed_data failed: {e}")
            traceback.print_exc()
            return (details, False)


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
    """Manage daemon_state.json with thread safety"""
    DEFAULT_STATE = {
        'daemon_running': False,
        'daemon_pid': None,
        'gui_running': False,
        'is_muted': False,
        'last_oura_score': None,
        'oura_details': {},
        'current_mode': 'mid',
        'next_break_timestamp': None,
        'is_shisha_active': False,
        'user_present': True,
        'idle_seconds': 0,
        'momentum_minutes': 0,
        'is_data_effective_today': True,
        'last_activity_iso': None,
        'command_queue': [],
        'current_shisha_session_id': None,
        'brain_state': {
            'state_label': 'IDLE',
            'cognitive_friction': 'CLEAR',
            'apm': 0,
            'mouse_pixels': 0,
            'correction_rate': 0.0,
            'fp_multiplier': 1.0,
            'mouse_pixels_cumulative': 0.0,
            'backspace_count_cumulative': 0,
            'key_count_cumulative': 0,
            'phantom_recovery': 0.0,
            'phantom_recovery_sum': 0.0,
            'scroll_steps_cumulative': 0,
            'effective_fp': None,
            'estimated_hr': None,
            'is_hr_estimated': False
        }
    }
    
    def __init__(self, file: Path, logger: logging.Logger):
        self.file = file
        self.logger = logger
        self.state = self.DEFAULT_STATE.copy()
        self._lock = threading.Lock()
    
    def load(self):
        """Load state from file"""
        with self._lock:
            loaded = safe_read_json(self.file, self.DEFAULT_STATE, self.logger)
            self.state.update(loaded)
    
    def save(self):
        """Save state to file"""
        with self._lock:
            safe_write_json(self.file, self.state, self.logger)
    
    def update(self, **kwargs):
        """Update state and persist"""
        with self._lock:
            self.state.update(kwargs)
        self.save()
    
    def update_brain_state(self, brain_state: Dict):
        """Update brain_state specifically (guaranteed persistence)"""
        with self._lock:
            if 'brain_state' not in self.state:
                self.state['brain_state'] = {}
            self.state['brain_state'].update(brain_state)
        self.save()


# ==============================================================================
# Main Daemon
# ==============================================================================
class LifeOSDaemon:
    """Main daemon process - SSOT Writer"""
    VERSION = "5.0.0"
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
        self.logger.info(f"State Path: {get_state_path()}")
        self.logger.info("v5.0.0: SSOT Writer + Command Queue")
        self.logger.info("=" * 60)
        self.state = StateManager(get_state_path(), self.logger)
        self.state.load()
        try:
            self.db = LifeOSDatabase(str(get_db_path()), self.logger)
        except Exception as e:
            self.logger.error(f"Database init failed: {e}")
            self.db = None
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
        """Process GUI command queue (SSOT: daemon handles all DB writes)"""
        with self.state._lock:
            queue = self.state.state.get('command_queue', [])
            if not queue:
                return
            processed = []
            for cmd in queue:
                try:
                    cmd_type = cmd.get('cmd')
                    ts_str = cmd.get('ts')
                    ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(JST)
                    if cmd_type == 'SHISHA_START':
                        if self.db:
                            session_id = self.db.start_shisha_session(ts)
                            self.state.state['current_shisha_session_id'] = session_id
                            self.state.state['is_shisha_active'] = True
                            self.logger.info(f"SHISHA_START: session_id={session_id}")
                    elif cmd_type == 'SHISHA_END':
                        session_id = self.state.state.get('current_shisha_session_id')
                        completed = cmd.get('completed', True)
                        if self.db and session_id:
                            self.db.end_shisha_session(session_id, ts, completed)
                            self.logger.info(f"SHISHA_END: session_id={session_id}, completed={completed}")
                        self.state.state['current_shisha_session_id'] = None
                        self.state.state['is_shisha_active'] = False
                    elif cmd_type == 'SHISHA_RECOVER':
                        if self.db:
                            incomplete = self.db.get_incomplete_shisha_session()
                            if incomplete:
                                self.db.end_shisha_session(incomplete['id'], ts, completed=False)
                                self.logger.info(f"SHISHA_RECOVER: closed session {incomplete['id']}")
                    processed.append(cmd)
                except Exception as e:
                    self.logger.error(f"Command processing error: {cmd} - {e}")
                    processed.append(cmd)
            self.state.state['command_queue'] = [c for c in queue if c not in processed]
        self.state.save()
    def _update_shadow_hr(self, brain_state: Dict) -> Dict:
        """Calculate and persist Shadow HR prediction"""
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
                if last_entry and last_entry.get('source') != 'shadow':
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
                should_save = (self._last_shadow_hr_save is None or 
                              (now - self._last_shadow_hr_save).total_seconds() >= self.SHADOW_HR_SAVE_INTERVAL)
                if should_save and self.db:
                    self.db.log_heartrate_stream([{'timestamp': now.isoformat(), 'bpm': estimated_hr, 'source': 'shadow'}])
                    self._last_shadow_hr_save = now
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
            
            hr_stream = details.get('hr_stream', [])
            if hr_stream:
                try:
                    # SSOT: log_heartrate_stream内でshadow自動削除が実行される
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
        
        last_oura_update = datetime.now()
        
        try:
            while True:
                loop_start = datetime.now()
                
                self.state.load()
                
                # Check GUI status
                if not self.state.state.get('gui_running', True):
                    self.logger.info("GUI closed. Shutting down...")
                    break
                
                self.voice.set_mute(self.state.state.get('is_muted', False))
                
                present = self.monitor.is_user_present()
                self.sched.check_and_execute(present)
                
                # Auto-update next break
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
                
                # Update state
                momentum = self.monitor.get_momentum_minutes()
                brain_state = self.telemetry.get_current_state()
                brain_state = self._update_shadow_hr(brain_state)
                last_activity_iso = self.monitor.last_activity.replace(tzinfo=JST).isoformat()
                self._process_command_queue()
                self.state.update_brain_state(brain_state)
                self.state.update(
                    user_present=present,
                    idle_seconds=self.monitor.get_idle_time(),
                    momentum_minutes=momentum,
                    last_activity_iso=last_activity_iso
                )
                
                elapsed = (datetime.now() - loop_start).total_seconds()
                sleep_time = max(0.1, 5 - elapsed)
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
