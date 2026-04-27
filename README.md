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

## Performance & Reliability

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

### Screenshots

<p align="center">
  <img src="docs/screenshots/rain-forecast-panel.png" width="400">
  <img src="docs/screenshots/moon-data-panel.png" width="400">
  <img src="docs/screenshots/moonrise-moonset.png" width="400">
  <img src="docs/screenshots/moon-phase.png" width="400">
  <img src="docs/screenshots/api-location.png" width="400">
</p>

---

### Highlights

* Schema-driven UI (auto-generated from a typed variable registry)
* Supports field types:

  * String, integer, float, boolean, enum
  * Special `NUMERIC_OR_SENTINEL` controls (numeric value **or** a symbolic option such as `moonrise` / `moonset` / `sunset`)
* Live validation with inline error feedback
* Dependency-aware UI (dependent fields are automatically disabled when their parent toggle is off)
* Comment-preserving file editing (inline comments and unrecognised lines are never modified)
* Change tracking with per-field undo and unsaved-change detection

---

### Window Layout & Navigation

The editor opens as a native GNOME window with a persistent **header bar** and an always-visible **search bar** directly beneath it.

**Header bar controls:**

| Control | Function |
|---|---|
| Open (folder icon) | Open any `.weather_config` file via a file-chooser dialog |
| Undo (↩ icon) | Step back through the change history one edit at a time |
| Reset (↺ icon) | Discard all unsaved changes and reload the current file |
| Save | Write changes back to disk; disabled when no changes are pending |

The **Save** button is styled as a suggested action and activates only when there are unsaved changes. On save, the editor creates a temporary backup, writes the updated file preserving all comments and unrecognised lines, restarts the GNOME Executor extension automatically, and removes the backup on success. A toast notification confirms the result.

---

### Live Search

A search bar is always visible below the header. Typing filters all preference rows in real time, matching against the variable label, its internal key name, and its description. Groups with no matching rows are hidden automatically.

---

### Configuration Groups

Settings are organised into labelled `PreferencesGroup` sections. Each section corresponds to a functional area of the script:

