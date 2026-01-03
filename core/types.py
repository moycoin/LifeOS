#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Life OS v5.4.1 - Types Module"""
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional
from enum import Enum
from PyQt5.QtGui import QFont
JST = timezone(timedelta(hours=9))
def now_jst() -> datetime:
    return datetime.now(JST)
HYDRATION_INTERVAL_MINUTES = 90
AUTO_BREAK_IDLE_SECONDS = 900
PHYSICS_TICK_INTERVAL = 1.0
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
