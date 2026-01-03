#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Life OS Daemon v4.1.2 - BioEngine (Shadow Heartrate + Awake Offset)

Location: core/engine.py

v4.1.2 Changes:
- Awake Offset: 予測式に +10bpm を追加
  覚醒時はRHRより高いはず（睡眠時RHRまで下がらない）
  pred = base_hr + AWAKE_OFFSET + apm_component + mouse_component + work_component

v3.9.0 Changes:
- ShadowHeartrate: PC操作量から現在の心拍数を推定
- 予測式: HR_pred = HR_base + (APM × α) + (Mouse × β) + (WorkTime × γ)
- 学習機能: 実測値到着時に係数を自動補正
- 予測値の可視化サポート（is_hr_estimated, estimated_hr）
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Deque
from collections import deque
from pathlib import Path

# types.pyから共通定義をインポート
from .types import (
    JST,
    now_jst,
    HYDRATION_INTERVAL_MINUTES,
    AUTO_BREAK_IDLE_SECONDS,
    PHYSICS_TICK_INTERVAL,
    ActivityState,
    EngineState,
    PredictionPoint,
    Snapshot,
)


# ==================== v3.9: Shadow Heartrate ====================
class ShadowHeartrate:
    """
    v3.9.0: リアルタイム心拍予測モジュール
    
    Oura APIのデータ遅延（数時間）を埋めるため、
    PC操作量から現在の心拍数を推定する。
    
    予測式:
        HR_pred = HR_base + (APM × α) + (Mouse × β) + (WorkTime × γ)
    
    係数:
        α (alpha): APM係数（初期値 0.1）
        β (beta):  マウス移動量係数（初期値 0.02）
        γ (gamma): 連続作業時間係数（初期値 0.05）
    
    学習機能:
        実測値到着時、予測値との誤差から係数を微調整
        Learning Rate = 0.001（保守的な学習率）
    """
    
    # 係数の初期値
    DEFAULT_ALPHA = 0.10   # APM係数
    DEFAULT_BETA = 0.02    # マウス移動量係数（px/secあたり）
    DEFAULT_GAMMA = 0.05   # 連続作業時間係数（時間あたり）
    
    # v4.1.2: 覚醒時オフセット（RHRは睡眠時の最低値なので、起きている間は常に高い）
    AWAKE_OFFSET = 10  # bpm
    
    # 学習率（保守的に設定）
    LEARNING_RATE = 0.001
    
    # 係数の上下限
    MIN_ALPHA = 0.01
    MAX_ALPHA = 0.5
    MIN_BETA = 0.001
    MAX_BETA = 0.1
    MIN_GAMMA = 0.01
    MAX_GAMMA = 0.2
    
    # 予測値の上下限
    MIN_HR = 45
    MAX_HR = 180
    
    def __init__(self, state_path: Optional[Path] = None):
        """
        Args:
            state_path: daemon_state.jsonのパス（係数の永続化用）
        """
        self.state_path = state_path
        
        # 係数を初期化
        self.alpha = self.DEFAULT_ALPHA
        self.beta = self.DEFAULT_BETA
        self.gamma = self.DEFAULT_GAMMA
        
        # 学習履歴（直近10件）
        self._error_history: Deque[float] = deque(maxlen=10)
        
        # 最後の予測情報（学習用）
        self._last_prediction: Optional[Dict] = None
        
        # 係数をファイルから読み込み
        self._load_coefficients()
    
    def _load_coefficients(self):
        """daemon_state.jsonから係数を読み込み"""
        if self.state_path is None:
            return
        
        try:
            if self.state_path.exists():
                with open(self.state_path, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                
                shadow_state = state.get('shadow_heartrate', {})
                self.alpha = shadow_state.get('alpha', self.DEFAULT_ALPHA)
                self.beta = shadow_state.get('beta', self.DEFAULT_BETA)
                self.gamma = shadow_state.get('gamma', self.DEFAULT_GAMMA)
                
                print(f"v3.9 Shadow HR: Loaded coefficients (α={self.alpha:.4f}, β={self.beta:.4f}, γ={self.gamma:.4f})")
        except Exception as e:
            print(f"v3.9 Shadow HR: Using default coefficients (load error: {e})")
    
    def _save_coefficients(self):
        """係数をdaemon_state.jsonに保存"""
        if self.state_path is None:
            return
        
        try:
            state = {}
            if self.state_path.exists():
                with open(self.state_path, 'r', encoding='utf-8') as f:
                    state = json.load(f)
            
            state['shadow_heartrate'] = {
                'alpha': self.alpha,
                'beta': self.beta,
                'gamma': self.gamma,
                'last_updated': now_jst().isoformat(),
            }
            
            with open(self.state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"v3.9 Shadow HR: Failed to save coefficients: {e}")
    
    def predict(
        self,
        base_hr: int,
        apm: float,
        mouse_speed: float,
        work_hours: float
    ) -> int:
        """
        心拍数を予測
        
        Args:
            base_hr: 基準心拍数（RHR）
            apm: 現在のAPM
            mouse_speed: マウス移動速度（px/sec）
            work_hours: 連続作業時間（時間）
        
        Returns:
            予測心拍数（45-180でクランプ）
        """
        # 予測式: HR_pred = HR_base + AWAKE_OFFSET + (APM × α) + (Mouse × β) + (WorkTime × γ)
        # v4.1.2: AWAKE_OFFSET追加（覚醒時はRHRより常に高い）
        apm_component = apm * self.alpha
        mouse_component = mouse_speed * self.beta
        # v3.9.1: Cardiac Drift強化（長時間作業による心拍上昇を2倍に見積もる）
        work_component = work_hours * self.gamma * 20  # 10 → 20
        
        pred = base_hr + self.AWAKE_OFFSET + apm_component + mouse_component + work_component
        
        # クランプ
        pred_clamped = max(self.MIN_HR, min(self.MAX_HR, int(pred)))
        
        # 最後の予測を保存（学習用）
        self._last_prediction = {
            'timestamp': now_jst().isoformat(),
            'base_hr': base_hr,
            'apm': apm,
            'mouse_speed': mouse_speed,
            'work_hours': work_hours,
            'predicted_hr': pred_clamped,
        }
        
        return pred_clamped
    
    def learn(
        self,
        actual_hr: int,
        predicted_hr: int,
        apm: float,
        mouse_speed: float,
        work_hours: float
    ) -> Dict:
        """
        実測値と予測値の誤差から係数を学習
        
        Args:
            actual_hr: 実測心拍数
            predicted_hr: 予測心拍数
            apm: その時点のAPM
            mouse_speed: その時点のマウス速度
            work_hours: その時点の連続作業時間
        
        Returns:
            学習結果のDict
        """
        # 誤差計算: E = HR_true - HR_pred
        error = actual_hr - predicted_hr
        self._error_history.append(error)
        
        # 係数の更新（勾配降下法の簡易版）
        # 各係数を誤差の方向に微調整
        old_alpha = self.alpha
        old_beta = self.beta
        old_gamma = self.gamma
        
        # α ← α + (E × LR × sign(APM))
        if apm > 0:
            self.alpha += error * self.LEARNING_RATE * (1 if apm > 50 else 0.5)
        
        # β ← β + (E × LR × sign(Mouse))
        if mouse_speed > 0:
            self.beta += error * self.LEARNING_RATE * (1 if mouse_speed > 100 else 0.5)
        
        # γ ← γ + (E × LR × sign(WorkTime))
        if work_hours > 0:
            self.gamma += error * self.LEARNING_RATE * (1 if work_hours > 1 else 0.5)
        
        # クランプ
        self.alpha = max(self.MIN_ALPHA, min(self.MAX_ALPHA, self.alpha))
        self.beta = max(self.MIN_BETA, min(self.MAX_BETA, self.beta))
        self.gamma = max(self.MIN_GAMMA, min(self.MAX_GAMMA, self.gamma))
        
        # 保存
        self._save_coefficients()
        
        result = {
            'error': error,
            'alpha_delta': self.alpha - old_alpha,
            'beta_delta': self.beta - old_beta,
            'gamma_delta': self.gamma - old_gamma,
            'new_alpha': self.alpha,
            'new_beta': self.beta,
            'new_gamma': self.gamma,
            'mean_error': sum(self._error_history) / len(self._error_history) if self._error_history else 0,
        }
        
        print(f"v3.9 Shadow HR Learn: error={error:+d}bpm, "
              f"α={self.alpha:.4f} (Δ{result['alpha_delta']:+.4f}), "
              f"β={self.beta:.4f} (Δ{result['beta_delta']:+.4f}), "
              f"γ={self.gamma:.4f} (Δ{result['gamma_delta']:+.4f})")
        
        return result
    
    def get_coefficients(self) -> Dict:
        """現在の係数を取得"""
        return {
            'alpha': self.alpha,
            'beta': self.beta,
            'gamma': self.gamma,
            'error_history': list(self._error_history),
            'mean_error': sum(self._error_history) / len(self._error_history) if self._error_history else 0,
        }


class BioEngine:
    """
    v3.4.4 BioEngine - Telemetry Polish
    
    v3.4.4 新機能:
    - Mouse Speed (px/sec): EMA平滑化された現在のマウス速度
    - Rolling Correction Rate: 直近60秒間の修正率
    
    継承機能:
    - Cumulative Strategy
    - Physics/Animation Tick分離
    - クロノタイプ動的学習
    - 負債返済の動的化
    """
    
    # 減衰率（Readinessベース、1時間あたり）
    DECAY_RATES = {
        'high': 0.04,      # 85+ : 4%/h
        'mid': 0.07,       # 60-84: 7%/h
        'low': 0.12,       # 40-59: 12%/h
        'critical': 0.18   # <40 : 18%/h
    }
    
    # v3.9.1: FP計算・予測の一元化定数
    DEBT_PENALTY_MULTIPLIER = 3.0   # 負債ペナルティ係数（5.0→3.0に緩和）
    BREAK_RECOMMEND_THRESHOLD = 20.0  # 休憩推奨FP閾値（30→20に緩和）
    BEDTIME_THRESHOLD = 10.0        # 活動限界FP閾値（15→10に緩和）
    
    # 活動状態ごとのブースト効率
    ACTIVITY_EFFICIENCY = {
        ActivityState.IDLE: 0.0,
        ActivityState.LIGHT: 0.3,
        ActivityState.MODERATE: 0.7,
        ActivityState.DEEP_DIVE: 1.5,
        ActivityState.HYPERFOCUS: 2.0,
    }
    
    # 連続作業による減衰加速
    WORK_DECAY_MULTIPLIERS = {
        2.0: 1.2,
        3.0: 1.5,
        4.0: 1.8,
        5.0: 2.0,
    }
    
    def __init__(
        self,
        readiness: int = 75,
        sleep_score: int = 75,
        wake_time: Optional[datetime] = None,
        db_path: Optional[Path] = None
    ):
        """
        v3.4.3: Cumulative Strategy対応初期化
        
        Args:
            readiness: Oura Readiness Score (0-100)
            sleep_score: Oura Sleep Score (0-100)
            wake_time: 起床時刻
            db_path: DBパス（クロノタイプ学習用）
        """
        self.initial_readiness = readiness
        self.readiness = readiness
        self.sleep_score = sleep_score
        self.db_path = db_path
        self.main_sleep_seconds = 0
        now = now_jst()
        if wake_time is None:
            self.wake_time = now - timedelta(hours=8)
        else:
            self.wake_time = wake_time
        
        self.hours_since_wake = max(0, (now - self.wake_time).total_seconds() / 3600)
        
        # Initial FP = readiness * 0.7 + sleep_score * 0.3
        initial_fp = readiness * 0.7 + sleep_score * 0.3
        initial_fp = max(10, min(100, initial_fp))
        
        # 経過時間分の減衰を適用
        decay_rate = self._get_base_decay_rate(readiness, sleep_score)
        self.base_fp = initial_fp * math.exp(-decay_rate * self.hours_since_wake)
        self.base_fp = max(10, min(100, self.base_fp))
        
        # ブースト（現在値と目標値）
        self.boost_fp = 0.0
        self.target_boost_fp = 0.0
        
        # 負債（上限10.0）
        self.debt = 0.0
        
        # 負荷
        self.current_load = 0.0
        
        # 活動状態
        self.activity_state = ActivityState.IDLE
        
        # Correction Factor
        self.correction_factor = 1.0
        
        # リアルタイム予測用
        self.estimated_readiness = float(readiness)
        self.baseline_hr = 60
        self.cumulative_hr_deviation = 0.0
        self.cumulative_load = 0.0
        
        # 連続作業追跡
        self.work_start_time: Optional[datetime] = None
        self.last_active_time: Optional[datetime] = None
        self.continuous_work_hours = 0.0
        self.idle_threshold_seconds = 300
        
        # 自動休憩用IDLE継続時間追跡
        self.idle_start_time: Optional[datetime] = None
        self.continuous_idle_seconds = 0.0
        
        # 水分補給追跡
        self.last_break_time = now
        
        # Time Machine Buffer
        self.history: Deque[Snapshot] = deque(maxlen=360)
        
        # 最終更新時刻
        self.last_update = now
        
        # v3.4.2: Physics Tick用
        self.last_physics_tick = now
        self.physics_accumulated_dt = 0.0
        
        # v3.4.2: 予測曲線キャッシュ
        self._cached_prediction: Optional[Dict[str, List[PredictionPoint]]] = None
        self._prediction_cache_time: Optional[datetime] = None
        
        # v3.4.2: クロノタイプ動的学習
        self.hourly_efficiency: Dict[int, float] = {}
        self.daily_avg_apm = 1.0  # ゼロ除算防止
        self._load_chronotype_data()
        
        # v3.4.3: 累計値追跡（Cumulative Strategy）
        # Daemonから受け取った累計値を保持
        self.session_mouse_pixels = 0.0
        self.session_backspace_count = 0
        self.session_apm_samples = []
        # 前回受け取った累計値（差分計算用）
        self._last_cumulative_mouse = 0.0
        self._last_cumulative_backspace = 0
        self._last_cumulative_keys = 0
        self._last_cumulative_scroll = 0  # v3.5: スクロール
        # Phantom Recovery総量
        self.phantom_recovery_sum = 0.0
        self._last_phantom_recovery_sum = 0.0  # v3.5: 増分計算用
        
        # v3.4.4: 速度計 (Speedometer)
        self.current_mouse_speed = 0.0  # px/sec (EMA平滑化)
        self._mouse_speed_ema_alpha = 0.3  # EMA係数（0.3 = 適度な平滑化）
        
        # v3.4.4: 直近修正率 (Rolling Window)
        self._rolling_backspace_window: Deque[int] = deque(maxlen=60)  # 直近60秒
        self._rolling_keys_window: Deque[int] = deque(maxlen=60)
        self.recent_correction_rate = 0.0  # 直近修正率
        
        # v3.5: スクロール検知
        self.session_scroll_steps = 0
        self._rolling_scroll_window: Deque[int] = deque(maxlen=60)
        
        # v3.5: Shisha Override
        self.is_shisha_active = False
        
        # v3.5.1: 遡及補正 (Retroactive Correction)
        self._last_retroactive_check = now
        self._retroactive_interval = 5.0  # 5秒に1回チェック
        self._processed_hr_timestamps: set = set()  # 処理済みタイムスタンプ
        
        # v3.5.2: Nap Recovery（仮眠によるFP回復）
        self._last_total_nap_minutes = 0.0
        self.total_nap_minutes = 0.0
        
        # v3.5.3: Hydration（DB駆動型状態復元）
        self.cumulative_hr_deviation = 0.0  # 基準心拍からの乖離累積
        self.cumulative_load = 0.0  # 負荷の累積
        self._hydration_completed = False
        
        # v3.6: Heart-Linked Debt（心拍連動型負債）
        self.current_hr: Optional[int] = None  # 現在の心拍数
        self._last_hr_source: str = 'unknown'  # 最後のHRソース
        
        # v3.9: Shadow Heartrate（リアルタイム心拍予測）
        state_path = (self.db_path.parent / "logs" / "daemon_state.json") if self.db_path else None
        self.shadow_hr = ShadowHeartrate(state_path=state_path)
        self.estimated_hr: Optional[int] = None  # 予測心拍数
        self.is_hr_estimated: bool = False  # 予測値かどうか
        self.hr_last_update: Optional[datetime] = None
        self._hr_stale_threshold_seconds = 300
        self._cached_boost_efficiency = 1.0
        self._cached_decay_rate = decay_rate
        self.stress_index = 0.0
        self._stress_ema_alpha = 0.1
        self.recovery_efficiency = 1.0
        self.MAX_HR = 190
        self.main_sleep_seconds = 0
        self._hydrate_from_db()
    
    def _hydrate_from_db(self):
        """
        v3.5.3: 起動時の記憶復元 (Database-Driven Physics)
        
        DBから過去24時間分の心拍データを取得し、
        エンジンの物理状態を「現在までの履歴に基づいた正しい値」に復元する。
        
        復元される状態:
        - cumulative_hr_deviation: 基準心拍(RHR)からの乖離累積
        - cumulative_load: 負荷の累積
        - estimated_readiness: 上記累積値に基づく現在のReadiness再評価
        - base_fp: 累積負荷を反映したFP調整
        """
        if self.db_path is None:
            print("v3.5.3 Hydration: No DB path configured, skipping")
            return
        
        try:
            db_file = self.db_path / "life_os.db"
            if not db_file.exists():
                print("v3.5.3 Hydration: DB file not found, starting fresh")
                return
            
            conn = sqlite3.connect(str(db_file))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 過去24時間分の心拍データを取得
            now = now_jst()
            start_time = now - timedelta(hours=24)
            
            cursor.execute('''
                SELECT timestamp, bpm, source
                FROM heartrate_logs
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            ''', (start_time.isoformat(), now.isoformat()))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                print("v3.5.3 Hydration: No HR data in last 24h, starting fresh")
                return
            
            # 心拍データを時系列で走査してコンテキストを再構築
            total_deviation = 0.0
            total_load = 0.0
            awake_count = 0
            rest_count = 0
            high_hr_minutes = 0  # 高心拍状態の分数
            
            for row in rows:
                bpm = row['bpm']
                source = row['source'] or 'unknown'
                
                # 基準心拍からの乖離を計算
                deviation = bpm - self.baseline_hr
                total_deviation += deviation
                
                # 負荷を推定（心拍が高いほど負荷が高い）
                if deviation > 0:
                    # 正の乖離 = 負荷がかかっている状態
                    load_factor = min(1.0, deviation / 40)  # 40bpm乖離で最大負荷
                    total_load += load_factor
                    
                    if deviation > 20:  # 基準+20以上は高負荷
                        high_hr_minutes += 1
                
                if source == 'awake':
                    awake_count += 1
                elif source == 'rest':
                    rest_count += 1
            
            record_count = len(rows)
            
            # 累積値を保存
            self.cumulative_hr_deviation = total_deviation
            self.cumulative_load = total_load
            
            # estimated_readinessの調整
            # 高負荷時間が長いほどReadinessを下げる
            if record_count > 0:
                avg_deviation = total_deviation / record_count
                avg_load = total_load / record_count
                
                # 負荷に基づくReadiness調整（-20 〜 +5の範囲）
                # 平均乖離が正（心拍高め）= 疲労 → Readiness低下
                # 平均乖離が負（心拍低め）= 回復 → Readiness微増
                readiness_adjustment = -avg_deviation * 0.3  # 乖離10 = -3 Readiness
                readiness_adjustment = max(-20, min(5, readiness_adjustment))
                
                old_readiness = self.estimated_readiness
                self.estimated_readiness = max(30, min(100, 
                    self.readiness + readiness_adjustment))
                
                # base_fpの調整（累積負荷を反映）
                # 高負荷時間が多いほどFPを下げる
                fp_penalty = high_hr_minutes * 0.05  # 1分あたり0.05 FP減少
                fp_penalty = min(20, fp_penalty)  # 最大20 FP減少
                
                old_fp = self.base_fp
                self.base_fp = max(30, self.base_fp - fp_penalty)
                
                self._hydration_completed = True
                
                print(f"v3.5.3 Hydration Complete: "
                      f"processed {record_count} records over 24h, "
                      f"avg_deviation={avg_deviation:.1f}bpm, "
                      f"high_hr_minutes={high_hr_minutes}, "
                      f"readiness: {old_readiness:.0f}→{self.estimated_readiness:.0f}, "
                      f"base_fp: {old_fp:.1f}→{self.base_fp:.1f}")
                
                # v3.9: 最新のHRデータでShadow HR初期化
                last_row = rows[-1]
                try:
                    last_ts = datetime.fromisoformat(last_row['timestamp'])
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=JST)
                    
                    self.current_hr = last_row['bpm']
                    self.hr_last_update = last_ts
                    
                    hr_age_seconds = (now - last_ts).total_seconds()
                    if hr_age_seconds >= self._hr_stale_threshold_seconds:
                        self.is_hr_estimated = True
                        print(f"v3.9 Shadow HR Hydration: Last HR is {hr_age_seconds/60:.1f}min old, enabling estimation")
                    else:
                        self.is_hr_estimated = False
                        self.estimated_hr = last_row['bpm']
                        print(f"v3.9 Shadow HR Hydration: Using actual HR (age={hr_age_seconds/60:.1f}min)")
                except Exception as e:
                    print(f"v3.9 Shadow HR Hydration: Timestamp parse error: {e}")
            else:
                print("v3.5.3 Hydration: No valid records to process")
        
        except Exception as e:
            # DB接続エラーやデータ不在時もデフォルト値で起動
            print(f"v3.5.3 Hydration Error: {e}, starting with defaults")
            self._hydration_completed = False
    
    def _calculate_effective_fp(self) -> float:
        """
        v3.9.1: FP計算ロジックの一元化 (Physiological Integrity)
        
        effective_fpの計算式を集約し、どこから呼んでも一貫した値を返す。
        
        計算式:
            effective_fp = base_fp + (boost_fp × boost_efficiency) - (debt × DEBT_PENALTY_MULTIPLIER)
        
        Returns:
            float: 10.0 〜 100.0 にクランプされたeffective_fp
        """
        raw_fp = (
            self.base_fp 
            + (self.boost_fp * self._cached_boost_efficiency) 
            - (self.debt * self.DEBT_PENALTY_MULTIPLIER)
        )
        return max(10.0, min(100.0, raw_fp))
    
    def _load_chronotype_data(self):
        """
        v3.7: 適応型クロノタイプ学習（学習要件緩和）
        
        DBから直近7日間のデータを読み込み、時間帯別平均APMを計算。
        データ不足時はデフォルトの概日リズムとブレンドする。
        
        ブレンド式:
            W = min(1.0, N_act / N_req)  where N_req = 48 (24h × 2days)
            E_hour = (E_learned × W) + (E_default × (1 - W))
        
        v3.7: N_reqを168→48に緩和（2日分のデータで学習開始）
        """
        # v3.6: デフォルトの概日リズム（一般的なサーカディアンリズム）
        DEFAULT_CIRCADIAN_RHYTHM = {
            0: 0.6, 1: 0.5, 2: 0.4, 3: 0.4, 4: 0.5, 5: 0.6,      # 深夜〜早朝: 低い
            6: 0.7, 7: 0.8, 8: 0.9, 9: 1.1, 10: 1.2, 11: 1.3,    # 朝: 上昇→ピーク
            12: 1.1, 13: 0.9, 14: 0.8, 15: 0.85, 16: 0.95,       # 昼食後: 低下
            17: 1.1, 18: 1.2, 19: 1.15, 20: 1.0, 21: 0.9,        # 夕方: 第二ピーク
            22: 0.8, 23: 0.7                                       # 夜: 低下
        }
        
        # v3.7: 必要なデータ数を緩和（2日分 = 24時間 × 2日）
        N_REQ = 48
        
        # デフォルト値で初期化
        self.hourly_efficiency = DEFAULT_CIRCADIAN_RHYTHM.copy()
        self.daily_avg_apm = 1.0
        self._using_default_chronotype = True
        self._chronotype_blend_ratio = 0.0
        
        if self.db_path is None:
            print("v3.7 Chronotype: Using default circadian rhythm (no DB)")
            return
        
        try:
            db_file = self.db_path / "life_os.db"
            if not db_file.exists():
                print("v3.7 Chronotype: Using default circadian rhythm (DB not found)")
                return
            
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            
            # 直近7日間のデータを取得（時間帯別の平均APMとデータ数）
            seven_days_ago = (now_jst() - timedelta(days=7)).isoformat()
            
            # v4.4.0: テーブル名修正 (telemetry -> tactile_logs)
            cursor.execute("""
                SELECT strftime('%H', timestamp) as hour, 
                       avg(apm) as avg_apm,
                       count(*) as count
                FROM tactile_logs
                WHERE timestamp >= ? AND apm > 0
                GROUP BY hour
            """, (seven_days_ago,))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                print("v3.6 Chronotype: Using default circadian rhythm (no data)")
                return
            
            # 時間帯別APMとデータ数を集計
            hourly_apm = {}
            hourly_count = {}
            total_apm = 0.0
            total_count = 0
            
            for row in rows:
                hour = int(row[0])
                avg_apm = float(row[1])
                count = int(row[2])
                hourly_apm[hour] = avg_apm
                hourly_count[hour] = count
                total_apm += avg_apm * count
                total_count += count
            
            if total_count == 0:
                print("v3.6 Chronotype: Using default circadian rhythm (no valid data)")
                return
            
            # v3.6: ブレンド率を計算
            # W = min(1.0, N_act / N_req)
            blend_weight = min(1.0, total_count / N_REQ)
            self._chronotype_blend_ratio = blend_weight
            
            # 全体平均APMを計算
            self.daily_avg_apm = total_apm / total_count
            
            # v3.6: 適応型ブレンド
            # E_hour = (E_learned × W) + (E_default × (1 - W))
            for hour in range(24):
                e_default = DEFAULT_CIRCADIAN_RHYTHM[hour]
                
                if hour in hourly_apm and self.daily_avg_apm > 0:
                    e_learned = hourly_apm[hour] / self.daily_avg_apm
                    # ブレンド
                    self.hourly_efficiency[hour] = (e_learned * blend_weight) + (e_default * (1 - blend_weight))
                else:
                    # データがない時間帯はデフォルトを使用
                    self.hourly_efficiency[hour] = e_default
            
            if blend_weight >= 0.5:
                self._using_default_chronotype = False
            
            print(f"v3.6 Chronotype: Blended ({total_count}/{N_REQ} records, W={blend_weight:.2f})")
            
        except Exception as e:
            # エラー時はデフォルト値を維持
            print(f"v3.6 Chronotype: Using default circadian rhythm (error: {e})")
    
    def set_readiness(self, readiness: int):
        """Readiness更新"""
        self.initial_readiness = readiness
        self.readiness = readiness
        self.estimated_readiness = float(readiness)
        self.cumulative_hr_deviation = 0.0
        self.cumulative_load = 0.0
    
    def set_sleep_score(self, sleep_score: int):
        """Sleep Score更新"""
        self.sleep_score = sleep_score
    
    def set_wake_time(self, wake_time: datetime):
        """
        v3.5: 起床時刻を設定 + Morning Reset
        
        起床時刻が4時間以上変化した場合は「新しい一日」と判断し、
        base_fpをreadiness + sleep_scoreに基づいて再計算する
        """
        # v3.5: Morning Reset判定
        MORNING_RESET_THRESHOLD_HOURS = 4
        
        if self.wake_time is not None:
            time_diff = abs((wake_time - self.wake_time).total_seconds() / 3600)
            
            if time_diff >= MORNING_RESET_THRESHOLD_HOURS:
                # 新しい一日 → FPを初期化
                initial_fp = self.readiness * 0.7 + self.sleep_score * 0.3
                initial_fp = max(10, min(100, initial_fp))
                
                old_fp = self.base_fp
                self.base_fp = initial_fp
                
                # Phantom Recovery累計もリセット
                self._last_phantom_recovery_sum = self.phantom_recovery_sum
                
                print(f"v3.5 Morning Reset: Wake time updated ({time_diff:.1f}h change). "
                      f"Resetting FP: {old_fp:.1f} → {initial_fp:.1f}")
        
        self.wake_time = wake_time
        now = now_jst()
        self.hours_since_wake = max(0, (now - wake_time).total_seconds() / 3600)
    
    def set_baseline_hr(self, rhr: int):
        """基準心拍設定"""
        self.baseline_hr = rhr
    
    def record_break(self):
        """休憩を記録（水分補給タイマーリセット）"""
        self.last_break_time = now_jst()
        self.idle_start_time = None
        self.continuous_idle_seconds = 0.0
    
    def _get_base_decay_rate(self, readiness: int, sleep_score: int) -> float:
        """基本減衰率を算出"""
        if readiness >= 85:
            base = self.DECAY_RATES['high']
        elif readiness >= 60:
            base = self.DECAY_RATES['mid']
        elif readiness >= 40:
            base = self.DECAY_RATES['low']
        else:
            base = self.DECAY_RATES['critical']
        
        if sleep_score < 70:
            base *= 1.2
        elif sleep_score > 85:
            base *= 0.9
        
        return base
    
    def _get_work_decay_multiplier(self) -> float:
        """連続作業時間に基づく減衰倍率"""
        multiplier = 1.0
        for hours, mult in sorted(self.WORK_DECAY_MULTIPLIERS.items()):
            if self.continuous_work_hours >= hours:
                multiplier = mult
        return multiplier
    
    def _determine_activity_state(self, apm: float, mouse_pixels: float) -> ActivityState:
        """活動状態を判定"""
        intensity = min(1.0, (apm / 100 + mouse_pixels / 5000)) / 2
        
        if intensity < 0.05:
            return ActivityState.IDLE
        elif intensity < 0.2:
            return ActivityState.LIGHT
        elif intensity < 0.5:
            return ActivityState.MODERATE
        elif intensity < 0.8:
            return ActivityState.DEEP_DIVE
        else:
            return ActivityState.HYPERFOCUS
    
    def _determine_activity_state_with_scroll(self, apm: float, mouse_pixels: float, 
                                              scroll_steps: int) -> ActivityState:
        """
        v3.5: スクロール対応の活動状態判定
        スクロールがある場合はIDLEではなくLIGHT/MODERATEと判定
        """
        intensity = min(1.0, (apm / 100 + mouse_pixels / 5000)) / 2
        
        # v3.5: スクロールも強度に加算
        SCROLL_INTENSITY_FACTOR = 0.01  # スクロール1ステップあたりの強度
        scroll_intensity = min(0.3, scroll_steps * SCROLL_INTENSITY_FACTOR)
        total_intensity = min(1.0, intensity + scroll_intensity)
        
        if total_intensity < 0.05:
            return ActivityState.IDLE
        elif total_intensity < 0.2:
            return ActivityState.LIGHT
        elif total_intensity < 0.5:
            return ActivityState.MODERATE
        elif total_intensity < 0.8:
            return ActivityState.DEEP_DIVE
        else:
            return ActivityState.HYPERFOCUS
    
    def _calculate_correction_factor(self, apm: float, backspace_count: int) -> float:
        """Correction Factor計算"""
        if apm <= 0:
            return 1.0
        
        ratio = backspace_count / apm
        corr = max(0.5, 1.0 - ratio * 2)
        return corr
    
    def _get_boost_efficiency(self) -> float:
        """
        v3.4.2: ブースト効率を計算（クロノタイプ動的学習）
        
        Eff = (AvgAPM_hour / AvgAPM_daily) × ReadinessFactor
        """
        now = now_jst()
        hour = now.hour
        
        # v3.4.2: クロノタイプベースの時間帯補正
        chronotype_factor = self.hourly_efficiency.get(hour, 1.0)
        
        # Readiness補正
        readiness_factor = max(0.5, min(1.2, self.readiness / 75))
        
        return chronotype_factor * readiness_factor
    
    def _get_dynamic_repayment_rate(self) -> float:
        """
        v3.4.2: 動的負債返済率
        
        RepaymentRate = 0.002 × (Readiness/80) × (SleepScore/75)
        """
        readiness_factor = self.readiness / 80.0
        sleep_factor = self.sleep_score / 75.0
        
        return 0.002 * readiness_factor * sleep_factor
    
    def _update_work_tracking(self, apm: float, now: datetime):
        """連続作業時間の追跡"""
        is_active = apm > 10
        
        if is_active:
            if self.work_start_time is None:
                self.work_start_time = now
            self.last_active_time = now
            self.continuous_work_hours = (now - self.work_start_time).total_seconds() / 3600
        else:
            if self.last_active_time:
                idle_duration = (now - self.last_active_time).total_seconds()
                if idle_duration > self.idle_threshold_seconds:
                    self.work_start_time = None
                    self.continuous_work_hours = 0.0
    
    def _update_idle_tracking(self, now: datetime):
        """IDLE継続時間の追跡と自動休憩記録"""
        if self.activity_state == ActivityState.IDLE:
            if self.idle_start_time is None:
                self.idle_start_time = now
            self.continuous_idle_seconds = (now - self.idle_start_time).total_seconds()
            
            if self.continuous_idle_seconds >= AUTO_BREAK_IDLE_SECONDS:
                self.record_break()
        else:
            self.idle_start_time = None
            self.continuous_idle_seconds = 0.0
    
    def _update_realtime_readiness(self, hr: Optional[int], dt_seconds: float):
        """リアルタイムReadiness予測"""
        if hr and self.baseline_hr > 0:
            hr_deviation = max(0, hr - self.baseline_hr)
            self.cumulative_hr_deviation += hr_deviation * (dt_seconds / 3600) * 0.5
        
        self.cumulative_load += self.current_load * (dt_seconds / 3600) * 0.3
        
        estimated = self.initial_readiness - (self.cumulative_hr_deviation * 0.1) - (self.cumulative_load * 0.05)
        self.estimated_readiness = max(0, min(100, estimated))
    
    def _calculate_hr_stress_factor(self) -> float:
        """
        v3.9.1: 心拍ストレス係数 (F_HR) の計算 - Shadow HR統合
        
        Formula:
            Ratio = HR_target / HR_baseline
            F_HR = clamp(1.0, 3.0, 1.0 + (Ratio - 1.0) × 2.0)
        
        v3.9.1: 実測HRがない場合はShadow HR（予測値）を使用
        
        Returns:
            float: 1.0 〜 3.0 のストレス係数
        """
        # v3.9.1: Physics Integration - target_hrを決定
        # Shadow HRが有効な場合は予測値を使用し、物理演算に反映
        if self.is_hr_estimated and self.estimated_hr is not None:
            target_hr = self.estimated_hr
        else:
            target_hr = self.current_hr
        
        if target_hr is None or self.baseline_hr <= 0:
            return 1.0
        
        ratio = target_hr / self.baseline_hr
        f_hr = 1.0 + (ratio - 1.0) * 2.0
        return max(1.0, min(3.0, f_hr))
    
    def _calculate_physics(self, dt_seconds: float, apm: float, mouse_pixels: float, backspace_count: int):
        dt_hours = dt_seconds / 3600
        f_hr = self._calculate_hr_stress_factor()
        self._cached_decay_rate = self._get_base_decay_rate(int(self.estimated_readiness), self.sleep_score)
        work_multiplier = self._get_work_decay_multiplier()
        self.correction_factor = self._calculate_correction_factor(apm, backspace_count)
        friction_multiplier = 1.0 + (1.0 - self.correction_factor) * 2.0
        effective_decay = self._cached_decay_rate * work_multiplier * (1 + self.debt * 0.1) * f_hr * friction_multiplier
        self.base_fp = self.base_fp * math.exp(-effective_decay * dt_hours)
        self.base_fp = max(5, self.base_fp)
        intensity = min(1.0, (apm / 100 + mouse_pixels / 5000)) / 2
        capacity = max(0, (self.readiness - 40) / 60)
        efficiency = self.ACTIVITY_EFFICIENCY.get(self.activity_state, 0.5)
        self.target_boost_fp = intensity * efficiency * capacity * self.correction_factor * 50.0
        if self.boost_fp > 5:
            base_accumulation = self.boost_fp * 0.001 * dt_seconds
            self.debt += base_accumulation * f_hr
        elif self.boost_fp < 2:
            repayment_rate = self._get_dynamic_repayment_rate()
            repayment_penalty = 1.0 / f_hr
            self.debt -= repayment_rate * dt_seconds * repayment_penalty
        self.debt = max(0, min(10.0, self.debt))
        self._cached_boost_efficiency = self._get_boost_efficiency()
        self._cached_prediction = None
    
    def _animate_boost(self, dt_seconds: float):
        """
        v3.4.2: Animation Tick - 軽い計算（毎フレーム）
        
        現在のboost_fpを目標値に向かってLerp
        """
        if self.target_boost_fp > self.boost_fp:
            lerp_factor = min(1.0, 0.15 * dt_seconds * 10)  # 上昇は速め
        else:
            lerp_factor = min(1.0, 0.02 * dt_seconds * 10)  # 下降は緩やか
        
        self.boost_fp += (self.target_boost_fp - self.boost_fp) * lerp_factor
        self.boost_fp = max(0, min(100, self.boost_fp))
    
    def update(
        self,
        apm: float = 0,
        cumulative_mouse_pixels: float = 0,
        cumulative_backspace_count: int = 0,
        cumulative_key_count: int = 0,
        cumulative_scroll_steps: int = 0,
        phantom_recovery_sum: float = 0,
        hr: Optional[int] = None,
        hr_stream: Optional[List[Dict]] = None,
        total_nap_minutes: float = 0.0,
        dt_seconds: float = 0.1,
        is_shisha_active: bool = False,
        is_hr_estimated: bool = False  # v3.9: 予測HRフラグ
    ) -> EngineState:
        """
        v3.9: メイン更新ループ（Shadow Heartrate対応）
        
        Args:
            apm: Actions Per Minute（瞬時値）
            cumulative_mouse_pixels: マウス移動距離（累計値）
            cumulative_backspace_count: バックスペース回数（累計値）
            cumulative_key_count: キー押下回数（累計値）
            cumulative_scroll_steps: スクロール回数（累計値）[v3.5]
            phantom_recovery_sum: Phantom Recovery総量
            hr: 現在の心拍数（実測or予測）
            hr_stream: 心拍ストリーム [v3.5.1]
            total_nap_minutes: 仮眠の合計時間（分）[v3.5.2]
            dt_seconds: 経過秒数
            is_shisha_active: シーシャセッション中か [v3.5]
            is_hr_estimated: hrが予測値かどうか [v3.9]
        
        Note:
            累計値は上書きし、差分は内部で計算する
        """
        now = now_jst()
        
        # v3.5: Shisha Override状態を保存
        self.is_shisha_active = is_shisha_active
        
        # ===== v3.9: Shadow Heartrate（リアルタイム心拍予測） =====
        # v3.9: 呼び出し側から予測フラグを受け取る
        if hr is not None:
            self.current_hr = hr
            self.estimated_hr = hr
            self.is_hr_estimated = is_hr_estimated  # 呼び出し側の判定を尊重
            
            if not is_hr_estimated:
                # 実測値の場合のみhr_last_updateを更新
                self.hr_last_update = now
        else:
            # 心拍データがない場合、内部でShadow HRを計算
            if self.hr_last_update is not None:
                hr_age_seconds = (now - self.hr_last_update).total_seconds()
            else:
                hr_age_seconds = float('inf')
            
            # 5分以上古い場合はシャドウ心拍を計算
            if hr_age_seconds >= self._hr_stale_threshold_seconds:
                self.is_hr_estimated = True
                self.estimated_hr = self.shadow_hr.predict(
                    base_hr=self.baseline_hr,
                    apm=apm,
                    mouse_speed=self.current_mouse_speed,
                    work_hours=self.continuous_work_hours
                )
                self.current_hr = self.estimated_hr  # 予測値をcurrent_hrにも設定
            else:
                # データは古いが閾値内 → 最後のHRを維持
                self.is_hr_estimated = False
                self.estimated_hr = self.current_hr
        
        # v3.4.3: 累計値から差分を計算
        delta_mouse = max(0, cumulative_mouse_pixels - self._last_cumulative_mouse)
        delta_backspace = max(0, cumulative_backspace_count - self._last_cumulative_backspace)
        delta_keys = max(0, cumulative_key_count - self._last_cumulative_keys)
        delta_scroll = max(0, cumulative_scroll_steps - self._last_cumulative_scroll)
        if apm == 0 and delta_mouse == 0:
            self.stress_index *= 0.95
            if self.estimated_hr is not None:
                self.estimated_hr = int(self.estimated_hr + (self.baseline_hr - self.estimated_hr) * 0.05)
                self.current_hr = self.estimated_hr
            self._update_recovery_efficiency()
        else:
            self._update_stress_index(apm)
            self._update_recovery_efficiency()
        delta_nap = max(0, total_nap_minutes - self._last_total_nap_minutes)
        if delta_nap > 0:
            DEEP_SLEEP_THRESHOLD = 180
            if delta_nap >= DEEP_SLEEP_THRESHOLD:
                initial_fp = self.readiness * 0.7 + 30
                initial_fp = max(50, min(self._calculate_recovery_ceiling(), initial_fp))
                old_fp = self.base_fp
                self.base_fp = max(self.base_fp, initial_fp)
                print(f"v3.5.2 Deep Sleep Detected: {delta_nap:.0f}min sleep → FP boost (base_fp: {old_fp:.1f} → {self.base_fp:.1f})")
            else:
                NAP_RECOVERY_RATE = 1.0
                nap_recovery = delta_nap * NAP_RECOVERY_RATE * self.recovery_efficiency
                old_fp = self.base_fp
                ceiling = self._calculate_recovery_ceiling()
                self.base_fp = min(ceiling, self.base_fp + nap_recovery)
                print(f"v5.0.4 Nap Recovery: +{delta_nap:.0f}min * {self.recovery_efficiency:.2f} → +{nap_recovery:.1f} FP (ceiling={ceiling:.0f}, base_fp: {old_fp:.1f} → {self.base_fp:.1f})")
        self._last_total_nap_minutes = total_nap_minutes
        self.total_nap_minutes = total_nap_minutes
        time_since_last_retro = (now - self._last_retroactive_check).total_seconds()
        if hr_stream and time_since_last_retro >= self._retroactive_interval:
            self._process_retroactive_data(hr_stream)
            self._last_retroactive_check = now
        delta_phantom_recovery = max(0, phantom_recovery_sum - self._last_phantom_recovery_sum)
        if delta_phantom_recovery > 0:
            PHANTOM_RECOVERY_RATE = 0.5
            effective_recovery = delta_phantom_recovery * PHANTOM_RECOVERY_RATE * self.recovery_efficiency
            old_fp = self.base_fp
            ceiling = self._calculate_recovery_ceiling()
            self.base_fp = min(ceiling, self.base_fp + effective_recovery)
            print(f"v5.0.4 Fuel Injection: +{delta_phantom_recovery:.1f} * {self.recovery_efficiency:.2f} → +{effective_recovery:.1f} FP (ceiling={ceiling:.0f}, base_fp: {old_fp:.1f} → {self.base_fp:.1f})")
        
        # 累計値を上書き（+=ではなく=）
        self.session_mouse_pixels = cumulative_mouse_pixels
        self.session_backspace_count = cumulative_backspace_count
        self.session_scroll_steps = cumulative_scroll_steps  # v3.5
        self.phantom_recovery_sum = phantom_recovery_sum
        
        # 前回値を更新
        self._last_cumulative_mouse = cumulative_mouse_pixels
        self._last_cumulative_backspace = cumulative_backspace_count
        self._last_cumulative_keys = cumulative_key_count
        self._last_cumulative_scroll = cumulative_scroll_steps  # v3.5
        self._last_phantom_recovery_sum = phantom_recovery_sum  # v3.5
        
        # ===== v3.4.4: 速度計 (Speedometer) =====
        if dt_seconds > 0:
            instant_speed = delta_mouse / dt_seconds
        else:
            instant_speed = 0.0
        
        # EMA平滑化
        self.current_mouse_speed = (
            self._mouse_speed_ema_alpha * instant_speed +
            (1 - self._mouse_speed_ema_alpha) * self.current_mouse_speed
        )
        
        if delta_mouse < 1:
            self.current_mouse_speed *= 0.5
        
        # ===== v3.4.4: 直近修正率 (Rolling Window) =====
        self._rolling_backspace_window.append(int(delta_backspace))
        self._rolling_keys_window.append(int(delta_keys))
        self._rolling_scroll_window.append(int(delta_scroll))  # v3.5
        
        total_backspace = sum(self._rolling_backspace_window)
        total_keys = sum(self._rolling_keys_window)
        
        if total_keys > 0:
            self.recent_correction_rate = total_backspace / max(1, total_keys)
        else:
            self.recent_correction_rate = 0.0
        
        if apm > 0:
            self.session_apm_samples.append(apm)
        
        # 起床からの経過時間
        self.hours_since_wake = max(0, (now - self.wake_time).total_seconds() / 3600)
        
        # 作業時間追跡
        self._update_work_tracking(apm, now)
        
        # v3.9.1: リアルタイムReadiness予測 - target_hrを使用
        # Shadow HRが有効な場合は予測値を物理演算に反映
        target_hr = self.estimated_hr if (self.is_hr_estimated and self.estimated_hr) else hr
        self._update_realtime_readiness(target_hr, dt_seconds)
        
        # ===== v3.5: スクロール対応の活動状態判定 =====
        recent_scroll = sum(self._rolling_scroll_window)
        self.activity_state = self._determine_activity_state_with_scroll(
            apm, delta_mouse, recent_scroll
        )
        
        # IDLE追跡と自動休憩記録
        self._update_idle_tracking(now)
        
        # ===== Physics Tick (1秒に1回) =====
        self.physics_accumulated_dt += dt_seconds
        if self.physics_accumulated_dt >= PHYSICS_TICK_INTERVAL:
            if not is_shisha_active:
                self._calculate_physics(self.physics_accumulated_dt, apm, delta_mouse, delta_backspace)
            else:
                self.base_fp = min(100.0, self.base_fp + 0.0083 * self.physics_accumulated_dt)
            self.physics_accumulated_dt = 0.0
            self.last_physics_tick = now
        ceiling = self._calculate_recovery_ceiling()
        if self.base_fp > ceiling:
            gravity = (self.base_fp - ceiling) * 0.5 * dt_seconds
            self.base_fp = max(ceiling, self.base_fp - gravity)
        self._animate_boost(dt_seconds)
        if is_shisha_active:
            # シーシャ中は徐々に冷却
            target_load = 0.0
            self.current_load = self.current_load * 0.99 + target_load * 0.01
        else:
            intensity = min(1.0, (apm / 100 + delta_mouse / 5000)) / 2
            target_load = min(1.0, intensity * 1.5) if (apm > 0 or delta_mouse > 0) else 0.0
            
            if target_load > self.current_load:
                # 上昇: 速やかに加熱（10%追従）
                self.current_load = self.current_load * 0.9 + target_load * 0.1
            else:
                # 下降: 極めてゆっくり冷却（1%追従）
                self.current_load = self.current_load * 0.99 + target_load * 0.01
        
        # ===== v3.6: 最終FP算出（一元化） =====
        effective_fp = self._calculate_effective_fp()
        
        # スナップショット
        state = EngineState(
            timestamp=now,
            base_fp=self.base_fp,
            boost_fp=self.boost_fp,
            effective_fp=effective_fp,
            debt=self.debt,
            current_load=self.current_load,
            readiness=self.readiness,
            estimated_readiness=self.estimated_readiness,
            continuous_work_hours=self.continuous_work_hours,
            decay_multiplier=self._get_work_decay_multiplier(),
            hours_since_wake=self.hours_since_wake,
            activity_state=self.activity_state.name,
            boost_efficiency=self._cached_boost_efficiency,
            correction_factor=self.correction_factor,
            # v3.9: Shadow Heartrate
            estimated_hr=self.estimated_hr,
            is_hr_estimated=self.is_hr_estimated,
            hr_last_update=self.hr_last_update
        )
        
        self.history.append(Snapshot(
            timestamp=now,
            apm=apm,
            hr=hr,
            state=state
        ))
        self.last_update = now
        return state
    def _update_stress_index(self, apm: float):
        rhr = self.baseline_hr
        hr = self.current_hr if self.current_hr else rhr
        hrr = max(0, (hr - rhr) / (self.MAX_HR - rhr)) if (self.MAX_HR - rhr) > 0 else 0
        hr_stress = 50 * math.tanh(3 * hrr)
        work_norm = 0.6 * (apm / 100) + 0.4 * (self.current_mouse_speed / 1000)
        work_stress = 40 * (1 - math.exp(-2 * work_norm))
        imbalance = abs(work_norm - hrr)
        imbalance_stress = 10 * imbalance * (1 + self.debt / 5)
        friction_penalty = self.recent_correction_rate * 100
        raw_stress = min(100, hr_stress + work_stress + imbalance_stress + friction_penalty)
        self.stress_index = self._stress_ema_alpha * raw_stress + (1 - self._stress_ema_alpha) * self.stress_index
    def _update_recovery_efficiency(self):
        effective_fp = self._calculate_effective_fp()
        eff_fp = math.pow(1 - effective_fp / 100, 1.5)
        eff_inertia = math.exp(-0.02 * self.stress_index)
        eff_debt = 1 / (1 + self.debt / 5)
        self.recovery_efficiency = max(0.01, eff_fp * eff_inertia * eff_debt)
    def _calculate_recovery_ceiling(self) -> float:
        quantity_factor = 0.5 if self.main_sleep_seconds <= 0 or self.main_sleep_seconds < 10800 else (0.7 if self.main_sleep_seconds < 18000 else 1.0)
        quality_factor = 0.8 if self.sleep_score < 60 else 1.0
        uptime_factor = math.pow(0.95, max(0, self.hours_since_wake - 16)) if self.hours_since_wake > 16 else 1.0
        return max(20.0, 100.0 * quantity_factor * quality_factor * uptime_factor)
    def set_main_sleep_seconds(self, seconds: int):
        self.main_sleep_seconds = seconds
    def _process_retroactive_data(self, hr_stream: List[Dict]):
        """
        v3.7: 遅延データの遡及補正 (Contextual Retroactive Correction)
        
        遅れて届いた心拍データを過去のFP計算に反映する「タイムマシン機能」
        hr_streamにないデータはDBから補完する。
        
        v3.7: シーシャセッション中の心拍データを回復として評価
        - シーシャ中の高心拍 → ストレスではなく回復として処理
        - 補正式: FP_delta = +0.05 × (Duration / 60) (1分あたり0.05回復)
        
        Args:
            hr_stream: Ouraから取得した心拍ストリーム
                       [{'timestamp': 'ISO8601', 'bpm': int, 'source': str}, ...]
        """
        if not self.history:
            return
        
        corrected_count = 0
        total_fp_delta = 0.0
        db_fallback_count = 0
        rest_recovery_count = 0
        shisha_recovery_count = 0  # v3.7: シーシャ回復カウント
        shisha_recovery_fp = 0.0   # v3.7: シーシャ回復FP
        
        # v3.7: シーシャセッション情報を取得
        shisha_sessions = []
        if self.db_path is not None:
            try:
                db_file = self.db_path / "life_os.db"
                if db_file.exists():
                    conn = sqlite3.connect(str(db_file))
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    # 過去24時間のシーシャセッションを取得
                    now = now_jst()
                    start_range = (now - timedelta(hours=24)).isoformat()
                    
                    cursor.execute('''
                        SELECT id, start_time, end_time, duration_seconds
                        FROM shisha_logs
                        WHERE start_time >= ? OR end_time >= ?
                        ORDER BY start_time ASC
                    ''', (start_range, start_range))
                    
                    for row in cursor.fetchall():
                        start_time = datetime.fromisoformat(row['start_time'])
                        if start_time.tzinfo is None:
                            start_time = start_time.replace(tzinfo=JST)
                        
                        end_time = None
                        if row['end_time']:
                            end_time = datetime.fromisoformat(row['end_time'])
                            if end_time.tzinfo is None:
                                end_time = end_time.replace(tzinfo=JST)
                        
                        shisha_sessions.append({
                            'start': start_time,
                            'end': end_time,
                            'duration': row['duration_seconds']
                        })
                    
                    conn.close()
            except Exception as e:
                print(f"v3.7 Shisha Session Load Warning: {e}")
        
        # hr_streamをタイムスタンプでインデックス化（bpmとsourceを保持）
        hr_by_time: Dict[str, Dict] = {}
        for entry in (hr_stream or []):
            try:
                ts_str = entry.get('timestamp', '')
                bpm = entry.get('bpm')
                source = entry.get('source', 'unknown')
                if ts_str and bpm is not None:
                    hr_by_time[ts_str] = {'bpm': bpm, 'source': source}
            except Exception:
                continue
        
        # DBからの補完データを取得
        db_hr_data: Dict[str, Dict] = {}
        if self.db_path is not None:
            try:
                db_file = self.db_path / "life_os.db"
                if db_file.exists():
                    conn = sqlite3.connect(str(db_file))
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    # Historyの最古〜最新の範囲でDBを検索
                    if self.history:
                        oldest_ts = min(s.timestamp for s in self.history)
                        newest_ts = max(s.timestamp for s in self.history)
                        
                        cursor.execute('''
                            SELECT timestamp, bpm, source FROM heartrate_logs
                            WHERE timestamp >= ? AND timestamp <= ?
                            ORDER BY timestamp ASC
                        ''', (oldest_ts.isoformat(), newest_ts.isoformat()))
                        
                        for row in cursor.fetchall():
                            ts_str = row['timestamp']
                            bpm = row['bpm']
                            source = row['source'] or 'unknown'
                            if ts_str and bpm is not None:
                                # hr_streamにないデータのみ追加
                                if ts_str not in hr_by_time:
                                    db_hr_data[ts_str] = {'bpm': bpm, 'source': source}
                    
                    conn.close()
            except Exception as e:
                print(f"v3.7 DB Fallback Warning: {e}")
        
        # hr_streamとDB補完データを統合
        combined_hr_data = {**hr_by_time, **db_hr_data}
        
        if not combined_hr_data:
            return
        
        # v3.7: シーシャセッション内かどうかを判定するヘルパー関数
        def is_in_shisha_session(ts: datetime) -> bool:
            for session in shisha_sessions:
                start = session['start']
                end = session['end'] or now_jst()  # 未終了セッションは現在時刻まで
                if start <= ts <= end:
                    return True
            return False
        
        # History内のスナップショットを走査
        for snapshot in self.history:
            # すでにHRが記録されている場合はスキップ
            if snapshot.hr is not None:
                continue
            
            # タイムスタンプを文字列に変換してマッチングを試みる
            snapshot_ts = snapshot.timestamp
            snapshot_ts_str = snapshot_ts.isoformat()
            
            # 処理済みチェック
            if snapshot_ts_str in self._processed_hr_timestamps:
                continue
            
            # 統合データの中から最も近いタイムスタンプを探す（±30秒以内）
            matched_hr = None
            matched_source = 'unknown'
            matched_from_db = False
            min_diff = 30.0  # 30秒以内
            
            for hr_ts_str, hr_data in combined_hr_data.items():
                try:
                    hr_ts = datetime.fromisoformat(hr_ts_str)
                    # タイムゾーン対応
                    if hr_ts.tzinfo is None:
                        hr_ts = hr_ts.replace(tzinfo=JST)
                    if snapshot_ts.tzinfo is None:
                        snapshot_ts = snapshot_ts.replace(tzinfo=JST)
                    
                    diff = abs((hr_ts - snapshot_ts).total_seconds())
                    if diff < min_diff:
                        min_diff = diff
                        matched_hr = hr_data['bpm']
                        matched_source = hr_data.get('source', 'unknown')
                        matched_from_db = hr_ts_str in db_hr_data
                except Exception:
                    continue
            
            if matched_hr is not None:
                # v3.7: シーシャセッション中かどうかを判定
                in_shisha = is_in_shisha_session(snapshot_ts)
                
                if in_shisha:
                    # v3.7: シーシャ中は高心拍でも回復として処理
                    # 補正式: FP_delta = +0.05 × (Duration / 60) = +0.05/min
                    # 1スナップショット ≈ 1秒なので、0.05 / 60 ≈ 0.000833 per snapshot
                    fp_delta = 0.05 / 60  # 1秒あたりの回復量
                    shisha_recovery_count += 1
                    shisha_recovery_fp += fp_delta
                else:
                    # 通常の遡及補正（sourceも渡す）
                    fp_delta = self._calculate_decay_delta(
                        snapshot.state.readiness,
                        matched_hr,
                        snapshot.hr,  # None
                        matched_source
                    )
                
                if abs(fp_delta) > 0.001:  # v3.7: 閾値を下げてシーシャ回復も検出
                    total_fp_delta += fp_delta
                    corrected_count += 1
                    if matched_from_db:
                        db_fallback_count += 1
                    if fp_delta > 0 and matched_source == 'rest' and not in_shisha:
                        rest_recovery_count += 1
                
                # スナップショットのHRを更新
                object.__setattr__(snapshot, 'hr', matched_hr)
                
                # 処理済みとしてマーク
                self._processed_hr_timestamps.add(snapshot_ts_str)
        
        # 補正を現在のbase_fpに適用
        if corrected_count > 0 and abs(total_fp_delta) > 0.01:
            old_fp = self.base_fp
            self.base_fp = max(10.0, min(100.0, self.base_fp + total_fp_delta))
            
            # v3.7: 改善されたログ出力
            db_note = f", {db_fallback_count} from DB" if db_fallback_count > 0 else ""
            rest_note = f", {rest_recovery_count} rest" if rest_recovery_count > 0 else ""
            shisha_note = f", {shisha_recovery_count} shisha (+{shisha_recovery_fp:.2f})" if shisha_recovery_count > 0 else ""
            
            print(f"v3.7 Contextual Retroactive: corrected {corrected_count} points{db_note}{rest_note}{shisha_note}")
            print(f"  FP adjustment: {total_fp_delta:+.2f}, base_fp: {old_fp:.1f} → {self.base_fp:.1f}")
        
        # 古い処理済みタイムスタンプをクリーンアップ（6時間以上前のもの）
        now = now_jst()
        cutoff = now - timedelta(hours=6)
        self._processed_hr_timestamps = {
            ts for ts in self._processed_hr_timestamps
            if self._parse_timestamp(ts) and self._parse_timestamp(ts) > cutoff
        }
    
    def _calculate_decay_delta(self, readiness: int, actual_hr: Optional[int], 
                               recorded_hr: Optional[int], source: str = 'unknown') -> float:
        """
        v3.6: 遡及補正の強化 v2 (Stronger Retroactive)
        
        本来の減衰量と適用済み減衰量の差分を計算。
        係数0.015 + 状態に応じた分岐
        
        Args:
            readiness: その時点のReadiness
            actual_hr: 実際の心拍数（遅延データ）
            recorded_hr: 記録時の心拍数（None = データなし）
            source: 心拍データのソース ('awake', 'rest', 'unknown')
        
        Returns:
            FP差分（正=回復方向、負=減衰方向）
        
        Cases:
            A: Rest & Relax (source='rest' and Diff < 0) → 回復
            B: Stress (Diff > 0) → ダメージ
            C: その他 → 0.0
        """
        if actual_hr is None:
            return 0.0
        
        # v3.6: 基本係数
        BASE_COEFFICIENT = 0.015
        
        # 基準心拍からの偏差
        diff = actual_hr - self.baseline_hr
        
        # ===== Case A: Rest & Relax =====
        # source='rest' かつ 心拍が基準より低い（リラックス状態）
        if source == 'rest' and diff < 0:
            # 回復係数: Readinessが高いほど回復が良い
            # M_rec = 0.5 + (Readiness / 200.0)
            # readiness=100 → 1.0, readiness=0 → 0.5
            m_rec = 0.5 + (readiness / 200.0)
            
            # 補正値 = |Diff| × 0.015 × M_rec（正の値=FP回復）
            recovery = abs(diff) * BASE_COEFFICIENT * m_rec
            return recovery
        
        # ===== Case B: Stress =====
        # 心拍が基準より高い（ストレス状態）
        if diff > 0:
            # ダメージ係数: Readinessが低いほどダメージが大きい
            # M_stress = max(0.5, 2.0 - (Readiness / 100.0))
            # readiness=100 → 1.0, readiness=50 → 1.5, readiness=0 → 2.0
            m_stress = max(0.5, 2.0 - (readiness / 100.0))
            
            if recorded_hr is None:
                # 本来適用されるべきだった減衰量を計算
                # 補正値 = -Diff × 0.015 × M_stress（負の値=FP減少）
                damage = diff * BASE_COEFFICIENT * m_stress
                return -damage
            else:
                # 既に何らかのHRで計算されていた場合、差分のみ
                old_diff = max(0, recorded_hr - self.baseline_hr)
                delta_diff = diff - old_diff
                if delta_diff > 0:
                    damage = delta_diff * BASE_COEFFICIENT * m_stress
                    return -damage
        
        # ===== Case C: その他 =====
        return 0.0
    
    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """タイムスタンプ文字列をdatetimeにパース（安全版）"""
        try:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt
        except Exception:
            return None
    
    def predict_trajectory(self, minutes: int = 240) -> Dict[str, List[PredictionPoint]]:
        """
        v3.4.2: 未来予測（キャッシュ付き）
        """
        now = now_jst()
        
        # キャッシュ有効性チェック（5秒以内なら再利用）
        if (self._cached_prediction is not None and
            self._prediction_cache_time is not None and
            (now - self._prediction_cache_time).total_seconds() < 5.0):
            return self._cached_prediction
        
        continue_points = []
        rest_points = []
        
        sim_base_continue = self.base_fp
        sim_boost_continue = self.boost_fp
        sim_debt_continue = self.debt
        
        sim_base_rest = self.base_fp
        sim_boost_rest = self.boost_fp
        sim_debt_rest = self.debt
        
        decay = self._cached_decay_rate
        work_mult = self._get_work_decay_multiplier()
        
        for minute in range(0, minutes + 1, 5):
            future_time = now + timedelta(minutes=minute)
            dt_hours = 5 / 60
            
            if minute > 0:
                # 継続シナリオ
                eff_decay = decay * work_mult * (1 + sim_debt_continue * 0.1)
                sim_base_continue = sim_base_continue * math.exp(-eff_decay * dt_hours)
                sim_boost_continue *= 0.98
                sim_debt_continue += sim_boost_continue * 0.001 * 300
                sim_debt_continue = min(10.0, sim_debt_continue)
                
                # 休憩シナリオ
                sim_base_rest = sim_base_rest * math.exp(-decay * 0.3 * dt_hours)
                sim_base_rest = min(100, sim_base_rest + 3 * dt_hours)
                sim_boost_rest *= 0.7
                repayment = self._get_dynamic_repayment_rate()
                sim_debt_rest -= repayment * 300
                sim_debt_rest = max(0, sim_debt_rest)
            
            boost_eff = self._cached_boost_efficiency
            
            # v3.9.1: 定数を使用（バグ修正 - 5.0→DEBT_PENALTY_MULTIPLIER）
            fp_continue = sim_base_continue + (sim_boost_continue * boost_eff) - (sim_debt_continue * self.DEBT_PENALTY_MULTIPLIER)
            fp_rest = sim_base_rest + (sim_boost_rest * boost_eff) - (sim_debt_rest * self.DEBT_PENALTY_MULTIPLIER)
            
            continue_points.append(PredictionPoint(
                timestamp=future_time,
                fp=max(10, min(100, fp_continue)),
                scenario='continue'
            ))
            
            rest_points.append(PredictionPoint(
                timestamp=future_time,
                fp=max(10, min(100, fp_rest)),
                scenario='rest'
            ))
        
        result = {'continue': continue_points, 'rest': rest_points}
        
        # キャッシュ更新
        self._cached_prediction = result
        self._prediction_cache_time = now
        
        return result
    
    def get_recommended_break_time(self) -> datetime:
        """v3.9.1: 推奨休憩時刻（閾値定数化）"""
        now = now_jst()
        
        # 水分補給限界
        hydration_limit = self.last_break_time + timedelta(minutes=HYDRATION_INTERVAL_MINUTES)
        
        # FP枯渇予測
        prediction = self.predict_trajectory(240)
        fp_limit = now + timedelta(hours=4)
        
        for point in prediction['continue']:
            if point.fp < self.BREAK_RECOMMEND_THRESHOLD:
                fp_limit = point.timestamp
                break
        
        return min(hydration_limit, fp_limit)
    
    def get_exhaustion_time(self) -> datetime:
        """v3.9.1: 消耗予測時刻（閾値定数化）"""
        now = now_jst()
        prediction = self.predict_trajectory(480)
        
        for point in prediction['continue']:
            if point.fp < self.BEDTIME_THRESHOLD:
                return point.timestamp
        
        return now + timedelta(hours=8)
    
    def get_status_code(self) -> Tuple[str, str]:
        if self.is_shisha_active:
            return ("SHISHA RECOVERY", "シーシャセッション中。リラックスモード。")
        ceiling = self._calculate_recovery_ceiling()
        if ceiling < 40:
            return ("BIOLOGICAL CRITICAL", f"生物学的限界。上限{ceiling:.0f}。強制休息が必要。")
        effective_fp = self._calculate_effective_fp()
        if self.continuous_work_hours >= 4:
            return ("EXTENDED OPERATION", f"4h+連続稼働。休憩推奨。Debt: {self.debt:.1f}")
        now = now_jst()
        minutes_since_break = (now - self.last_break_time).total_seconds() / 60
        if minutes_since_break >= HYDRATION_INTERVAL_MINUTES:
            return ("HYDRATION REQUIRED", "90分経過。水分補給を。")
        state_names = {
            ActivityState.IDLE: "STANDBY",
            ActivityState.LIGHT: "LIGHT",
            ActivityState.MODERATE: "MODERATE",
            ActivityState.DEEP_DIVE: "DEEP DIVE",
            ActivityState.HYPERFOCUS: "HYPERFOCUS",
        }
        if effective_fp < 15:
            return ("CRITICAL CONDITION", "深刻なリソース枯渇。即座の休息が必要。")
        elif effective_fp < 30:
            return ("RESOURCE DEPLETED", f"リソース枯渇。Debt: {self.debt:.1f}")
        elif self.current_load > 0.8:
            return ("HIGH LOAD WARNING", f"高負荷状態。LOAD: {int(self.current_load*100)}%")
        elif effective_fp < 50:
            return ("CAUTION ADVISED", f"リソース低下中。{state_names.get(self.activity_state, 'ACTIVE')}")
        elif self.activity_state == ActivityState.HYPERFOCUS:
            return ("HYPERFOCUS MODE", f"最大効率稼働中。Boost: {self.boost_fp:.1f}")
        elif self.activity_state == ActivityState.DEEP_DIVE:
            return ("DEEP DIVE ACTIVE", f"集中モード。Boost: {self.boost_fp:.1f}")
        elif effective_fp >= 80:
            return ("OPTIMAL STATE", f"最適状態。{state_names.get(self.activity_state, 'ACTIVE')}")
        elif self.estimated_readiness >= 70:
            return ("NEURAL LINK ACTIVE", "システム安定稼働中。")
        else:
            return ("SYSTEM NOMINAL", f"通常稼働。{state_names.get(self.activity_state, 'ACTIVE')}")
    
    def get_health_metrics(self) -> Dict:
        effective_fp = self._calculate_effective_fp()
        return {
            'base_fp': self.base_fp,
            'boost_fp': self.boost_fp,
            'effective_fp': effective_fp,
            'debt': self.debt,
            'current_load': self.current_load,
            'readiness': self.readiness,
            'sleep_score': self.sleep_score,
            'estimated_readiness': self.estimated_readiness,
            'continuous_work_hours': self.continuous_work_hours,
            'decay_multiplier': self._get_work_decay_multiplier(),
            'hours_since_wake': self.hours_since_wake,
            'activity_state': self.activity_state.name,
            'boost_efficiency': self._cached_boost_efficiency,
            'correction_factor': self.correction_factor,
            'session_mouse_pixels': self.session_mouse_pixels,
            'session_backspace_count': self.session_backspace_count,
            'continuous_idle_seconds': self.continuous_idle_seconds,
            'chronotype_hour_efficiency': self.hourly_efficiency.get(now_jst().hour, 1.0),
            'phantom_recovery_sum': self.phantom_recovery_sum,
            'current_mouse_speed': self.current_mouse_speed,
            'recent_correction_rate': self.recent_correction_rate,
            'session_scroll_steps': self.session_scroll_steps,
            'is_shisha_active': self.is_shisha_active,
            'current_hr': self.current_hr,
            'cumulative_hr_deviation': self.cumulative_hr_deviation,
            'cumulative_load': self.cumulative_load,
            'hr_stress_factor': self._calculate_hr_stress_factor(),
            'chronotype_blend_ratio': getattr(self, '_chronotype_blend_ratio', 0.0),
            'estimated_hr': self.estimated_hr,
            'is_hr_estimated': self.is_hr_estimated,
            'hr_last_update': self.hr_last_update.isoformat() if self.hr_last_update else None,
            'shadow_hr_coefficients': self.shadow_hr.get_coefficients(),
            'stress_index': self.stress_index,
            'recovery_efficiency': self.recovery_efficiency,
            'recovery_ceiling': self._calculate_recovery_ceiling(),
        }
    
    def debug_fp_calculation(self) -> Dict:
        """
        v3.6: FP計算のデバッグ情報を取得
        
        UI表示と内部計算の一致を確認するためのメソッド
        
        Returns:
            Dict with all FP calculation components
        """
        f_hr = self._calculate_hr_stress_factor()
        effective_fp = self._calculate_effective_fp()
        
        return {
            # 入力値
            'base_fp': self.base_fp,
            'boost_fp': self.boost_fp,
            'boost_efficiency': self._cached_boost_efficiency,
            'debt': self.debt,
            
            # 計算過程（v3.9.1: 定数使用に統一）
            'boosted_fp': self.boost_fp * self._cached_boost_efficiency,
            'debt_penalty': self.debt * self.DEBT_PENALTY_MULTIPLIER,
            'raw_fp': self.base_fp + (self.boost_fp * self._cached_boost_efficiency) - (self.debt * self.DEBT_PENALTY_MULTIPLIER),
            
            # 最終値
            'effective_fp': effective_fp,
            
            # 心拍連動
            'current_hr': self.current_hr,
            'baseline_hr': self.baseline_hr,
            'hr_stress_factor': f_hr,
            
            # クロノタイプ
            'current_hour': now_jst().hour,
            'hour_efficiency': self.hourly_efficiency.get(now_jst().hour, 1.0),
            'using_default_chronotype': self._using_default_chronotype,
            'chronotype_blend_ratio': getattr(self, '_chronotype_blend_ratio', 0.0),
            
            # 計算式（文字列）v3.9.1: 定数使用
            'formula': f"FP_eff = clamp(10, 100, {self.base_fp:.2f} + ({self.boost_fp:.2f} × {self._cached_boost_efficiency:.2f}) - ({self.debt:.2f} × {self.DEBT_PENALTY_MULTIPLIER})) = {effective_fp:.2f}",
        }
    
    def get_prediction_bars(self, hours: int = 8) -> List[Dict]:
        """予測バーグラフ用データ（後方互換）"""
        prediction = self.predict_trajectory(hours * 60)
        
        bars = []
        intervals = [0, 60, 120, 240, 480]
        labels = ['Now', '+1h', '+2h', '+4h', '+8h']
        
        for interval, label in zip(intervals, labels):
            if interval < len(prediction['continue']) * 5:
                idx = interval // 5
                if idx < len(prediction['continue']):
                    fp = prediction['continue'][idx].fp
                    bars.append({
                        'label': label,
                        'fp': fp,
                        'color': self._get_fp_color(fp)
                    })
        
        return bars
    
    def _get_fp_color(self, fp: float) -> str:
        """FP値に応じた色"""
        if fp >= 60:
            return '#00D4AA'
        elif fp >= 30:
            return '#F39C12'
        return '#E74C3C'
    
    def retroactive_sync(self, timestamp: datetime, hr: int, apm: float):
        """遅延データの補正"""
        for snapshot in self.history:
            if abs((snapshot.timestamp - timestamp).total_seconds()) < 60:
                snapshot.hr = hr
                snapshot.apm = apm
                break
    
    def train_shadow_model(
        self,
        actual_hr: int,
        timestamp: datetime,
        hr_stream: Optional[List[Dict]] = None
    ) -> Optional[Dict]:
        """
        v3.9: Shadow Heartrate学習インターフェース
        
        実測データ到着時に、過去の予測値と比較して係数を学習する。
        
        Args:
            actual_hr: 実測心拍数
            timestamp: データのタイムスタンプ
            hr_stream: 心拍ストリーム（複数データポイントがある場合）
        
        Returns:
            学習結果のDict（学習が実行された場合）
        """
        # 対応する過去の状態を探す
        target_snapshot = None
        for snapshot in self.history:
            if abs((snapshot.timestamp - timestamp).total_seconds()) < 120:  # 2分以内
                target_snapshot = snapshot
                break
        
        if target_snapshot is None:
            return None
        
        # 予測値があった場合のみ学習
        if target_snapshot.state.is_hr_estimated and target_snapshot.state.estimated_hr is not None:
            predicted_hr = target_snapshot.state.estimated_hr
            
            # 学習実行
            result = self.shadow_hr.learn(
                actual_hr=actual_hr,
                predicted_hr=predicted_hr,
                apm=target_snapshot.apm or 0,
                mouse_speed=self.current_mouse_speed,
                work_hours=target_snapshot.state.continuous_work_hours
            )
            
            return result
        
        return None
