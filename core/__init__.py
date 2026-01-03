#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Life OS Core v5.4.1"""
try:
    from .types import (
        JST, now_jst,
        HYDRATION_INTERVAL_MINUTES, AUTO_BREAK_IDLE_SECONDS, PHYSICS_TICK_INTERVAL,
        Colors, Fonts, ActivityState, EngineState, PredictionPoint, Snapshot,
    )
    TYPES_AVAILABLE = True
except ImportError as e:
    TYPES_AVAILABLE = False
    print(f"[core/__init__] types.py import failed: {e}")
    from datetime import datetime, timedelta, timezone
    JST = timezone(timedelta(hours=9))
    def now_jst(): return datetime.now(JST)
    HYDRATION_INTERVAL_MINUTES, AUTO_BREAK_IDLE_SECONDS, PHYSICS_TICK_INTERVAL = 90, 900, 1.0
try:
    from .database import LifeOSDatabase
    DATABASE_AVAILABLE = True
except ImportError as e:
    DATABASE_AVAILABLE, LifeOSDatabase = False, None
    print(f"[core/__init__] database.py import failed: {e}")
try:
    from .engine import BioEngine, ShadowHeartrate
    ENGINE_AVAILABLE = True
except ImportError as e:
    ENGINE_AVAILABLE, BioEngine, ShadowHeartrate = False, None, None
    print(f"[core/__init__] engine.py import failed: {e}")
try:
    from .audio import (
        NeuroSoundEngine, NeuroSoundController, AudioConstants, VolumeManager,
        NeuroLinguisticCompiler, NeuroAssetGenerator,
    )
    AUDIO_AVAILABLE = True
except ImportError as e:
    AUDIO_AVAILABLE = False
    NeuroSoundEngine = NeuroSoundController = AudioConstants = VolumeManager = None
    NeuroLinguisticCompiler = NeuroAssetGenerator = None
    print(f"[core/__init__] audio.py import failed: {e}")
__all__ = [
    'JST', 'now_jst',
    'HYDRATION_INTERVAL_MINUTES', 'AUTO_BREAK_IDLE_SECONDS', 'PHYSICS_TICK_INTERVAL',
    'Colors', 'Fonts', 'ActivityState', 'EngineState', 'PredictionPoint', 'Snapshot',
    'LifeOSDatabase',
    'BioEngine', 'ShadowHeartrate',
    'NeuroSoundEngine', 'NeuroSoundController', 'AudioConstants', 'VolumeManager',
    'NeuroLinguisticCompiler', 'NeuroAssetGenerator',
    'TYPES_AVAILABLE', 'DATABASE_AVAILABLE', 'ENGINE_AVAILABLE', 'AUDIO_AVAILABLE',
]
__version__ = '5.4.1'
def get_status() -> dict:
    return {'version': __version__, 'types': TYPES_AVAILABLE, 'database': DATABASE_AVAILABLE, 'engine': ENGINE_AVAILABLE, 'audio': AUDIO_AVAILABLE}
if __name__ == '__main__':
    print("=== LifeOS Core Package Status ===")
    for k, v in get_status().items(): print(f"  {k}: {v if not isinstance(v, bool) else ('[OK]' if v else '[NG]')}")
