#!/usr/bin/env python3
"""
Weather & Astronomical Config Editor
A production-grade GNOME GTK4/libadwaita application for managing
environment variables of a weather + astronomical system.
"""

# GSETTINGS_SCHEMA_DIR=. python weather_config_editor.py

# weather-config-editor.desktop:

# [Desktop Entry]
# Name=Weather Config Editor
# Comment=Edit weather & astronomical configuration
# Exec=sh -c 'GSETTINGS_SCHEMA_DIR="$HOME/.local/share/bin" python3 "$HOME/.local/share/bin/weather_config_editor.py"'
# Icon=preferences-system
# Terminal=false
# Type=Application
# Categories=Utility;
# StartupNotify=true
# StartupWMClass=com.weather.ConfigEditor


from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402


# ─── Data Model ──────────────────────────────────────────────────────────────


class VarType(Enum):
    """Variable input types for schema-driven UI rendering."""
    STRING = auto()
    INTEGER = auto()
    FLOAT = auto()
    BOOLEAN = auto()
    ENUM = auto()
    MOON_WINDOW = auto()   # Special: numeric OR sentinel string


@dataclass
class VarSchema:
    """Schema definition for a single config variable."""
    key: str
    label: str
    var_type: VarType
    description: str = ""
    default: Any = None
    choices: list[str] = field(default_factory=list)          # for ENUM
    sentinel_label: str = ""                                   # for MOON_WINDOW
    sentinel_value: str = ""                                   # e.g. "moonrise"
    group: str = "General"
    readonly: bool = False                                     # bash `readonly`
    secret: bool = False                                       # mask when unfocused


@dataclass
class ConfigEntry:
    """Runtime value for a variable loaded from file."""
    schema: VarSchema
    raw_value: str          # raw string as found in file
    modified: bool = False

    @property
    def display_value(self) -> str:
        """Strip surrounding quotes."""
        v = self.raw_value.strip()
        if len(v) >= 2 and v[0] == v[-1] == '"':
            return v[1:-1]
        return v

    @display_value.setter
    def display_value(self, val: str) -> None:
        """Store with quotes if it was originally quoted."""
        v = self.raw_value.strip()
        quoted = len(v) >= 2 and v[0] == v[-1] == '"'
        self.raw_value = f'"{val}"' if quoted else val
        self.modified = True

DEPENDENCIES: dict[str, list[str]] = {
    "SHOW_SUNRISE_SUNSET": [
        "SUNRISE_WARNING_THRESHOLD",
        "SUNSET_WARNING_THRESHOLD",
        "SHOW_SUNRISE_SUNSET_WITH_RAIN_FORECAST",
        "SHOW_SUNRISE_SUNSET_DURING_RAIN"
    ],
    "SHOW_RAIN_FORECAST": [
        "RAIN_FORECAST_THRESHOLD",
        "RAIN_FORECAST_WINDOW",
        "SHOW_SUNRISE_SUNSET_WITH_RAIN_FORECAST",
        "SHOW_MOONRISE_MOONSET_WITH_RAIN_FORECAST",
        "MOON_PHASE_SHOW_WITH_RAIN_FORECAST",
    ],
    "MOON_PHASE_ENABLED": [
        "MOON_PHASE_WINDOW_START",
        "MOON_PHASE_WINDOW_DURATION",
        "MOON_PHASE_SHOW_DURING_RAIN",
        "MOON_PHASE_SHOW_WITH_RAIN_FORECAST",
        "SHOW_MOONPHASE_BENGALI",
        "SHOW_MOONPHASE_BILINGUAL",
        "SHOW_LUNAR_APSIDAL_SYZYGY",
        "ONLY_SHOW_VISIBLE_NIGHT_APSIDAL_SYZYGY",
    ],
    "SHOW_LUNAR_APSIDAL_SYZYGY": [
        "ONLY_SHOW_VISIBLE_NIGHT_APSIDAL_SYZYGY",
    ],
    "SHOW_MOONRISE_MOONSET": [
        "MOONRISE_WARNING_THRESHOLD",
        "MOONSET_WARNING_THRESHOLD",
        "SHOW_MOONRISE_MOONSET_DURING_RAIN",
        "SHOW_MOONRISE_MOONSET_WITH_RAIN_FORECAST",
    ],
        "SHOW_MOONPHASE_BILINGUAL": [
        "SHOW_MOONPHASE_BENGALI",
    ],
}

INVERSE_DEPENDENCIES: set[str] = {
    "SHOW_MOONPHASE_BILINGUAL",
}

# ─── Variable Schema Registry ────────────────────────────────────────────────


SCHEMA: list[VarSchema] = [
    # ── Configuration ───────────────────────────────────────────────────
    VarSchema("FEELS_LIKE_THRESHOLD",      "Feels-Like Offset",        VarType.INTEGER,
            "Show 'feels like' when it differs by this many °C", default=10, readonly=True,
            group="Configuration"),
    VarSchema("SHOW_RAIN_FORECAST",        "Rain Forecast",            VarType.BOOLEAN,
            "Show rain warnings in the forecast", readonly=True, group="Configuration"),
    VarSchema("RAIN_FORECAST_THRESHOLD",   "Rain Chance Cutoff",       VarType.FLOAT,
            "Minimum probability (0–100%) to trigger a warning",
            default=0.7, readonly=True, group="Configuration"),
    VarSchema("RAIN_FORECAST_WINDOW",      "Forecast Lookahead",       VarType.INTEGER,
            "How many hours ahead to check for rain", default=2, readonly=True,
            group="Configuration"),

    # ── Sunrise & Sunset ────────────────────────────────────────────────
    VarSchema("SHOW_SUNRISE_SUNSET",       "Sunrise &amp; Sunset",         VarType.BOOLEAN,
            "Show sunrise and sunset times", readonly=True,
            group="Sunrise &amp; Sunset"),

    VarSchema("SUNRISE_WARNING_THRESHOLD", "Sunrise Lead Time",        VarType.INTEGER,
            "Alert this many minutes before sunrise", default=30, readonly=True,
            group="Sunrise &amp; Sunset"),

    VarSchema("SUNSET_WARNING_THRESHOLD",  "Sunset Lead Time",         VarType.INTEGER,
            "Alert this many minutes before sunset", default=30, readonly=True,
            group="Sunrise &amp; Sunset"),
    VarSchema("SHOW_SUNRISE_SUNSET_DURING_RAIN",        "Show While Raining",       VarType.BOOLEAN,
              "Display even when it's currently raining", readonly=True,
              group="Sunrise &amp; Sunset"),
    VarSchema("SHOW_SUNRISE_SUNSET_WITH_RAIN_FORECAST", "Show When Rain Expected",  VarType.BOOLEAN,
              "Display even when rain is in the forecast", readonly=True,
              group="Sunrise &amp; Sunset"),

    # ── Moonrise & Moonset ────────────────────────────────────────────────────
    VarSchema("SHOW_MOONRISE_MOONSET",                    "Moonrise &amp; Moonset",             VarType.BOOLEAN,
              "Show moonrise and moonset times", readonly=True, group="Moonrise &amp; Moonset"),
    VarSchema("MOONRISE_WARNING_THRESHOLD",               "Moonrise Lead Time",             VarType.MOON_WINDOW,
              "Minutes before moonrise to alert, or immediately after sunset",
              sentinel_label="After Sunset", sentinel_value="sunset",
              readonly=True, group="Moonrise &amp; Moonset"),
    VarSchema("MOONSET_WARNING_THRESHOLD",                "Moonset Lead Time",              VarType.INTEGER,
              "Alert this many minutes before moonset", default=30, readonly=True,
              group="Moonrise &amp; Moonset"),
    VarSchema("SHOW_MOONRISE_MOONSET_DURING_DAYTIME", "Show During Daytime",               VarType.BOOLEAN,
              "Include moonrise/moonset times that fall during daylight", readonly=True,
              group="Moonrise &amp; Moonset"),
    VarSchema("SHOW_MOONRISE_MOONSET_DURING_RAIN",        "Show While Raining",             VarType.BOOLEAN,
              "Display even when it's currently raining", readonly=True,
              group="Moonrise &amp; Moonset"),
    VarSchema("SHOW_MOONRISE_MOONSET_WITH_RAIN_FORECAST", "Show When Rain Expected",        VarType.BOOLEAN,
              "Display even when rain is in the forecast", readonly=True,
              group="Moonrise &amp; Moonset"),


    # ── Moon Phase ────────────────────────────────────────────────────────────
    VarSchema("MOON_PHASE_ENABLED",               "Moon Phase",                    VarType.BOOLEAN,
              "Show the current moon phase", readonly=True, group="Moon Phase"),
    VarSchema("LUNAR_CACHE_MAX_AGE_HOURS",                "Moon Data Cache Max Age",              VarType.INTEGER,
              "Maximum age of cached moon data in hours during active moon window", default=2, readonly=True,
              group="Moon Phase"),
    VarSchema("MOON_PHASE_WINDOW_START",           "Display Window Start",         VarType.MOON_WINDOW,
              "Minutes after sunset to begin display, or from moonrise",
              sentinel_label="Moonrise", sentinel_value="moonrise",
              readonly=True, group="Moon Phase"),
    VarSchema("MOON_PHASE_WINDOW_DURATION",        "Display Window End",           VarType.MOON_WINDOW,
              "How long to show it, or until moonset",
              sentinel_label="Moonset", sentinel_value="moonset",
              readonly=True, group="Moon Phase"),
    VarSchema("SHOW_MOONPHASE_DURING_DAYTIME",       "Show During Daytime",        VarType.BOOLEAN,
              "Display moon phase regardless of daylight hours", readonly=True,
              group="Moon Phase"),
    VarSchema("SUPPRESS_MOONPHASE_NOT_VISIBLE", "Suppress Non-Visible Moon Phases", VarType.BOOLEAN,
          "Suppress moon phase display when the moon is too dim to be visible", 
          readonly=True, group="Moon Phase"),
    VarSchema("MOON_PHASE_SHOW_DURING_RAIN",       "Show While Raining",           VarType.BOOLEAN,
              "Display even when it's currently raining", readonly=True,
              group="Moon Phase"),
    VarSchema("MOON_PHASE_SHOW_WITH_RAIN_FORECAST","Show When Rain Expected",      VarType.BOOLEAN,
              "Display even when rain is in the forecast", readonly=True,
              group="Moon Phase"),
    VarSchema("SHOW_MOONPHASE_BILINGUAL",           "Bilingual Phase Name",        VarType.BOOLEAN,
              "Show phase name in both English and Bengali", readonly=True,
              group="Moon Phase"),
    VarSchema("SHOW_MOONPHASE_BENGALI",             "Bengali Phase Name",          VarType.BOOLEAN,
              "Show phase name in Bengali only", readonly=True, group="Moon Phase"),
    VarSchema("SHOW_LUNAR_APSIDAL_SYZYGY",             "Apsidal Syzygy Label",          VarType.BOOLEAN,
          "Show supermoon, super new moon, or micromoon label when applicable",
          readonly=True, group="Moon Phase"),
    VarSchema("ONLY_SHOW_VISIBLE_NIGHT_APSIDAL_SYZYGY", "Restrict Syzygy Label to Night Visibility", VarType.BOOLEAN,
          "Only show the syzygy label when the moon is visibly above the horizon at night", 
          readonly=True, group="Moon Phase"),

    # ── API Keys ──────────────────────────────────────────────────────────────
    VarSchema("API_KEY",       "OpenWeatherMap API Key",              VarType.STRING,
              "API key from openweathermap.org", readonly=True, group="API Keys", secret=True),
    VarSchema("API_KEY_TYPE",  "OpenWeatherMap Plan",                 VarType.ENUM,
              "Your OpenWeatherMap subscription tier",
              choices=["FREE", "PRO"], default="PRO", readonly=True, group="API Keys"),
    VarSchema("MOON_API_KEY",  "Moon API Key",             VarType.STRING,
              "API key from astroapi.byhrast.com", readonly=True, group="API Keys", secret=True),

    # ── Location & Timezone ───────────────────────────────────────────────────
    VarSchema("LOCATION",  "Coordinates",   VarType.STRING,
              "Latitude and longitude", readonly=True, group="Location"),
    VarSchema("TIMEZONE",  "Time Zone",     VarType.STRING,
              "IANA time zone (e.g. Asia/Dhaka)", readonly=True, group="Location"),

    # ── Retry Configuration ───────────────────────────────────────────────────
    VarSchema("MAX_CONNECTIVITY_RETRIES", "Max Retries",    VarType.INTEGER,
              "Number of attempts before giving up on connectivity", default=5, readonly=True,
              group="Network"),
    VarSchema("CONNECTIVITY_RETRY_DELAY", "Retry Interval", VarType.INTEGER,
              "Seconds to wait between each retry attempt", default=5, readonly=True,
              group="Network"),
]

