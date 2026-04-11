# 🌤️ linux-weather-bar

A lightweight, feature-rich Bash script that displays **live weather, rain forecasts, and moon phase** in your Linux status bar or GNOME top bar — powered by OpenWeatherMap and a lunar astronomy API. Works with GNOME (via Executor extension), Waybar, Polybar, i3blocks, and more.

---

## ✨ Features

### 🌦️ Weather Engine

* **Live weather data** (temperature, feels-like, condition, emoji)
* **Smart feels-like display** (only when meaningful difference)
* **Sunrise & sunset awareness**

  * Pre-warning notifications
  * Accurate across midnight transitions
* **Rain forecast system**

  * Predicts upcoming rain within a configurable window
  * Shows **time, probability, and condition**
  * Uses OpenWeatherMap forecast API (FREE & PRO supported)

---

### 🌙 Advanced Moon System (Highly Accurate)

This is not a simple “show moon phase” feature — it’s a **full lunar visibility engine**:

#### 🌘 Moon Phase Display

* Shown **only when physically meaningful**
* Based on **intersection of:**

  * Solar window → `sunset → sunrise`
  * Lunar window → `moonrise → moonset`
* Fully configurable:

  * Start: `moonrise` or offset from sunset
  * End: `moonset` or duration

#### 🌕 Moonrise & Moonset Alerts

* Independent from moon phase display
* Configurable warnings before events
* Supports:

  * Showing moonset **after sunrise**
  * Suppression during rain / forecast
  * Flexible thresholds

#### 🌐 Localization

* English
* Bengali
* Bilingual (English + Bengali)

---

### 🧠 Smart Time Engine

* Uses **Unix epoch math** for all comparisons
* Handles:

  * Midnight crossing
  * Overnight logic (yesterday vs today)
  * Missing API edge cases
* Automatically adjusts:

  * “Effective sunset” (yesterday vs today)
  * “Effective sunrise” (next day logic)

---

### ⚡ Performance & Reliability

* **Aggressive caching**

  * Sun data → `~/.cache/weather/sun-data.json`
  * Moon data → `~/.cache/weather/moon-data.json`
* **Minimal API usage**

  * Fetches only when necessary
* **Graceful degradation**

  * Falls back to cache if offline
* **Connectivity retry system**

  * Uses `nmcli` or fallback ping

---

## 🧩 Python Config Manager (GUI)

A major feature of this project is the included:

### 🖥️ `weather_config_editor.py`

A **modern GTK4 + libadwaita application** for managing `.weather_config`.

#### 🚀 Highlights

* **Schema-driven UI**

  * Automatically renders fields based on variable type
* Supports:

  * Integer, float, boolean, enum
  * Special moon window controls (numeric OR sentinel like `"moonrise"`)
* **Grouped settings UI**

  * Weather
  * Moon phase
  * Moonrise/moonset
  * API keys
  * Network
* **Dependency-aware controls**

  * Disables irrelevant settings automatically
* **Live validation**

  * Prevents invalid config values
* **Safe file editing**

  * Preserves comments and formatting
* **Change tracking**

  * Save button activates only when needed

---

### 📍 Smart Location Picker

* Loads locations from `location_mappings.csv`
* Features:

  * Dropdown of predefined locations
  * Manual lat/lon override
  * Google Maps integration (one-click open)
* Remembers last used dataset via GSettings

---

### 🧠 Intelligent Input Types

Special handling for advanced config:

* `MOON_WINDOW` fields:

  * Toggle between:

    * Numeric value
    * Semantic values (`moonrise`, `moonset`, `sunset`)
* Prevents invalid combinations automatically

---

### ▶️ Running the Config Manager

```bash
GSETTINGS_SCHEMA_DIR=. python weather_config_editor.py
```

Requires:

* GTK4
* libadwaita (Adw)

---

## 📸 Example Output

```
☀️   Clear Sky   32°C (Feels 36°C)    Sunset: 6:18 PM    🌕  Full Moon
⛅️   Few Clouds   28°C    🌧️   Rain Likely ≈ 9:00 PM (73%)
🌫️   Haze   28°C    🌕  পূর্ণিমা
☁️   Scattered Clouds   28°C    🌖  Waning Gibbous
🌫️   Haze   30°C    🌓  First Quarter (শুক্লপক্ষের অর্ধচন্দ্র)
🌦️   Light Rain   26°C
☀️   Clear Sky   32°C    Moonset: 8:24 AM
```

---

## 🔧 Installation

```bash
git clone https://github.com/YOUR_USERNAME/linux-weather-bar.git
cd linux-weather-bar

cp .weather_config.template .weather_config
nano .weather_config

chmod +x linux-weather-bar.sh
./linux-weather-bar.sh
```

---

## ⚙️ Configuration

All settings live in:

```
.weather_config
```

### Required

```bash
API_KEY="your_openweathermap_api_key"
LOCATION="lat=23.7626&lon=90.3786"
```

---

### Moon (Optional)

```bash
MOON_API_KEY="your_astroapi_key"
MOON_PHASE_ENABLED=true
TIMEZONE="Asia/Dhaka"
```

---

### Key Controls

#### Moon Phase Window

```bash
MOON_PHASE_WINDOW_START="moonrise"   # or minutes
MOON_PHASE_WINDOW_DURATION="moonset" # or minutes
```

#### Moonrise / Moonset

```bash
SHOW_MOONRISE_MOONSET=true
MOONRISE_WARNING_THRESHOLD=30
MOONSET_WARNING_THRESHOLD=30
SHOW_MOONSET_AFTER_SUNRISE=true
```

#### Rain Behavior

```bash
MOON_PHASE_SHOW_DURING_RAIN=false
MOON_PHASE_SHOW_WITH_RAIN_FORECAST=false
```

---

## 🔌 Bar Integration

### GNOME (Executor)

* Command:

```bash
/path/to/linux-weather-bar.sh
```

* Interval: `600`

---

### Waybar

```json
"custom/weather": {
  "exec": "~/.local/bin/linux-weather-bar.sh",
  "interval": 600
}
```

---

### Polybar

```ini
[module/weather]
type = custom/script
exec = ~/.local/bin/linux-weather-bar.sh
interval = 600
```

---

### i3blocks

```ini
[weather]
command=~/.local/bin/linux-weather-bar.sh
interval=600
```

---

## 📁 Project Structure

```
linux-weather-bar/
├── linux-weather-bar.sh        # main engine
├── weather_config_editor.py    # GTK config manager
├── .weather_config.template
├── .weather_config            # user config
└── README.md
```

---

## 🌐 APIs

| API            | Purpose                       |
| -------------- | ----------------------------- |
| OpenWeatherMap | Weather + forecast            |
| AstroAPI       | Moon phase, moonrise, moonset |

---

## 🧠 Design Philosophy

* **Accuracy over gimmicks**
* **Minimal API usage**
* **Correct handling of real-world astronomy edge cases**
* **User control without complexity**

---

## 📄 License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE) for details.

---

## 🙌 Contributing

Pull requests are welcome. For major changes, please open an issue first.

If this script works on your setup with a different bar or distro, feel free to open a PR adding it to the compatibility notes.
