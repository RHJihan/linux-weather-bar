# 🌤️ linux-weather-bar

A lightweight, feature-rich Bash script that displays **live weather, rain forecasts, and moon phase** in your Linux status bar or GNOME top bar — powered by OpenWeatherMap and a lunar astronomy API. Works with GNOME (via Executor extension), Waybar, Polybar, i3blocks, and more.

---

## ✨ Features

- **Current weather** — condition, temperature, feels-like, and weather emoji
- **Sunrise & sunset warnings** — alerts when sunrise/sunset is approaching
- **Rain forecast** — upcoming rain with time and probability from OWM forecast API
- **Moon phase display** — shows current moon phase with emoji, only during the actual lunar visibility window (sunset → moonrise → moonset → sunrise)
- **Moonrise & moonset announcements** — warns when moonrise or moonset is approaching, mirrors the sunrise/sunset warning pattern
- **Smart solar/lunar window engine** — calculates the intersection of the solar and lunar windows using Unix epoch math; handles midnight crossing correctly
- **Bilingual support** — moon phase names in English, Bengali, or both
- **Aggressive caching** — sun and moon data cached to JSON; API is called only when necessary
- **Graceful degradation** — falls back to cached data if API is unreachable
- **Configurable** — all behavior controlled via a single `.weather_config` file

---

## 📸 Output Examples

```
☀️   Clear Sky   32°C (Feels 36°C)    Sunset: 6:18 PM    🌕  Full Moon
⛅️   Few Clouds   28°C    🌧️   Rain Likely ≈ 9:00 PM (73%)
🌫️   Haze   28°C    🌕  পূর্ণিমা
☁️   Scattered Clouds   28°C    🌖  Waning Gibbous
🌦️   Light Rain   26°C
☀️   Clear Sky   32°C    Moonset: 8:24 AM
🌕   Full Moon   28°C    Moonrise: 7:45 PM
```

---

## 🔧 Requirements

| Tool | Purpose |
|------|---------|
| `bash` ≥ 4.0 | Script runtime |
| `curl` | API requests |
| `jq` | JSON parsing |
| `nmcli` *(optional)* | Connectivity check (falls back to ping) |

---

## 🚀 Installation

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/linux-weather-bar.git
cd linux-weather-bar

# Copy the config template
cp .weather_config.template .weather_config

# Edit your config
nano .weather_config

# Make executable
chmod +x linux-weather-bar.sh

# Test run
./linux-weather-bar.sh
```

---

## ⚙️ Configuration

All settings live in `.weather_config` (never committed — git-ignored). A template is provided at `.weather_config.template`.

### Required

```bash
API_KEY="your_openweathermap_api_key"
LOCATION="lat=23.7626&lon=90.3786"        # your coordinates
```

### Moon Phase (Optional)

```bash
MOON_API_KEY="your_astroapi_key"          # from astroapi.byhrast.com
MOON_PHASE_ENABLED=true                   # master toggle (default: false)
TIMEZONE="Asia/Dhaka"                     # your timezone
```

### Display Thresholds

```bash
FEELS_LIKE_THRESHOLD=3                    # show feels-like if diff exceeds this (°C)
SUNRISE_WARNING_THRESHOLD=30              # warn N minutes before sunrise
SUNSET_WARNING_THRESHOLD=30               # warn N minutes before sunset
SHOW_MOONRISE_MOONSET=true                # master toggle for moonrise/moonset announcements
MOONRISE_WARNING_THRESHOLD=30             # warn N minutes before moonrise
MOONSET_WARNING_THRESHOLD=30              # warn N minutes before moonset
```

### Rain Forecast

```bash
API_KEY_TYPE=FREE                         # FREE (3-hourly) or PRO (hourly)
RAIN_FORECAST_THRESHOLD=0.4               # probability threshold (0.0–1.0)
RAIN_FORECAST_WINDOW=6                    # hours ahead to check
MAX_CONNECTIVITY_RETRIES=3
CONNECTIVITY_RETRY_DELAY=2
```

### Moon Phase Window

```bash
# When to START showing the moon phase:
# "moonrise"  → exactly at moonrise
# numeric     → N minutes after the later of (sunset or moonrise)
MOON_PHASE_WINDOW_START="moonrise"