SCHEMA_MAP: dict[str, VarSchema] = {s.key: s for s in SCHEMA}
GROUPS: list[str] = list(dict.fromkeys(s.group for s in SCHEMA))


@dataclass(frozen=True)
class LocationEntry:
    """A unique (name, lat, lon) location from ip_mappings.csv."""
    name: str
    lat: str
    lon: str

    @property
    def display_label(self) -> str:
        return f"{self.name.title()} ({self.lat},{self.lon})"

    @property
    def location_value(self) -> str:
        return f"lat={self.lat}&lon={self.lon}"


class IpMappingStore:
    """
    Loads ip_mappings.csv, deduplicates by (NAME, LATITUDE, LONGITUDE),
    and persists the last used CSV path via GSettings (same key namespace).
    """

    CSV_FILENAME = "ip_mappings.csv"

    def __init__(self, settings: Optional[Gio.Settings]) -> None:
        self._settings = settings

    # ── Discovery (mirrors WeatherConfigApp._get_local_config pattern) ────────

    def find_default_csv(self) -> Optional[Path]:
        """Check script directory for ip_mappings.csv (auto-load, same as .weather_config)."""
        candidate = Path(__file__).resolve().parent / self.CSV_FILENAME
        return candidate if candidate.exists() else None

    def get_last_csv(self) -> Optional[Path]:
        """Restore last used CSV from GSettings."""
        if not self._settings:
            return None
        path_str = self._settings.get_string("last-ip-mapping-path")
        if path_str:
            p = Path(path_str)
            if p.exists():
                return p
        return None

    def save_last_csv(self, path: Path) -> None:
        if self._settings:
            self._settings.set_string("last-ip-mapping-path", str(path))

    def resolve_csv(self) -> Optional[Path]:
        """Priority: last saved → auto-detected in script dir."""
        return self.get_last_csv() or self.find_default_csv()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def load(self, path: Path) -> list[LocationEntry]:
        """
        Parse CSV, deduplicate by (NAME, LAT, LON), sort by NAME so same
        names are grouped, preserve original order within groups.
        """
        import csv
        seen: set[tuple[str, str, str]] = set()
        entries: list[LocationEntry] = []

        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["NAME"].strip().upper(),
                       row["LATITUDE"].strip(),
                       row["LONGITUDE"].strip())
                if key not in seen:
                    seen.add(key)
                    entries.append(LocationEntry(
                        name=row["NAME"].strip(),
                        lat=row["LATITUDE"].strip(),
                        lon=row["LONGITUDE"].strip(),
                    ))

        # Group same names together, stable within groups
        entries.sort(key=lambda e: e.name.upper())
        return entries


# ─── Timezone Store ───────────────────────────────────────────────────────────


class TimezoneStore:
    """
    Loads zone.tab from the script directory and parses IANA timezone identifiers.
    Falls back gracefully — if the file is absent or malformed, returns an empty list.

    zone.tab format (tab-separated):
        col 0: ISO 3166 country code(s)
        col 1: coordinates
        col 2: TZ identifier  ← what we want  (e.g. America/New_York)
        col 3: optional comment
    Lines beginning with '#' are comments and are skipped.
    """

    ZONE_TAB_FILENAME = "zone.tab"

    def __init__(self) -> None:
        self._timezones: list[str] = []
        self._loaded = False

    def find_zone_tab(self) -> Optional[Path]:
        """Look for zone.tab next to the script (same discovery pattern as ip_mappings.csv)."""
        candidate = Path(__file__).resolve().parent / self.ZONE_TAB_FILENAME
        return candidate if candidate.exists() else None

    def load(self) -> list[str]:
        """
        Parse zone.tab and return a sorted list of TZ identifiers (3rd column).
        Result is cached after the first call.
        Returns [] if file not found or entirely unreadable.
        """
        if self._loaded:
            return self._timezones

        self._loaded = True
        path = self.find_zone_tab()
        if not path:
            return self._timezones

        try:
            tzs: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    tz = parts[2].strip()
                    if tz:
                        tzs.append(tz)
            self._timezones = sorted(set(tzs))
        except Exception:
            # Malformed or unreadable — degrade silently to plain StringRow
            self._timezones = []

        return self._timezones

    def available(self) -> bool:
        """True if zone.tab was found and yielded at least one entry."""
        return bool(self.load())


# ─── Config File I/O ─────────────────────────────────────────────────────────


class ConfigParser:
    """Reads and writes bash-style .env / config files."""

    # Matches: [readonly] KEY="value"  or  KEY=value
    _LINE_RE = re.compile(
        r'^(?P<readonly>readonly\s+)?(?P<key>[A-Z_][A-Z0-9_]*)=(?P<value>.*)$'
    )

    def load(self, path: Path) -> dict[str, ConfigEntry]:
        """Parse file, return {key: ConfigEntry} only for known schema keys."""
        entries: dict[str, ConfigEntry] = {}
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = self._LINE_RE.match(line.strip())
            if not m:
                continue
            key = m.group("key")
            val = m.group("value").split("#")[0].strip()   # strip inline comment
            if key in SCHEMA_MAP:
                entries[key] = ConfigEntry(schema=SCHEMA_MAP[key], raw_value=val)
        # Fill missing keys with defaults
        for schema in SCHEMA:
            if schema.key not in entries:
                default = str(schema.default) if schema.default is not None else ""
                entries[schema.key] = ConfigEntry(schema=schema, raw_value=default)
        return entries

    def save(self, path: Path, entries: dict[str, ConfigEntry]) -> None:
        """Rewrite file, updating only the known variables, preserving everything else."""
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        for line in lines:
            m = self._LINE_RE.match(line.strip())
            if m and (key := m.group("key")) in entries:
                entry = entries[key]
                prefix = "readonly " if m.group("readonly") else ""
                # preserve inline comment if any
                comment_match = re.search(r'\s+#.*$', line)
                comment = comment_match.group(0) if comment_match else ""
                out.append(f"{prefix}{key}={entry.raw_value}{comment}\n")
            else:
                out.append(line)
        path.write_text("".join(out), encoding="utf-8")


