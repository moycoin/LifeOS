#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LifeOS Database v7.0.0 - 4-DB Architecture for Lightweight Resident App
# state.db: リアルタイム (~50KB固定)
# metrics.db: 7日ローリング (<10MB)
# summary.db: 集計済み (~1MB)
import sqlite3
import json
import logging
import threading
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any
from .types import get_root_path, get_db_path, __version__, JST

DATABASE_VERSION = '7.0.0'
RETENTION_DAYS = 7


class BaseDB:
    """FIXED: 接続キャッシュ付きベースDB"""
    def __init__(self, db_path: Path, logger: logging.Logger):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
    
    def _get_conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(
                    str(self.db_path), timeout=10.0, check_same_thread=False
                )
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA busy_timeout=5000;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            return self._conn
    
    def close(self):
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None


class StateDB(BaseDB):
    """FIXED: リアルタイム状態DB (~50KB固定) - 常時接続"""
    
    def init_tables(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS daemon_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            daemon_running INTEGER DEFAULT 0, daemon_pid INTEGER,
            gui_running INTEGER DEFAULT 0, is_muted INTEGER DEFAULT 0,
            is_shisha_active INTEGER DEFAULT 0, is_sleeping INTEGER DEFAULT 0,
            user_present INTEGER DEFAULT 1, idle_seconds REAL DEFAULT 0,
            momentum_minutes INTEGER DEFAULT 0, current_mode TEXT DEFAULT 'mid',
            last_oura_score INTEGER, is_data_effective_today INTEGER DEFAULT 1,
            current_shisha_session_id INTEGER, updated_at TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS command_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd TEXT NOT NULL, value TEXT, timestamp TEXT NOT NULL, processed INTEGER DEFAULT 0
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_cmd_processed ON command_queue(processed)')
        cursor.execute('''CREATE TABLE IF NOT EXISTS oura_cache (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            temperature_deviation REAL, sleep_score INTEGER, stress_high INTEGER,
            recovery_high INTEGER, true_rhr INTEGER, true_rhr_time TEXT,
            current_hr INTEGER, current_hr_time TEXT, wake_anchor_iso TEXT,
            total_nap_minutes REAL, recovery_score REAL, min_bpm INTEGER, max_bpm INTEGER,
            main_sleep_seconds INTEGER, max_continuous_rest_seconds INTEGER,
            data_date TEXT, is_effective_today INTEGER DEFAULT 1,
            contributors_json TEXT, nap_segments_json TEXT, hr_stream_json TEXT, updated_at TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS current_metrics (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            timestamp TEXT, effective_fp REAL, current_load REAL, estimated_readiness REAL,
            activity_state TEXT, status_code TEXT, status_sub TEXT,
            recommended_break_iso TEXT, exhaustion_iso TEXT,
            base_fp REAL, boost_fp REAL, debt REAL, continuous_work_hours REAL,
            estimated_hr INTEGER, is_hr_estimated INTEGER DEFAULT 0,
            stress_index REAL, recovery_efficiency REAL, recovery_ceiling REAL,
            decay_multiplier REAL, hours_since_wake REAL, boost_efficiency REAL,
            correction_factor REAL, hr_stress_factor REAL,
            current_mouse_speed REAL, recent_correction_rate REAL,
            prediction_json TEXT, apm REAL, mouse_pixels REAL,
            phantom_recovery REAL, phantom_recovery_sum REAL, state_label TEXT
        )''')
        cursor.execute('INSERT OR IGNORE INTO daemon_state (id) VALUES (1)')
        cursor.execute('INSERT OR IGNORE INTO oura_cache (id) VALUES (1)')
        cursor.execute('INSERT OR IGNORE INTO current_metrics (id) VALUES (1)')
        conn.commit()
    
    def get_daemon_state(self) -> Dict:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT * FROM daemon_state WHERE id = 1')
            row = cursor.fetchone()
            if row:
                return {k: bool(row[k]) if k in ('daemon_running', 'gui_running', 'is_muted', 
                        'is_shisha_active', 'is_sleeping', 'user_present', 'is_data_effective_today') 
                        else row[k] for k in row.keys()}
            return {}
        except Exception as e:
            self.logger.error(f"get_daemon_state: {e}")
            return {}
    
    def update_daemon_state(self, **kwargs) -> bool:
        if not kwargs:
            return True
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            kwargs['updated_at'] = datetime.now(JST).isoformat()
            cols = ', '.join(f'{k} = ?' for k in kwargs.keys())
            vals = [int(v) if isinstance(v, bool) else v for v in kwargs.values()]
            cursor.execute(f'UPDATE daemon_state SET {cols} WHERE id = 1', vals)
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"update_daemon_state: {e}")
            return False
    
    def get_oura_cache(self) -> Dict:
        try:
            cursor = self._get_conn().cursor()
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
            self.logger.error(f"get_oura_cache: {e}")
            return {}
    
    def update_oura_cache(self, data: Dict) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            contributors_json = json.dumps(data.get('contributors', {})) if data.get('contributors') else None
            nap_segments_json = json.dumps(data.get('nap_segments', [])) if data.get('nap_segments') else None
            hr_stream_json = json.dumps(data.get('hr_stream', [])) if data.get('hr_stream') else None
            cursor.execute('''UPDATE oura_cache SET 
                temperature_deviation=?, sleep_score=?, stress_high=?, recovery_high=?,
                true_rhr=?, true_rhr_time=?, current_hr=?, current_hr_time=?,
                wake_anchor_iso=?, total_nap_minutes=?, recovery_score=?, min_bpm=?, max_bpm=?,
                main_sleep_seconds=?, max_continuous_rest_seconds=?, data_date=?, is_effective_today=?,
                contributors_json=?, nap_segments_json=?, hr_stream_json=?, updated_at=?
                WHERE id = 1''', (
                data.get('temperature_deviation'), data.get('sleep_score'), data.get('stress_high'),
                data.get('recovery_high'), data.get('true_rhr'), data.get('true_rhr_time'),
                data.get('current_hr'), data.get('current_hr_time'), data.get('wake_anchor_iso'),
                data.get('total_nap_minutes'), data.get('recovery_score'), data.get('min_bpm'),
                data.get('max_bpm'), data.get('main_sleep_seconds'), data.get('max_continuous_rest_seconds'),
                data.get('data_date'), 1 if data.get('is_effective_today', True) else 0,
                contributors_json, nap_segments_json, hr_stream_json, datetime.now(JST).isoformat()
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"update_oura_cache: {e}")
            return False
    
    def get_current_metrics(self) -> Dict:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT * FROM current_metrics WHERE id = 1')
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
            self.logger.error(f"get_current_metrics: {e}")
            return {}
    
    def update_current_metrics(self, metrics: Dict) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            prediction_json = json.dumps(metrics.get('prediction', {})) if metrics.get('prediction') else None
            cursor.execute('''UPDATE current_metrics SET
                timestamp=?, effective_fp=?, current_load=?, estimated_readiness=?,
                activity_state=?, status_code=?, status_sub=?,
                recommended_break_iso=?, exhaustion_iso=?,
                base_fp=?, boost_fp=?, debt=?, continuous_work_hours=?,
                estimated_hr=?, is_hr_estimated=?, stress_index=?, recovery_efficiency=?,
                recovery_ceiling=?, decay_multiplier=?, hours_since_wake=?, boost_efficiency=?,
                correction_factor=?, hr_stress_factor=?, current_mouse_speed=?, recent_correction_rate=?,
                prediction_json=?, apm=?, mouse_pixels=?, phantom_recovery=?, phantom_recovery_sum=?, state_label=?
                WHERE id = 1''', (
                datetime.now(JST).isoformat(),
                metrics.get('effective_fp'), metrics.get('current_load'), metrics.get('estimated_readiness'),
                metrics.get('activity_state'), metrics.get('status_code'), metrics.get('status_sub'),
                metrics.get('recommended_break_iso'), metrics.get('exhaustion_iso'),
                metrics.get('base_fp'), metrics.get('boost_fp'), metrics.get('debt'),
                metrics.get('continuous_work_hours'), metrics.get('estimated_hr'),
                1 if metrics.get('is_hr_estimated') else 0,
                metrics.get('stress_index'), metrics.get('recovery_efficiency'), metrics.get('recovery_ceiling'),
                metrics.get('decay_multiplier'), metrics.get('hours_since_wake'), metrics.get('boost_efficiency'),
                metrics.get('correction_factor'), metrics.get('hr_stress_factor'),
                metrics.get('current_mouse_speed'), metrics.get('recent_correction_rate'),
                prediction_json, metrics.get('apm'), metrics.get('mouse_pixels'),
                metrics.get('phantom_recovery'), metrics.get('phantom_recovery_sum'), metrics.get('state_label')
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"update_current_metrics: {e}")
            return False
    
    def push_command(self, cmd: str, value: Any = None) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            value_json = json.dumps(value) if value is not None else None
            cursor.execute('INSERT INTO command_queue (cmd, value, timestamp) VALUES (?, ?, ?)',
                          (cmd, value_json, datetime.now(JST).isoformat()))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"push_command: {e}")
            return False
    
    def pop_commands(self) -> List[Dict]:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('SELECT id, cmd, value, timestamp FROM command_queue WHERE processed = 0 ORDER BY id ASC')
            rows = cursor.fetchall()
            if not rows:
                return []
            ids = [row['id'] for row in rows]
            cursor.execute(f'DELETE FROM command_queue WHERE id IN ({",".join("?" * len(ids))})', ids)
            conn.commit()
            result = []
            for row in rows:
                val = json.loads(row['value']) if row['value'] else None
                result.append({'cmd': row['cmd'], 'value': val, 'timestamp': row['timestamp']})
            return result
        except Exception as e:
            self.logger.error(f"pop_commands: {e}")
            return []


class MetricsDB(BaseDB):
    """FIXED: 7日ローリングDB (<10MB) - 60秒ごと接続"""
    
    def init_tables(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS brain_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            effective_fp REAL, current_load REAL, estimated_readiness REAL,
            activity_state TEXT, status_code TEXT, status_sub TEXT,
            recommended_break_iso TEXT, exhaustion_iso TEXT,
            base_fp REAL, boost_fp REAL, debt REAL, continuous_work_hours REAL,
            estimated_hr INTEGER, is_hr_estimated INTEGER DEFAULT 0,
            stress_index REAL, recovery_efficiency REAL, recovery_ceiling REAL,
            decay_multiplier REAL, hours_since_wake REAL, boost_efficiency REAL,
            correction_factor REAL, hr_stress_factor REAL,
            current_mouse_speed REAL, recent_correction_rate REAL,
            prediction_json TEXT, apm REAL, mouse_pixels REAL,
            phantom_recovery REAL, phantom_recovery_sum REAL, state_label TEXT
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_brain_ts ON brain_metrics(timestamp)')
        cursor.execute('''CREATE TABLE IF NOT EXISTS tactile_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            keystrokes INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, scroll_delta INTEGER DEFAULT 0,
            apm REAL DEFAULT 0, mouse_pixels REAL DEFAULT 0, correction_rate REAL DEFAULT 0,
            state_label TEXT, effective_fp REAL
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tactile_ts ON tactile_logs(timestamp)')
        cursor.execute('''CREATE TABLE IF NOT EXISTS heartrate_logs (
            timestamp TEXT PRIMARY KEY, bpm INTEGER NOT NULL, source TEXT DEFAULT 'oura'
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_hr_ts ON heartrate_logs(timestamp)')
        cursor.execute('''CREATE TABLE IF NOT EXISTS room_temperature_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            room_temp REAL NOT NULL, target_temp INTEGER, mode TEXT
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_room_temp_ts ON room_temperature_logs(timestamp)')
        conn.commit()
    
    def save_brain_metrics(self, metrics: Dict) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            prediction_json = json.dumps(metrics.get('prediction', {})) if metrics.get('prediction') else None
            cursor.execute('''INSERT INTO brain_metrics (
                timestamp, effective_fp, current_load, estimated_readiness, activity_state,
                status_code, status_sub, recommended_break_iso, exhaustion_iso,
                base_fp, boost_fp, debt, continuous_work_hours, estimated_hr, is_hr_estimated,
                stress_index, recovery_efficiency, recovery_ceiling, decay_multiplier,
                hours_since_wake, boost_efficiency, correction_factor, hr_stress_factor,
                current_mouse_speed, recent_correction_rate, prediction_json,
                apm, mouse_pixels, phantom_recovery, phantom_recovery_sum, state_label
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                datetime.now(JST).isoformat(),
                metrics.get('effective_fp'), metrics.get('current_load'), metrics.get('estimated_readiness'),
                metrics.get('activity_state'), metrics.get('status_code'), metrics.get('status_sub'),
                metrics.get('recommended_break_iso'), metrics.get('exhaustion_iso'),
                metrics.get('base_fp'), metrics.get('boost_fp'), metrics.get('debt'),
                metrics.get('continuous_work_hours'), metrics.get('estimated_hr'),
                1 if metrics.get('is_hr_estimated') else 0,
                metrics.get('stress_index'), metrics.get('recovery_efficiency'), metrics.get('recovery_ceiling'),
                metrics.get('decay_multiplier'), metrics.get('hours_since_wake'), metrics.get('boost_efficiency'),
                metrics.get('correction_factor'), metrics.get('hr_stress_factor'),
                metrics.get('current_mouse_speed'), metrics.get('recent_correction_rate'), prediction_json,
                metrics.get('apm'), metrics.get('mouse_pixels'),
                metrics.get('phantom_recovery'), metrics.get('phantom_recovery_sum'), metrics.get('state_label')
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"save_brain_metrics: {e}")
            return False
    
    def log_tactile_data(self, data: Dict) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO tactile_logs 
                (timestamp, keystrokes, clicks, scroll_delta, apm, mouse_pixels, correction_rate, state_label, effective_fp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                data.get('timestamp', datetime.now().isoformat()),
                data.get('keystrokes', 0), data.get('clicks', 0), data.get('scroll_delta', 0),
                data.get('apm', 0), data.get('mouse_pixels', 0), data.get('correction_rate', 0),
                data.get('state_label', ''), data.get('effective_fp')
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"log_tactile_data: {e}")
            return False
    
    def log_shadow_hr(self, timestamp: datetime, bpm: int) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO heartrate_logs (timestamp, bpm, source) VALUES (?, ?, ?)',
                          (timestamp.isoformat(), int(bpm), 'shadow'))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"log_shadow_hr: {e}")
            return False
    
    def log_heartrate_stream(self, hr_data: List[Dict], auto_purge_shadow: bool = True) -> int:
        if not hr_data:
            return 0
        try:
            conn = self._get_conn()
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
                    cursor.execute('INSERT OR REPLACE INTO heartrate_logs (timestamp, bpm, source) VALUES (?, ?, ?)',
                                  (ts, int(bpm), source))
                    saved += 1
                except:
                    continue
            if auto_purge_shadow and oura_timestamps:
                min_ts, max_ts = min(oura_timestamps), max(oura_timestamps)
                cursor.execute('DELETE FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source = ?',
                              (min_ts, max_ts, 'shadow'))
            conn.commit()
            return saved
        except Exception as e:
            self.logger.error(f"log_heartrate_stream: {e}")
            return 0
    
    def get_heartrate_range(self, start_time: datetime, end_time: datetime, include_shadow: bool = True) -> List[Dict]:
        try:
            cursor = self._get_conn().cursor()
            if include_shadow:
                cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC',
                              (start_time.isoformat(), end_time.isoformat()))
            else:
                cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source != ? ORDER BY timestamp ASC',
                              (start_time.isoformat(), end_time.isoformat(), 'shadow'))
            return [{'timestamp': row['timestamp'], 'bpm': row['bpm'], 'source': row['source']} for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"get_heartrate_range: {e}")
            return []
    
    def get_tactile_range(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT * FROM tactile_logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC',
                          (start_time.isoformat(), end_time.isoformat()))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"get_tactile_range: {e}")
            return []
    
    
    def log_room_temperature(self, room_temp: float, target_temp: int = None, mode: str = None) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('INSERT INTO room_temperature_logs (timestamp, room_temp, target_temp, mode) VALUES (?, ?, ?, ?)',
                          (datetime.now(JST).isoformat(), room_temp, target_temp, mode))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"log_room_temperature: {e}")
            return False
    
    def get_temperature_trend(self, minutes: int = 30) -> Tuple[Optional[float], List[Dict]]:
        try:
            cursor = self._get_conn().cursor()
            since = (datetime.now(JST) - timedelta(minutes=minutes)).isoformat()
            cursor.execute('SELECT timestamp, room_temp FROM room_temperature_logs WHERE timestamp >= ? ORDER BY timestamp ASC', (since,))
            rows = [{'timestamp': row['timestamp'], 'room_temp': row['room_temp']} for row in cursor.fetchall()]
            if len(rows) < 2:
                return None, rows
            first, last = rows[0], rows[-1]
            t0 = datetime.fromisoformat(first['timestamp'])
            t1 = datetime.fromisoformat(last['timestamp'])
            elapsed_min = (t1 - t0).total_seconds() / 60
            if elapsed_min < 1:
                return None, rows
            rate = (last['room_temp'] - first['room_temp']) / elapsed_min
            return rate, rows
        except Exception as e:
            self.logger.error(f"get_temperature_trend: {e}")
            return None, []
    
    def purge_old_data(self, before: datetime) -> Dict[str, int]:
        result = {'brain_metrics': 0, 'tactile_logs': 0, 'heartrate_logs': 0, 'room_temperature_logs': 0}
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            ts = before.isoformat()
            for table in result.keys():
                cursor.execute(f'DELETE FROM {table} WHERE timestamp < ?', (ts,))
                result[table] = cursor.rowcount
            conn.commit()
            if any(result.values()):
                conn.execute('VACUUM')
            return result
        except Exception as e:
            self.logger.error(f"purge_old_data: {e}")
            return result


