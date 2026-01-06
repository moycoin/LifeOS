#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import io
import json
import math
import os
import random
import struct
import threading
import time
import wave
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from .types import __version__, now_jst
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
try:
    from scipy import signal as scipy_signal
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

class AudioConstants:
    SAMPLE_RATE = 44100
    BIT_DEPTH = 16
    CHANNELS = 2
    MAX_AMPLITUDE = 32767
    MIN_AMPLITUDE = -32768
    DURATION_SECONDS = 300
    FADE_DURATION_MS = 500
    FADE_SAMPLES = int(SAMPLE_RATE * FADE_DURATION_MS / 1000)
    CHUNK_SECONDS = 10
    BUFFER_SIZE = 8192
    FADE_STEP_MS = 50
    FREQ_FOCUS = (400, 40)
    FREQ_FLOW = (300, 14)
    FREQ_RELAX = (250, 10)
    FREQ_SLEEP = (200, 2.0)
    ENV_BASE = 0.7
    ENV_DEPTH = 0.3
    ENV_RATE = 0.8
    STOCHASTIC_SIGMA = 0.01
    PHASE_DRIFT_MAX = 0.01
    NLC_ALPHA_HZ = 10.0
    NLC_ALPHA_DEPTH = 0.06
    FIRE_SPECTRAL_EXPONENT = 1.5
    FIRE_CRACKLE_FREQ_MIN = 0.5
    FIRE_CRACKLE_FREQ_MAX = 2.0
    FIRE_LPF_CUTOFF = 200
    AMBIENT_SOURCES = ['Rain', 'Fire']
    ALLOWED_ASSETS = ['bgm_focus.wav', 'bgm_flow.wav', 'bgm_relax.wav', 'bgm_sleep.wav', 'ambient_rain.wav', 'ambient_fire.wav']

class NeuroAdaptiveOptimizer:
    HEADPHONE = 'headphone'
    SPEAKER = 'speaker'
    EARBUD = 'earbud'
    DEVICE_MULTIPLIERS = {'headphone': 1.0, 'speaker': 1.2, 'earbud': 0.9}
    @classmethod
    def optimize_carrier(cls, base_carrier: float, device: str) -> float:
        return base_carrier * cls.DEVICE_MULTIPLIERS.get(device, 1.0)
    @classmethod
    def get_profile_factor(cls, device: str) -> float:
        return {'headphone': 1.0, 'speaker': 0.85, 'earbud': 0.95}.get(device, 1.0)