# ─── Validation ──────────────────────────────────────────────────────────────


class Validator:
    """Validates entry values; returns error string or empty string."""

    def validate(self, entry: ConfigEntry) -> str:
        schema = entry.schema
        val = entry.display_value
        vt = schema.var_type

        if vt == VarType.INTEGER:
            try:
                int(val)
            except ValueError:
                return f"Must be a whole number"
        elif vt == VarType.FLOAT:
            try:
                fv = float(val)
                if not 0.0 <= fv <= 1.0:
                    return "Must be between 0.0 and 1.0"
            except ValueError:
                return "Must be a decimal number"
        elif vt == VarType.BOOLEAN:
            if val.lower() not in ("true", "false"):
                return "Must be true or false"
        elif vt == VarType.STRING and schema.key == "TIMEZONE":
            # Only validate against zone.tab when it was successfully loaded.
            tzs = TimezoneStore().load()
            if tzs and val not in tzs:
                return "Not a recognised IANA timezone. Check zone.tab for valid values."
        elif vt == VarType.ENUM:
            if val not in schema.choices:
                return f"Must be one of: {', '.join(schema.choices)}"
        elif vt == VarType.MOON_WINDOW:
            if val != schema.sentinel_value:
                try:
                    int(val)
                except ValueError:
                    return f"Must be a number or \"{schema.sentinel_value}\""
        return ""


# ─── Row Widgets ─────────────────────────────────────────────────────────────


class BaseRow(Adw.ActionRow):
    """Base preference row with label + description."""

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__()
        self.entry = entry
        self._on_change = on_change
        self.set_title(entry.schema.label)
        if entry.schema.description:
            self.set_subtitle(entry.schema.description)
        self.set_activatable(False)

    def _notify_change(self) -> None:
        self._on_change(self.entry)


class StringRow(BaseRow):
    """Text-entry row for STRING variables."""

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__(entry, on_change)
        self._is_secret = entry.schema.secret
        self._entry = Gtk.Entry()
        self._entry.set_text(entry.display_value)
        self._entry.set_valign(Gtk.Align.CENTER)
        self._entry.set_hexpand(True)
        if self._is_secret:
            # Start masked; reveal on focus
            self._entry.set_visibility(False)
            self._entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            focus_ctrl = Gtk.EventControllerFocus()
            focus_ctrl.connect("enter", self._on_focus_enter)
            focus_ctrl.connect("leave", self._on_focus_leave)
            self._entry.add_controller(focus_ctrl)
        self._entry.connect("changed", self._on_text_changed)
        self.add_suffix(self._entry)
        self.set_activatable_widget(self._entry)

    def _on_focus_enter(self, *_: Any) -> None:
        self._entry.set_visibility(True)

    def _on_focus_leave(self, *_: Any) -> None:
        self._entry.set_visibility(False)

    def _on_text_changed(self, widget: Gtk.Entry) -> None:
        self.entry.display_value = widget.get_text()
        self._notify_change()

    def reset(self) -> None:
        self._entry.set_text(self.entry.display_value)


class IntegerRow(BaseRow):
    """Spin-button row for INTEGER variables."""

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__(entry, on_change)
        adj = Gtk.Adjustment(value=self._safe_int(),
                             lower=0, upper=99999,
                             step_increment=1, page_increment=10)
        self._spin = Gtk.SpinButton(adjustment=adj, digits=0)
        self._spin.set_valign(Gtk.Align.CENTER)
        self._spin.connect("value-changed", self._on_value_changed)
        # GTK4: SpinButton implements Editable directly; "changed" fires on every keystroke
        self._spin.connect("changed", self._on_text_changed)
        self.add_suffix(self._spin)
        self.set_activatable_widget(self._spin)

    def _safe_int(self) -> int:
        try:
            return int(self.entry.display_value)
        except ValueError:
            return 0

    def _on_text_changed(self, widget: Gtk.SpinButton) -> None:
        """Fires on every keystroke; commit the typed text immediately."""
        self._spin.update()
        self._on_value_changed(self._spin)

    def _on_value_changed(self, widget: Gtk.SpinButton) -> None:
        self.entry.display_value = str(int(widget.get_value()))
        self._notify_change()

    def reset(self) -> None:
        self._spin.set_value(self._safe_int())


class FloatRow(BaseRow):
    """Spin-button row for FLOAT variables (e.g., Rain Probability)."""

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__(entry, on_change)
        
        # Adjustment for 0.0 to 1.0 range
        adj = Gtk.Adjustment(value=self._safe_float(),
                             lower=0.0, upper=1.0,
                             step_increment=0.05, page_increment=0.1)
        self._spin = Gtk.SpinButton(adjustment=adj, digits=2)
        self._spin.set_valign(Gtk.Align.CENTER)
        
        # 1. Handle numeric changes (mouse clicks, arrow keys, wheel)
        self._spin.connect("value-changed", self._on_value_changed)
        
        # 2. Handle typing changes (keystrokes)
        # We connect to 'changed' but do NOT call .update() here to prevent cursor jumping.
        self._spin.connect("changed", self._on_text_changed)
        
        self.add_suffix(self._spin)
        self.set_activatable_widget(self._spin)

    def _safe_float(self) -> float:
        try:
            return float(self.entry.display_value)
        except (ValueError, TypeError):
            return 0.0

    def _on_text_changed(self, editable: Gtk.Editable) -> None:
        text = editable.get_text()

        # Allow intermediate states like "", ".", "0.", etc.
        if text in ("", ".", "-", "-.", "0."):
            return

        try:
            float(text)
        except ValueError:
            return  # Ignore invalid partial input

        # Only update model if valid float → prevents cursor jump
        self.entry.display_value = text
        self._notify_change()

    def _on_value_changed(self, widget: Gtk.SpinButton) -> None:
        """Fires when the numeric value changes via UI controls."""
        val_str = f"{widget.get_value():.2f}"
        if self.entry.display_value != val_str:
            self.entry.display_value = val_str
            self._notify_change()

    def reset(self) -> None:
        """Reverts the widget to the current model value."""
        self._spin.set_value(self._safe_float())


class BooleanRow(BaseRow):
    """Switch row for BOOLEAN variables."""

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__(entry, on_change)
        self._switch = Gtk.Switch()
        self._switch.set_active(entry.display_value.lower() == "true")
        self._switch.set_valign(Gtk.Align.CENTER)
        self._switch.connect("notify::active", self._on_toggled)
        self.add_suffix(self._switch)
        self.set_activatable_widget(self._switch)

    def _on_toggled(self, widget: Gtk.Switch, _param: Any) -> None:
        self.entry.display_value = "true" if widget.get_active() else "false"
        self._notify_change()

    def reset(self) -> None:
        self._switch.set_active(self.entry.display_value.lower() == "true")


class EnumRow(BaseRow):
    """Dropdown row for ENUM variables."""

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__(entry, on_change)
        choices = entry.schema.choices
        self._dropdown = Gtk.DropDown.new_from_strings(choices)
        cur = entry.display_value
        idx = choices.index(cur) if cur in choices else 0
        self._dropdown.set_selected(idx)
        self._dropdown.set_valign(Gtk.Align.CENTER)
        self._dropdown.connect("notify::selected", self._on_selected)
        self.add_suffix(self._dropdown)

    def _on_selected(self, widget: Gtk.DropDown, _param: Any) -> None:
        idx = widget.get_selected()
        choices = self.entry.schema.choices
        if 0 <= idx < len(choices):
            self.entry.display_value = choices[idx]
            self._notify_change()

    def reset(self) -> None:
        choices = self.entry.schema.choices
        cur = self.entry.display_value
        self._dropdown.set_selected(choices.index(cur) if cur in choices else 0)

