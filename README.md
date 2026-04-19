# 🌤️ linux-weather-bar

A lightweight, feature-rich Bash script that displays **live weather, rain forecasts, and advanced lunar data** in your Linux status bar or GNOME top bar — powered by OpenWeatherMap and an astronomical API.

Works with **GNOME (Executor)**, **Waybar**, **Polybar**, **i3blocks**, and more.

---

## Features

---

## Weather Engine

* **Live weather data**

  * Temperature, feels-like, condition, emoji
* **Smart feels-like display**

  * Only shown when difference is meaningful
* **Sunrise & Sunset system**

  * Accurate across midnight transitions
  * Smart “effective sunrise/sunset” handling
  * Pre-warning notifications

---

## Rain Forecast Engine

* Predicts rain within configurable time window
* Displays:

  * Time
  * Probability
  * Condition + icon
* Works with:

  * OpenWeatherMap FREE (3-hour)
  * PRO (hourly)

---

## Advanced Lunar System (Highly Accurate)

This is not a simple phase display — it's a **full astronomy-aware lunar engine**.

---

### Moon Phase Display (Visibility-Aware)

* Displayed only when **astronomically meaningful**
* Based on intersection of:

  * **Solar window:** `sunset → sunrise`
  * **Lunar window:** `moonrise → moonset`
* Supports:

  * Custom start/end logic
  * Offset or symbolic values (`moonrise`, `moonset`, `sunset`)

---

### Visibility Filtering

* Prevents unrealistic moon display when:

  * Illumination is too low
  * Moon is not physically visible
* Controlled via:

```bash
SUPPRESS_MOONPHASE_NOT_VISIBLE=true
```

---

### Apsidal Syzygy (Supermoon / Micromoon)

Automatically detects and labels:

* **Supermoon**
* **Micromoon**
* **Super New Moon**

Config:

```bash
SHOW_LUNAR_APSIDAL_SYZYGY=true
ONLY_SHOW_VISIBLE_NIGHT_APSIDAL_SYZYGY=true
```

✔ Adds label next to phase name
✔ Can restrict to **night-visible events only**

---

### Moonrise & Moonset Alerts

* Independent from phase display
* Configurable lead-time alerts
* Supports:

  * Showing events during daytime
  * Rain-aware suppression
  * Post-sunrise moonset visibility

---

### Localization

* English
* Bengali
* Bilingual mode

---

## Smart Time Engine

* Uses **epoch-based calculations**
* Handles:

  * Midnight crossover
  * Overnight logic (yesterday vs today)
  * Missing API edge cases

### Smart Adjustments

* Effective sunset (yesterday vs today)
* Effective sunrise (next day logic)
* Lunar cycle continuity

---

## erformance & Reliability

### Intelligent Caching

* Sun data → `~/.cache/weather/sun-data.json`
* Moon data → `~/.cache/weather/moon-data.json`

---

### Smart Lunar Cache Refresh

* Configurable freshness window:

```bash
LUNAR_CACHE_MAX_AGE_HOURS=2
```

* Refresh triggers:

  * After moonrise (if stale)
  * When cache exceeds age threshold
  * During active lunar cycle only

✔ Prevents stale illumination/position
✔ Minimizes API calls

---

### Network Resilience

* Connectivity retry system
* Uses:

  * `nmcli` (preferred)
  * fallback ping

---

### Graceful Degradation

* Uses cached data when offline
* Avoids crashes on API failure

---

## Python Config Manager (GUI)

### `weather_config_editor.py`

A **GTK4 + libadwaita GUI** for editing `.weather_config`.

---

### Highlights

* Schema-driven UI (auto-generated)
* Supports:

  * Integer, float, boolean, enum
  * Special lunar controls (numeric OR symbolic)
* Live validation
* Dependency-aware UI
* Comment-preserving file editing
* Change tracking

---

### Full Lunar Controls

