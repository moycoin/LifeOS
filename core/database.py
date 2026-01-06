#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LifeOS Database v6.1.0 - DB-centric SSOT Architecture
import sqlite3
import json
import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any
from .types import get_root_path, get_db_path, __version__, JST
DATABASE_VERSION = '6.1.0'
class LifeOSDatabase:
    def __init__(self, db_path: Optional[Union[str, Path]] = None, logger: Optional[logging.Logger] = None):
        self.db_path = Path(db_path) if isinstance(db_path, str) else (db_path if db_path else get_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)
        self.init_db()
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn
    def init_db(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS daily_logs (date TEXT PRIMARY KEY,readiness_score INTEGER,sleep_score INTEGER,main_sleep_seconds INTEGER,true_rhr REAL,sleep_efficiency REAL,restfulness REAL,deep_sleep REAL,rem_sleep REAL,updated_at TEXT)''')
                cursor.execute('''CREATE TABLE IF NOT EXISTS tactile_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,keystrokes INTEGER DEFAULT 0,clicks INTEGER DEFAULT 0,scroll_delta INTEGER DEFAULT 0,apm REAL DEFAULT 0,mouse_pixels REAL DEFAULT 0,correction_rate REAL DEFAULT 0,state_label TEXT,effective_fp REAL)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_tactile_timestamp ON tactile_logs(timestamp)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS heartrate_logs (timestamp TEXT PRIMARY KEY,bpm INTEGER NOT NULL,source TEXT DEFAULT 'oura')''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_hr_timestamp ON heartrate_logs(timestamp)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_hr_source ON heartrate_logs(source)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS shisha_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,start_time TEXT NOT NULL,end_time TEXT,duration_seconds INTEGER,completed INTEGER DEFAULT 0)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_shisha_start ON shisha_logs(start_time)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS daemon_state (id INTEGER PRIMARY KEY CHECK (id = 1),daemon_running INTEGER DEFAULT 0,daemon_pid INTEGER,gui_running INTEGER DEFAULT 0,is_muted INTEGER DEFAULT 0,is_shisha_active INTEGER DEFAULT 0,is_sleeping INTEGER DEFAULT 0,user_present INTEGER DEFAULT 1,idle_seconds REAL DEFAULT 0,momentum_minutes INTEGER DEFAULT 0,current_mode TEXT DEFAULT 'mid',last_oura_score INTEGER,is_data_effective_today INTEGER DEFAULT 1,current_shisha_session_id INTEGER,updated_at TEXT)''')
                cursor.execute('''CREATE TABLE IF NOT EXISTS brain_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,effective_fp REAL,current_load REAL,estimated_readiness REAL,activity_state TEXT,status_code TEXT,status_sub TEXT,recommended_break_iso TEXT,exhaustion_iso TEXT,base_fp REAL,boost_fp REAL,debt REAL,continuous_work_hours REAL,estimated_hr INTEGER,is_hr_estimated INTEGER DEFAULT 0,stress_index REAL,recovery_efficiency REAL,recovery_ceiling REAL,decay_multiplier REAL,hours_since_wake REAL,boost_efficiency REAL,correction_factor REAL,hr_stress_factor REAL,current_mouse_speed REAL,recent_correction_rate REAL,prediction_json TEXT,apm REAL,mouse_pixels REAL,phantom_recovery REAL,phantom_recovery_sum REAL,state_label TEXT)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_brain_timestamp ON brain_metrics(timestamp)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS command_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,cmd TEXT NOT NULL,value TEXT,timestamp TEXT NOT NULL,processed INTEGER DEFAULT 0)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_cmd_processed ON command_queue(processed)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS oura_cache (id INTEGER PRIMARY KEY CHECK (id = 1),temperature_deviation REAL,sleep_score INTEGER,stress_high INTEGER,recovery_high INTEGER,true_rhr INTEGER,true_rhr_time TEXT,current_hr INTEGER,current_hr_time TEXT,wake_anchor_iso TEXT,total_nap_minutes REAL,recovery_score REAL,min_bpm INTEGER,max_bpm INTEGER,main_sleep_seconds INTEGER,max_continuous_rest_seconds INTEGER,data_date TEXT,is_effective_today INTEGER DEFAULT 1,contributors_json TEXT,nap_segments_json TEXT,hr_stream_json TEXT,updated_at TEXT)''')
                self._migrate_daily_logs(cursor)
                self._migrate_tactile_logs(cursor)
                self._ensure_daemon_state_row(cursor)
                self._ensure_oura_cache_row(cursor)
                conn.commit()
        except Exception as e:
            self.logger.error(f"Database init failed: {e}")
    def _migrate_daily_logs(self, cursor):
        cursor.execute("PRAGMA table_info(daily_logs)")
        cols = {row[1] for row in cursor.fetchall()}
        for col, dtype in [('true_rhr', 'REAL'), ('sleep_efficiency', 'REAL'), ('restfulness', 'REAL'), ('deep_sleep', 'REAL'), ('rem_sleep', 'REAL')]:
            if col not in cols:
                try:
                    cursor.execute(f'ALTER TABLE daily_logs ADD COLUMN {col} {dtype}')
                except sqlite3.OperationalError:
                    pass
    def _migrate_tactile_logs(self, cursor):
        cursor.execute("PRAGMA table_info(tactile_logs)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'effective_fp' not in cols:
            try:
                cursor.execute('ALTER TABLE tactile_logs ADD COLUMN effective_fp REAL')
            except sqlite3.OperationalError:
                pass
    def _ensure_daemon_state_row(self, cursor):
        cursor.execute('INSERT OR IGNORE INTO daemon_state (id) VALUES (1)')
    def _ensure_oura_cache_row(self, cursor):
        cursor.execute('INSERT OR IGNORE INTO oura_cache (id) VALUES (1)')
    def get_daemon_state(self) -> Dict:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daemon_state'")
                if not cursor.fetchone():
                    return {}
                cursor.execute('SELECT * FROM daemon_state WHERE id = 1')
                row = cursor.fetchone()
                if row:
                    return {k: bool(row[k]) if k in ('daemon_running', 'gui_running', 'is_muted', 'is_shisha_active', 'is_sleeping', 'user_present', 'is_data_effective_today') else row[k] for k in row.keys()}
                return {}
        except Exception as e:
            self.logger.error(f"Failed to get daemon state: {e}")
            return {}
    def update_daemon_state(self, **kwargs) -> bool:
        if not kwargs:
            return True
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daemon_state'")
                if not cursor.fetchone():
                    cursor.execute('''CREATE TABLE IF NOT EXISTS daemon_state (id INTEGER PRIMARY KEY CHECK (id = 1),daemon_running INTEGER DEFAULT 0,daemon_pid INTEGER,gui_running INTEGER DEFAULT 0,is_muted INTEGER DEFAULT 0,is_shisha_active INTEGER DEFAULT 0,is_sleeping INTEGER DEFAULT 0,user_present INTEGER DEFAULT 1,idle_seconds REAL DEFAULT 0,momentum_minutes INTEGER DEFAULT 0,current_mode TEXT DEFAULT 'mid',last_oura_score INTEGER,is_data_effective_today INTEGER DEFAULT 1,current_shisha_session_id INTEGER,updated_at TEXT)''')
                    cursor.execute('INSERT OR IGNORE INTO daemon_state (id) VALUES (1)')
                kwargs['updated_at'] = datetime.now(JST).isoformat()
                cols = ', '.join(f'{k} = ?' for k in kwargs.keys())
                vals = [int(v) if isinstance(v, bool) else v for v in kwargs.values()]
                cursor.execute(f'UPDATE daemon_state SET {cols} WHERE id = 1', vals)
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to update daemon state: {e}")
            return False
    def get_latest_brain_metrics(self) -> Dict:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='brain_metrics'")
                if not cursor.fetchone():
                    return {}
                cursor.execute('SELECT * FROM brain_metrics ORDER BY timestamp DESC LIMIT 1')
                row = cursor.fetchone()
                if row:
                    result = {k: row[k] for k in row.keys()}
                    result['is_hr_estimated'] = bool(result.get('is_hr_estimated', 0))
                    if result.get('prediction_json'):
                        try:
                            result['prediction'] = json.loads(result['prediction_json'])
                        except:
                            result['prediction'] = {'continue': [], 'rest': []}
                    else:
                        result['prediction'] = {'continue': [], 'rest': []}
                    return result
                return {}
        except Exception as e:
            self.logger.error(f"Failed to get latest brain metrics: {e}")
            return {}
    def save_brain_metrics(self, metrics: Dict) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='brain_metrics'")
                if not cursor.fetchone():
                    cursor.execute('''CREATE TABLE IF NOT EXISTS brain_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,effective_fp REAL,current_load REAL,estimated_readiness REAL,activity_state TEXT,status_code TEXT,status_sub TEXT,recommended_break_iso TEXT,exhaustion_iso TEXT,base_fp REAL,boost_fp REAL,debt REAL,continuous_work_hours REAL,estimated_hr INTEGER,is_hr_estimated INTEGER DEFAULT 0,stress_index REAL,recovery_efficiency REAL,recovery_ceiling REAL,decay_multiplier REAL,hours_since_wake REAL,boost_efficiency REAL,correction_factor REAL,hr_stress_factor REAL,current_mouse_speed REAL,recent_correction_rate REAL,prediction_json TEXT,apm REAL,mouse_pixels REAL,phantom_recovery REAL,phantom_recovery_sum REAL,state_label TEXT)''')
                    cursor.execute('CREATE INDEX IF NOT EXISTS idx_brain_timestamp ON brain_metrics(timestamp)')
                else:
                    cursor.execute("PRAGMA table_info(brain_metrics)")
                    cols = [c[1] for c in cursor.fetchall()]
                    if 'state_label' not in cols:
                        cursor.execute('ALTER TABLE brain_metrics ADD COLUMN state_label TEXT')
                prediction = metrics.get('prediction', {})
                prediction_json = json.dumps(prediction) if prediction else None
                cursor.execute('''INSERT INTO brain_metrics (timestamp, effective_fp, current_load, estimated_readiness, activity_state, status_code, status_sub, recommended_break_iso, exhaustion_iso, base_fp, boost_fp, debt, continuous_work_hours, estimated_hr, is_hr_estimated, stress_index, recovery_efficiency, recovery_ceiling, decay_multiplier, hours_since_wake, boost_efficiency, correction_factor, hr_stress_factor, current_mouse_speed, recent_correction_rate, prediction_json, apm, mouse_pixels, phantom_recovery, phantom_recovery_sum, state_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (datetime.now(JST).isoformat(), metrics.get('effective_fp'), metrics.get('current_load'), metrics.get('estimated_readiness'), metrics.get('activity_state'), metrics.get('status_code'), metrics.get('status_sub'), metrics.get('recommended_break_iso'), metrics.get('exhaustion_iso'), metrics.get('base_fp'), metrics.get('boost_fp'), metrics.get('debt'), metrics.get('continuous_work_hours'), metrics.get('estimated_hr'), 1 if metrics.get('is_hr_estimated') else 0, metrics.get('stress_index'), metrics.get('recovery_efficiency'), metrics.get('recovery_ceiling'), metrics.get('decay_multiplier'), metrics.get('hours_since_wake'), metrics.get('boost_efficiency'), metrics.get('correction_factor'), metrics.get('hr_stress_factor'), metrics.get('current_mouse_speed'), metrics.get('recent_correction_rate'), prediction_json, metrics.get('apm'), metrics.get('mouse_pixels'), metrics.get('phantom_recovery'), metrics.get('phantom_recovery_sum'), metrics.get('state_label')))
                cursor.execute('DELETE FROM brain_metrics WHERE id NOT IN (SELECT id FROM brain_metrics ORDER BY timestamp DESC LIMIT 1000)')
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to save brain metrics: {e}")
            return False
    def get_oura_cache(self) -> Dict:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='oura_cache'")
                if not cursor.fetchone():
                    return {}
                cursor.execute('SELECT * FROM oura_cache WHERE id = 1')
                row = cursor.fetchone()
                if row:
                    result = {k: row[k] for k in row.keys()}
                    result['is_effective_today'] = bool(result.get('is_effective_today', 1))
                    for jk in ('contributors_json', 'nap_segments_json', 'hr_stream_json'):
                        key = jk.replace('_json', '')
                        if result.get(jk):
                            try:
                                result[key] = json.loads(result[jk])
                            except:
                                result[key] = [] if 'segments' in jk or 'stream' in jk else {}
                        else:
                            result[key] = [] if 'segments' in jk or 'stream' in jk else {}
                    return result
                return {}
        except Exception as e:
            self.logger.error(f"Failed to get oura cache: {e}")
            return {}
    def update_oura_cache(self, data: Dict) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='oura_cache'")
                if not cursor.fetchone():
                    cursor.execute('''CREATE TABLE IF NOT EXISTS oura_cache (id INTEGER PRIMARY KEY CHECK (id = 1),temperature_deviation REAL,sleep_score INTEGER,stress_high INTEGER,recovery_high INTEGER,true_rhr INTEGER,true_rhr_time TEXT,current_hr INTEGER,current_hr_time TEXT,wake_anchor_iso TEXT,total_nap_minutes REAL,recovery_score REAL,min_bpm INTEGER,max_bpm INTEGER,main_sleep_seconds INTEGER,max_continuous_rest_seconds INTEGER,data_date TEXT,is_effective_today INTEGER DEFAULT 1,contributors_json TEXT,nap_segments_json TEXT,hr_stream_json TEXT,updated_at TEXT)''')
                    cursor.execute('INSERT OR IGNORE INTO oura_cache (id) VALUES (1)')
                contributors_json = json.dumps(data.get('contributors', {})) if data.get('contributors') else None
                nap_segments_json = json.dumps(data.get('nap_segments', [])) if data.get('nap_segments') else None
                hr_stream_json = json.dumps(data.get('hr_stream', [])) if data.get('hr_stream') else None
                cursor.execute('''UPDATE oura_cache SET temperature_deviation = ?, sleep_score = ?, stress_high = ?, recovery_high = ?, true_rhr = ?, true_rhr_time = ?, current_hr = ?, current_hr_time = ?, wake_anchor_iso = ?, total_nap_minutes = ?, recovery_score = ?, min_bpm = ?, max_bpm = ?, main_sleep_seconds = ?, max_continuous_rest_seconds = ?, data_date = ?, is_effective_today = ?, contributors_json = ?, nap_segments_json = ?, hr_stream_json = ?, updated_at = ? WHERE id = 1''', (data.get('temperature_deviation'), data.get('sleep_score'), data.get('stress_high'), data.get('recovery_high'), data.get('true_rhr'), data.get('true_rhr_time'), data.get('current_hr'), data.get('current_hr_time'), data.get('wake_anchor_iso'), data.get('total_nap_minutes'), data.get('recovery_score'), data.get('min_bpm'), data.get('max_bpm'), data.get('main_sleep_seconds'), data.get('max_continuous_rest_seconds'), data.get('data_date'), 1 if data.get('is_effective_today', True) else 0, contributors_json, nap_segments_json, hr_stream_json, datetime.now(JST).isoformat()))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to update oura cache: {e}")
            return False
    def push_command(self, cmd: str, value: Any = None) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='command_queue'")
                if not cursor.fetchone():
                    cursor.execute('''CREATE TABLE IF NOT EXISTS command_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,cmd TEXT NOT NULL,value TEXT,timestamp TEXT NOT NULL,processed INTEGER DEFAULT 0)''')
                    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cmd_processed ON command_queue(processed)')
                value_json = json.dumps(value) if value is not None else None
                cursor.execute('INSERT INTO command_queue (cmd, value, timestamp) VALUES (?, ?, ?)', (cmd, value_json, datetime.now(JST).isoformat()))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to push command: {e}")
            return False
    def pop_commands(self) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='command_queue'")
                if not cursor.fetchone():
                    return []
                cursor.execute('SELECT id, cmd, value, timestamp FROM command_queue WHERE processed = 0 ORDER BY id ASC')
                rows = cursor.fetchall()
                if not rows:
                    return []
                ids = [row['id'] for row in rows]
                cursor.execute(f'UPDATE command_queue SET processed = 1 WHERE id IN ({",".join("?" * len(ids))})', ids)
                cursor.execute('DELETE FROM command_queue WHERE processed = 1 AND id NOT IN (SELECT id FROM command_queue ORDER BY id DESC LIMIT 100)')
                conn.commit()
                result = []
                for row in rows:
                    val = None
                    if row['value']:
                        try:
                            val = json.loads(row['value'])
                        except:
                            val = row['value']
                    result.append({'cmd': row['cmd'], 'value': val, 'timestamp': row['timestamp']})
                return result
        except Exception as e:
            self.logger.error(f"Failed to pop commands: {e}")
            return []
    def get_combined_state(self) -> Dict:
        """GUI用: daemon_state + oura_cache + brain_metricsを結合して返す"""
        state = self.get_daemon_state()
        oura = self.get_oura_cache()
        brain = self.get_latest_brain_metrics()
        def nv(d, k, default):
            v = d.get(k)
            return v if v is not None else default
        return {'daemon_running': state.get('daemon_running', False), 'gui_running': state.get('gui_running', False), 'is_muted': state.get('is_muted', False), 'is_shisha_active': state.get('is_shisha_active', False), 'is_sleeping': state.get('is_sleeping', False), 'user_present': state.get('user_present', True), 'idle_seconds': state.get('idle_seconds', 0), 'momentum_minutes': state.get('momentum_minutes', 0), 'current_mode': state.get('current_mode', 'mid'), 'last_oura_score': state.get('last_oura_score'), 'is_data_effective_today': state.get('is_data_effective_today', True), 'current_shisha_session_id': state.get('current_shisha_session_id'), 'oura_details': {'temperature_deviation': oura.get('temperature_deviation'), 'sleep_score': oura.get('sleep_score'), 'stress_high': oura.get('stress_high'), 'recovery_high': oura.get('recovery_high'), 'true_rhr': oura.get('true_rhr'), 'true_rhr_time': oura.get('true_rhr_time'), 'current_hr': oura.get('current_hr'), 'current_hr_time': oura.get('current_hr_time'), 'wake_anchor_iso': oura.get('wake_anchor_iso'), 'total_nap_minutes': oura.get('total_nap_minutes'), 'recovery_score': oura.get('recovery_score'), 'min_bpm': oura.get('min_bpm'), 'max_bpm': oura.get('max_bpm'), 'main_sleep_seconds': oura.get('main_sleep_seconds'), 'max_continuous_rest_seconds': oura.get('max_continuous_rest_seconds'), 'data_date': oura.get('data_date'), 'is_effective_today': oura.get('is_effective_today', True), 'contributors': oura.get('contributors', {}), 'nap_segments': oura.get('nap_segments', []), 'hr_stream': oura.get('hr_stream', [])}, 'brain_state': {'effective_fp': nv(brain, 'effective_fp', 75.0), 'current_load': nv(brain, 'current_load', 0.0), 'estimated_readiness': nv(brain, 'estimated_readiness', 75.0), 'activity_state': nv(brain, 'activity_state', 'IDLE'), 'state_label': nv(brain, 'state_label', 'IDLE'), 'status_code': nv(brain, 'status_code', 'INITIALIZING'), 'status_sub': nv(brain, 'status_sub', ''), 'recommended_break_iso': brain.get('recommended_break_iso'), 'exhaustion_iso': brain.get('exhaustion_iso'), 'base_fp': nv(brain, 'base_fp', 75.0), 'boost_fp': nv(brain, 'boost_fp', 0.0), 'debt': nv(brain, 'debt', 0.0), 'continuous_work_hours': nv(brain, 'continuous_work_hours', 0.0), 'estimated_hr': brain.get('estimated_hr'), 'is_hr_estimated': brain.get('is_hr_estimated', False), 'stress_index': nv(brain, 'stress_index', 0.0), 'recovery_efficiency': nv(brain, 'recovery_efficiency', 1.0), 'recovery_ceiling': nv(brain, 'recovery_ceiling', 100.0), 'apm': nv(brain, 'apm', 0), 'mouse_pixels': nv(brain, 'mouse_pixels', 0), 'current_mouse_speed': nv(brain, 'current_mouse_speed', 0.0), 'recent_correction_rate': nv(brain, 'recent_correction_rate', 0.0), 'phantom_recovery': nv(brain, 'phantom_recovery', 0.0), 'phantom_recovery_sum': nv(brain, 'phantom_recovery_sum', 0.0), 'prediction': brain.get('prediction', {'continue': [], 'rest': []})}}
    def upsert_daily_log(self, data: Dict) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                log_date = data.get('date')
                if not log_date:
                    return False
                cursor.execute('SELECT * FROM daily_logs WHERE date = ?', (log_date,))
                existing = cursor.fetchone()
                def get_val(key, default=None):
                    new_val = data.get(key)
                    return new_val if new_val is not None else (existing[key] if existing and key in existing.keys() else default)
                cursor.execute('''INSERT OR REPLACE INTO daily_logs (date, readiness_score, sleep_score, main_sleep_seconds, true_rhr, sleep_efficiency, restfulness, deep_sleep, rem_sleep, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (log_date, get_val('readiness_score'), get_val('sleep_score'), get_val('main_sleep_seconds'), get_val('true_rhr'), get_val('sleep_efficiency'), get_val('restfulness'), get_val('deep_sleep'), get_val('rem_sleep'), datetime.now().isoformat()))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to upsert daily log: {e}")
            return False
    def get_daily_log(self, log_date: str) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM daily_logs WHERE date = ?', (log_date,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"Failed to get daily log: {e}")
            return None
    def get_sleep_data_for_range(self, start_date: str, end_date: str) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT date, sleep_score, sleep_efficiency, restfulness, deep_sleep, rem_sleep FROM daily_logs WHERE date >= ? AND date <= ?', (start_date, end_date))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Failed to get sleep data range: {e}")
            return []
    def log_tactile_data(self, data: Dict) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''INSERT INTO tactile_logs (timestamp, keystrokes, clicks, scroll_delta, apm, mouse_pixels, correction_rate, state_label, effective_fp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (data.get('timestamp', datetime.now().isoformat()), data.get('keystrokes', 0), data.get('clicks', 0), data.get('scroll_delta', 0), data.get('apm', 0), data.get('mouse_pixels', 0), data.get('correction_rate', 0), data.get('state_label', ''), data.get('effective_fp')))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to log tactile data: {e}")
            return False
    def log_shadow_hr(self, timestamp: datetime, bpm: int) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT OR REPLACE INTO heartrate_logs (timestamp, bpm, source) VALUES (?, ?, ?)', (timestamp.isoformat(), int(bpm), 'shadow'))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to log shadow HR: {e}")
            return False
    def log_heartrate_stream(self, hr_data: List[Dict], auto_purge_shadow: bool = True) -> int:
        if not hr_data:
            return 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                oura_timestamps = []
                saved = 0
                for entry in hr_data:
                    ts, bpm, source = entry.get('timestamp'), entry.get('bpm'), entry.get('source', 'oura')
                    if not ts or bpm is None:
                        continue
                    if source != 'shadow':
                        oura_timestamps.append(ts)
                    try:
                        cursor.execute('INSERT OR REPLACE INTO heartrate_logs (timestamp, bpm, source) VALUES (?, ?, ?)', (ts, int(bpm), source))
                        saved += 1
                    except:
                        continue
                if auto_purge_shadow and oura_timestamps:
                    min_ts, max_ts = min(oura_timestamps), max(oura_timestamps)
                    cursor.execute('DELETE FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source = ?', (min_ts, max_ts, 'shadow'))
                    purged = cursor.rowcount
                    if purged > 0:
                        self.logger.info(f"Auto-purged {purged} shadow HR records")
                conn.commit()
                return saved
        except Exception as e:
            self.logger.error(f"Failed to log heartrate stream: {e}")
            return 0
    def purge_shadow_for_range(self, start_time: datetime, end_time: datetime) -> int:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source = ?', (start_time.isoformat(), end_time.isoformat(), 'shadow'))
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            self.logger.error(f"Failed to purge shadow for range: {e}")
            return 0
    def get_heartrate_range(self, start_time: datetime, end_time: datetime, include_shadow: bool = True) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if include_shadow:
                    cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC', (start_time.isoformat(), end_time.isoformat()))
                else:
                    cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source != ? ORDER BY timestamp ASC', (start_time.isoformat(), end_time.isoformat(), 'shadow'))
                return [{'timestamp': row['timestamp'], 'bpm': row['bpm'], 'source': row['source']} for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Failed to get heartrate range: {e}")
            return []
    def get_daily_summary(self, target_date: date) -> Dict:
        result = {'date': target_date.isoformat(), 'readiness_score': None, 'sleep_score': None, 'main_sleep_hours': None, 'total_keystrokes': 0, 'total_clicks': 0, 'deep_dive_minutes': 0, 'scavenging_minutes': 0, 'cruising_minutes': 0, 'idle_minutes': 0, 'avg_apm': 0.0, 'avg_correction_rate': 0.0}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT readiness_score, sleep_score, main_sleep_seconds FROM daily_logs WHERE date = ?', (target_date.isoformat(),))
                row = cursor.fetchone()
                if row:
                    result['readiness_score'], result['sleep_score'] = row['readiness_score'], row['sleep_score']
                    if row['main_sleep_seconds']:
                        result['main_sleep_hours'] = row['main_sleep_seconds'] / 3600
                start, end = datetime.combine(target_date, datetime.min.time()), datetime.combine(target_date, datetime.max.time())
                cursor.execute('SELECT SUM(keystrokes), SUM(clicks), AVG(apm), AVG(correction_rate) FROM tactile_logs WHERE timestamp >= ? AND timestamp <= ?', (start.isoformat(), end.isoformat()))
                row = cursor.fetchone()
                if row:
                    result['total_keystrokes'], result['total_clicks'], result['avg_apm'], result['avg_correction_rate'] = row[0] or 0, row[1] or 0, row[2] or 0.0, row[3] or 0.0
                cursor.execute('SELECT state_label, COUNT(*) as cnt FROM tactile_logs WHERE timestamp >= ? AND timestamp <= ? GROUP BY state_label', (start.isoformat(), end.isoformat()))
                state_map = {'DEEP_DIVE': 'deep_dive_minutes', 'SCAVENGING': 'scavenging_minutes', 'CRUISING': 'cruising_minutes', 'IDLE': 'idle_minutes'}
                for row in cursor.fetchall():
                    if row['state_label'] in state_map:
                        result[state_map[row['state_label']]] = row['cnt']
            return result
        except Exception as e:
            self.logger.error(f"Failed to get daily summary: {e}")
            return result
    def get_average_sleep(self, days: int = 3) -> Optional[int]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT AVG(main_sleep_seconds) as avg_sleep FROM (SELECT main_sleep_seconds FROM daily_logs WHERE main_sleep_seconds IS NOT NULL ORDER BY date DESC LIMIT ?)', (days,))
                row = cursor.fetchone()
                return int(row['avg_sleep']) if row and row['avg_sleep'] else None
        except Exception as e:
            self.logger.error(f"Failed to get average sleep: {e}")
            return None
    def start_shisha_session(self, start_time: datetime) -> Optional[int]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO shisha_logs (start_time, completed) VALUES (?, 0)', (start_time.isoformat(),))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            self.logger.error(f"Failed to start shisha session: {e}")
            return None
    def end_shisha_session(self, session_id: int, end_time: datetime, completed: bool = True) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT start_time FROM shisha_logs WHERE id = ?', (session_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                duration = int((end_time - datetime.fromisoformat(row['start_time'])).total_seconds())
                cursor.execute('UPDATE shisha_logs SET end_time = ?, duration_seconds = ?, completed = ? WHERE id = ?', (end_time.isoformat(), duration, 1 if completed else 0, session_id))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to end shisha session: {e}")
            return False
    def get_shisha_sessions(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, start_time, end_time, duration_seconds, completed FROM shisha_logs WHERE (start_time >= ? AND start_time <= ?) OR (end_time >= ? AND end_time <= ?) OR (start_time <= ? AND (end_time >= ? OR end_time IS NULL)) ORDER BY start_time ASC', (start_time.isoformat(), end_time.isoformat(), start_time.isoformat(), end_time.isoformat(), start_time.isoformat(), end_time.isoformat()))
                return [{'id': row['id'], 'start_time': row['start_time'], 'end_time': row['end_time'], 'duration_seconds': row['duration_seconds'], 'completed': bool(row['completed'])} for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Failed to get shisha sessions: {e}")
            return []
    def get_incomplete_shisha_session(self) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, start_time, end_time, duration_seconds, completed FROM shisha_logs WHERE end_time IS NULL ORDER BY start_time DESC LIMIT 1')
                row = cursor.fetchone()
                return {'id': row['id'], 'start_time': row['start_time'], 'end_time': row['end_time'], 'duration_seconds': row['duration_seconds'], 'completed': bool(row['completed'])} if row else None
        except Exception as e:
            self.logger.error(f"Failed to get incomplete shisha session: {e}")
            return None
    def is_time_in_shisha_session(self, timestamp: datetime) -> Tuple[bool, Optional[int]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                ts = timestamp.isoformat()
                cursor.execute('SELECT id FROM shisha_logs WHERE start_time <= ? AND (end_time >= ? OR end_time IS NULL) LIMIT 1', (ts, ts))
                row = cursor.fetchone()
                return (True, row['id']) if row else (False, None)
        except Exception as e:
            self.logger.error(f"Failed to check shisha session: {e}")
            return (False, None)
DATABASE_AVAILABLE = True
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = LifeOSDatabase()
    print(f"LifeOS Database v{DATABASE_VERSION}")
    print(f"Database path: {db.db_path}")