class LocationRow(BaseRow):
    """
    LOCATION row with:
    - Preset dropdown loaded from ip_mappings.csv
    - CUSTOM checkbox to reveal manual lat/lon entries (existing UI)
    - pin button to open Google Maps
    """

    def __init__(self, entry: ConfigEntry,
                on_change: Callable[[ConfigEntry], None],
                ip_store: "IpMappingStore") -> None:
        super().__init__(entry, on_change)

        self._ip_store = ip_store
        self._locations: list[LocationEntry] = []

        lat, lon = self._parse_location(entry.display_value)

        # ── Single horizontal row ─────────────────────────────────────────────────
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_box.set_valign(Gtk.Align.CENTER)

        self._dropdown = Gtk.DropDown()
        self._dropdown.set_hexpand(True)
        self._dropdown.connect("notify::selected", self._on_dropdown_selected)

        # Manual lat/lon (inline, hidden by default)
        self._manual_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self._manual_box.set_valign(Gtk.Align.CENTER)
        self._manual_box.set_hexpand(True)

        lat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lat_label = Gtk.Label(label="Latitude")
        lat_label.set_valign(Gtk.Align.CENTER)
        self._lat_entry = Gtk.Entry()
        self._lat_entry.set_width_chars(6)
        self._lat_entry.set_text(lat)
        lat_box.append(lat_label)
        lat_box.append(self._lat_entry)

        lon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lon_label = Gtk.Label(label="Longitude")
        lon_label.set_valign(Gtk.Align.CENTER)
        self._lon_entry = Gtk.Entry()
        self._lon_entry.set_width_chars(6)
        self._lon_entry.set_text(lon)
        lon_box.append(lon_label)
        lon_box.append(self._lon_entry)

        self._manual_box.append(lat_box)
        self._manual_box.append(lon_box)

        self._lat_entry.connect("changed", self._on_manual_changed)
        self._lon_entry.connect("changed", self._on_manual_changed)

        self._custom_check = Gtk.CheckButton(label="Custom")
        self._custom_check.connect("toggled", self._on_custom_toggled)

        self._pin_btn = Gtk.Button()
        self._pin_btn.set_icon_name("find-location-symbolic")
        self._pin_btn.set_tooltip_text("Open in Google Maps")
        self._pin_btn.set_valign(Gtk.Align.CENTER)
        self._pin_btn.connect("clicked", self._on_pin_clicked)

        row_box.append(self._dropdown)
        row_box.append(self._manual_box)
        row_box.append(self._custom_check)
        row_box.append(self._pin_btn)

        self.add_suffix(row_box)

        # ── Load CSV and initialise state ─────────────────────────────────────
        self._load_locations()
        self._sync_initial_state(lat, lon)

    # ── CSV loading ───────────────────────────────────────────────────────────

    def _load_locations(self) -> None:
        csv_path = self._ip_store.resolve_csv()
        if csv_path:
            try:
                self._locations = self._ip_store.load(csv_path)
                self._ip_store.save_last_csv(csv_path)
            except Exception:
                self._locations = []
        else:
            self._locations = []

        labels = [loc.display_label for loc in self._locations]
        model = Gtk.StringList.new(labels)
        self._dropdown.set_model(model)

    # ── State sync ────────────────────────────────────────────────────────────

    def _sync_initial_state(self, lat: str, lon: str) -> None:
        """On load: match current lat/lon to a preset, else enable Custom."""
        matched_idx = next(
            (i for i, loc in enumerate(self._locations)
             if loc.lat == lat and loc.lon == lon),
            None
        )
        if matched_idx is not None and self._locations:
            self._dropdown.set_selected(matched_idx)
            self._set_custom_mode(False)
        else:
            self._set_custom_mode(True)

    def _set_custom_mode(self, custom: bool) -> None:
        """Toggle between preset dropdown and manual entry."""
        # Block the check signal to avoid recursion
        self._custom_check.handler_block_by_func(self._on_custom_toggled)
        self._custom_check.set_active(custom)
        self._custom_check.handler_unblock_by_func(self._on_custom_toggled)

        self._dropdown.set_visible(not custom)
        self._manual_box.set_visible(custom)

    # ── Signals ───────────────────────────────────────────────────────────────

    def _on_custom_toggled(self, widget: Gtk.CheckButton) -> None:
        self._set_custom_mode(widget.get_active())
        if not widget.get_active():
            # Switching back to preset: apply currently selected dropdown item
            self._on_dropdown_selected(self._dropdown, None)
        else:
            # Switching to custom: apply manual entries
            self._on_manual_changed()

    def _on_dropdown_selected(self, widget: Gtk.DropDown, _param: Any) -> None:
        if self._custom_check.get_active():
            return
        idx = widget.get_selected()
        if 0 <= idx < len(self._locations):
            loc = self._locations[idx]
            self.entry.display_value = loc.location_value
            # Keep manual entries in sync (useful if user later switches to Custom)
            self._lat_entry.handler_block_by_func(self._on_manual_changed)
            self._lon_entry.handler_block_by_func(self._on_manual_changed)
            self._lat_entry.set_text(loc.lat)
            self._lon_entry.set_text(loc.lon)
            self._lat_entry.handler_unblock_by_func(self._on_manual_changed)
            self._lon_entry.handler_unblock_by_func(self._on_manual_changed)
            self._notify_change()

    def _on_manual_changed(self, *_: Any) -> None:
        lat = self._lat_entry.get_text().strip()
        lon = self._lon_entry.get_text().strip()
        if lat and lon:
            self.entry.display_value = f"lat={lat}&lon={lon}"
            self._notify_change()

    def _on_pin_clicked(self, *_: Any) -> None:
        lat, lon = self._parse_location(self.entry.display_value)
        if lat and lon:
            url = f"https://www.google.com/maps?q={lat},{lon}"
            Gio.AppInfo.launch_default_for_uri(url, None)

    # ── Reuse existing helpers ────────────────────────────────────────────────

    def _parse_location(self, value: str) -> tuple[str, str]:
        lat, lon = "", ""
        try:
            parts = value.split("&")
            for p in parts:
                if p.startswith("lat="):
                    lat = p.split("=", 1)[1]
                elif p.startswith("lon="):
                    lon = p.split("=", 1)[1]
        except Exception:
            pass
        return lat, lon

    def reset(self) -> None:
        lat, lon = self._parse_location(self.entry.display_value)
        self._lat_entry.set_text(lat)
        self._lon_entry.set_text(lon)
        self._sync_initial_state(lat, lon)


# ─── Timezone Row ─────────────────────────────────────────────────────────────


class TimezoneRow(BaseRow):
    """
    TIMEZONE row — searchable DropDown (GTK4-native) when zone.tab is present,
    plain text entry (original StringRow behaviour) when it is not.

    DropDown mode:
    - Uses Gtk.DropDown + Gtk.StringFilter; no deprecated APIs.
    - Typing in the built-in search box filters the list via SUBSTRING match.
    - Selecting from the list sets the value immediately.
    - An 'error' CSS class is applied to the search entry when the typed text
      is not a valid timezone, giving the user live visual feedback.
    - On save, Validator.validate() performs a hard check against the list.

    Fallback mode (no zone.tab):
    - Behaves identically to StringRow; no functionality is changed.
    """

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None],
                 tz_store: "TimezoneStore") -> None:
        super().__init__(entry, on_change)

        self._tz_store = tz_store
        self._all_timezones: list[str] = tz_store.load()

        if not self._all_timezones:
            # ── Fallback: plain text entry (no zone list available) ───────────
            self._entry: Gtk.Entry = Gtk.Entry()
            self._entry.set_text(entry.display_value)
            self._entry.set_placeholder_text("e.g. Asia/Dhaka")
            self._entry.set_valign(Gtk.Align.CENTER)
            self._entry.set_hexpand(True)
            self._entry.connect("changed", self._on_entry_changed)
            self._dropdown: Optional[Gtk.DropDown] = None
            self._search_entry: Optional[Gtk.SearchEntry] = None
            self._filter: Optional[Gtk.StringFilter] = None
            self._string_list: Optional[Gtk.StringList] = None
            self.add_suffix(self._entry)
            self.set_activatable_widget(self._entry)
            return

        # ── GTK4-native DropDown with built-in search ─────────────────────────
        # StringList is the GTK4 model for a flat list of strings
        self._string_list = Gtk.StringList.new(self._all_timezones)

        # StringFilter filters the model as the user types
        self._filter = Gtk.StringFilter.new(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._filter.set_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._filter.set_ignore_case(True)

        filtered_model = Gtk.FilterListModel.new(self._string_list, self._filter)

        # SingleSelection wraps the filtered model for DropDown
        selection = Gtk.SingleSelection.new(filtered_model)
        selection.set_autoselect(False)

        # Factory to render each row as a simple label
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)

        self._dropdown = Gtk.DropDown.new(selection, None)
        self._dropdown.set_factory(factory)
        self._dropdown.set_enable_search(True)
        self._dropdown.set_hexpand(True)
        self._dropdown.set_valign(Gtk.Align.CENTER)

        # Wait for the DropDown's internal search entry to appear, then wire it up
        self._dropdown.connect("notify::selected-item", self._on_dropdown_selected)

        # We also need a plain entry so the user can type freely and see
        # validation feedback; the DropDown's search field handles filtering.
        # Connect to the search entry inside the DropDown after it's realised.
        self._dropdown.connect("realize", self._on_dropdown_realize)

        # Seed the dropdown selection to the current saved value
        self._entry = None          # not used in dropdown mode
        self._search_entry = None   # resolved in _on_dropdown_realize

        self.add_suffix(self._dropdown)
        self.set_activatable_widget(self._dropdown)

        # Set initial selection
        self._select_timezone(entry.display_value)

    # ── DropDown factory callbacks ─────────────────────────────────────────────

    def _on_factory_setup(self, factory: Gtk.SignalListItemFactory,
                          list_item: Gtk.ListItem) -> None:
        list_item.set_child(Gtk.Label(xalign=0))

    def _on_factory_bind(self, factory: Gtk.SignalListItemFactory,
                         list_item: Gtk.ListItem) -> None:
        obj = list_item.get_item()
        label: Gtk.Label = list_item.get_child()
        if obj is not None:
            label.set_label(obj.get_string())

    # ── DropDown signal handlers ───────────────────────────────────────────────

    def _on_dropdown_realize(self, widget: Gtk.DropDown) -> None:
        """
        Once the DropDown is realised its internal search entry exists.
        Walk the widget tree to find it and connect a 'changed' handler
        so we can apply live error styling while the user types.
        """
        search_entry = self._find_search_entry(widget)
        if search_entry:
            self._search_entry = search_entry
            search_entry.connect("search-changed", self._on_search_changed)

    def _find_search_entry(self, widget: Gtk.Widget) -> Optional[Gtk.SearchEntry]:
        """Recursively find the first GtkSearchEntry inside widget."""
        if isinstance(widget, Gtk.SearchEntry):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self._find_search_entry(child)
            if found:
                return found
            child = child.get_next_sibling()
        return None

    def _on_search_changed(self, search_entry: Gtk.SearchEntry) -> None:
        """
        Fires on every keystroke inside the DropDown's search box.
        ONLY drives the StringFilter and live error styling.
        Never writes to entry.display_value — that is _on_dropdown_selected's job.
        """
        text = search_entry.get_text().strip()
        # Drive the filter so the popup list narrows in real time
        self._filter.set_search(text)
        # Show error styling while the user is mid-search; clear when empty or exact match
        is_valid = not text or text in self._all_timezones
        if not is_valid:
            search_entry.add_css_class("error")
        else:
            search_entry.remove_css_class("error")
        # Do NOT write to entry.display_value here.
        # Partial search text (e.g. "asia/dha") must never become the saved value.

    def _on_dropdown_selected(self, dropdown: Gtk.DropDown,
                              _param: object) -> None:
        """
        Fires when the user picks an item from the dropdown list (or on initial
        programmatic selection). get_selected_item() always returns the correct
        StringObject from the filtered model — we just read its string directly.
        """
        obj = dropdown.get_selected_item()
        if obj is None:
            return
        text: str = obj.get_string()
        if not text:
            return
        if self._search_entry:
            self._search_entry.remove_css_class("error")
        self.entry.display_value = text
        self._notify_change()

    def _select_timezone(self, tz: str) -> None:
        """Set the DropDown's selected item to match *tz* (exact match).
        Must clear the filter first so indices in the unfiltered StringList
        align with what the DropDown's model sees.
        """
        if not tz or self._string_list is None or self._dropdown is None:
            return
        # Clear any active search filter so the full list is visible and
        # indices in _string_list match the DropDown's model positions.
        if self._filter is not None:
            self._filter.set_search("")
        if self._search_entry is not None:
            self._search_entry.handler_block_by_func(self._on_search_changed)
            self._search_entry.set_text("")
            self._search_entry.handler_unblock_by_func(self._on_search_changed)
        n = self._string_list.get_n_items()
        for i in range(n):
            item = self._string_list.get_item(i)
            if item and item.get_string() == tz:
                self._dropdown.set_selected(i)
                return

    # ── Fallback (no tz list) signal handler ──────────────────────────────────

    def _on_entry_changed(self, widget: Gtk.Entry) -> None:
        """Plain entry handler used only when zone.tab is unavailable."""
        self.entry.display_value = widget.get_text()
        self._notify_change()

    # ── BaseRow interface ─────────────────────────────────────────────────────

    def reset(self) -> None:
        val = self.entry.display_value
        if self._dropdown is not None:
            self._select_timezone(val)
            if self._search_entry:
                is_valid = not val or val in self._all_timezones
                if not is_valid:
                    self._search_entry.add_css_class("error")
                else:
                    self._search_entry.remove_css_class("error")
        elif self._entry is not None:
            self._entry.set_text(val)