GUI exposes all advanced lunar features:

* Moon phase window logic
* Visibility suppression
* Apsidal syzygy toggle
* Cache duration control
* Rain interaction behavior

---

### 📊 Moon Data Awareness

The config system is tightly integrated with:

* Moon API structure
* Cached lunar data lifecycle
* Visibility and illumination logic

---

### Smart Location Picker

* Uses `ip_mappings.csv`
* Features:

  * Dropdown presets
  * Manual lat/lon override
  * Google Maps integration

---

### Timezone Support

* Uses `zone.tab`
* Validates IANA timezone names

---

### Run Config Editor

```bash
GSETTINGS_SCHEMA_DIR=. python weather_config_editor.py
```

**Requires:**

* GTK4
* libadwaita

---

## Example Output

```
☀️   Clear Sky   32°C (Feels 36°C)    Sunset: 6:18 PM    🌕 Full Moon (Supermoon)
⛅️   Few Clouds   28°C    🌧️ Rain ≈ 9:00 PM (73%)
🌫️   Haze   28°C    🌕 পূর্ণিমা
☁️   Scattered Clouds   28°C    🌖 Waning Gibbous (Micromoon)
🌦️   Light Rain   26°C
☀️   Clear Sky   32°C    Moonset: 8:24 AM
```

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/linux-weather-bar.git
cd linux-weather-bar

cp .weather_config.template .weather_config
nano .weather_config

chmod +x linux-weather-bar.sh
./linux-weather-bar.sh
```

---

## Configuration

All settings:

```
.weather_config
```

---

### Required

```bash
API_KEY="your_openweathermap_api_key"
LOCATION="lat=23.7626&lon=90.3786"
```

---

### Moon (Optional)

```bash
MOON_API_KEY="your_astroapi_key"
TIMEZONE="Asia/Dhaka"
MOON_PHASE_ENABLED=true
```

---

## Key Controls

---

### Moon Phase Window

```bash
MOON_PHASE_WINDOW_START="moonrise"   # or minutes
MOON_PHASE_WINDOW_DURATION="moonset" # or minutes
```

---

### Visibility Control

```bash
SUPPRESS_MOONPHASE_NOT_VISIBLE=true
SHOW_MOONPHASE_DURING_DAYTIME=false
```

---

### Apsidal Syzygy

```bash
SHOW_LUNAR_APSIDAL_SYZYGY=true
ONLY_SHOW_VISIBLE_NIGHT_APSIDAL_SYZYGY=true
```

---

### Moonrise / Moonset

```bash
SHOW_MOONRISE_MOONSET=true
MOONRISE_WARNING_THRESHOLD=30
MOONSET_WARNING_THRESHOLD=30
SHOW_MOONRISE_MOONSET_DURING_DAYTIME=false
```

---

### Rain Interaction

```bash
MOON_PHASE_SHOW_DURING_RAIN=false
MOON_PHASE_SHOW_WITH_RAIN_FORECAST=false
```

---

### Lunar Cache

```bash
LUNAR_CACHE_MAX_AGE_HOURS=2
```

---

## Bar Integration

### GNOME (Executor)

```bash
/path/to/linux-weather-bar.sh
```

Interval: `600`

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

## Project Structure

```
linux-weather-bar/
├── linux-weather-bar.sh        # main engine
├── weather_config_editor.py    # GTK config manager
├── .weather_config.template
├── .weather_config
├── ip_mappings.csv            # optional location presets
├── zone.tab                   # timezone database
└── README.md
```

---

## APIs

| API            | Purpose                        |
| -------------- | ------------------------------ |
| OpenWeatherMap | Weather + forecast             |
| AstroAPI       | Moon phase, rise/set, position |

---

## Design Philosophy

* **Astronomical correctness over gimmicks**
* **Minimal API usage**
* **Robust edge-case handling**
* **High configurability without complexity**

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE) for details.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first.

---
