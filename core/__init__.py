#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from .types import (
    __version__,
    JST,
    now_jst,
    Colors,
    Fonts,
    ActivityState,
    CommandType,
    Command,
    EngineState,
    PredictionPoint,
    Snapshot,
    HYDRATION_INTERVAL_MINUTES,
    AUTO_BREAK_IDLE_SECONDS,
    PHYSICS_TICK_INTERVAL,
    COMMAND_QUEUE_FILENAME,
    CommandQueue,
    safe_read_json,
    safe_write_json,
    get_root_path,
    get_command_queue_path,
    get_state_path,
    get_config_path,
    get_db_path,
    get_style_path,
    enqueue_command,
)
try:
    from .database import LifeOSDatabase, DATABASE_VERSION
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    LifeOSDatabase = None
    DATABASE_VERSION = None
try:
    from .engine import BioEngine, ShadowHeartrate
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False
    BioEngine = None
    ShadowHeartrate = None
try:
    from .audio import NeuroSoundEngine, NeuroSoundController, AudioConstants
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False
    NeuroSoundEngine = None
    NeuroSoundController = None
    AudioConstants = None
try:
    from .home import AmbientSync, KirigamineController, HueController, BraviaController, MonitorController, DesktopOrganizer, PHUE_AVAILABLE, REQUESTS_AVAILABLE
    HOME_AVAILABLE = True
except ImportError:
    HOME_AVAILABLE = False
    AmbientSync = None
    KirigamineController = None
    HueController = None
    BraviaController = None
    MonitorController = None
    DesktopOrganizer = None
    PHUE_AVAILABLE = False
    REQUESTS_AVAILABLE = False
__all__ = [
    '__version__', 'JST', 'now_jst', 'Colors', 'Fonts', 'ActivityState', 'CommandType', 'Command',
    'EngineState', 'PredictionPoint', 'Snapshot', 'HYDRATION_INTERVAL_MINUTES', 'AUTO_BREAK_IDLE_SECONDS',
    'PHYSICS_TICK_INTERVAL', 'COMMAND_QUEUE_FILENAME', 'CommandQueue', 'safe_read_json', 'safe_write_json',
    'get_root_path', 'get_command_queue_path', 'get_state_path', 'get_config_path', 'get_db_path', 'get_style_path',
    'enqueue_command', 'LifeOSDatabase', 'DATABASE_AVAILABLE', 'DATABASE_VERSION', 'BioEngine', 'ShadowHeartrate',
    'ENGINE_AVAILABLE', 'NeuroSoundEngine', 'NeuroSoundController', 'AudioConstants', 'AUDIO_AVAILABLE',
    'AmbientSync', 'KirigamineController', 'HueController', 'BraviaController', 'MonitorController',
    'DesktopOrganizer', 'HOME_AVAILABLE', 'PHUE_AVAILABLE', 'REQUESTS_AVAILABLE',
]