class NeuroOptimalGenerator:
    @classmethod
    def generate_binaural(cls, filepath: Path, carrier_hz: float, beat_hz: float, amplitude: float = 0.5, device: str = 'headphone') -> bool:
        if not NUMPY_AVAILABLE: return cls._generate_chunked(filepath, carrier_hz, beat_hz, amplitude)
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            num_samples = sr * duration
            t = np.linspace(0, duration, num_samples, dtype=np.float64)
            fc = NeuroAdaptiveOptimizer.optimize_carrier(carrier_hz, device)
            left_freq = fc - beat_hz / 2
            right_freq = fc + beat_hz / 2
            envelope = AudioConstants.ENV_BASE + AudioConstants.ENV_DEPTH * np.sin(2 * np.pi * AudioConstants.ENV_RATE * t)
            np.random.seed(int(carrier_hz * 1000 + beat_hz * 100))
            stochastic = np.random.normal(1.0, AudioConstants.STOCHASTIC_SIGMA, num_samples).astype(np.float32)
            left_drift = np.random.uniform(-AudioConstants.PHASE_DRIFT_MAX, AudioConstants.PHASE_DRIFT_MAX)
            right_drift = np.random.uniform(-AudioConstants.PHASE_DRIFT_MAX, AudioConstants.PHASE_DRIFT_MAX)
            left_phase = left_drift * t
            right_phase = right_drift * t
            left_signal = (envelope * stochastic * np.sin(2 * np.pi * left_freq * t + left_phase)).astype(np.float32)
            right_signal = (envelope * stochastic * np.sin(2 * np.pi * right_freq * t + right_phase)).astype(np.float32)
            fade_samples = AudioConstants.FADE_SAMPLES
            fade_in = (0.5 * (1 - np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            fade_out = (0.5 * (1 + np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            left_signal[:fade_samples] *= fade_in; right_signal[:fade_samples] *= fade_in
            left_signal[-fade_samples:] *= fade_out; right_signal[-fade_samples:] *= fade_out
            amp = AudioConstants.MAX_AMPLITUDE * amplitude
            stereo = np.empty(num_samples * 2, dtype=np.int16)
            stereo[0::2] = np.clip(left_signal * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            stereo[1::2] = np.clip(right_signal * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8)
                wav.setframerate(sr); wav.writeframes(stereo.tobytes())
            return True
        except Exception as e:
            print(f"!!! NEURO OPTIMAL GEN FAILED: {filepath.name} - {e}"); return False
    @classmethod
    def _generate_chunked(cls, filepath: Path, carrier_hz: float, beat_hz: float, amplitude: float) -> bool:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            total_samples, chunk_samples = sr * duration, sr * AudioConstants.CHUNK_SECONDS
            left_freq = carrier_hz - beat_hz / 2
            right_freq = carrier_hz + beat_hz / 2
            amp, fade_samples = int(AudioConstants.MAX_AMPLITUDE * amplitude), AudioConstants.FADE_SAMPLES
            random.seed(int(carrier_hz * 1000 + beat_hz * 100))
            left_drift = random.uniform(-AudioConstants.PHASE_DRIFT_MAX, AudioConstants.PHASE_DRIFT_MAX)
            right_drift = random.uniform(-AudioConstants.PHASE_DRIFT_MAX, AudioConstants.PHASE_DRIFT_MAX)
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8); wav.setframerate(sr)
                for chunk_idx in range((total_samples + chunk_samples - 1) // chunk_samples):
                    chunk_start, buffer = chunk_idx * chunk_samples, []
                    for i in range(min(chunk_samples, total_samples - chunk_start)):
                        sample_idx, t = chunk_start + i, (chunk_start + i) / sr
                        envelope = AudioConstants.ENV_BASE + AudioConstants.ENV_DEPTH * math.sin(2 * math.pi * AudioConstants.ENV_RATE * t)
                        stochastic = random.gauss(1.0, AudioConstants.STOCHASTIC_SIGMA)
                        left_phase = left_drift * t
                        right_phase = right_drift * t
                        left = envelope * stochastic * math.sin(2 * math.pi * left_freq * t + left_phase)
                        right = envelope * stochastic * math.sin(2 * math.pi * right_freq * t + right_phase)
                        fade = 0.5 * (1 - math.cos(math.pi * sample_idx / fade_samples)) if sample_idx < fade_samples else (0.5 * (1 + math.cos(math.pi * (sample_idx - (total_samples - fade_samples)) / fade_samples)) if sample_idx > total_samples - fade_samples else 1.0)
                        buffer.append(struct.pack('<hh', max(AudioConstants.MIN_AMPLITUDE, min(AudioConstants.MAX_AMPLITUDE, int(left * fade * amp))), max(AudioConstants.MIN_AMPLITUDE, min(AudioConstants.MAX_AMPLITUDE, int(right * fade * amp)))))
                    wav.writeframes(b''.join(buffer))
            return True
        except Exception as e:
            print(f"!!! NEURO OPTIMAL GEN FAILED (chunked): {filepath.name} - {e}"); return False

class NeuroFireGenerator:
    @classmethod
    def generate(cls, filepath: Path, amplitude: float = 0.4) -> bool:
        if not NUMPY_AVAILABLE: return cls._generate_chunked(filepath, amplitude)
        if not SCIPY_AVAILABLE: return cls._generate_simple(filepath, amplitude)
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            num_samples = sr * duration
            white = np.random.randn(num_samples).astype(np.float64)
            freqs = np.fft.rfftfreq(num_samples, 1 / sr)
            freqs[0] = 1e-10
            fft_white = np.fft.rfft(white)
            fft_filtered = fft_white / (freqs ** (AudioConstants.FIRE_SPECTRAL_EXPONENT / 2))
            base_noise = np.fft.irfft(fft_filtered, num_samples).astype(np.float32)
            sos = scipy_signal.butter(4, AudioConstants.FIRE_LPF_CUTOFF / (sr / 2), btype='low', output='sos')
            base_noise = scipy_signal.sosfilt(sos, base_noise).astype(np.float32)
            t = np.linspace(0, duration, num_samples, dtype=np.float32)
            crackle_freq = np.random.uniform(AudioConstants.FIRE_CRACKLE_FREQ_MIN, AudioConstants.FIRE_CRACKLE_FREQ_MAX)
            crackle_env = 0.3 + 0.7 * np.clip(np.sin(2 * np.pi * crackle_freq * t) ** 8, 0, 1)
            burst_times = np.random.exponential(2.0, int(duration / 2))
            burst_positions = (np.cumsum(burst_times) * sr).astype(int)
            burst_positions = burst_positions[burst_positions < num_samples]
            for pos in burst_positions:
                burst_len = int(sr * np.random.uniform(0.02, 0.08))
                if pos + burst_len < num_samples:
                    burst = np.random.randn(burst_len).astype(np.float32) * np.hanning(burst_len) * 0.5
                    base_noise[pos:pos + burst_len] += burst
            fire = base_noise * crackle_env
            stereo_diff = np.random.uniform(-0.02, 0.02, num_samples).astype(np.float32)
            left, right = fire * (1 + stereo_diff), fire * (1 - stereo_diff)
            max_val = max(np.max(np.abs(left)), np.max(np.abs(right)))
            if max_val > 0: left, right = left / max_val, right / max_val
            fade_samples = AudioConstants.FADE_SAMPLES
            fade_in = (0.5 * (1 - np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            fade_out = (0.5 * (1 + np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            left[:fade_samples] *= fade_in; right[:fade_samples] *= fade_in
            left[-fade_samples:] *= fade_out; right[-fade_samples:] *= fade_out
            amp = AudioConstants.MAX_AMPLITUDE * amplitude
            stereo = np.empty(num_samples * 2, dtype=np.int16)
            stereo[0::2] = np.clip(left * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            stereo[1::2] = np.clip(right * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8)
                wav.setframerate(sr); wav.writeframes(stereo.tobytes())
            return True
        except Exception as e:
            print(f"!!! NEURO FIRE GEN FAILED: {filepath.name} - {e}"); return False
    @classmethod
    def _generate_simple(cls, filepath: Path, amplitude: float) -> bool:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            num_samples = sr * duration
            pink = np.zeros(num_samples, dtype=np.float32)
            for octave in range(16):
                period = 2 ** octave
                rows = (num_samples + period - 1) // period
                pink += np.random.uniform(-1, 1, rows + 1).astype(np.float32)[np.minimum(np.arange(num_samples) // period, rows)][:num_samples] / 16
            t = np.linspace(0, duration, num_samples, dtype=np.float32)
            crackle = 0.3 + 0.7 * np.clip(np.sin(2 * np.pi * 1.0 * t) ** 8, 0, 1)
            fire = pink * crackle
            stereo_diff = np.random.uniform(-0.02, 0.02, num_samples).astype(np.float32)
            left, right = fire * (1 + stereo_diff), fire * (1 - stereo_diff)
            max_val = max(np.max(np.abs(left)), np.max(np.abs(right)))
            if max_val > 0: left, right = left / max_val, right / max_val
            fade_samples = AudioConstants.FADE_SAMPLES
            fade_in = (0.5 * (1 - np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            fade_out = (0.5 * (1 + np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            left[:fade_samples] *= fade_in; right[:fade_samples] *= fade_in
            left[-fade_samples:] *= fade_out; right[-fade_samples:] *= fade_out
            amp = AudioConstants.MAX_AMPLITUDE * amplitude
            stereo = np.empty(num_samples * 2, dtype=np.int16)
            stereo[0::2] = np.clip(left * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            stereo[1::2] = np.clip(right * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8)
                wav.setframerate(sr); wav.writeframes(stereo.tobytes())
            return True
        except Exception as e:
            print(f"!!! NEURO FIRE GEN (simple) FAILED: {e}"); return False
    @classmethod
    def _generate_chunked(cls, filepath: Path, amplitude: float) -> bool:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            total_samples, chunk_samples = sr * duration, sr * AudioConstants.CHUNK_SECONDS
            amp, fade_samples = int(AudioConstants.MAX_AMPLITUDE * amplitude), AudioConstants.FADE_SAMPLES
            b_vals = [0.0] * 7
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8); wav.setframerate(sr)
                for chunk_idx in range((total_samples + chunk_samples - 1) // chunk_samples):
                    chunk_start, buffer = chunk_idx * chunk_samples, []
                    for i in range(min(chunk_samples, total_samples - chunk_start)):
                        sample_idx, t = chunk_start + i, (chunk_start + i) / sr
                        white = random.uniform(-1, 1)
                        b_vals[0] = 0.99886 * b_vals[0] + white * 0.0555179
                        b_vals[1] = 0.99332 * b_vals[1] + white * 0.0750759
                        b_vals[2] = 0.96900 * b_vals[2] + white * 0.1538520
                        b_vals[3] = 0.86650 * b_vals[3] + white * 0.3104856
                        b_vals[4] = 0.55000 * b_vals[4] + white * 0.5329522
                        b_vals[5] = -0.7616 * b_vals[5] - white * 0.0168980
                        pink = (sum(b_vals[:6]) + white * 0.5362) / 4.5
                        crackle = 0.3 + 0.7 * max(0, math.sin(2 * math.pi * 1.0 * t) ** 8)
                        fire = pink * crackle
                        fade = 0.5 * (1 - math.cos(math.pi * sample_idx / fade_samples)) if sample_idx < fade_samples else (0.5 * (1 + math.cos(math.pi * (sample_idx - (total_samples - fade_samples)) / fade_samples)) if sample_idx > total_samples - fade_samples else 1.0)
                        val = max(AudioConstants.MIN_AMPLITUDE, min(AudioConstants.MAX_AMPLITUDE, int(fire * fade * amp)))
                        buffer.append(struct.pack('<hh', val, val))
                    wav.writeframes(b''.join(buffer))
            return True
        except Exception as e:
            print(f"!!! NEURO FIRE GEN (chunked) FAILED: {e}"); return False

class NeuroPinkNoiseGenerator:
    @classmethod
    def generate(cls, filepath: Path, amplitude: float = 0.4) -> bool:
        if not NUMPY_AVAILABLE: return cls._generate_chunked(filepath, amplitude)
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            num_samples = sr * duration
            pink = np.zeros(num_samples, dtype=np.float64)
            for octave in range(16):
                period = 2 ** octave
                rows = (num_samples + period - 1) // period
                pink += np.random.uniform(-1, 1, rows + 1)[np.minimum(np.arange(num_samples) // period, rows)][:num_samples] / 16
            t = np.linspace(0, duration, num_samples, dtype=np.float32)
            pink *= (1.0 + 0.05 * np.sin(2 * np.pi * 0.05 * t))
            stereo_diff = np.random.uniform(-0.02, 0.02, num_samples).astype(np.float32)
            left, right = (pink * (1 + stereo_diff)).astype(np.float32), (pink * (1 - stereo_diff)).astype(np.float32)
            max_val = max(np.max(np.abs(left)), np.max(np.abs(right)))
            if max_val > 0: left, right = left / max_val, right / max_val
            fade_samples = AudioConstants.FADE_SAMPLES
            fade_in = (0.5 * (1 - np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            fade_out = (0.5 * (1 + np.cos(np.pi * np.arange(fade_samples) / fade_samples))).astype(np.float32)
            left[:fade_samples] *= fade_in; right[:fade_samples] *= fade_in
            left[-fade_samples:] *= fade_out; right[-fade_samples:] *= fade_out
            amp = AudioConstants.MAX_AMPLITUDE * amplitude
            stereo = np.empty(num_samples * 2, dtype=np.int16)
            stereo[0::2] = np.clip(left * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            stereo[1::2] = np.clip(right * amp, AudioConstants.MIN_AMPLITUDE, AudioConstants.MAX_AMPLITUDE).astype(np.int16)
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8)
                wav.setframerate(sr); wav.writeframes(stereo.tobytes())
            return True
        except Exception as e:
            print(f"!!! PINK NOISE GEN FAILED: {filepath.name} - {e}"); return False
    @classmethod
    def _generate_chunked(cls, filepath: Path, amplitude: float) -> bool:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            sr, duration = AudioConstants.SAMPLE_RATE, AudioConstants.DURATION_SECONDS
            total_samples, chunk_samples = sr * duration, sr * AudioConstants.CHUNK_SECONDS
            amp, fade_samples = int(AudioConstants.MAX_AMPLITUDE * amplitude), AudioConstants.FADE_SAMPLES
            b_vals = [0.0] * 7
            with wave.open(str(filepath), 'w') as wav:
                wav.setnchannels(AudioConstants.CHANNELS); wav.setsampwidth(AudioConstants.BIT_DEPTH // 8); wav.setframerate(sr)
                for chunk_idx in range((total_samples + chunk_samples - 1) // chunk_samples):
                    chunk_start, buffer = chunk_idx * chunk_samples, []
                    for i in range(min(chunk_samples, total_samples - chunk_start)):
                        sample_idx = chunk_start + i
                        white = random.uniform(-1, 1)
                        b_vals[0] = 0.99886 * b_vals[0] + white * 0.0555179
                        b_vals[1] = 0.99332 * b_vals[1] + white * 0.0750759
                        b_vals[2] = 0.96900 * b_vals[2] + white * 0.1538520
                        b_vals[3] = 0.86650 * b_vals[3] + white * 0.3104856
                        b_vals[4] = 0.55000 * b_vals[4] + white * 0.5329522
                        b_vals[5] = -0.7616 * b_vals[5] - white * 0.0168980
                        pink = (sum(b_vals[:6]) + white * 0.5362) / 4.5
                        fade = 0.5 * (1 - math.cos(math.pi * sample_idx / fade_samples)) if sample_idx < fade_samples else (0.5 * (1 + math.cos(math.pi * (sample_idx - (total_samples - fade_samples)) / fade_samples)) if sample_idx > total_samples - fade_samples else 1.0)
                        val = max(AudioConstants.MIN_AMPLITUDE, min(AudioConstants.MAX_AMPLITUDE, int(pink * fade * amp)))
                        buffer.append(struct.pack('<hh', val, val))
                    wav.writeframes(b''.join(buffer))
            return True
        except Exception as e:
            print(f"!!! PINK NOISE GEN (chunked) FAILED: {e}"); return False

class NeuroLinguisticCompiler:
    FADE_MS = 200
    SHISHA_SCRIPTS = [
        ("sys_intro_init.wav", "This is your Shisha assistant checking in. All systems are standing by."),
        ("phase1_ignition.wav", "The coals are almost ready. Would you mind checking the white coating? Just lift the lid for a quick look."),
        ("phase2_ventilation.wav", "The coals have reached optimal temperature. You may set them on the bowl now. I'll guide you through the next step shortly."),
        ("phase3_heatsoak.wav", "Begin your first draw now. Let the warmth spread evenly through the bowl."),
        ("phase4_calibration.wav", "Everything is perfectly prepared. Your shisha experience begins now. Settle in, and I'll keep watch over the session for you."),
        ("phase5_termination.wav", "Your shisha is approaching completion. The smoke will fade soon. Thank you for this session. Until next time."),
    ]
    @classmethod
    def _hash_key(cls, text: str, definition: str, voice: str) -> str:
        return hashlib.md5(f"v6_alpha_only|{text}|{definition}|{voice}".encode()).hexdigest()[:12]
    @classmethod
    def _apply_modulation(cls, samples: np.ndarray, sr: int) -> np.ndarray:
        n, t = len(samples), np.arange(len(samples), dtype=np.float64) / sr
        alpha_env = 1.0 + AudioConstants.NLC_ALPHA_DEPTH * np.sin(2 * np.pi * AudioConstants.NLC_ALPHA_HZ * t)
        modulated = samples * alpha_env
        fade_samples = int(sr * cls.FADE_MS / 1000)
        if fade_samples > 0 and n > fade_samples * 2:
            modulated[:fade_samples] *= np.linspace(0, 1, fade_samples)
            modulated[-fade_samples:] *= np.linspace(1, 0, fade_samples)
        max_val = np.max(np.abs(modulated))
        if max_val > 0: modulated = modulated / max_val
        return np.clip(modulated, -1, 1)
    @classmethod
    def _generate_tts(cls, text: str, api_key: str, voice: str = 'nova') -> Optional[bytes]:
        if not REQUESTS_AVAILABLE: return None
        try:
            resp = requests.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": "tts-1-hd", "voice": voice, "input": text, "response_format": "wav"}, timeout=60)
            return resp.content if resp.status_code == 200 else None
        except: return None
    SHISHA_ASSET_VERSION = "v6_alpha_only"
    @classmethod
    def generate_shisha_assets(cls, output_dir: Path, api_key: str, voice: str = 'nova') -> int:
        if not api_key or not all([NUMPY_AVAILABLE, REQUESTS_AVAILABLE]): return 0
        output_dir.mkdir(parents=True, exist_ok=True)
        version_marker = output_dir / f".{cls.SHISHA_ASSET_VERSION}"
        if not version_marker.exists():
            for f in list(output_dir.glob("*.wav")) + list(output_dir.glob("*.mp3")): f.unlink()
            neuro_dir = output_dir / "neuro"
            if neuro_dir.exists():
                for f in neuro_dir.glob("*"): f.unlink() if f.is_file() else None
                try: neuro_dir.rmdir()
                except: pass
            print(f"[Shisha] Upgrading to {cls.SHISHA_ASSET_VERSION} (cleared old assets)")
        generated = 0
        for filename, text in cls.SHISHA_SCRIPTS:
            out_path = output_dir / filename
            if out_path.exists(): generated += 1; continue
            raw_wav = cls._generate_tts(text, api_key, voice)
            if not raw_wav: print(f"[Shisha] TTS failed: {filename}"); continue
            try:
                with wave.open(io.BytesIO(raw_wav), 'rb') as wav_in:
                    sr, nch, sw = wav_in.getframerate(), wav_in.getnchannels(), wav_in.getsampwidth()
                    raw = np.frombuffer(wav_in.readframes(wav_in.getnframes()), dtype=np.int16 if sw == 2 else np.int32)
                if nch == 2: raw = ((raw[0::2].astype(np.float64) + raw[1::2].astype(np.float64)) / 2).astype(raw.dtype)
                samples = raw.astype(np.float64) / (32768 if sw == 2 else 2147483648)
                modulated = cls._apply_modulation(samples, sr)
                stereo = np.empty(len(samples) * 2, dtype=np.int16)
                stereo[0::2] = stereo[1::2] = (modulated * 32767).astype(np.int16)
                with wave.open(str(out_path), 'w') as wav_out:
                    wav_out.setnchannels(2); wav_out.setsampwidth(2); wav_out.setframerate(sr)
                    wav_out.writeframes(stereo.tobytes())
                print(f"[Shisha] Generated: {filename}")
                generated += 1
            except Exception as e: print(f"[Shisha] Failed {filename}: {e}")
        if not version_marker.exists(): version_marker.touch()
        print(f"[Shisha] Assets ready: {generated}/{len(cls.SHISHA_SCRIPTS)}")
        return generated
    @classmethod
    def compile_single(cls, text: str, definition: str, output_dir: Path, api_key: str, voice: str = 'nova') -> Optional[Path]:
        if not all([NUMPY_AVAILABLE, SCIPY_AVAILABLE, REQUESTS_AVAILABLE]): return None
        output_dir.mkdir(parents=True, exist_ok=True)
        file_hash = cls._hash_key(text, definition, voice)
        out_path = output_dir / f"{file_hash}.wav"
        if out_path.exists(): return out_path
        try:
            prompt = f"{text}. {definition}" if definition else text
            resp = requests.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": "tts-1-hd", "voice": voice, "input": prompt, "response_format": "wav"}, timeout=30)
            if resp.status_code != 200: print(f"[NLC] API error {resp.status_code}: {resp.text[:100]}"); return None
            with wave.open(io.BytesIO(resp.content), 'rb') as wav_in:
                sr, nch, sw = wav_in.getframerate(), wav_in.getnchannels(), wav_in.getsampwidth()
                raw = np.frombuffer(wav_in.readframes(wav_in.getnframes()), dtype=np.int16 if sw == 2 else np.int32)
            if nch == 2: raw = ((raw[0::2].astype(np.float64) + raw[1::2].astype(np.float64)) / 2).astype(raw.dtype)
            samples = raw.astype(np.float64) / (32768 if sw == 2 else 2147483648)
            modulated = cls._apply_modulation(samples, sr)
            stereo = np.empty(len(samples) * 2, dtype=np.int16)
            stereo[0::2] = stereo[1::2] = (modulated * 32767).astype(np.int16)
            with wave.open(str(out_path), 'w') as wav_out:
                wav_out.setnchannels(2); wav_out.setsampwidth(2); wav_out.setframerate(sr)
                wav_out.writeframes(stereo.tobytes())
            print(f"[NLC] Compiled: {text[:20]}... → {file_hash}.wav")
            return out_path
        except Exception as e:
            print(f"[NLC] Compile failed: {e}"); return None
    @classmethod
    def modulate_file(cls, input_path: Path, output_path: Path) -> bool:
        if not NUMPY_AVAILABLE: return False
        try:
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(str(input_path))
                sr, nch = audio.frame_rate, audio.channels
                raw = np.array(audio.get_array_of_samples(), dtype=np.float64)
                if nch == 2: raw = (raw[0::2] + raw[1::2]) / 2
                samples = raw / 32768.0
            except ImportError:
                if not input_path.suffix.lower() == '.wav': print(f"[NLC] pydub required for non-WAV: {input_path}"); return False
                with wave.open(str(input_path), 'rb') as wav_in:
                    sr, nch, sw = wav_in.getframerate(), wav_in.getnchannels(), wav_in.getsampwidth()
                    raw = np.frombuffer(wav_in.readframes(wav_in.getnframes()), dtype=np.int16 if sw == 2 else np.int32)
                if nch == 2: raw = ((raw[0::2].astype(np.float64) + raw[1::2].astype(np.float64)) / 2)
                samples = raw.astype(np.float64) / (32768 if sw == 2 else 2147483648)
            modulated = cls._apply_modulation(samples, sr)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            stereo = np.empty(len(samples) * 2, dtype=np.int16)
            stereo[0::2] = stereo[1::2] = (modulated * 32767).astype(np.int16)
            with wave.open(str(output_path), 'w') as wav_out:
                wav_out.setnchannels(2); wav_out.setsampwidth(2); wav_out.setframerate(sr)
                wav_out.writeframes(stereo.tobytes())
            print(f"[NLC] Modulated: {input_path.name} → {output_path.name}")
            return True
        except Exception as e:
            print(f"[NLC] Modulate failed: {e}"); return False
    @classmethod
    def compile_all(cls, vocab_path: Path, output_dir: Path, api_key: str, voice: str = 'nova') -> List[Path]:
        if not vocab_path.exists(): return []
        try:
            with open(vocab_path, 'r', encoding='utf-8') as f: vocab = json.load(f)
        except Exception as e: print(f"[NLC] vocab.json load error: {e}"); return []
        compiled = []
        for item in vocab:
            text = item.get('text', '')
            definition = item.get('definition', item.get('meaning', ''))
            if not text: continue
            path = cls.compile_single(text, definition, output_dir, api_key, voice)
            if path: compiled.append(path)
        print(f"[NLC] Compilation complete: {len(compiled)}/{len(vocab)} files")
        return compiled

class NeuroAssetGenerator:
    @classmethod
    def generate_all_files(cls, bgm_dir: Path, device: str = 'headphone') -> Dict[str, bool]:
        results, generated = {}, []
        bgm_dir.mkdir(parents=True, exist_ok=True)
        for name, (carrier, beat) in [('bgm_focus.wav', AudioConstants.FREQ_FOCUS), ('bgm_flow.wav', AudioConstants.FREQ_FLOW), ('bgm_relax.wav', AudioConstants.FREQ_RELAX), ('bgm_sleep.wav', AudioConstants.FREQ_SLEEP)]:
            path = bgm_dir / name
            if not path.exists():
                if NeuroOptimalGenerator.generate_binaural(path, carrier, beat, amplitude=0.5, device=device):
                    generated.append(name)
            results[name] = path.exists()
        rain_path = bgm_dir / 'ambient_rain.wav'
        if not rain_path.exists():
            if NeuroPinkNoiseGenerator.generate(rain_path, amplitude=0.4):
                generated.append('ambient_rain.wav')
        results['ambient_rain.wav'] = rain_path.exists()
        fire_path = bgm_dir / 'ambient_fire.wav'
        if not fire_path.exists():
            if NeuroFireGenerator.generate(fire_path, amplitude=0.4):
                generated.append('ambient_fire.wav')
        results['ambient_fire.wav'] = fire_path.exists()
        if generated: print(f"[Audio] Generated: {', '.join(generated)}")
        return results

def cleanup_audio_assets(bgm_path: Path, dry_run: bool = True) -> List[str]:
    removed = []
    if bgm_path.exists():
        for wav_file in bgm_path.glob("*.wav"):
            if wav_file.name not in AudioConstants.ALLOWED_ASSETS:
                if not dry_run: wav_file.unlink()
                removed.append(wav_file.name)
    return removed

class OutputProfile:
    HEADPHONE = 'headphone'
    SPEAKER = 'speaker'
    EARBUD = 'earbud'
    @classmethod
    def apply_profile(cls, volume: float, profile: str) -> float:
        return volume * NeuroAdaptiveOptimizer.get_profile_factor(profile)
    @classmethod
    def get_stereo_volumes(cls, volume: float, profile: str, pan: float = 0.0) -> Tuple[float, float]:
        effective = cls.apply_profile(volume, profile)
        return effective * (1 - max(0, pan)), effective * (1 + min(0, pan))

class VolumeManager:
    def __init__(self):
        self._volumes = {'master': 1.0, 'bgm': 0.08, 'voice': 0.6, 'sfx': 0.5, 'ambient_0': 0.15, 'ambient_1': 0.15, 'ambient_2': 0.15}
        self._profile = OutputProfile.HEADPHONE
        self._lock = threading.Lock()
    def set_volume(self, key: str, value: float):
        with self._lock: self._volumes[key] = max(0.0, min(1.0, value))
    def get_volume(self, key: str) -> float:
        with self._lock: return self._volumes.get(key, 1.0)
    def set_profile(self, profile: str):
        with self._lock: self._profile = profile
    def get_profile(self) -> str:
        with self._lock: return self._profile
    def get_effective_volume(self, key: str) -> float:
        with self._lock: return OutputProfile.apply_profile(self._volumes.get(key, 1.0) * self._volumes.get('master', 1.0), self._profile)

class NeuroSoundEngine:
    STATE_MODE_MAP = {'DEEP_DIVE': 'FOCUS', 'HYPERFOCUS': 'FOCUS', 'peak': 'FOCUS', 'high': 'FOCUS', 'MODERATE': 'FLOW', 'moderate': 'FLOW', 'LIGHT': 'FLOW', 'low': 'FLOW', 'CRUISING': 'FLOW', 'ACTIVE': 'FLOW', 'REST': 'RELAX', 'rest': 'RELAX', 'SHISHA': 'RELAX', 'shisha': 'RELAX', 'IDLE': 'FLOW', 'CRITICAL': 'SLEEP', 'EXHAUSTED': 'SLEEP', 'DEPLETED': 'SLEEP'}
    MODE_BGM_MAP = {'FOCUS': 'bgm_focus.wav', 'FLOW': 'bgm_flow.wav', 'RELAX': 'bgm_relax.wav', 'SLEEP': 'bgm_sleep.wav', 'SHISHA': None}
    MODE_AROUSAL_LEVEL = {'FOCUS': 4, 'FLOW': 3, 'RELAX': 2, 'SLEEP': 1, 'SHISHA': 2}
    TRANSITION_FADE_MS = {
        'upward': 5000,
        'slight_up': 8000,
        'slight_down': 20000,
        'downward': 40000,
        'same': 10000,
    }
    AMBIENT_FILE_MAP = {'Rain': 'ambient_rain.wav', 'Fire': 'ambient_fire.wav'}
    TTS_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']
    def __init__(self, data_path: Path = None, config: Dict = None):
        self.data_path = Path(data_path) if data_path else Path("Data")
        self.bgm_path, self.voice_path = self.data_path / "sounds" / "bgm", self.data_path / "sounds" / "voice"
        self.learning_path = self.data_path / "sounds" / "learning"
        self.bgm_path.mkdir(parents=True, exist_ok=True); self.voice_path.mkdir(parents=True, exist_ok=True); self.learning_path.mkdir(parents=True, exist_ok=True)
        self.volume_manager = VolumeManager()
        self._full_config = config or {}
        default_config = {'enabled': True, 'master_volume': 1.0, 'bgm_enabled': True, 'bgm_volume': 0.08, 'voice_enabled': True, 'voice_volume': 0.6, 'sfx_volume': 0.5, 'headphone_mode': True, 'device_type': 'headphone', 'bas_enabled': False, 'fade_duration_ms': 10000, 'duck_ratio': 0.3, 'voice_cooldown_sec': 5, 'state_inertia_seconds': 30, 'ambient_slots': [], 'learning_volume_ratio': 0.6}
        self.config = {**default_config, **(config.get('audio', config) if config and isinstance(config.get('audio', config), dict) else {})} if config else default_config
        self._bgm_channel_a, self._bgm_channel_b = None, None
        self._bgm_sound_cache: Dict[str, 'pygame.mixer.Sound'] = {}
        self._active_channel = 'A'
        self._bgm_lock = threading.Lock()
        self._bgm_request_queue: List[Tuple[str, int]] = []
        self._bgm_worker_thread = None
        self._bgm_worker_stop = threading.Event()
        self._crossfade_in_progress = False
        self.ambient_channels, self.voice_channel, self.sfx_channel = [None, None, None], None, None
        self.learning_channel = None
        self.current_mode, self.current_bgm_file = None, None
        self._bgm_target_volume, self._bgm_current_volume = self.config['bgm_volume'], 0.0
        self._user_bgm_volume = self.config['bgm_volume']
        ambient_slots_cfg = self.config.get('ambient_slots', [])
        self._ambient_slots = [{'source': s.get('source', 'Rain'), 'volume': s.get('volume', 0.15), 'enabled': s.get('enabled', False), 'sound': None} for s in ambient_slots_cfg[:3]] if ambient_slots_cfg else [{'source': 'Rain', 'volume': 0.15, 'enabled': False, 'sound': None}, {'source': 'Fire', 'volume': 0.15, 'enabled': False, 'sound': None}, {'source': 'Rain', 'volume': 0.15, 'enabled': False, 'sound': None}]
        while len(self._ambient_slots) < 3: self._ambient_slots.append({'source': 'Rain', 'volume': 0.15, 'enabled': False, 'sound': None})
        self._shisha_mode, self._pre_shisha_bgm_volume, self._pre_shisha_ambient_volumes, self._pre_shisha_mode = False, 0.0, [0.0, 0.0, 0.0], 'FLOW'
        self._fade_thread, self._fade_stop_event, self._fade_lock = None, threading.Event(), threading.Lock()
        self._last_voice_time, self.is_ducking, self._duck_thread, self._duck_stop_event = None, False, None, threading.Event()
        self._initialized, self._mixer_initialized, self._bgm_ready = False, False, False
        self._generation_thread, self._generation_complete_event = None, threading.Event()
        self._muted = False
        self._learning_files: List[Path] = []
        self._learning_compile_thread = None
        self._learning_loop_thread, self._learning_stop_event = None, threading.Event()
        self._learning_interval_min = self.config.get('learning_interval_min', 120)
        self._learning_interval_max = self.config.get('learning_interval_max', 300)
        self._idle_mode, self._pre_idle_bgm_volume = False, 0.0
        self._idle_threshold_sec = self.config.get('idle_threshold_sec', 900)
        self._bio_context = {'state': 'FLOW', 'load': 0.0}
        self._last_volume_update = 0.0
        self._last_volume_target = 0.0
        if 'shisha_volume' not in self.config: self.config['shisha_volume'] = 0.5
        device = self.config.get('device_type', 'headphone') if not self.config.get('headphone_mode', True) else 'headphone'
        self.volume_manager.set_profile(device)
        self.shisha_voice_path = self.data_path / "sounds" / "shisha"
        self.bgm_channel_a, self.bgm_channel_b = None, None
        self._active_bgm_channel = 'A'
        self.bgm_channels = [None, None]
        self._bgm_sounds = [None, None]
        self._active_bgm_idx = 0
    def initialize(self, wait_for_assets: bool = False, timeout: float = 30.0):
        if not PYGAME_AVAILABLE: print("[Audio] pygame not available"); return
        self._init_mixer()
        device = self.volume_manager.get_profile()
        openai_cfg = self._full_config.get('openai', {}) if self._full_config else {}
        api_key = openai_cfg.get('api_key', '')
        voice = openai_cfg.get('voice', 'nova')
        def background_init():
            try:
                removed = cleanup_audio_assets(self.bgm_path, dry_run=False)
                if removed: print(f"[Audio] Cleaned: {', '.join(removed)}")
                results = NeuroAssetGenerator.generate_all_files(self.bgm_path, device=device)
                print(f"[Audio] {sum(1 for v in results.values() if v)}/{len(results)} BGM assets ready")
                if api_key:
                    NeuroLinguisticCompiler.generate_shisha_assets(self.shisha_voice_path, api_key, voice)
                self._preload_bgm_cache()
                self._bgm_ready = True
                self.start_bgm()
            except Exception as e: print(f"!!! AUDIO ASSET GENERATION FAILED: {e}"); self._bgm_ready = False
            finally: self._generation_complete_event.set()
        required = ['bgm_focus.wav', 'bgm_flow.wav', 'bgm_relax.wav', 'bgm_sleep.wav', 'ambient_rain.wav', 'ambient_fire.wav']
        shisha_required = [s[0] for s in NeuroLinguisticCompiler.SHISHA_SCRIPTS]
        bgm_ready = all((self.bgm_path / f).exists() for f in required)
        shisha_ready = all((self.shisha_voice_path / f).exists() for f in shisha_required)
        if bgm_ready and (shisha_ready or not api_key):
            self._preload_bgm_cache()
            self._bgm_ready = True
            self._generation_complete_event.set()
            print("[Audio] All assets already exist")
            self.start_bgm()
        else:
            print("[Audio] Generating neural-optimized assets...")
            self._generation_thread = threading.Thread(target=background_init, daemon=True, name="Audio-Generator")
            self._generation_thread.start()
            if wait_for_assets: print(f"[Audio] Waiting (timeout={timeout}s)..."); self._generation_complete_event.wait(timeout=timeout)
        self._initialized = True; print(f"[Audio] Initialized (ready={self._bgm_ready})")
        has_key = bool(api_key)
        print(f"[NLC] Config loaded. API Key present: {'Yes' if has_key else 'No'}, enabled: {openai_cfg.get('enabled', False)}")
        if openai_cfg.get('enabled', False) and has_key:
            vocab_path = self.data_path / "vocab.json"
            if vocab_path.exists():
                self.start_learning_compilation(vocab_path, api_key, voice)
                print(f"[NLC] Auto-compilation started (voice={voice})")
        self._learning_stop_event.clear()
        self._learning_loop_thread = threading.Thread(target=self._learning_scheduler, daemon=True, name="Learning-Scheduler")
        self._learning_loop_thread.start()
        print("[NLC] Learning scheduler started")
    def wait_for_ready(self, timeout: float = 30.0) -> bool:
        return True if self._bgm_ready else self._generation_complete_event.wait(timeout=timeout)
    def _init_mixer(self):
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=AudioConstants.SAMPLE_RATE, size=-AudioConstants.BIT_DEPTH, channels=AudioConstants.CHANNELS, buffer=AudioConstants.BUFFER_SIZE)
            self._mixer_initialized = True
            pygame.mixer.set_num_channels(10)
            self._bgm_channel_a = pygame.mixer.Channel(0)
            self._bgm_channel_b = pygame.mixer.Channel(1)
            self._bgm_channel_a.set_volume(0, 0)
            self._bgm_channel_b.set_volume(0, 0)
            self.bgm_channel_a, self.bgm_channel_b = self._bgm_channel_a, self._bgm_channel_b
            self.bgm_channels = [self._bgm_channel_a, self._bgm_channel_b]
            self.ambient_channels = [pygame.mixer.Channel(2), pygame.mixer.Channel(3), pygame.mixer.Channel(4)]
            self.voice_channel, self.sfx_channel = pygame.mixer.Channel(5), pygame.mixer.Channel(6)
            self.learning_channel = pygame.mixer.Channel(7)
            self.voice_channel.set_volume(self.config['voice_volume'], self.config['voice_volume'])
            self.sfx_channel.set_volume(self.config['sfx_volume'], self.config['sfx_volume'])
            self.learning_channel.set_volume(self.config['voice_volume'], self.config['voice_volume'])
            self._start_bgm_worker()
        except Exception as e: print(f"!!! MIXER INIT FAILED: {e}"); self._mixer_initialized = False
    def _preload_bgm_cache(self):
        for bgm_file in self.MODE_BGM_MAP.values():
            if bgm_file:
                path = self.bgm_path / bgm_file
                if path.exists() and bgm_file not in self._bgm_sound_cache:
                    try:
                        self._bgm_sound_cache[bgm_file] = pygame.mixer.Sound(str(path))
                    except: pass
        print(f"[Audio] BGM cache: {len(self._bgm_sound_cache)} files")
    def _get_cached_sound(self, bgm_file: str) -> 'pygame.mixer.Sound':
        if bgm_file in self._bgm_sound_cache:
            return self._bgm_sound_cache[bgm_file]
        path = self.bgm_path / bgm_file
        if path.exists():
            sound = pygame.mixer.Sound(str(path))
            self._bgm_sound_cache[bgm_file] = sound
            return sound
        return None
    def _start_bgm_worker(self):
        if self._bgm_worker_thread and self._bgm_worker_thread.is_alive(): return
        self._bgm_worker_stop.clear()
        self._bgm_worker_thread = threading.Thread(target=self._bgm_worker_loop, daemon=True, name="BGM-Worker")
        self._bgm_worker_thread.start()
    def _bgm_worker_loop(self):
        """Protocol 2: LIFO Queue Compression - 最新のリクエストのみ処理"""
        while not self._bgm_worker_stop.is_set():
            try:
                target_file, fade_ms = None, None
                with self._bgm_lock:
                    if self._bgm_request_queue:
                        target_file, fade_ms = self._bgm_request_queue[-1]
                        self._bgm_request_queue.clear()
                if target_file and target_file != self.current_bgm_file and not self._crossfade_in_progress:
                    self._execute_crossfade(target_file, fade_ms)
                time.sleep(0.05)
            except Exception as e:
                print(f"[Audio] BGM worker error: {e}")
                time.sleep(0.2)
    def _check_new_request(self) -> bool:
        """Protocol 3: キューに新しいリクエストがあるかチェック（Early Exit用）"""
        with self._bgm_lock:
            return len(self._bgm_request_queue) > 0
    def _execute_crossfade(self, new_bgm_file: str, fade_ms: int = None):
        """
        Protocol 1: Zero-Start & Tail-Padding
        Protocol 2: Biological Transition Dynamics
        Protocol 3: Interruptible Fading Loop
        """
        if not self.config['bgm_enabled'] or self._muted: return
        sound = self._get_cached_sound(new_bgm_file)
        if not sound: return
        self._crossfade_in_progress = True
        try:
            outgoing_ch = self._bgm_channel_a if self._active_channel == 'A' else self._bgm_channel_b
            incoming_ch = self._bgm_channel_b if self._active_channel == 'A' else self._bgm_channel_a
            next_channel = 'B' if self._active_channel == 'A' else 'A'
            if fade_ms is None: fade_ms = self.config['fade_duration_ms']
            step_ms = 50
            total_steps = max(1, fade_ms // step_ms)
            master = self.config.get('master_volume', 1.0)
            profile_factor = NeuroAdaptiveOptimizer.get_profile_factor(self.volume_manager.get_profile())
            target_vol = self._bgm_target_volume * (self.config['duck_ratio'] if self.is_ducking else 1.0)
            start_vol = self._bgm_current_volume
            incoming_ch.set_volume(0, 0)
            incoming_ch.play(sound, loops=-1)
            time.sleep(0.05)
            if self._check_new_request():
                incoming_ch.stop()
                self._crossfade_in_progress = False
                return
            for step in range(total_steps + 1):
                if self._check_new_request():
                    final_v = target_vol * master * profile_factor
                    incoming_ch.set_volume(final_v, final_v)
                    outgoing_ch.set_volume(0, 0)
                    time.sleep(0.05)
                    outgoing_ch.stop()
                    self._active_channel = next_channel
                    self.current_bgm_file = new_bgm_file
                    self._bgm_current_volume = target_vol
                    self._crossfade_in_progress = False
                    return
                if self._muted or self._bgm_worker_stop.is_set(): break
                progress = step / total_steps
                out_v = start_vol * math.cos(progress * math.pi / 2) * master * profile_factor
                in_v = target_vol * math.sin(progress * math.pi / 2) * master * profile_factor
                outgoing_ch.set_volume(out_v, out_v)
                incoming_ch.set_volume(in_v, in_v)
                self._bgm_current_volume = start_vol * (1 - progress) + target_vol * progress
                time.sleep(step_ms / 1000)
            outgoing_ch.set_volume(0, 0)
            time.sleep(0.05)
            outgoing_ch.stop()
            final_v = target_vol * master * profile_factor
            incoming_ch.set_volume(final_v, final_v)
            self._active_channel = next_channel
            self.current_bgm_file = new_bgm_file
            self._bgm_current_volume = target_vol
            direction = "↑" if fade_ms <= 10000 else "↓"
            print(f"[Audio] Crossfade {direction} ({fade_ms/1000:.0f}s): {new_bgm_file} (Ch {next_channel})")
        except Exception as e:
            print(f"!!! CROSSFADE FAILED: {e}")
        finally:
            self._crossfade_in_progress = False
    def set_enabled(self, enabled: bool):
        self._muted = not enabled
        self.config['enabled'] = enabled
        if enabled:
            self._apply_all_volumes()
            if self.config['bgm_enabled'] and not self.current_bgm_file: self.start_bgm()
        else:
            self._mute_all_channels()
    def _mute_all_channels(self):
        for ch in [self._bgm_channel_a, self._bgm_channel_b] + self.ambient_channels + [self.voice_channel, self.sfx_channel, self.learning_channel]:
            if ch: ch.set_volume(0, 0)
    def _apply_all_volumes(self):
        master = self.config.get('master_volume', 1.0)
        profile = self.volume_manager.get_profile()
        profile_factor = NeuroAdaptiveOptimizer.get_profile_factor(profile)
        channel = self._bgm_channel_a if self._active_channel == 'A' else self._bgm_channel_b
        if channel:
            vol = self._bgm_current_volume * master * profile_factor
            channel.set_volume(vol, vol)
        for i, slot in enumerate(self._ambient_slots):
            if self.ambient_channels[i] and slot['enabled']:
                vol = slot['volume'] * master * profile_factor
                self.ambient_channels[i].set_volume(vol, vol)
        if self.voice_channel:
            vol = self.config['voice_volume'] * master * profile_factor
            self.voice_channel.set_volume(vol, vol)
    def set_master_volume(self, volume: float):
        self.config['master_volume'] = max(0.0, min(1.0, volume))
        self.volume_manager.set_volume('master', volume)
        if not self._muted: self._apply_all_volumes()
    def set_bgm_enabled(self, enabled: bool):
        self.config['bgm_enabled'] = enabled
        if enabled:
            if self._bgm_ready and not self.current_bgm_file: self.start_bgm()
        else:
            self.stop_bgm()
    def set_voice_enabled(self, enabled: bool):
        self.config['voice_enabled'] = enabled
    def set_voice_volume(self, volume: float):
        self.config['voice_volume'] = max(0.0, min(1.0, volume))
        if self.voice_channel and not self._muted:
            profile = self.volume_manager.get_profile()
            profile_factor = NeuroAdaptiveOptimizer.get_profile_factor(profile)
            vol = volume * self.config.get('master_volume', 1.0) * profile_factor
            self.voice_channel.set_volume(vol, vol)
    def set_headphone_mode(self, enabled: bool):
        self.config['headphone_mode'] = enabled
        device = 'headphone' if enabled else self.config.get('device_type', 'speaker')
        self.volume_manager.set_profile(device)
        if not self._muted: self._apply_all_volumes()
        print(f"[Audio] Profile: {device}")
    def set_device_type(self, device: str):
        if device in [NeuroAdaptiveOptimizer.HEADPHONE, NeuroAdaptiveOptimizer.SPEAKER, NeuroAdaptiveOptimizer.EARBUD]:
            self.config['device_type'] = device
            self.volume_manager.set_profile(device)
            if not self._muted: self._apply_all_volumes()
            print(f"[Audio] Device: {device}")
    def set_bas_enabled(self, enabled: bool):
        self.config['bas_enabled'] = enabled
        print(f"[Audio] BAS: {'enabled' if enabled else 'disabled'}")
    def set_ambient_slot(self, slot: int, source: str = None, volume: float = None, enabled: bool = None):
        if not 0 <= slot < 3: return
        if source is not None: self.set_ambient_source(slot, source)
        if volume is not None: self.set_ambient_volume(slot, volume)
        if enabled is not None: self.enable_ambient(slot, enabled)
    def get_mode_for_state(self, state: str) -> str:
        return self.STATE_MODE_MAP.get(state, 'FLOW')
    def _calculate_transition_fade_ms(self, from_mode: str, to_mode: str) -> int:
        """Protocol 2: 生物学的遷移時間の計算"""
        from_level = self.MODE_AROUSAL_LEVEL.get(from_mode, 3)
        to_level = self.MODE_AROUSAL_LEVEL.get(to_mode, 3)
        diff = to_level - from_level
        if diff >= 2:
            return self.TRANSITION_FADE_MS['upward']
        elif diff == 1:
            return self.TRANSITION_FADE_MS['slight_up']
        elif diff == 0:
            return self.TRANSITION_FADE_MS['same']
        elif diff == -1:
            return self.TRANSITION_FADE_MS['slight_down']
        else:
            return self.TRANSITION_FADE_MS['downward']
    def set_mode(self, mode: str):
        if not self._bgm_ready: return
        if mode == 'SHISHA': self.enter_shisha_mode(); return
        if self._shisha_mode: self.resume_from_shisha()
        if mode != self.current_mode:
            from_mode = self.current_mode or 'FLOW'
            fade_ms = self._calculate_transition_fade_ms(from_mode, mode)
            self.current_mode = mode
            bgm_file = self.MODE_BGM_MAP.get(mode, 'bgm_flow.wav')
            if bgm_file: self._crossfade_to_bgm(bgm_file, fade_ms)
    def _crossfade_to_bgm(self, new_bgm_file: str, fade_ms: int = None):
        if not self.config['bgm_enabled'] or self._muted: return
        if self.current_bgm_file == new_bgm_file: return
        if fade_ms is None: fade_ms = self.config['fade_duration_ms']
        with self._bgm_lock:
            self._bgm_request_queue.append((new_bgm_file, fade_ms))
    def _stop_fade_thread(self):
        if self._fade_thread and self._fade_thread.is_alive():
            self._fade_stop_event.set()
            self._fade_thread.join(timeout=0.3)
            self._fade_stop_event.clear()
    def _fade_bgm_to(self, target_vol: float, duration_ms: int):
        if self._muted: return
        with self._fade_lock:
            self._stop_fade_thread()
            def fade_worker():
                step_ms, steps = AudioConstants.FADE_STEP_MS, max(1, duration_ms // AudioConstants.FADE_STEP_MS)
                start_vol, master = self._bgm_current_volume, self.config.get('master_volume', 1.0)
                profile = self.volume_manager.get_profile()
                profile_factor = NeuroAdaptiveOptimizer.get_profile_factor(profile)
                channel = self._bgm_channel_a if self._active_channel == 'A' else self._bgm_channel_b
                for step in range(steps + 1):
                    if self._fade_stop_event.is_set() or self._muted: break
                    current = start_vol + (target_vol - start_vol) * step / steps
                    if channel: channel.set_volume(current * master * profile_factor, current * master * profile_factor)
                    self._bgm_current_volume = current
                    time.sleep(step_ms / 1000)
            self._fade_thread = threading.Thread(target=fade_worker, daemon=True, name="BGM-Fade")
            self._fade_thread.start()
    def start_bgm(self, mode: str = None):
        if not self.config['bgm_enabled'] or not self._bgm_ready or self._muted: return
        effective_mode = mode or self.current_mode or 'FLOW'
        bgm_file = self.MODE_BGM_MAP.get(effective_mode, 'bgm_flow.wav')
        if not bgm_file: return
        sound = self._get_cached_sound(bgm_file)
        if not sound:
            print(f"[Audio] BGM file not found: {bgm_file}")
            return
        try:
            channel = self._bgm_channel_a if self._active_channel == 'A' else self._bgm_channel_b
            channel.set_volume(0, 0)
            channel.play(sound, loops=-1)
            time.sleep(0.05)
            self.current_bgm_file = bgm_file
            if self.current_mode is None: self.current_mode = effective_mode
            self._fade_bgm_to(self._bgm_target_volume, 2000)
            print(f"[Audio] Started BGM: {bgm_file} (mode={effective_mode})")
        except Exception as e: print(f"!!! BGM START FAILED: {e}")
    def stop_bgm(self):
        self._stop_fade_thread()
        with self._bgm_lock:
            self._bgm_request_queue.clear()
        for ch in [self._bgm_channel_a, self._bgm_channel_b]:
            if ch:
                ch.set_volume(0, 0)
        time.sleep(0.05)
        for ch in [self._bgm_channel_a, self._bgm_channel_b]:
            if ch: ch.stop()
        self._bgm_current_volume, self.current_bgm_file = 0.0, None
    def set_bgm_volume(self, volume: float):
        self._user_bgm_volume = max(0.0, min(1.0, volume))
        self._bgm_target_volume = self._user_bgm_volume
        self.config['bgm_volume'] = self._user_bgm_volume
        if not self.is_ducking and not self._muted: self._fade_bgm_to(self._bgm_target_volume, 500)
    def start_ambient(self, slot: int):
        if not 0 <= slot < 3 or not self._ambient_slots[slot]['enabled'] or self._muted: return
        filename = self.AMBIENT_FILE_MAP.get(self._ambient_slots[slot]['source'])
        if not filename: return
        filepath = self.bgm_path / filename
        if not filepath.exists(): return
        try:
            sound = pygame.mixer.Sound(str(filepath))
            profile = self.volume_manager.get_profile()
            profile_factor = NeuroAdaptiveOptimizer.get_profile_factor(profile)
            vol = self._ambient_slots[slot]['volume'] * self.config.get('master_volume', 1.0) * profile_factor
            self.ambient_channels[slot].set_volume(0, 0)
            self.ambient_channels[slot].play(sound, loops=-1)
            time.sleep(0.05)
            self.ambient_channels[slot].set_volume(vol, vol)
            self._ambient_slots[slot]['sound'] = sound
            print(f"[Audio] Started ambient slot {slot}: {self._ambient_slots[slot]['source']}")
        except Exception as e: print(f"!!! AMBIENT START FAILED: {e}")
    def stop_ambient(self, slot: int):
        if 0 <= slot < 3 and self.ambient_channels[slot]: self.ambient_channels[slot].stop(); self._ambient_slots[slot]['sound'] = None
    def set_ambient_volume(self, slot: int, volume: float):
        if 0 <= slot < 3:
            self._ambient_slots[slot]['volume'] = max(0.0, min(1.0, volume))
            if self.ambient_channels[slot] and not self._muted:
                profile = self.volume_manager.get_profile()
                profile_factor = NeuroAdaptiveOptimizer.get_profile_factor(profile)
                vol = volume * self.config.get('master_volume', 1.0) * profile_factor
                self.ambient_channels[slot].set_volume(vol, vol)
    def set_ambient_source(self, slot: int, source: str):
        if 0 <= slot < 3 and source in AudioConstants.AMBIENT_SOURCES:
            was_enabled = self._ambient_slots[slot]['enabled']
            if was_enabled: self.stop_ambient(slot)
            self._ambient_slots[slot]['source'] = source
            if was_enabled: self.start_ambient(slot)
    def enable_ambient(self, slot: int, enabled: bool):
        if 0 <= slot < 3:
            self._ambient_slots[slot]['enabled'] = enabled
            self.start_ambient(slot) if enabled else self.stop_ambient(slot)
    def enter_shisha_mode(self):
        if self._shisha_mode: return
        self._shisha_mode, self._pre_shisha_bgm_volume = True, self._bgm_target_volume
        self._pre_shisha_ambient_volumes, self._pre_shisha_mode = [s['volume'] for s in self._ambient_slots], self.current_mode or 'FLOW'
        self._fade_bgm_to(0, 2000)
        for i, slot in enumerate(self._ambient_slots):
            if slot['enabled']: self.set_ambient_volume(i, 0)
        print("[Audio] Entered Shisha Mode")
    def resume_from_shisha(self):
        if not self._shisha_mode: return
        self._shisha_mode, self._bgm_target_volume = False, self._pre_shisha_bgm_volume
        self._fade_bgm_to(self._bgm_target_volume, 2000)
        for i, vol in enumerate(self._pre_shisha_ambient_volumes):
            if self._ambient_slots[i]['enabled']: self.set_ambient_volume(i, vol)
        self.current_mode = self._pre_shisha_mode
        print("[Audio] Resumed from Shisha Mode")
    def enter_idle_mode(self):
        if self._idle_mode or self._shisha_mode: return
        self._idle_mode = True
        self._pre_idle_bgm_volume = self._bgm_target_volume
        self._fade_bgm_to(0, 4000)
        print("[Audio] Entered Idle Mode (BGM fading out)")
    def resume_from_idle(self):
        if not self._idle_mode: return
        self._idle_mode = False
        self._bgm_target_volume = self._pre_idle_bgm_volume
        self._fade_bgm_to(self._bgm_target_volume, 2000)
        print("[Audio] Resumed from Idle Mode")
    def is_idle_mode(self) -> bool: return self._idle_mode
    def set_shisha_volume(self, volume: float):
        self.config['shisha_volume'] = max(0.0, min(2.0, volume))
        if self.sfx_channel:
            master = self.config.get('master_volume', 1.0)
            vol = min(self.config['shisha_volume'] * master, 1.0)
            self.sfx_channel.set_volume(vol, vol)
        print(f"[Audio] Shisha volume set to {int(volume * 100)}%")
    def play_shisha_voice(self, filename_or_path):
        if not self.config.get('voice_enabled', True) or self._muted: return
        filepath = Path(filename_or_path) if isinstance(filename_or_path, (str, Path)) and Path(filename_or_path).is_absolute() else self.voice_path / str(filename_or_path)
        if not filepath.exists(): print(f"[Audio] Shisha voice not found: {filepath}"); return
        try:
            sound = pygame.mixer.Sound(str(filepath))
            master = self.config.get('master_volume', 1.0)
            vol = min(self.config.get('shisha_volume', 0.5) * master, 1.0)
            self.sfx_channel.set_volume(vol, vol)
            self.sfx_channel.play(sound, fade_ms=1000)
            print(f"[Audio] Playing shisha voice (fade-in, vol={int(vol*100)}%): {filepath.name}")
        except Exception as e: print(f"!!! SHISHA VOICE FAILED: {e}")
    def update_bio_context(self, activity_state: str, current_load: float = 0.0):
        self._bio_context = {'state': activity_state, 'load': max(0.0, min(1.0, current_load))}
        self._update_dynamic_bgm_volume()
    def _update_dynamic_bgm_volume(self):
        if self._muted or self._idle_mode or self._shisha_mode: return
        now = time.time()
        if now - self._last_volume_update < 0.5: return
        state = self._bio_context.get('state', 'FLOW')
        state_factor = {'DEEP_DIVE': 0.6, 'HYPERFOCUS': 0.6, 'FLOW': 1.0, 'CRUISING': 1.0, 'MODERATE': 1.0, 'SLEEP': 0.9, 'IDLE': 0.3}.get(state, 0.8)
        load_factor = 1.0 - (self._bio_context.get('load', 0.0) * 0.4)
        target = max(0.01, min(1.0, self._user_bgm_volume * state_factor * load_factor))
        if abs(target - self._last_volume_target) < 0.05: return
        self._last_volume_update = now
        self._last_volume_target = target
        self._bgm_target_volume = target
        self._fade_bgm_to(self._bgm_target_volume, 2000)
    def _calculate_sfx_volume(self) -> float:
        master = self.config.get('master_volume', 1.0)
        bgm_actual = self._bgm_current_volume * master
        ratio = self.config.get('learning_volume_ratio', 0.6)
        state = self._bio_context.get('state', 'FLOW')
        state_mult = {'DEEP_DIVE': 0.5, 'HYPERFOCUS': 0.5, 'FLOW': 0.7, 'CRUISING': 0.7, 'MODERATE': 0.7, 'IDLE': 0.8, 'LIGHT': 0.8, 'SCAVENGING': 0.8}.get(state, 0.6)
        if bgm_actual < 0.01: return master * 0.05
        return min(bgm_actual * ratio * state_mult * 2.0, master * 0.3)
    def _liquid_inject(self, sound_path: Path, channel, volume_key: str = 'voice_volume', duck_ratio: float = 0.8, fade_ms: int = 500) -> bool:
        if self._muted: return False
        if not PYGAME_AVAILABLE or not self._mixer_initialized or not channel: return False
        if not self.config.get('enabled', True): return False
        if not sound_path.exists(): return False
        def _inject_thread():
            try:
                sound = pygame.mixer.Sound(str(sound_path))
                duration_sec = sound.get_length()
                target_vol = self._calculate_sfx_volume()
                self._fade_bgm_to(self._bgm_target_volume * duck_ratio, 2000)
                time.sleep(1.0)
                channel.set_volume(0, 0)
                channel.play(sound)
                fade_steps = max(1, fade_ms // 50)
                for i in range(fade_steps):
                    fade_vol = target_vol * (i + 1) / fade_steps
                    channel.set_volume(fade_vol, fade_vol)
                    time.sleep(0.05)
                time.sleep(duration_sec)
                time.sleep(0.5)
                self._fade_bgm_to(self._bgm_target_volume, 2000)
                print(f"[Audio] Liquid inject complete (adaptive_vol={int(target_vol*100)}%): {sound_path.name}")
            except Exception as e: print(f"[Audio] Liquid inject error: {e}")
        threading.Thread(target=_inject_thread, daemon=True, name="Liquid-Inject").start()
        print(f"[Audio] Liquid injecting: {sound_path.name}")
        return True
    def play_voice(self, text_or_file: str, priority: int = 1):
        if not self.config['voice_enabled'] or self._muted: return
        now = datetime.now()
        if self._last_voice_time and (now - self._last_voice_time).total_seconds() < self.config['voice_cooldown_sec']: return
        filepath = self.voice_path / text_or_file if not text_or_file.startswith('/') else Path(text_or_file)
        if not filepath.exists(): return
        try:
            sound = pygame.mixer.Sound(str(filepath))
            self._start_ducking(); self.voice_channel.play(sound); self._last_voice_time = now
            self._schedule_duck_release(sound.get_length())
        except Exception as e: print(f"!!! VOICE PLAY FAILED: {e}")
    def _start_ducking(self):
        if not self.is_ducking:
            self.is_ducking = True
            self._fade_bgm_to(self._bgm_target_volume * self.config['duck_ratio'], 300)
    def _stop_duck_thread(self):
        if self._duck_thread and self._duck_thread.is_alive():
            self._duck_stop_event.set(); self._duck_thread.join(timeout=0.5); self._duck_stop_event.clear()
    def _schedule_duck_release(self, delay_seconds: float):
        self._stop_duck_thread()
        def duck_release():
            elapsed = 0
            while elapsed < delay_seconds:
                if self._duck_stop_event.is_set(): return
                time.sleep(0.1); elapsed += 0.1
            self.is_ducking = False
            self._fade_bgm_to(self._bgm_target_volume, 500)
        self._duck_thread = threading.Thread(target=duck_release, daemon=True)
        self._duck_thread.start()
    def stop_all(self):
        self._stop_fade_thread(); self._stop_duck_thread()
        with self._bgm_lock:
            self._bgm_request_queue.clear()
        all_channels = [self._bgm_channel_a, self._bgm_channel_b] + self.ambient_channels + [self.voice_channel, self.sfx_channel, self.learning_channel]
        for ch in all_channels:
            if ch: ch.set_volume(0, 0)
        time.sleep(0.05)
        for ch in all_channels:
            if ch: ch.stop()
        self._bgm_current_volume, self.current_bgm_file, self.is_ducking, self._shisha_mode = 0.0, None, False, False
    def cleanup(self):
        self._learning_stop_event.set()
        self._bgm_worker_stop.set()
        if self._learning_loop_thread and self._learning_loop_thread.is_alive():
            self._learning_loop_thread.join(timeout=1.0)
        if self._bgm_worker_thread and self._bgm_worker_thread.is_alive():
            self._bgm_worker_thread.join(timeout=1.0)
        self.stop_all()
        if pygame.mixer.get_init(): pygame.mixer.quit()
    def is_bgm_ready(self) -> bool: return self._bgm_ready
    def is_shisha_mode(self) -> bool: return self._shisha_mode
    def start_learning_compilation(self, vocab_path: Path, api_key: str, voice: str = 'nova'):
        if not api_key or self._learning_compile_thread and self._learning_compile_thread.is_alive(): return
        def compile_task():
            try:
                self._learning_files = NeuroLinguisticCompiler.compile_all(vocab_path, self.learning_path, api_key, voice)
            except Exception as e: print(f"[NLC] Background compile error: {e}")
        self._learning_compile_thread = threading.Thread(target=compile_task, daemon=True)
        self._learning_compile_thread.start()
    def inject_learning_pulse(self, audio_path: Path = None) -> bool:
        if self._shisha_mode: return False
        if not audio_path and not self._learning_files: return False
        target = audio_path if audio_path else random.choice(self._learning_files)
        return self._liquid_inject(target, self.learning_channel, 'voice_volume', duck_ratio=0.8, fade_ms=500)
    def get_learning_files(self) -> List[Path]: return self._learning_files.copy()
    def set_learning_interval(self, min_sec: int, max_sec: int):
        self._learning_interval_min = max(30, min(min_sec, 600))
        self._learning_interval_max = max(60, min(max_sec, 900))
        if self._learning_interval_min > self._learning_interval_max:
            self._learning_interval_min, self._learning_interval_max = self._learning_interval_max, self._learning_interval_min
        print(f"[NLC] Interval updated: {self._learning_interval_min}-{self._learning_interval_max}s")
    def _learning_scheduler(self):
        while not self._learning_stop_event.is_set():
            interval = random.uniform(self._learning_interval_min, self._learning_interval_max)
            elapsed = 0.0
            while elapsed < interval and not self._learning_stop_event.is_set():
                time.sleep(0.5); elapsed += 0.5
            if self._learning_stop_event.is_set(): break
            try:
                openai_cfg = self._full_config.get('openai', {}) if self._full_config else {}
                if not openai_cfg.get('enabled', False): continue
                if not self._learning_files: continue
                if not (self.config.get('enabled', True) and self.config.get('bgm_enabled', True)): continue
                if self._muted or self._shisha_mode: continue
                self.inject_learning_pulse()
            except Exception as e: print(f"[NLC] Scheduler error: {e}")

class NeuroSoundController:
    FP_ANESTHESIA_THRESHOLD = 20
    FP_CRITICAL_THRESHOLD = 30
    def __init__(self, engine: NeuroSoundEngine):
        self.engine, self.last_state, self.last_switch_time = engine, None, 0
        self.inertia_seconds = engine.config.get('state_inertia_seconds', 30)
        self._last_activity_time = time.time()
        self._idle_threshold = engine.config.get('idle_threshold_sec', 900)
    def update_state(self, activity_state: str, fp: float = 100, idle_seconds: float = 0):
        now = time.time()
        if activity_state in ('shisha', 'SHISHA'):
            self.engine.set_mode('SHISHA'); self.last_state, self.last_switch_time = 'shisha', now; return
        if fp < self.FP_ANESTHESIA_THRESHOLD:
            if self.engine.current_mode != 'SLEEP': self.engine.set_mode('SLEEP'); self.last_switch_time = now
            self.last_state = 'ANESTHESIA'; return
        if fp < self.FP_CRITICAL_THRESHOLD:
            if self.engine.current_mode not in ('SLEEP', 'RELAX'): self.engine.set_mode('RELAX'); self.last_switch_time = now
            self.last_state = 'CRITICAL'; return
        if idle_seconds > 0 and idle_seconds < self._idle_threshold:
            self._last_activity_time = now
            if self.engine.is_idle_mode(): self.engine.resume_from_idle()
        if idle_seconds >= self._idle_threshold and not self.engine.is_idle_mode():
            self.engine.enter_idle_mode(); return
        target_mode = self.engine.get_mode_for_state(activity_state)
        if target_mode != self.engine.current_mode and (now - self.last_switch_time) > self.inertia_seconds:
            self.engine.set_mode(target_mode); self.last_switch_time, self.last_state = now, activity_state
    def on_user_input(self):
        self._last_activity_time = time.time()
        if self.engine.is_idle_mode(): self.engine.resume_from_idle()
    def force_mode(self, mode: str):
        self.engine.set_mode(mode); self.last_switch_time = time.time()
    def get_status(self) -> dict:
        elapsed = time.time() - self.last_switch_time
        return {'current_mode': self.engine.current_mode, 'last_state': self.last_state, 'elapsed_since_switch': elapsed, 'inertia_seconds': self.inertia_seconds, 'can_switch': elapsed > self.inertia_seconds, 'idle_mode': self.engine.is_idle_mode()}

if __name__ == "__main__":
    print(f"=== NeuroSoundEngine {__version__} (Zone Support) ===")
    print(f"NumPy: {NUMPY_AVAILABLE} | SciPy: {SCIPY_AVAILABLE} | Pygame: {PYGAME_AVAILABLE} | Requests: {REQUESTS_AVAILABLE}")
