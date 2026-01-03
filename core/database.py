#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Life OS v5.4.1 - Database Module (Polymorphic Retrieval)"""
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import logging
def get_root_path() -> Path:
    return Path(__file__).parent.parent.resolve()
class LifeOSDatabase:
    def __init__(self, db_path: Optional[str] = None, logger: Optional[logging.Logger] = None):
        self.db_path = Path(db_path) if db_path else get_root_path() / "Data" / "life_os.db"
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
                cursor.execute('''CREATE TABLE IF NOT EXISTS daily_logs (date TEXT PRIMARY KEY, readiness_score INTEGER, sleep_score INTEGER, main_sleep_seconds INTEGER, true_rhr REAL, sleep_efficiency REAL, restfulness REAL, deep_sleep REAL, rem_sleep REAL, updated_at TEXT)''')
                cursor.execute('''CREATE TABLE IF NOT EXISTS tactile_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, keystrokes INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, scroll_delta INTEGER DEFAULT 0, apm REAL DEFAULT 0, mouse_pixels REAL DEFAULT 0, correction_rate REAL DEFAULT 0, state_label TEXT, effective_fp REAL)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_tactile_timestamp ON tactile_logs(timestamp)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS heartrate_logs (timestamp TEXT PRIMARY KEY, bpm INTEGER NOT NULL, source TEXT DEFAULT 'oura')''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_hr_timestamp ON heartrate_logs(timestamp)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_hr_source ON heartrate_logs(source)')
                cursor.execute('''CREATE TABLE IF NOT EXISTS shisha_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time TEXT NOT NULL, end_time TEXT, duration_seconds INTEGER, completed INTEGER DEFAULT 0)''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_shisha_start ON shisha_logs(start_time)')
                self._migrate_daily_logs(cursor)
                self._migrate_tactile_logs(cursor)
                conn.commit()
        except Exception as e:
            self.logger.error(f"Database init failed: {e}")
    def _migrate_daily_logs(self, cursor):
        cursor.execute("PRAGMA table_info(daily_logs)")
        cols = {row[1] for row in cursor.fetchall()}
        for col, dtype in [('true_rhr', 'REAL'), ('sleep_efficiency', 'REAL'), ('restfulness', 'REAL'), ('deep_sleep', 'REAL'), ('rem_sleep', 'REAL')]:
            if col not in cols:
                try: cursor.execute(f'ALTER TABLE daily_logs ADD COLUMN {col} {dtype}')
                except: pass
    def _migrate_tactile_logs(self, cursor):
        cursor.execute("PRAGMA table_info(tactile_logs)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'effective_fp' not in cols:
            try: cursor.execute('ALTER TABLE tactile_logs ADD COLUMN effective_fp REAL')
            except: pass
    def upsert_daily_log(self, data: Dict) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                log_date = data.get('date')
                if not log_date: return False
                cursor.execute('SELECT * FROM daily_logs WHERE date = ?', (log_date,))
                existing = cursor.fetchone()
                def get_val(key, default=None):
                    new_val = data.get(key)
                    return new_val if new_val is not None else (existing[key] if existing and key in existing.keys() else default)
                cursor.execute('INSERT OR REPLACE INTO daily_logs (date, readiness_score, sleep_score, main_sleep_seconds, true_rhr, sleep_efficiency, restfulness, deep_sleep, rem_sleep, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (log_date, get_val('readiness_score'), get_val('sleep_score'), get_val('main_sleep_seconds'), get_val('true_rhr'), get_val('sleep_efficiency'), get_val('restfulness'), get_val('deep_sleep'), get_val('rem_sleep'), datetime.now().isoformat()))
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
                cursor.execute('INSERT INTO tactile_logs (timestamp, keystrokes, clicks, scroll_delta, apm, mouse_pixels, correction_rate, state_label, effective_fp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (data.get('timestamp', datetime.now().isoformat()), data.get('keystrokes', 0), data.get('clicks', 0), data.get('scroll_delta', 0), data.get('apm', 0.0), data.get('mouse_pixels', 0.0), data.get('correction_rate', 0.0), data.get('state_label'), data.get('effective_fp')))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to log tactile data: {e}")
            return False
    def get_tactile_range(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT timestamp, keystrokes, clicks, scroll_delta, apm, mouse_pixels, correction_rate, state_label, effective_fp FROM tactile_logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC', (start_time.isoformat(), end_time.isoformat()))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Failed to get tactile range: {e}")
            return []
    def log_heartrate_stream(self, hr_stream: List[Dict], auto_purge_shadow: bool = False) -> int:
        if not hr_stream: return 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('BEGIN IMMEDIATE')
                saved, timestamps = 0, []
                for entry in hr_stream:
                    ts, bpm, source = entry.get('timestamp'), entry.get('bpm'), entry.get('source', 'oura')
                    if ts and bpm:
                        cursor.execute('INSERT OR REPLACE INTO heartrate_logs (timestamp, bpm, source) VALUES (?, ?, ?)', (ts, bpm, source))
                        saved += 1
                        timestamps.append(ts)
                conn.commit()
                if auto_purge_shadow and timestamps:
                    start_ts, end_ts = datetime.fromisoformat(min(timestamps)), datetime.fromisoformat(max(timestamps))
                    deleted = self.delete_shadow_logs(start_ts, end_ts)
                    if deleted > 0: self.logger.info(f"SSOT: Purged {deleted} shadow records in [{timestamps[0]} ~ {timestamps[-1]}]")
                return saved
        except Exception as e:
            self.logger.error(f"Failed to log heartrate stream: {e}")
            return 0
    def save_single_heartrate(self, timestamp: datetime, bpm: int, source: str = 'shadow') -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT OR REPLACE INTO heartrate_logs (timestamp, bpm, source) VALUES (?, ?, ?)', (timestamp.isoformat(), bpm, source))
                conn.commit()
                return True
        except Exception as e:
            self.logger.error(f"Failed to save single heartrate: {e}")
            return False
    def get_latest_heartrate(self, hours: int = 24) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 1', ((datetime.now() - timedelta(hours=hours)).isoformat(),))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"Failed to get latest heartrate: {e}")
            return None
    def delete_shadow_logs(self, start_time: datetime, end_time: datetime) -> int:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? AND source = ?', (start_time.isoformat(), end_time.isoformat(), 'shadow'))
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            self.logger.error(f"Failed to delete shadow logs: {e}")
            return 0
    def get_heartrate_range(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT timestamp, bpm, source FROM heartrate_logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC', (start_time.isoformat(), end_time.isoformat()))
                rows = cursor.fetchall()
                ts_map = {}
                for row in rows:
                    ts, bpm, source = row['timestamp'], row['bpm'], row['source']
                    if source != 'shadow': ts_map[ts] = {'timestamp': ts, 'bpm': bpm, 'source': source}
                for row in rows:
                    ts, bpm, source = row['timestamp'], row['bpm'], row['source']
                    if source == 'shadow' and ts not in ts_map: ts_map[ts] = {'timestamp': ts, 'bpm': bpm, 'source': source}
                return sorted(ts_map.values(), key=lambda x: x['timestamp'])
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
                    if row['main_sleep_seconds']: result['main_sleep_hours'] = row['main_sleep_seconds'] / 3600
                start, end = datetime.combine(target_date, datetime.min.time()), datetime.combine(target_date, datetime.max.time())
                cursor.execute('SELECT SUM(keystrokes), SUM(clicks), AVG(apm), AVG(correction_rate) FROM tactile_logs WHERE timestamp >= ? AND timestamp <= ?', (start.isoformat(), end.isoformat()))
                row = cursor.fetchone()
                if row: result['total_keystrokes'], result['total_clicks'], result['avg_apm'], result['avg_correction_rate'] = row[0] or 0, row[1] or 0, row[2] or 0.0, row[3] or 0.0
                cursor.execute('SELECT state_label, COUNT(*) as cnt FROM tactile_logs WHERE timestamp >= ? AND timestamp <= ? GROUP BY state_label', (start.isoformat(), end.isoformat()))
                state_map = {'DEEP_DIVE': 'deep_dive_minutes', 'SCAVENGING': 'scavenging_minutes', 'CRUISING': 'cruising_minutes', 'IDLE': 'idle_minutes'}
                for row in cursor.fetchall():
                    if row['state_label'] in state_map: result[state_map[row['state_label']]] = row['cnt']
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
                if not row: return False
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
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = LifeOSDatabase()
    print(f"Database path: {db.db_path}")