class MoonWindowRow(BaseRow):
    """
    Special row for MOON_WINDOW variables.
    Has a checkbox (Use Sentinel) + integer spin.
    When checked: value = sentinel_value, spin disabled.
    When unchecked: value = integer from spin.
    """

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None]) -> None:
        super().__init__(entry, on_change)
        schema = entry.schema
        cur = entry.display_value

        self._is_sentinel = (cur == schema.sentinel_value)

        # Box: [checkbox] [spin]
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)

        self._check = Gtk.CheckButton(label=schema.sentinel_label)
        self._check.set_active(self._is_sentinel)
        self._check.connect("toggled", self._on_check_toggled)

        adj = Gtk.Adjustment(value=self._safe_int(cur),
                             lower=0, upper=9999,
                             step_increment=1, page_increment=10)
        self._spin = Gtk.SpinButton(adjustment=adj, digits=0)
        self._spin.set_sensitive(not self._is_sentinel)
        self._spin.connect("value-changed", self._on_spin_changed)
        # GTK4: SpinButton implements Editable directly; "changed" fires on every keystroke
        self._spin.connect("changed", self._on_spin_text_changed)

        box.append(self._check)
        box.append(self._spin)
        self.add_suffix(box)

    def _safe_int(self, val: str) -> int:
        try:
            return int(val)
        except ValueError:
            return 0

    def _on_check_toggled(self, widget: Gtk.CheckButton) -> None:
        self._is_sentinel = widget.get_active()
        self._spin.set_sensitive(not self._is_sentinel)
        if self._is_sentinel:
            self.entry.display_value = self.entry.schema.sentinel_value
        else:
            self.entry.display_value = str(int(self._spin.get_value()))
        self._notify_change()

    def _on_spin_text_changed(self, widget: Gtk.SpinButton) -> None:
        """Fires on every keystroke; commit the typed text immediately."""
        self._spin.update()
        self._on_spin_changed(self._spin)

    def _on_spin_changed(self, widget: Gtk.SpinButton) -> None:
        if not self._is_sentinel:
            self.entry.display_value = str(int(widget.get_value()))
            self._notify_change()

    def reset(self) -> None:
        cur = self.entry.display_value
        self._is_sentinel = (cur == self.entry.schema.sentinel_value)
        self._check.set_active(self._is_sentinel)
        self._spin.set_sensitive(not self._is_sentinel)
        if not self._is_sentinel:
            self._spin.set_value(self._safe_int(cur))


def make_row(entry: ConfigEntry,
             on_change: Callable[[ConfigEntry], None],
             ip_store: Optional["IpMappingStore"] = None,
             tz_store: Optional["TimezoneStore"] = None) -> BaseRow:

    if entry.schema.key == "LOCATION":
        return LocationRow(entry, on_change, ip_store or IpMappingStore(None))

    if entry.schema.key == "TIMEZONE":
        return TimezoneRow(entry, on_change, tz_store or TimezoneStore())

    vt = entry.schema.var_type

    if vt == VarType.STRING:
        return StringRow(entry, on_change)

    if vt == VarType.INTEGER:
        return IntegerRow(entry, on_change)

    if vt == VarType.FLOAT:
        return FloatRow(entry, on_change)

    if vt == VarType.BOOLEAN:
        return BooleanRow(entry, on_change)

    if vt == VarType.ENUM:
        return EnumRow(entry, on_change)

    if vt == VarType.MOON_WINDOW:
        return MoonWindowRow(entry, on_change)

    raise ValueError(f"Unknown VarType: {vt}")


# ─── Main Application Window ─────────────────────────────────────────────────


