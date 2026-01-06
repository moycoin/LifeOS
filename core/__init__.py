#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from .types import (
    __version__,
    JST, now_jst,
    HYDRATION_INTERVAL_MINUTES, AUTO_BREAK_IDLE_SECONDS, PHYSICS_TICK_INTERVAL,
    COMMAND_QUEUE_FILENAME,
    Colors, Fonts, ActivityState, CommandType,
    Command, CommandQueue,
    EngineState, PredictionPoint, Snapshot,
    safe_read_json, safe_write_json,
    get_root_path, get_command_queue_path, get_state_path, get_config_path, get_db_path, get_style_path,
    enqueue_command,
)
TYPES_AVAILABLE = True
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
try:
    from .home import AmbientSync, HueController, BraviaController, SleepDetector, MonitorController
    HOME_AVAILABLE = True
except ImportError as e:
    HOME_AVAILABLE = False
    AmbientSync = HueController = BraviaController = SleepDetector = MonitorController = None
    print(f"[core/__init__] home.py import failed: {e}")
__all__ = [
    '__version__',
    'JST', 'now_jst',
    'HYDRATION_INTERVAL_MINUTES', 'AUTO_BREAK_IDLE_SECONDS', 'PHYSICS_TICK_INTERVAL',
    'COMMAND_QUEUE_FILENAME',
    'Colors', 'Fonts', 'ActivityState', 'CommandType',
    'Command', 'CommandQueue', 'enqueue_command',
    'EngineState', 'PredictionPoint', 'Snapshot',
    'safe_read_json', 'safe_write_json',
    'get_root_path', 'get_command_queue_path', 'get_state_path', 'get_config_path', 'get_db_path', 'get_style_path',
    'LifeOSDatabase',
    'BioEngine', 'ShadowHeartrate',
    'NeuroSoundEngine', 'NeuroSoundController', 'AudioConstants', 'VolumeManager',
    'NeuroLinguisticCompiler', 'NeuroAssetGenerator',
    'AmbientSync', 'HueController', 'BraviaController', 'SleepDetector', 'MonitorController',
    'TYPES_AVAILABLE', 'DATABASE_AVAILABLE', 'ENGINE_AVAILABLE', 'AUDIO_AVAILABLE', 'HOME_AVAILABLE',
]
def get_status() -> dict:
    return {'version': __version__, 'types': TYPES_AVAILABLE, 'database': DATABASE_AVAILABLE, 'engine': ENGINE_AVAILABLE, 'audio': AUDIO_AVAILABLE, 'home': HOME_AVAILABLE}
if __name__ == '__main__':
    print(f"=== LifeOS Core v{__version__} ===")
    for k, v in get_status().items():
        print(f"  {k}: {v if not isinstance(v, bool) else ('[OK]' if v else '[NG]')}")
