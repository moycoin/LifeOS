# ⚙️ Configuration Guide / 設定ガイド

This guide explains all configuration options in `config.json`.

このガイドでは `config.json` の全設定項目を説明します。

---

## 📋 Setup / セットアップ

```bash
cp config.example.json config.json
```

Then edit `config.json` with your settings.

その後、`config.json` をあなたの設定で編集してください。

---

## 🔑 Oura Settings / Oura設定

```json
"oura": {
  "api_token": "YOUR_OURA_API_TOKEN_HERE",
  "rhr": 50
}
```

| Key | Description | 説明 |
|-----|-------------|------|
| `api_token` | Get from https://cloud.ouraring.com/personal-access-tokens | Ouraのパーソナルアクセストークン |
| `rhr` | Your resting heart rate (bpm) | あなたの安静時心拍数 |

---

## 🌿 Shisha Settings / シーシャ設定

```json
"shisha": {
  "ignition_time": 930,
  "ventilation_time": 240,
  "heat_soak_time": 510,
  "calibration_time": 180,
  "cruise_time": 3000
}
```

| Key | Description | 説明 |
|-----|-------------|------|
| `ignition_time` | Ignition phase duration (seconds) | 着火フェーズ時間（秒） |
| `ventilation_time` | Ventilation phase duration | 換気フェーズ時間 |
| `heat_soak_time` | Heat soak phase duration | 蓄熱フェーズ時間 |
| `calibration_time` | Calibration phase duration | 調整フェーズ時間 |
| `cruise_time` | Cruise phase duration | クルーズフェーズ時間 |

---

## 🔊 Audio Settings / オーディオ設定

```json
"audio": {
  "enabled": true,
  "master_volume": 1.0,
  "bgm_enabled": false,
  "bgm_volume": 0.0,
  "voice_enabled": true,
  "voice_volume": 0.22,
  "sfx_volume": 0.5,
  "device_type": "headphone",
  "state_inertia_seconds": 30
}
```

| Key | Description | 説明 |
|-----|-------------|------|
| `enabled` | Enable audio system | オーディオシステム有効化 |
| `master_volume` | Master volume (0.0-1.0) | マスター音量 |
| `bgm_enabled` | Enable binaural BGM | バイノーラルBGM有効化 |
| `bgm_volume` | BGM volume | BGM音量 |
| `voice_enabled` | Enable voice notifications | 音声通知有効化 |
| `voice_volume` | Voice volume | 音声音量 |
| `sfx_volume` | Sound effects volume | 効果音音量 |
| `device_type` | `"headphone"` / `"speaker"` / `"earbud"` | 出力デバイスタイプ |
| `state_inertia_seconds` | Mode switch delay (seconds) | モード切替の遅延時間 |

### Ambient Slots / アンビエントスロット

```json
"ambient_slots": [
  {"source": "Rain", "volume": 0.15, "enabled": false},
  {"source": "Fire", "volume": 0.15, "enabled": false}
]
```

| Source | Description | 説明 |
|--------|-------------|------|
| `Rain` | Rain ambient sound | 雨の環境音 |
| `Fire` | Fire crackling sound | 焚き火の音 |

---

## 🤖 OpenAI Settings / OpenAI設定

```json
"openai": {
  "enabled": false,
  "api_key": "YOUR_OPENAI_API_KEY_HERE",
  "voice": "nova"
}
```

| Key | Description | 説明 |
|-----|-------------|------|
| `enabled` | Enable TTS voice synthesis | 音声合成有効化 |
| `api_key` | OpenAI API key | OpenAI APIキー |
| `voice` | Voice model (`nova`, `alloy`, `echo`, `fable`, `onyx`, `shimmer`) | 音声モデル |

---

## 🏠 Home Automation / ホームオートメーション設定

```json
"home": {
  "hue_ip": "192.168.x.x",
  "hue_room": "Living Room",
  "bravia_ip": "192.168.x.x",
  "bravia_psk": "0000",
  "auto_start": false,
  "focus_lighting": false
}
```

| Key | Description | 説明 |
|-----|-------------|------|
| `hue_ip` | Philips Hue Bridge IP address | Hue BridgeのIPアドレス |
| `hue_room` | Target room name in Hue app | Hueアプリでの部屋名 |
| `bravia_ip` | Sony Bravia TV IP address | BraviaのIPアドレス |
| `bravia_psk` | Bravia Pre-Shared Key | Bravia事前共有キー |
| `auto_start` | Auto-start home sync on launch | 起動時に自動開始 |
| `focus_lighting` | Auto-dim other rooms during focus | 集中時に他の部屋を消灯 |

### Brightness Thresholds / 明るさ閾値

```json
"thresholds": {
  "off": 50,
  "low": 20,
  "high": 4
}
```

| Key | TV Power Saving Mode | 説明 |
|-----|---------------------|------|
| `off` | If brightness > 50% → Power saving OFF | 明るさ50%超→省電力OFF |
| `low` | If brightness > 20% → Power saving LOW | 明るさ20%超→省電力LOW |
| `high` | If brightness ≤ 4% → Power saving HIGH | 明るさ4%以下→省電力HIGH |

### Volume Profiles / 音量プロファイル

```json
"volume_profiles": {
  "Spotify": {"enabled": true, "volume": 20},
  "Netflix": {"enabled": true, "volume": 36},
  "YouTube": {"enabled": true, "volume": 22}
}
```

Auto-adjust TV volume based on current app.

アプリに応じてTV音量を自動調整します。

---

## 🔒 Security Note / セキュリティ注意

**Never commit `config.json` to Git!**

**`config.json`をGitにコミットしないでください！**

It contains your API tokens and is already in `.gitignore`.

APIトークンが含まれており、`.gitignore`で除外済みです。