class WeatherConfigWindow(Adw.ApplicationWindow):
    """Main application window."""

    def _update_dependent_states(self, changed_key: str) -> None:
        if changed_key not in DEPENDENCIES:
            return

        master_value = self._entries[changed_key].display_value.lower() == "true"

        if changed_key in INVERSE_DEPENDENCIES:
            master_value = not master_value  # true → disable dependents

        for dep_key in DEPENDENCIES[changed_key]:
            if dep_key in self._rows:
                self._rows[dep_key].set_sensitive(master_value)

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app)
        self.set_title("Weather Config Editor")
        self.set_default_size(720, 820)

        self._parser = ConfigParser()
        self._validator = Validator()
        self._config_path: Optional[Path] = None
        self._entries: dict[str, ConfigEntry] = {}
        self._rows: dict[str, BaseRow] = {}
        self._search_text: str = ""
        self._undo_stack: list[tuple[str, str]] = []   # (key, old_raw_value)
        self._original_values: dict[str, str] = {}     # key → raw_value at load time
        self._moon_value_labels: dict[str, Gtk.Label] = {}  # for in-place Moon Data refresh
        self._moon_data_group: Optional[Adw.PreferencesGroup] = None

        self._build_ui()

    def _has_changes(self) -> bool:
        if not self._original_values:
            return False
        return any(
            entry.raw_value != self._original_values.get(key)
            for key, entry in self._entries.items()
        )

    def _update_button_states(self) -> None:
        has_changes = self._has_changes()
        self._save_btn.set_sensitive(has_changes)
        self._undo_btn.set_sensitive(bool(self._undo_stack))

    def _update_save_button(self) -> None:
        self._update_button_states()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Build the entire window layout."""
        toolbar_view = Adw.ToolbarView()

        # ToastOverlay wraps everything — set once, reused forever
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(toolbar_view)
        self.set_content(self._toast_overlay)

        # ── Header bar ────────────────────────────────────────────────────────
        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)

        # File open button
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open config file")
        open_btn.connect("clicked", self._on_open_clicked)
        header.pack_start(open_btn)

        # Undo button
        self._undo_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self._undo_btn.set_tooltip_text("Undo last change")
        self._undo_btn.set_sensitive(False)
        self._undo_btn.connect("clicked", self._on_undo_clicked)
        header.pack_start(self._undo_btn)

        # Save / Reset buttons
        self._save_btn = Gtk.Button(label="Save")
        self._save_btn.set_sensitive(False)  # initially disabled
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.connect("clicked", self._on_save_clicked)

        header.pack_end(self._save_btn)

        reset_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reset_btn.set_tooltip_text("Reset all fields to loaded values")
        reset_btn.connect("clicked", self._on_reset_clicked)

        header.pack_end(reset_btn)
        toolbar_view.add_top_bar(header)

        # ── Search bar ────────────────────────────────────────────────────────
        search_bar = Gtk.SearchBar()
        search_entry = Gtk.SearchEntry()
        search_entry.set_hexpand(True)
        search_entry.set_placeholder_text("Search variables…")
        search_entry.connect("search-changed", self._on_search_changed)
        search_bar.set_child(search_entry)
        search_bar.set_search_mode(True)
        toolbar_view.add_top_bar(search_bar)

        # ── Main content ──────────────────────────────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        toolbar_view.set_content(scroll)

        self._main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._main_box.set_margin_top(24)
        self._main_box.set_margin_bottom(24)
        self._main_box.set_margin_start(24)
        self._main_box.set_margin_end(24)
        scroll.set_child(self._main_box)

        # Welcome / status banner
        self._banner = Adw.Banner()
        self._banner.set_title("Open a config file to get started")
        self._banner.set_button_label("Open File")
        self._banner.connect("button-clicked", self._on_open_clicked)
        self._banner.set_revealed(True)
        self._main_box.append(self._banner)

        # File path label
        self._path_label = Gtk.Label(label="No file selected")
        self._path_label.add_css_class("caption")
        self._path_label.add_css_class("dim-label")
        self._path_label.set_halign(Gtk.Align.START)
        self._path_label.set_margin_top(8)
        self._path_label.set_margin_bottom(8)
        self._main_box.append(self._path_label)

        # Groups container
        self._groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._main_box.append(self._groups_box)

        self._group_widgets: dict[str, tuple[Adw.PreferencesGroup, list[BaseRow]]] = {}

    # ── Moon Data helpers ─────────────────────────────────────────────────────

    _MOON_FIELD_LABELS: dict[str, str] = {
        "date": "Date",
        "illumination": "Illumination",
        "moonrise": "Moonrise",
        "phase": "Phase",
        "moonset": "Moonset",
        "phase_value": "Progress",
        "position": "Position",
        "distance": "Distance",
    }

    @staticmethod
    def _format_phase_progress(value: Any) -> str:
        """Convert a phase_value float string (0.0–1.0) to a percentage like '3%'."""
        if value is None:
            return "—"
        try:
            pct = float(str(value)) * 100
            return f"{round(pct)}%"
        except (ValueError, TypeError):
            return str(value)

    @staticmethod
    def _format_position(value: Any) -> str:
        """
        Convert a position dict with azimuth/altitude (in radians) to a
        human-readable string like 'Direction: East (85°) Height: 63°'.

        Azimuth is measured in radians clockwise from North (0 = N, π/2 = E,
        π = S, 3π/2 = W).  Altitude is measured in radians above the horizon.
        """
        if value is None:
            return "—"
        try:
            if isinstance(value, str):
                import json as _json
                value = _json.loads(value)
            az_rad  = float(str(value.get("azimuth", 0)))
            alt_rad = float(str(value.get("altitude", 0)))

            import math
            az_deg  = math.degrees(az_rad) % 360
            alt_deg = math.degrees(alt_rad)

            # Cardinal / intercardinal direction from azimuth
            directions = [
                (22.5,  "N"),  (67.5,  "NE"), (112.5, "E"),  (157.5, "SE"),
                (202.5, "S"),  (247.5, "SW"), (292.5, "W"),  (337.5, "NW"),
            ]
            compass = "N"
            for threshold, name in directions:
                if az_deg < threshold:
                    compass = name
                    break
            else:
                compass = "N"   # wraps back past 337.5°

            # Full cardinal name for readability
            full_names = {
                "N": "North", "NE": "Northeast", "E": "East",  "SE": "Southeast",
                "S": "South", "SW": "Southwest", "W": "West",  "NW": "Northwest",
            }
            direction_name = full_names.get(compass, compass)

            return (
                f"{direction_name} ({round(az_deg)}°) "
                f"∡ {round(alt_deg)}°"
            )
        except (ValueError, TypeError, AttributeError):
            return str(value)

    @staticmethod
    def _parse_moon_dt(value: str) -> Optional[datetime]:
        value = value.strip()
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            pass
        try:
            return datetime.strptime(value, "%H:%M")
        except Exception:
            return None

    @staticmethod
    def _format_moon_value(key: str, value: Any) -> str:
        if value is None:
            return "—"
        if key == "phase_value":
            return WeatherConfigWindow._format_phase_progress(value)
        if key == "position":
            return WeatherConfigWindow._format_position(value)
        text = str(value).strip()
        if key == "date":
            try:
                d, m, y = text.split("/")
                dt = datetime(int(y), int(m), int(d))
                return f"{dt.day} {dt.strftime('%B %Y')}"
            except Exception:
                return text
        if key in ("moonrise", "moonset"):
            dt = WeatherConfigWindow._parse_moon_dt(text)
            if dt:
                parts = dt.strftime("%I:%M %p").split(" ")
                return f"{parts[0]} {parts[1].upper()}"
            return text
        if key == "retrieved_at":
            dt = WeatherConfigWindow._parse_moon_dt(text)
            if dt:
                date_part = f"{dt.day} {dt.strftime('%B %Y')}"
                time_part = dt.strftime("%I:%M %p").lstrip("0")
                return f"{date_part} {time_part.upper()}"
            return text
        if key == "distance":
            try:
                return f"{float(text):,.2f} km"
            except (ValueError, TypeError):
                return text
        return text

    @staticmethod
    def _load_sun_data() -> dict[str, Any]:
        """
        Load ~/.cache/weather/sun-data.json.
        Returns the parsed dict, or {} if the file is absent or unreadable.
        """
        sun_path = Path.home() / ".cache" / "weather" / "sun-data.json"
        try:
            return json.loads(sun_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _sunset_local_minutes(sun_data: dict[str, Any], tz_name: str) -> Optional[int]:
        """
        Convert the 'sunset' unix timestamp in sun_data to local minutes-since-midnight
        using tz_name (an IANA timezone string, e.g. 'Asia/Dhaka').
        Returns None if the data or timezone is unavailable/invalid.

        The date in sun-data is intentionally ignored: if sun-data is from a
        different day (e.g. yesterday), the stored sunset time is used as an
        approximation. Sunset shifts by only ~1-2 minutes per day, so this is
        accurate enough for the night-visibility check.
        """
        sunset_ts = sun_data.get("sunset")
        if sunset_ts is None:
            return None
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            try:
                import pytz  # type: ignore
                tz = pytz.timezone(tz_name)
            except Exception:
                return None
        try:
            sunset_dt = datetime.fromtimestamp(int(sunset_ts), tz=tz)
            return sunset_dt.hour * 60 + sunset_dt.minute
        except Exception:
            return None

    @staticmethod
    def _compute_moon_alert(data: dict[str, Any], tz_name: str = "") -> Optional[str]:
        """
        Return a one-line alert string combining all applicable conditions,
        or None when nothing notable applies.

        Conditions (all independent, combined with '. ' when both apply):
          • Supermoon  – New Moon or Full Moon, distance ≤ 367 600 km
            (Super New Moon when phase is New Moon)
          • Micromoon  – New Moon or Full Moon, distance ≥ 401 000 km
            Note: Supermoon and Micromoon are mutually exclusive by their
            distance thresholds and therefore cannot both trigger at once.
          • Not visible at night – low illumination OR moon down before sunset
        """
        phase = str(data.get("phase", "")).strip()
        illumination_raw = str(data.get("illumination", "0")).replace("%", "").strip()
        try:
            illumination = float(illumination_raw)
        except ValueError:
            illumination = 0.0

        # Extract distance from position sub-dict
        position = data.get("position", {})
        distance_raw = (
            position.get("distance") if isinstance(position, dict)
            else data.get("distance")
        )
        try:
            distance_km = float(str(distance_raw))
        except (ValueError, TypeError):
            distance_km = None

        moonrise_str = str(data.get("moonrise", "")).strip()
        moonset_str  = str(data.get("moonset", "")).strip()

        is_new_or_full = phase in ("New Moon", "Full Moon")

        alerts: list[str] = []

        # ── Supermoon ─────────────────────────────────────────────────────────
        SUPERMOON_THRESHOLD = 367_600  # km
        if is_new_or_full and distance_km is not None and distance_km <= SUPERMOON_THRESHOLD:
            if phase == "New Moon":
                alerts.append("🌑 Super New Moon")
            else:
                alerts.append("🌕 Supermoon")

        # ── Micromoon ─────────────────────────────────────────────────────────
        # (distance thresholds guarantee this never fires alongside Supermoon)
        MICROMOON_THRESHOLD = 401_000  # km
        if is_new_or_full and distance_km is not None and distance_km >= MICROMOON_THRESHOLD:
            alerts.append("🌑 Micromoon")

        # ── Not visible at night ──────────────────────────────────────────────
        not_visible = False

        # Very thin crescent / new moon → almost never visible
        if illumination < 5.0:
            not_visible = True

        if not not_visible:
            # Moonset before sunset AND moonrise before nightfall means the moon
            # is only up during daytime/evening — not usefully visible after dark.
            # Sunset time is read from sun-data.json (unix timestamp); nightfall is
            # sunset + 60 min. Falls back to 19:00 / 20:00 if file is unavailable.
            try:
                def _hhmm(s: str) -> int:
                    """Return minutes since midnight for a 'HH:MM[-like]' string."""
                    h, m = s.split(":")
                    return int(h) * 60 + int(m)

                moonset_min  = _hhmm(moonset_str)
                moonrise_min = _hhmm(moonrise_str)

                sun_data   = WeatherConfigWindow._load_sun_data()
                sunset_min = WeatherConfigWindow._sunset_local_minutes(sun_data, tz_name)
                if sunset_min is not None:
                    nightfall = sunset_min + 60   # 60 min after sunset = start of night
                else:
                    sunset_min = 19 * 60   # fallback: 19:00
                    nightfall  = 20 * 60   # fallback: 20:00

                if moonset_min < sunset_min and moonrise_min < nightfall:
                    not_visible = True
            except Exception:
                pass  # malformed times – skip this check

        if not_visible:
            alerts.append("Not visible at night.")

        if not alerts:
            return None

        return ". ".join(alerts)

    def _call_moon_api(self) -> dict[str, Any]:
        """Fetch moon data from astroapi.byhrast.com using values from the loaded config."""
        def _get(key: str) -> str:
            entry = self._entries.get(key)
            if not entry:
                raise ValueError(f"Config key '{key}' not loaded")
            val = entry.display_value.strip()
            if not val:
                raise ValueError(f"Config key '{key}' is empty")
            return val

        api_key      = _get("MOON_API_KEY")
        location     = _get("LOCATION")
        timezone_val = _get("TIMEZONE")

        now        = datetime.now()
        date_param = now.strftime("%d/%m/%Y")
        time_param = now.strftime("%H:%M")

        url = (
            f"https://astroapi.byhrast.com/moon.php"
            f"?key={api_key}&{location}&tz={timezone_val}"
            f"&date={date_param}&time={time_param}"
        )

        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

        data = json.loads(raw)
        data = json.loads(raw, parse_float=Decimal)

        if "moonrise" not in data:
            raise RuntimeError("Unexpected response: 'moonrise' field missing")

        data["retrieved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        return data

    def _on_moon_update_clicked(self, btn: Gtk.Button) -> None:
        if not self._entries:
            self._show_error("No config file loaded. Please open a config file first.")
            return

        btn.set_sensitive(False)
        btn.set_label("Updating…")

        def _worker() -> None:
            try:
                data = self._call_moon_api()
                moon_path = Path.home() / ".cache" / "weather" / "moon-data.json"
                moon_path.parent.mkdir(parents=True, exist_ok=True)
                moon_path.write_text(
                    json.dumps(data, indent=2, default=str),
                    encoding="utf-8"
                )
                GLib.idle_add(_on_success, data)
            except Exception as exc:
                GLib.idle_add(_on_error, str(exc))

        def _on_success(data: dict[str, Any]) -> bool:
            btn.set_label("Update")
            btn.set_sensitive(True)
            if self._moon_value_labels:
                # Section was built successfully before — just update labels in-place
                self._refresh_moon_data_values(data)
            else:
                # Section was in error state — rebuild and swap it in
                new_group = self._build_moon_data_section()
                if self._moon_data_group and self._moon_data_group.get_parent():
                    self._groups_box.insert_child_after(new_group, self._moon_data_group)
                    self._groups_box.remove(self._moon_data_group)
                self._moon_data_group = new_group
            self._show_toast("Moon data updated successfully")
            return False

        def _on_error(msg: str) -> bool:
            btn.set_label("Update")
            btn.set_sensitive(True)
            self._show_error(f"Moon API call failed:\n{msg}")
            return False

        threading.Thread(target=_worker, daemon=True).start()

    def _moon_retrieved_description(self, data: dict[str, Any]) -> str:
        """Build the Moon Data group description, appending a formatted retrieved_at timestamp."""
        description = ""
        retrieved_raw = data.get("retrieved_at", "")
        if retrieved_raw:
            dt = self._parse_moon_dt(str(retrieved_raw).strip())
            if dt:
                date_part = f"{dt.day} {dt.strftime('%B %Y')}"
                time_part = dt.strftime("%I:%M %p").lstrip("0")
                return f"{description}Retrieved: {date_part} {time_part.upper()}"
        return description

    def _refresh_moon_data_values(self, data: dict[str, Any]) -> None:
        """Update the Moon Data value labels in-place without rebuilding the whole UI."""
        def _get(key: str) -> Any:
            if key == "phase_value":
                phase_details = data.get("phase_details")
                if isinstance(phase_details, dict):
                    return phase_details.get("phase_value")
                return data.get("phase_value")
            if key == "distance":
                position = data.get("position")
                if isinstance(position, dict):
                    return position.get("distance")
                return data.get("distance")
            return data.get(key)

        for key, val_label in self._moon_value_labels.items():
            val_label.set_label(self._format_moon_value(key, _get(key)))

        # Refresh the optional alert row
        if hasattr(self, "_moon_alert_row") and self._moon_alert_row is not None:
            tz_name = self._entries.get("TIMEZONE", None)
            tz_name = tz_name.display_value.strip() if tz_name else ""
            alert = self._compute_moon_alert(data, tz_name)
            if alert:
                self._moon_alert_label.set_label(alert)
                self._moon_alert_row.set_visible(True)
            else:
                self._moon_alert_row.set_visible(False)

        # Also refresh the group description with the new retrieved_at timestamp
        if self._moon_data_group:
            self._moon_data_group.set_description(self._moon_retrieved_description(data))

    def _build_moon_data_section(self) -> Adw.PreferencesGroup:
        """
        Load ~/.cache/weather/moon-data.json and display all fields
        in a compact two-column layout.
        Times are 12h with uppercase AM/PM, while dates retain proper casing.
        """
        group = Adw.PreferencesGroup()
        group.set_title("Moon Data")
        group.set_margin_top(4)

        # ── Update button in group header ─────────────────────────────────────
        update_btn = Gtk.Button(label="Update")
        update_btn.add_css_class("flat")
        update_btn.set_valign(Gtk.Align.CENTER)
        update_btn.set_tooltip_text("Fetch latest moon data from the API and save to moon-data.json")
        update_btn.connect("clicked", self._on_moon_update_clicked)
        group.set_header_suffix(update_btn)

        moon_path = Path.home() / ".cache" / "weather" / "moon-data.json"

        main_row = Adw.ActionRow()
        main_row.set_activatable(False)

        # Keep a reference so we can refresh values without rebuilding
        self._moon_value_labels: dict[str, Gtk.Label] = {}

        try:
            data: dict[str, Any] = json.loads(moon_path.read_text(encoding="utf-8"))
        except Exception as exc:
            main_row.set_title("Moon data unavailable")
            main_row.set_subtitle(str(exc))
            group.add(main_row)
            return group

        group.set_description(self._moon_retrieved_description(data))

        outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer_box.set_hexpand(True)
        outer_box.set_margin_start(16)
        outer_box.set_margin_end(16)
        outer_box.set_margin_top(12)
        outer_box.set_margin_bottom(12)

        left_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        right_grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        for grid in (left_grid, right_grid):
            grid.set_hexpand(True)
            grid.set_halign(Gtk.Align.FILL)

        def _get_moon_value(key: str) -> Any:
            """
            Resolve a display key to its value in the JSON.
            phase_value → data["phase_details"]["phase_value"]
            position    → data["position"]  (dict)
            distance    → data["position"]["distance"]
            Everything else → data[key]
            """
            if key == "phase_value":
                phase_details = data.get("phase_details")
                if isinstance(phase_details, dict):
                    return phase_details.get("phase_value")
                return data.get("phase_value")  # top-level fallback
            if key == "distance":
                position = data.get("position")
                if isinstance(position, dict):
                    return position.get("distance")
                return data.get("distance")
            return data.get(key)

        fields = list(self._MOON_FIELD_LABELS.items())
        for i, (key, label) in enumerate(fields):
            target = left_grid if i % 2 == 0 else right_grid
            row_idx = i // 2

            lbl = Gtk.Label(label=f"{label}:")
            lbl.set_halign(Gtk.Align.START)
            lbl.add_css_class("dim-label")

            val = Gtk.Label(label=self._format_moon_value(key, _get_moon_value(key)))
            val.set_halign(Gtk.Align.END)
            val.set_hexpand(True)
            val.set_selectable(False)

            # Store reference for in-place refresh
            self._moon_value_labels[key] = val

            target.attach(lbl, 0, row_idx, 1, 1)
            target.attach(val, 1, row_idx, 1, 1)

        vsep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        vsep.set_margin_start(24)
        vsep.set_margin_end(24)

        outer_box.append(left_grid)
        outer_box.append(vsep)
        outer_box.append(right_grid)

        # ── Optional alert row (separator + label) ────────────────────────────
        _tz_entry = self._entries.get("TIMEZONE", None)
        _tz_name  = _tz_entry.display_value.strip() if _tz_entry else ""
        alert_text = self._compute_moon_alert(data, _tz_name)

        alert_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        alert_sep.set_margin_start(16)
        alert_sep.set_margin_end(16)

        alert_label = Gtk.Label(label=alert_text or "")
        alert_label.set_halign(Gtk.Align.CENTER)
        alert_label.set_margin_top(8)
        alert_label.set_margin_bottom(8)
        alert_label.add_css_class("dim-label")

        alert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        alert_box.append(alert_sep)
        alert_box.append(alert_label)
        alert_box.set_visible(alert_text is not None)

        # Store references for in-place refresh
        self._moon_alert_row   = alert_box
        self._moon_alert_label = alert_label

        # Wrap grids + optional alert in a vertical container
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(outer_box)
        content_box.append(alert_box)

        main_row.set_child(content_box)
        group.add(main_row)

        return group

    def _build_preferences(self) -> None:
        """Rebuild preference groups from loaded entries."""

        # ── Clear existing UI ──────────────────────────────────────────────
        while (child := self._groups_box.get_first_child()):
            self._groups_box.remove(child)

        self._group_widgets.clear()
        self._rows.clear()

        # ── Build groups ───────────────────────────────────────────────────
        for group_name in GROUPS:
            group = Adw.PreferencesGroup()
            group.set_title(group_name)
            group.set_margin_top(4)

            rows: list[BaseRow] = []

            for schema in SCHEMA:
                if schema.group != group_name:
                    continue
                if schema.key not in self._entries:
                    continue

                entry = self._entries[schema.key]

                # Create row widget
                app = self.get_application()
                ip_store = getattr(app, "ip_store", None)
                tz_store = getattr(app, "tz_store", None)
                row = make_row(entry, self._on_entry_changed, ip_store, tz_store)

                # Add to group
                group.add(row)

                rows.append(row)
                self._rows[schema.key] = row

            # Only add group if it has rows
            if rows:
                self._groups_box.append(group)
                self._group_widgets[group_name] = (group, rows)

            # Inject Moon Data section immediately after Moon Phase group
            if group_name == "Moon Phase":
                self._moon_data_group = self._build_moon_data_section()
                self._groups_box.append(self._moon_data_group)

        # ── Apply dependency states AFTER UI is built ──────────────────────
        for master_key, dependent_keys in DEPENDENCIES.items():
            master_entry = self._entries.get(master_key)
            if not master_entry:
                continue

            is_enabled = master_entry.display_value.lower() == "true"

            for dep_key in dependent_keys:
                row = self._rows.get(dep_key)
                if row:
                    row.set_sensitive(is_enabled)

    # ── File Operations ───────────────────────────────────────────────────────

    def _on_open_clicked(self, *_: Any) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Open Config File")
        f = Gio.File.new_for_path(str(Path.home()))
        dialog.set_initial_folder(f)
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog: Gtk.FileDialog,
                         result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path())
        self._load_file(path)

    def _load_file(self, path: Path) -> None:
        try:
            self._entries = self._parser.load(path)
            self._config_path = path
            self._undo_stack.clear()
            # Snapshot original values so we can detect "back to unchanged"
            self._original_values = {k: e.raw_value for k, e in self._entries.items()}
            self._update_button_states()

            # Build UI first
            self._build_preferences()

            # apply dependency states AFTER UI is built
            for master_key in DEPENDENCIES:
                if master_key in self._entries:
                    self._update_dependent_states(master_key)

            self._path_label.set_label(str(path))
            self._banner.set_revealed(False)

            # Persist last opened file
            app = self.get_application()
            if hasattr(app, "save_last_opened"):
                app.save_last_opened(path)

        except OSError as exc:
            self._show_error(f"Could not open file:\n{exc}")

    def _on_save_clicked(self, *_: Any) -> None:
        if not self._config_path:
            self._show_error("No file is loaded. Please open a config file first.")
            return
        # Validate all
        errors: list[str] = []
        for key, entry in self._entries.items():
            err = self._validator.validate(entry)
            if err:
                errors.append(f"{entry.schema.label}: {err}")
        if errors:
            self._show_error("Validation errors:\n• " + "\n• ".join(errors))
            return
        # Backup then save
        backup = self._config_path.with_suffix(".bak")
        try:
            shutil.copy2(self._config_path, backup)

            self._parser.save(self._config_path, self._entries)

            # Restart GNOME Executor extension
            subprocess.run(
                ["gnome-extensions", "disable", "executor@raujonas.github.io"],
                check=True
            )
            subprocess.run(
                ["gnome-extensions", "enable", "executor@raujonas.github.io"],
                check=True
            )

            # Delete backup
            if backup.exists():
                backup.unlink()

            self._show_toast("Configuration saved successfully")

            # Advance the baseline so Save deactivates, but keep undo stack
            # intact so the user can still undo changes made before saving.
            self._original_values = {k: e.raw_value for k, e in self._entries.items()}
            self._update_button_states()

        except subprocess.CalledProcessError as exc:
            self._show_error(f"Extension reload failed:\n{exc}")

        except OSError as exc:
            self._show_error(f"Save failed:\n{exc}")

    def _on_reset_clicked(self, *_: Any) -> None:
        if not self._config_path:
            return
        self._load_file(self._config_path)
        self._show_toast("All fields reset to saved values")

    # ── Undo ──────────────────────────────────────────────────────────────────

    def _on_entry_changed(self, entry: ConfigEntry) -> None:
        key = entry.schema.key

        # Push to undo stack only on first edit of this key in this session.
        # We store the *original loaded* value so undo always reverts to it.
        if not any(k == key for k, _ in self._undo_stack):
            original = self._original_values.get(key, entry.raw_value)
            self._undo_stack.append((key, original))

        self._update_dependent_states(key)
        self._update_button_states()

    def _on_undo_clicked(self, *_: Any) -> None:
        if not self._undo_stack:
            return
        key, old_raw = self._undo_stack.pop()
        if key in self._entries:
            self._entries[key].raw_value = old_raw
            self._entries[key].modified = False
            if key in self._rows:
                self._rows[key].reset()
        self._undo_btn.set_sensitive(bool(self._undo_stack))
        self._update_button_states()

    # ── Search ────────────────────────────────────────────────────────────────

    def _on_search_changed(self, widget: Gtk.SearchEntry) -> None:
        self._search_text = widget.get_text().lower()
        for group_name, (group_widget, rows) in self._group_widgets.items():
            group_visible = False
            for row in rows:
                visible = (
                    not self._search_text
                    or self._search_text in row.entry.schema.label.lower()
                    or self._search_text in row.entry.schema.key.lower()
                    or self._search_text in row.entry.schema.description.lower()
                )
                row.set_visible(visible)
                if visible:
                    group_visible = True
            group_widget.set_visible(group_visible)

    # ── Notifications ─────────────────────────────────────────────────────────

    def _show_toast(self, message: str) -> None:
        """Show a transient toast notification."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(3)
        self._toast_overlay.add_toast(toast)

    def _show_error(self, message: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Error",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.connect("response", lambda d, _r: d.close())
        dialog.present()


# ─── Application Entry Point ─────────────────────────────────────────────────


class WeatherConfigApp(Adw.Application):
    """GNOME application wrapper with persistence + auto-detection."""

    SETTINGS_SCHEMA = "com.weather.ConfigEditor"

    def __init__(self) -> None:
        super().__init__(
            application_id="com.weather.ConfigEditor",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.connect("activate", self._on_activate)

        # GSettings (requires schema installed, fallback handled)
        try:
            self.settings = Gio.Settings.new(self.SETTINGS_SCHEMA)
        except Exception:
            self.settings = None
        
        self.ip_store = IpMappingStore(self.settings)
        self.tz_store = TimezoneStore()   # loaded lazily on first row construction

    def _on_activate(self, app: Adw.Application) -> None:
        win = WeatherConfigWindow(app)
        win.present()

        # Try load in priority order
        config_path = (
            self._get_last_opened_file()
            or self._get_local_config()
            or self._get_home_config()
        )

        if config_path:
            win._load_file(config_path)

    # ── Config Detection ─────────────────────────────────────────────

    def _get_last_opened_file(self) -> Optional[Path]:
        """Restore last opened config from GSettings."""
        if not self.settings:
            return None

        path_str = self.settings.get_string("last-config-path")
        if path_str:
            path = Path(path_str)
            if path.exists():
                return path
        return None

    def _get_local_config(self) -> Optional[Path]:
        """Detect .weather_config in script directory."""
        script_dir = Path(__file__).resolve().parent
        local_config = script_dir / ".weather_config"
        return local_config if local_config.exists() else None

    def _get_home_config(self) -> Optional[Path]:
        """Fallback to ~/.weather_config."""
        home_config = Path.home() / ".weather_config"
        return home_config if home_config.exists() else None

    # ── Save Last Opened File ────────────────────────────────────────

    def save_last_opened(self, path: Path) -> None:
        if self.settings:
            self.settings.set_string("last-config-path", str(path))


if __name__ == "__main__":
    import sys
    app = WeatherConfigApp()
    sys.exit(app.run(sys.argv))
