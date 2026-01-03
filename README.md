<div align="center">

# 🧬 LifeOS v5.4.1

**Biometric-Driven Productivity System with Neural Acoustic Programming**

[![License: MIT](https://img.shields.io/badge/License-MIT-00D4AA.svg)](LICENSE)
[![Python 3.7+](https://img.shields.io/badge/Python-3.7+-3776AB.svg)](https://python.org)
[![Oura Ring](https://img.shields.io/badge/Oura-Ring-000000.svg)](https://ouraring.com)

---

### 💎 Support This Project

If LifeOS has been valuable to you, consider supporting its development:

**Ethereum / Polygon / BSC / Arbitrum / Base**
```
0x9d8CC17a83b9A75D488E2A15dbcB842AC44a022F
```

<img src="https://img.shields.io/badge/ETH-0x9d8C...022F-627EEA?style=for-the-badge&logo=ethereum" alt="ETH">

---

</div>

## 🎯 Overview

LifeOS is a comprehensive biometric monitoring and productivity optimization system that integrates:

- **Oura Ring API** - Sleep, Readiness, Heart Rate tracking
- **BioEngine** - Real-time Focus Points (FP) calculation with predictive modeling
- **Shadow Heartrate** - APM-based heart rate estimation during API latency
- **Neural Acoustic Engine** - Binaural beats, ambient soundscapes, and linguistic programming
- **Home Automation** - Philips Hue & Sony Bravia integration for ambient synchronization

## 🏗️ Architecture

```
LifeOS/
├── LifeOS_GUI.py          # Main PyQt5 Interface
├── config.json            # Configuration (create from config.example.json)
├── core/
│   ├── __init__.py        # Package initializer
│   ├── types.py           # Type definitions & constants
│   ├── database.py        # SQLite WAL database layer
│   ├── engine.py          # BioEngine & ShadowHeartrate
│   ├── audio.py           # NeuroSoundEngine
│   ├── daemon.py          # Background daemon (SSOT writer)
│   └── home.py            # Home automation controllers
├── Data/
│   ├── style.qss          # Qt stylesheet
│   └── sounds/            # Audio assets directory
└── logs/                  # Runtime logs & state
```

## 📋 Requirements

```
Python >= 3.7
PyQt5
pygame
numpy
scipy (optional)
pynput
requests
phue (optional, for Hue)
```

## 🚀 Quick Start

```bash
# 1. Clone repository
git clone https://github.com/moycoin/LifeOS.git
cd LifeOS

# 2. Install dependencies
pip install PyQt5 pygame numpy pynput requests

# 3. Configure
cp config.example.json config.json
# Edit config.json with your Oura API token

# 4. Run
python LifeOS_GUI.py
```

## ⚙️ Configuration

Copy `config.example.json` to `config.json` and configure:

| Key | Description |
|-----|-------------|
| `oura.api_token` | Your Oura API Personal Access Token |
| `oura.rhr` | Your resting heart rate baseline |
| `audio.*` | Audio engine settings |
| `home.*` | Smart home device IPs (optional) |
| `openai.*` | OpenAI API for voice synthesis (optional) |

## 🔬 Core Concepts

### Focus Points (FP)
A composite metric representing cognitive resource availability:
```
FP_effective = Base_FP + (Boost_FP × Efficiency) - (Debt × Penalty)
```

### Shadow Heartrate
Real-time HR estimation during Oura API latency:
```
HR_pred = HR_base + AWAKE_OFFSET + (APM × α) + (Mouse × β) + (WorkTime × γ)
```

### Neural Acoustic Programming
- **Binaural Beats**: Focus (40Hz), Flow (14Hz), Relax (10Hz), Sleep (2Hz)
- **Ambient Layers**: Rain, Fire with 1/f noise characteristics
- **Neuro-Linguistic Compiler**: Vocabulary learning with alpha wave modulation

## 📊 Database Schema

| Table | Purpose |
|-------|---------|
| `daily_logs` | Readiness, Sleep scores, RHR |
| `tactile_logs` | APM, keystrokes, activity states |
| `heartrate_logs` | HR stream (Oura + Shadow) |
| `shisha_logs` | Session tracking |

## 🤝 Contributing

Contributions are welcome. Please maintain the existing code style:
- Python 3.7 compatible syntax
- High-density formatting (ternary, comprehensions)
- Minimal comments

## 📜 License

MIT License - Copyright (c) 2025 [@moycoin](https://twitter.com/moycoin)

See [LICENSE](LICENSE) for details.

---

<div align="center">

**Created with 🧠 by [@moycoin](https://twitter.com/moycoin)**

*"Optimize your biology, amplify your cognition."*

</div>