class SummaryDB(BaseDB):
    """FIXED: 集計済みDB (~1MB) - 日次/起動時"""
    
    def init_tables(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS daily_logs (
            date TEXT PRIMARY KEY, readiness_score INTEGER, sleep_score INTEGER,
            main_sleep_seconds INTEGER, true_rhr REAL, sleep_efficiency REAL,
            restfulness REAL, deep_sleep REAL, rem_sleep REAL, updated_at TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS shisha_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL, end_time TEXT, duration_seconds INTEGER, completed INTEGER DEFAULT 0
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shisha_start ON shisha_logs(start_time)')
        conn.commit()
    
    def upsert_daily_log(self, data: Dict) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            log_date = data.get('date')
            if not log_date:
                return False
            cursor.execute('SELECT * FROM daily_logs WHERE date = ?', (log_date,))
            existing = cursor.fetchone()
            def get_val(key, default=None):
                new_val = data.get(key)
                return new_val if new_val is not None else (existing[key] if existing and key in existing.keys() else default)
            cursor.execute('''INSERT OR REPLACE INTO daily_logs 
                (date, readiness_score, sleep_score, main_sleep_seconds, true_rhr,
                 sleep_efficiency, restfulness, deep_sleep, rem_sleep, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                log_date, get_val('readiness_score'), get_val('sleep_score'),
                get_val('main_sleep_seconds'), get_val('true_rhr'),
                get_val('sleep_efficiency'), get_val('restfulness'),
                get_val('deep_sleep'), get_val('rem_sleep'), datetime.now().isoformat()
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"upsert_daily_log: {e}")
            return False
    
    def get_daily_log(self, log_date: str) -> Optional[Dict]:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT * FROM daily_logs WHERE date = ?', (log_date,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"get_daily_log: {e}")
            return None
    
    def get_average_sleep(self, days: int = 3) -> Optional[int]:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('''SELECT AVG(main_sleep_seconds) as avg_sleep FROM 
                (SELECT main_sleep_seconds FROM daily_logs WHERE main_sleep_seconds IS NOT NULL ORDER BY date DESC LIMIT ?)''', (days,))
            row = cursor.fetchone()
            return int(row['avg_sleep']) if row and row['avg_sleep'] else None
        except Exception as e:
            self.logger.error(f"get_average_sleep: {e}")
            return None
    
    def start_shisha_session(self, start_time: datetime) -> Optional[int]:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('INSERT INTO shisha_logs (start_time, completed) VALUES (?, 0)', (start_time.isoformat(),))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            self.logger.error(f"start_shisha_session: {e}")
            return None
    
    def end_shisha_session(self, session_id: int, end_time: datetime, completed: bool = True) -> bool:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('SELECT start_time FROM shisha_logs WHERE id = ?', (session_id,))
            row = cursor.fetchone()
            if not row:
                return False
            duration = int((end_time - datetime.fromisoformat(row['start_time'])).total_seconds())
            cursor.execute('UPDATE shisha_logs SET end_time = ?, duration_seconds = ?, completed = ? WHERE id = ?',
                          (end_time.isoformat(), duration, 1 if completed else 0, session_id))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"end_shisha_session: {e}")
            return False
    
    def get_shisha_sessions(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('''SELECT id, start_time, end_time, duration_seconds, completed FROM shisha_logs 
                WHERE (start_time >= ? AND start_time <= ?) OR (end_time >= ? AND end_time <= ?)
                OR (start_time <= ? AND (end_time >= ? OR end_time IS NULL)) ORDER BY start_time ASC''',
                (start_time.isoformat(), end_time.isoformat(), start_time.isoformat(), end_time.isoformat(),
                 start_time.isoformat(), end_time.isoformat()))
            return [{'id': row['id'], 'start_time': row['start_time'], 'end_time': row['end_time'],
                    'duration_seconds': row['duration_seconds'], 'completed': bool(row['completed'])} for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"get_shisha_sessions: {e}")
            return []
    
    def get_incomplete_shisha_session(self) -> Optional[Dict]:
        try:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT id, start_time, end_time, duration_seconds, completed FROM shisha_logs WHERE end_time IS NULL ORDER BY start_time DESC LIMIT 1')
            row = cursor.fetchone()
            return {'id': row['id'], 'start_time': row['start_time'], 'end_time': row['end_time'],
                   'duration_seconds': row['duration_seconds'], 'completed': bool(row['completed'])} if row else None
        except Exception as e:
            self.logger.error(f"get_incomplete_shisha_session: {e}")
            return None
    
    def is_time_in_shisha_session(self, timestamp: datetime) -> Tuple[bool, Optional[int]]:
        try:
            cursor = self._get_conn().cursor()
            ts = timestamp.isoformat()
            cursor.execute('SELECT id FROM shisha_logs WHERE start_time <= ? AND (end_time >= ? OR end_time IS NULL) LIMIT 1', (ts, ts))
            row = cursor.fetchone()
            return (True, row['id']) if row else (False, None)
        except Exception as e:
            self.logger.error(f"is_time_in_shisha_session: {e}")
            return (False, None)


class LifeOSDatabase:
    """FIXED: 4-DB統合ファサード - 既存API互換"""
    
    def __init__(self, db_path: Optional[Union[str, Path]] = None, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        base_dir = Path(db_path).parent if db_path else get_db_path().parent
        base_dir.mkdir(parents=True, exist_ok=True)
        
        self.state = StateDB(base_dir / 'state.db', self.logger)
        self.metrics = MetricsDB(base_dir / 'metrics.db', self.logger)
        self.summary = SummaryDB(base_dir / 'summary.db', self.logger)
        self.db_path = base_dir / 'state.db'
        
        self._init_all()
        self._last_purge: Optional[datetime] = None
    
    def _init_all(self):
        self.state.init_tables()
        self.metrics.init_tables()
        self.summary.init_tables()
    
    def close(self):
        self.state.close()
        self.metrics.close()
        self.summary.close()
    
    def get_daemon_state(self) -> Dict:
        return self.state.get_daemon_state()
    
    def update_daemon_state(self, **kwargs) -> bool:
        return self.state.update_daemon_state(**kwargs)
    
    def get_oura_cache(self) -> Dict:
        return self.state.get_oura_cache()
    
    def update_oura_cache(self, data: Dict) -> bool:
        return self.state.update_oura_cache(data)
    
    def get_latest_brain_metrics(self) -> Dict:
        return self.state.get_current_metrics()
    
    def save_brain_metrics(self, metrics: Dict) -> bool:
        self.state.update_current_metrics(metrics)
        result = self.metrics.save_brain_metrics(metrics)
        self._auto_purge()
        return result
    
    def push_command(self, cmd: str, value: Any = None) -> bool:
        return self.state.push_command(cmd, value)
    
    def pop_commands(self) -> List[Dict]:
        return self.state.pop_commands()
    
    def log_tactile_data(self, data: Dict) -> bool:
        return self.metrics.log_tactile_data(data)
    
    def log_shadow_hr(self, timestamp: datetime, bpm: int) -> bool:
        return self.metrics.log_shadow_hr(timestamp, bpm)
    
    def log_heartrate_stream(self, hr_data: List[Dict], auto_purge_shadow: bool = True) -> int:
        return self.metrics.log_heartrate_stream(hr_data, auto_purge_shadow)
    
    def get_heartrate_range(self, start_time: datetime, end_time: datetime, include_shadow: bool = True) -> List[Dict]:
        return self.metrics.get_heartrate_range(start_time, end_time, include_shadow)
    
    def purge_shadow_for_range(self, start_time: datetime, end_time: datetime) -> int:
        try:
            conn = self.metrics._get_conn()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source = ?',
                          (start_time.isoformat(), end_time.isoformat(), 'shadow'))
            conn.commit()
            return cursor.rowcount
        except:
            return 0
    
    def upsert_daily_log(self, data: Dict) -> bool:
        return self.summary.upsert_daily_log(data)
    
    def get_daily_log(self, log_date: str) -> Optional[Dict]:
        return self.summary.get_daily_log(log_date)
    
    def get_sleep_data_for_range(self, start_date: str, end_date: str) -> List[Dict]:
        try:
            cursor = self.summary._get_conn().cursor()
            cursor.execute('SELECT date, sleep_score, sleep_efficiency, restfulness, deep_sleep, rem_sleep FROM daily_logs WHERE date >= ? AND date <= ?',
                          (start_date, end_date))
            return [dict(row) for row in cursor.fetchall()]
        except:
            return []
    
    def get_average_sleep(self, days: int = 3) -> Optional[int]:
        return self.summary.get_average_sleep(days)
    
    def start_shisha_session(self, start_time: datetime) -> Optional[int]:
        return self.summary.start_shisha_session(start_time)
    
    def end_shisha_session(self, session_id: int, end_time: datetime, completed: bool = True) -> bool:
        return self.summary.end_shisha_session(session_id, end_time, completed)
    
    def get_shisha_sessions(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        return self.summary.get_shisha_sessions(start_time, end_time)
    
    def get_incomplete_shisha_session(self) -> Optional[Dict]:
        return self.summary.get_incomplete_shisha_session()
    
    def is_time_in_shisha_session(self, timestamp: datetime) -> Tuple[bool, Optional[int]]:
        return self.summary.is_time_in_shisha_session(timestamp)
    
    def get_combined_state(self) -> Dict:
        state = self.state.get_daemon_state()
        oura = self.state.get_oura_cache()
        brain = self.state.get_current_metrics()
        def nv(d, k, default):
            v = d.get(k)
            return v if v is not None else default
        return {
            'daemon_running': state.get('daemon_running', False),
            'gui_running': state.get('gui_running', False),
            'is_muted': state.get('is_muted', False),
            'is_shisha_active': state.get('is_shisha_active', False),
            'is_sleeping': state.get('is_sleeping', False),
            'user_present': state.get('user_present', True),
            'idle_seconds': state.get('idle_seconds', 0),
            'momentum_minutes': state.get('momentum_minutes', 0),
            'current_mode': state.get('current_mode', 'mid'),
            'last_oura_score': state.get('last_oura_score'),
            'is_data_effective_today': state.get('is_data_effective_today', True),
            'current_shisha_session_id': state.get('current_shisha_session_id'),
            'oura_details': {
                'temperature_deviation': oura.get('temperature_deviation'),
                'sleep_score': oura.get('sleep_score'),
                'stress_high': oura.get('stress_high'),
                'recovery_high': oura.get('recovery_high'),
                'true_rhr': oura.get('true_rhr'),
                'true_rhr_time': oura.get('true_rhr_time'),
                'current_hr': oura.get('current_hr'),
                'current_hr_time': oura.get('current_hr_time'),
                'wake_anchor_iso': oura.get('wake_anchor_iso'),
                'total_nap_minutes': oura.get('total_nap_minutes'),
                'recovery_score': oura.get('recovery_score'),
                'min_bpm': oura.get('min_bpm'),
                'max_bpm': oura.get('max_bpm'),
                'main_sleep_seconds': oura.get('main_sleep_seconds'),
                'max_continuous_rest_seconds': oura.get('max_continuous_rest_seconds'),
                'data_date': oura.get('data_date'),
                'is_effective_today': oura.get('is_effective_today', True),
                'contributors': oura.get('contributors', {}),
                'nap_segments': oura.get('nap_segments', []),
                'hr_stream': oura.get('hr_stream', [])
            },
            'brain_state': {
                'effective_fp': nv(brain, 'effective_fp', 75.0),
                'current_load': nv(brain, 'current_load', 0.0),
                'estimated_readiness': nv(brain, 'estimated_readiness', 75.0),
                'activity_state': nv(brain, 'activity_state', 'IDLE'),
                'state_label': nv(brain, 'state_label', 'IDLE'),
                'status_code': nv(brain, 'status_code', 'INITIALIZING'),
                'status_sub': nv(brain, 'status_sub', ''),
                'recommended_break_iso': brain.get('recommended_break_iso'),
                'exhaustion_iso': brain.get('exhaustion_iso'),
                'base_fp': nv(brain, 'base_fp', 75.0),
                'boost_fp': nv(brain, 'boost_fp', 0.0),
                'debt': nv(brain, 'debt', 0.0),
                'continuous_work_hours': nv(brain, 'continuous_work_hours', 0.0),
                'estimated_hr': brain.get('estimated_hr'),
                'is_hr_estimated': brain.get('is_hr_estimated', False),
                'stress_index': nv(brain, 'stress_index', 0.0),
                'recovery_efficiency': nv(brain, 'recovery_efficiency', 1.0),
                'recovery_ceiling': nv(brain, 'recovery_ceiling', 100.0),
                'apm': nv(brain, 'apm', 0),
                'mouse_pixels': nv(brain, 'mouse_pixels', 0),
                'current_mouse_speed': nv(brain, 'current_mouse_speed', 0.0),
                'recent_correction_rate': nv(brain, 'recent_correction_rate', 0.0),
                'phantom_recovery': nv(brain, 'phantom_recovery', 0.0),
                'phantom_recovery_sum': nv(brain, 'phantom_recovery_sum', 0.0),
                'prediction': brain.get('prediction', {'continue': [], 'rest': []})
            }
        }
    
    def get_daily_summary(self, target_date: date) -> Dict:
        result = {'date': target_date.isoformat(), 'readiness_score': None, 'sleep_score': None,
                 'main_sleep_hours': None, 'total_keystrokes': 0, 'total_clicks': 0,
                 'deep_dive_minutes': 0, 'scavenging_minutes': 0, 'cruising_minutes': 0, 'idle_minutes': 0,
                 'avg_apm': 0.0, 'avg_correction_rate': 0.0}
        try:
            daily = self.summary.get_daily_log(target_date.isoformat())
            if daily:
                result['readiness_score'] = daily.get('readiness_score')
                result['sleep_score'] = daily.get('sleep_score')
                if daily.get('main_sleep_seconds'):
                    result['main_sleep_hours'] = daily['main_sleep_seconds'] / 3600
            start = datetime.combine(target_date, datetime.min.time())
            end = datetime.combine(target_date, datetime.max.time())
            tactile = self.metrics.get_tactile_range(start, end)
            if tactile:
                result['total_keystrokes'] = sum(r.get('keystrokes', 0) for r in tactile)
                result['total_clicks'] = sum(r.get('clicks', 0) for r in tactile)
                result['avg_apm'] = sum(r.get('apm', 0) for r in tactile) / len(tactile)
                result['avg_correction_rate'] = sum(r.get('correction_rate', 0) for r in tactile) / len(tactile)
        except Exception as e:
            self.logger.error(f"get_daily_summary: {e}")
        return result
    
    def _get_connection(self) -> sqlite3.Connection:
        return self.state._get_conn()
    
    def init_db(self):
        self._init_all()
    
    def _auto_purge(self):
        now = datetime.now()
        if self._last_purge and (now - self._last_purge).total_seconds() < 3600:
            return
        cutoff = now - timedelta(days=RETENTION_DAYS)
        result = self.metrics.purge_old_data(cutoff)
        if any(result.values()):
            self.logger.info(f"Auto-purge: {result}")
        self._last_purge = now
    
    def log_room_temperature(self, room_temp: float, target_temp: int = None, mode: str = None) -> bool:
        return self.metrics.log_room_temperature(room_temp, target_temp, mode)
    
    def get_temperature_trend(self, minutes: int = 30) -> Tuple[Optional[float], List[Dict]]:
        return self.metrics.get_temperature_trend(minutes)


DATABASE_AVAILABLE = True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = LifeOSDatabase()
    print(f"LifeOS Database v{DATABASE_VERSION} (4-DB Architecture)")
    print(f"State DB: {db.state.db_path}")
    print(f"Metrics DB: {db.metrics.db_path}")
    print(f"Summary DB: {db.summary.db_path}")