* **Configuration** — general behaviour (feels-like threshold, rain forecast toggle, precipitation probability, lookahead window)
* **Sunrise & Sunset** — display toggles, lead-time warnings, rain interaction
* **Moonrise & Moonset** — display toggles, lead-time thresholds (numeric or "After Sunset"), daytime visibility, rain interaction
* **Moon Phase** — phase window start/end, daytime display, visibility suppression, rain interaction, localisation, apsidal syzygy
* **API Keys** — OpenWeatherMap and Moon API credentials (masked by default; see [Secret Fields](#secret-fields))
* **Location** — coordinates picker (see [Smart Location Picker](#smart-location-picker))
* **Network** — connectivity retry count and retry delay

Dependent rows are automatically greyed out when their controlling toggle is disabled. For example, all moonrise/moonset sub-options are insensitive while `SHOW_MOONRISE_MOONSET` is off. Inverse dependencies are also supported: enabling bilingual mode disables the Bengali-only option.

---

### Secret Fields

API key fields (`API_KEY`, `MOON_API_KEY`) are treated as secrets. Their values are masked with a password-style entry widget that hides the text when the field is not focused, preventing accidental credential exposure.

---

### Full Lunar Controls

The GUI exposes the full set of advanced lunar features available in the script:

* Moon phase display window (start and end, numeric minutes or symbolic `moonrise`/`moonset`)
* Daytime phase display toggle
* Visibility suppression for both phase and moonrise/moonset
* Apsidal syzygy toggle and night-only restriction
* Moonrise/moonset lead-time thresholds (minutes or "After Sunset" sentinel)
* Moon data cache duration (`MOON_DATA_CACHE_MAX_AGE`)
* Rain-interaction behaviour (show during rain / show when rain is forecast) for phase, moonrise, and moonset independently
* Localisation (Bengali-only or bilingual phase names)

---

### Moon Data Live Panel

A **Moon Data** section is automatically injected beneath the Moon Phase settings group. It reads `~/.cache/weather/moon-data.json` and displays current lunar values in a compact two-column grid:

| Field | Field |
|---|---|
| Date | Phase |
| Illumination | Phase progress |
| Moonrise | Moonset |
| Position (direction + altitude) | Distance (km) |

Times are displayed in 12-hour format with uppercase AM/PM in the configured timezone. If a moonrise or moonset event has already passed, its value is visually dimmed. An alert row beneath the grid shows context-sensitive notices such as Supermoon / Micromoon status and the current lunar window progress (updated every second).

An **Update** button in the section header fetches fresh data from the Moon API in a background thread using the API key, location, and timezone from the loaded config, writes the result to `moon-data.json`, and updates the display without requiring a save or restart. The panel reflects the cached `retrieved_at` timestamp in its description.

---

### Sun Data Live Panel

A **Sun Data** section is injected beneath the Configuration group. It reads sunrise and sunset times from `~/.cache/weather/weather-data.json` and displays them side by side in the configured timezone. Events that have already passed are visually dimmed.

An **Update** button runs the weather script in a background thread to refresh `weather-data.json` and rebuilds the panel with the new data.

---

### Rain Forecast Live Panel

A **Rain Forecast** section is injected below the Sun Data panel. It reads `~/.cache/weather/forecast-data.json` and displays upcoming rain entries that meet the configured threshold. Each entry shows:

* Date and time (labelled "Today" or "Tomorrow" when applicable)
* Weather condition and temperature
* Feels-like temperature (shown only when the difference exceeds the configured threshold)
* Precipitation probability

The panel header provides two live controls — **Min Precip** (precipitation probability threshold) and **Max Items** (maximum entries to display) — that re-filter the forecast data instantly without saving. An **Update** button runs the weather script to refresh the underlying cache file; the file monitor detects the change and updates the panel automatically.

---

### Smart Location Picker

The **Location** row combines a searchable preset dropdown with a manual entry fallback.

* **Preset mode** — loads `location_mappings.csv`, deduplicates entries by `(NAME, LATITUDE, LONGITUDE)`, sorts alphabetically, and presents them in a searchable dropdown. Selecting a preset immediately populates the underlying `lat=…&lon=…` config value and keeps the manual entry fields in sync.
* **Custom mode** — enabled by ticking the **Custom** checkbox, or automatically when the current coordinates do not match any preset. Exposes inline latitude and longitude fields.
* **Google Maps button** — opens the current coordinates in Google Maps via the system browser.

When `location_mappings.csv` is absent, preset mode is skipped and Custom mode is always active. The last used CSV path is persisted via GSettings and restored on next launch.

---

### Timezone Support

The **Timezone** row uses a searchable dropdown populated from `zone.tab` (standard IANA timezone database). Typing filters the list by substring match; an error style is applied while the entered text does not match a known timezone. On save, validation performs a hard check against the loaded list.

When `zone.tab` is absent, the field falls back to a plain text entry accepting any IANA timezone string.

---

### Undo System

Every field edit pushes the pre-edit value onto an undo stack. Clicking **Undo** in the header bar reverts the most recent change one step at a time. The undo stack survives a save — earlier edits can still be undone after writing. The Undo button is disabled when the stack is empty.

---

### Auto-Load & Session Persistence

On launch the editor searches for a config file in this priority order:

1. Last opened file (restored from GSettings)
2. `.weather_config` in the script directory
3. `~/.weather_config`

The path of the most recently opened file is saved to GSettings so it is reopened automatically on the next launch.

---

### Toast Notifications & Error Dialogs

Transient **toast** messages confirm successful saves, resets, and data refreshes. Validation failures, API errors, and file I/O problems surface as modal **error dialogs** with a descriptive message.

---

### Desktop Integration

The editor ships with a `.desktop` entry for GNOME integration:

```ini
[Desktop Entry]
Name=Weather Config Editor
Comment=Edit weather & astronomical configuration
Exec=sh -c 'GSETTINGS_SCHEMA_DIR="$HOME/.local/share/bin/linux-weather-bar" python3 "$HOME/.local/share/bin/linux-weather-bar/weather_config_editor.py"'
Icon=preferences-system
Terminal=false
Type=Application
Categories=Utility;
StartupNotify=true
StartupWMClass=com.weather.ConfigEditor
```

Place this file in `~/.local/share/applications/` to add the editor to the GNOME application launcher.

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
☀️   Clear Sky   32°C (Feels 36°C)    Sunset: 6:18 PM
⛅️   Few Clouds   28°C    🌧️ Rain ≈ 9:00 PM (73%)
☁️   Scattered Clouds   31°C    🌗 Last Quarter
🌫️   Haze   28°C    🌕 পূর্ণিমা
☁️   Scattered Clouds   28°C    🌕 Full Moon (Supermoon)
🌦️   Light Rain   26°C
☀️   Clear Sky   32°C    Moonset: 8:24 AM
🌫️   Haze   31°C    ☔️   Moderate Rain ≈ 9:00 PM (80%)
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
SHOW_MOON_PHASE_DURING_RAIN=false
SHOW_MOON_PHASE_WITH_RAIN_FORECAST=false
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
  "exec": "~/.local/share/bin/linux-weather-bar/linux-weather-bar.sh",
  "interval": 600
}
```

---

### Polybar

```ini
[module/weather]
type = custom/script
exec = ~/.local/share/bin/linux-weather-bar/linux-weather-bar.sh
interval = 600
```

---

### i3blocks

```ini
[weather]
command=~/.local/share/bin/linux-weather-bar/linux-weather-bar.sh
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
├── location_mappings.csv       # optional location presets
├── zone.tab                    # timezone database
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