# How long to show the moon phase:
# "moonset"   → until moonset
# numeric     → N minutes after start
MOON_PHASE_WINDOW_DURATION="moonset"

# Rain suppression
MOON_PHASE_SHOW_DURING_RAIN=false        # hide moon phase if currently raining
MOON_PHASE_SHOW_WITH_RAIN_FORECAST=false # hide moon phase if rain is forecast
```

### Language

```bash
SHOW_MOONPHASE_BENGALI=false             # Bengali only
SHOW_MOONPHASE_BILINGUAL=false           # English + Bengali (overrides BENGALI)
```

---

## 🌙 Moon Phase Logic

The moon phase is shown **only during the intersection window** of:

- **Solar window:** Sunset (day N) → Sunrise (day N+1)
- **Lunar window:** Moonrise → Moonset

### Moonrise & Moonset Announcements

Independently of moon phase display, the script warns when moonrise or moonset is approaching (within `MOONRISE_WARNING_THRESHOLD` / `MOONSET_WARNING_THRESHOLD` minutes). Moonset is shown even after sunrise if the moon hasn't set yet. Controlled by the `SHOW_MOONRISE_MOONSET` master toggle.

All time comparisons use Unix epoch to handle midnight crossing correctly.

**Corner cases handled:**
- `moonrise: "Not visible"` → treated as Sunset + 30 minutes
- `moonset: "Not visible"` → treated as 23:59 of the current day
- Overnight (after midnight, before sunrise) → uses yesterday's moon data
- Cache is refreshed only when needed — at most once per day

---

## 🔌 Bar Integration

### GNOME (Executor Extension) — Recommended

Install the [Executor](https://extensions.gnome.org/extension/2932/executor/) GNOME Shell extension, then add a new command:

- **Command:** `/path/to/linux-weather-bar.sh`
- **Interval:** `600` seconds (10 minutes)
- **Location:** Top bar, left/center/right — your choice

The script output appears directly in your GNOME top bar and refreshes every 10 minutes.

### Waybar

In your `config.jsonc`:

```json
"custom/weather": {
    "exec": "~/.local/share/bin/linux-weather-bar.sh",
    "interval": 600,
    "format": "{}",
    "tooltip": false
}
```

### Polybar

```ini
[module/weather]
type = custom/script
exec = ~/.local/share/bin/linux-weather-bar.sh
interval = 600
```

### i3blocks

```ini
[weather]
command=~/.local/share/bin/linux-weather-bar.sh
interval=600
```

Any bar or launcher that can execute a shell script and display its stdout output will work.

---

## 📁 File Structure

```
linux-weather-bar/
├── linux-weather-bar.sh        # main script
├── .weather_config.template    # config template (committed)
├── .weather_config             # your config (git-ignored)
└── README.md
```

Cache files (auto-created at runtime):

```
~/.cache/weather/
├── sun-data.json               # today's sunrise/sunset
└── moon-data.json              # current moon data
```

---

## 🌐 APIs Used

| API | Free Tier | Used For |
|-----|-----------|---------|
| [OpenWeatherMap](https://openweathermap.org/api) | ✅ Yes | Weather, forecast, sunrise/sunset |
| [AstroAPI by Hrast](https://astroapi.byhrast.com) | ✅ Yes | Moon phase, moonrise, moonset |

---

## 📄 License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE) for details.

---

## 🙌 Contributing

Pull requests are welcome. For major changes, please open an issue first.

If this script works on your setup with a different bar or distro, feel free to open a PR adding it to the compatibility notes.

---

## 🔍 Keywords

`gnome` `gnome shell` `executor` `gnome extension` `gnome top bar` `waybar` `polybar` `weather` `bash` `shell script` `openweathermap` `moon phase` `lunar` `i3` `sway` `hyprland` `linux` `status bar` `Bengali` `bilingual` `moonrise` `moonset` `rain forecast`
