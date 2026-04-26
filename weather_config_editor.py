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
# Exec=sh -c 'GSETTINGS_SCHEMA_DIR="$HOME/.local/share/bin/linux-weather-bar" python3 "$HOME/.local/share/bin/linux-weather-bar/weather_config_editor.py"'
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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402


# ─── Data Model ──────────────────────────────────────────────────────────────


class VarType(Enum):
    """Variable input types for schema-driven UI rendering."""
    STRING = auto()
    INTEGER = auto()
    FLOAT = auto()
    BOOLEAN = auto()
    ENUM = auto()
    NUMERIC_OR_SENTINEL = auto()   # Special: numeric OR sentinel string


@dataclass
class VarSchema:
    """Schema definition for a single config variable."""
    key: str
    label: str
    var_type: VarType
    description: str = ""
    default: Any = None
    choices: list[str] = field(default_factory=list)          # for ENUM
    # for NUMERIC_OR_SENTINEL
    sentinel_label: str = ""
    sentinel_value: str = ""                                   # e.g. "moonrise"
    group: str = "General"
    readonly: bool = False                                     # bash `readonly`
    # mask when unfocused
    secret: bool = False


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
        "SHOW_APSIDAL_MOON_EVENTS",
        "SUPPRESS_NOT_VISIBLE_NIGHT_APSIDAL_MOON_EVENTS",
    ],
    "SHOW_APSIDAL_MOON_EVENTS": [
        "SUPPRESS_NOT_VISIBLE_NIGHT_APSIDAL_MOON_EVENTS",
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
    VarSchema("FEELS_LIKE_THRESHOLD",      "Feels-Like Threshold",        VarType.NUMERIC_OR_SENTINEL,
              "Minimum temperature difference (°C) to display 'feels like'", default=10,
              sentinel_label="Disable", sentinel_value="disable",
              readonly=True, group="Configuration"),

    VarSchema("SHOW_RAIN_FORECAST",        "Rain Forecast",            VarType.BOOLEAN,
              "Show rain warnings in the forecast", readonly=True, group="Configuration"),

    VarSchema("RAIN_FORECAST_THRESHOLD",   "Minimum Precipitation Threshold",       VarType.FLOAT,
              "Minimum precipitation probability (0.00 – 1.00) to trigger a warning",
              default=0.7, readonly=True, group="Configuration"),

    VarSchema("RAIN_FORECAST_WINDOW",      "Rain Forecast Lookahead Window",       VarType.INTEGER,
              "How many hours ahead to check for rain", default=3, readonly=True,
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

    VarSchema("MOONRISE_WARNING_THRESHOLD",               "Moonrise Lead Time",             VarType.NUMERIC_OR_SENTINEL,
              "Minutes before moonrise to alert, or immediately after sunset",
              sentinel_label="After Sunset", sentinel_value="sunset",
              readonly=True, group="Moonrise &amp; Moonset"),

    VarSchema("MOONSET_WARNING_THRESHOLD",                "Moonset Lead Time",              VarType.NUMERIC_OR_SENTINEL,
              "Minutes before moonset to alert, or immediately after sunset",
              sentinel_label="After Sunset", sentinel_value="sunset",
              readonly=True, group="Moonrise &amp; Moonset"),

    VarSchema("SHOW_MOONRISE_MOONSET_DURING_DAYTIME", "Show During Daytime",               VarType.BOOLEAN,
              "Include moonrise/moonset times that fall during daylight", readonly=True,
              group="Moonrise &amp; Moonset"),

    VarSchema("SUPPRESS_NOT_VISIBLE_MOONRISE_MOONSET", "Suppress Non-Visible Moonrise/Moonset", VarType.BOOLEAN,
              "Suppress moonrise/moonset display when the moon is too dim to be visible",
              readonly=True, group="Moonrise &amp; Moonset"),

    VarSchema("SHOW_MOONRISE_MOONSET_DURING_RAIN",        "Show While Raining",             VarType.BOOLEAN,
              "Display even when it's currently raining", readonly=True,
              group="Moonrise &amp; Moonset"),

    VarSchema("SHOW_MOONRISE_MOONSET_WITH_RAIN_FORECAST", "Show When Rain Expected",        VarType.BOOLEAN,
              "Display even when rain is in the forecast", readonly=True,
              group="Moonrise &amp; Moonset"),


    # ── Moon Phase ────────────────────────────────────────────────────────────
    VarSchema("MOON_PHASE_ENABLED",               "Moon Phase",                    VarType.BOOLEAN,
              "Show the current moon phase", readonly=True, group="Moon Phase"),

    VarSchema("MOON_DATA_CACHE_MAX_AGE",                "Moon Data Cache Max Age",              VarType.INTEGER,
              "Maximum age of cached moon data in hours during active moon window", default=2, readonly=True,
              group="Moon Phase"),

    VarSchema("MOON_PHASE_WINDOW_START",           "Display Window Start",         VarType.NUMERIC_OR_SENTINEL,
              "Minutes after sunset/moonrise, or immediately after moonrise",
              sentinel_label="Moonrise", sentinel_value="moonrise",
              readonly=True, group="Moon Phase"),

    VarSchema("MOON_PHASE_WINDOW_DURATION",        "Display Window End",           VarType.NUMERIC_OR_SENTINEL,
              "Window duration in minutes, or until moonset",
              sentinel_label="Moonset", sentinel_value="moonset",
              readonly=True, group="Moon Phase"),

    VarSchema("SHOW_MOONPHASE_DURING_DAYTIME",       "Show During Daytime",        VarType.BOOLEAN,
              "Display moon phase regardless of daylight hours", readonly=True,
              group="Moon Phase"),

    VarSchema("SUPPRESS_NOT_VISIBLE_MOONPHASE", "Suppress Non-Visible Moon Phases", VarType.BOOLEAN,
              "Suppress moon phase display when the moon is too dim to be visible",
              readonly=True, group="Moon Phase"),

    VarSchema("MOON_PHASE_SHOW_DURING_RAIN",       "Show While Raining",           VarType.BOOLEAN,
              "Display even when it's currently raining", readonly=True,
              group="Moon Phase"),

    VarSchema("MOON_PHASE_SHOW_WITH_RAIN_FORECAST", "Show When Rain Expected",      VarType.BOOLEAN,
              "Display even when rain is in the forecast", readonly=True,
              group="Moon Phase"),

    VarSchema("SHOW_MOONPHASE_BILINGUAL",           "Bilingual Phase Name",        VarType.BOOLEAN,
              "Show phase name in both English and Bengali", readonly=True,
              group="Moon Phase"),

    VarSchema("SHOW_MOONPHASE_BENGALI",             "Bengali Phase Name",          VarType.BOOLEAN,
              "Show phase name in Bengali only", readonly=True, group="Moon Phase"),

    VarSchema("SHOW_APSIDAL_MOON_EVENTS",             "Apsidal Moon Events",          VarType.BOOLEAN,
              "Show supermoon, super new moon, or micromoon label when applicable",
              readonly=True, group="Moon Phase"),

    VarSchema("SUPPRESS_NOT_VISIBLE_NIGHT_APSIDAL_MOON_EVENTS", "Suppress Non-Visible Night Apsidal Moon Events", VarType.BOOLEAN,
              "Show apsidal moon events only when the Moon is visibly above the horizon at night",
              readonly=True, group="Moon Phase"),

    # ── API Keys ──────────────────────────────────────────────────────────────
    VarSchema("API_KEY",       "OpenWeatherMap API Key",              VarType.STRING,
              "API key from openweathermap.org", readonly=True, group="API Keys", secret=True),

    VarSchema("API_KEY_TYPE",  "OpenWeatherMap Plan",                 VarType.ENUM,
              "Your OpenWeatherMap subscription tier",
              choices=["FREE", "PRO"], default="FREE", readonly=True, group="API Keys"),

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
    """A unique (name, lat, lon) location from location_mappings.csv."""
    name: str
    lat: str
    lon: str

    @property
    def display_label(self) -> str:
        return f"{self.name.title()} ({self.lat},{self.lon})"

    @property
    def location_value(self) -> str:
        return f"lat={self.lat}&lon={self.lon}"


class LocationMappingStore:
    """
    Loads location_mappings.csv, deduplicates by (NAME, LATITUDE, LONGITUDE),
    and persists the last used CSV path via GSettings (same key namespace).
    """

    CSV_FILENAME = "location_mappings.csv"

    def __init__(self, settings: Optional[Gio.Settings]) -> None:
        self._settings = settings

    # ── Discovery (mirrors WeatherConfigApp._get_local_config pattern) ────────

    def find_default_csv(self) -> Optional[Path]:
        """Check script directory for location_mappings.csv (auto-load, same as .weather_config)."""
        candidate = Path(__file__).resolve().parent / self.CSV_FILENAME
        return candidate if candidate.exists() else None

    def get_last_csv(self) -> Optional[Path]:
        """Restore last used CSV from GSettings."""
        if not self._settings:
            return None
        path_str = self._settings.get_string("last-location-mapping-path")
        if path_str:
            p = Path(path_str)
            if p.exists():
                return p
        return None

    def save_last_csv(self, path: Path) -> None:
        if self._settings:
            self._settings.set_string("last-location-mapping-path", str(path))

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
        """Look for zone.tab next to the script (same discovery pattern as location_mappings.csv)."""
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


# ─── Searchable DropDown ─────────────────────────────────────


def _find_search_entry(widget: Gtk.Widget) -> Optional[Gtk.SearchEntry]:
    """Recursively find the first GtkSearchEntry inside *widget*."""
    if isinstance(widget, Gtk.SearchEntry):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find_search_entry(child)
        if found:
            return found
        child = child.get_next_sibling()
    return None


class SearchableDropDown:
    """
    GTK4-native DropDown with built-in substring search/filtering.

    Wraps Gtk.DropDown + Gtk.StringFilter + Gtk.FilterListModel with a
    SignalListItemFactory that renders each item as a plain left-aligned label.

    Both ``TimezoneRow`` and ``LocationRow`` use this class so all searchable-
    dropdown machinery is defined exactly once (DRY).

    Features:
    - Fixed, consistent width (does not resize based on selected item)
    - Text truncation with ellipsis for items longer than fixed width
    - Compact dropdown button that remains consistent
    - Stable layout with no horizontal shifting

    Parameters
    ----------
    items:
        Flat list of strings to display.
    on_selected:
        Called with the chosen string whenever the user picks an item.
    validate:
        Optional predicate; receives the current search text and returns True
        when it is acceptable.  Drives the "error" CSS class on the search
        entry for live visual feedback.  Defaults to always-valid.
    fixed_width:
        Optional fixed width in pixels. If None, calculates from average item length.
    """

    # Note: GTK4 doesn't use CSS for width constraints via IDs.
    # We use set_width_request() directly on the widget instead.

    _instance_counter = 0

    def __init__(
        self,
        items: list[str],
        on_selected: Callable[[str], None],
        validate: Optional[Callable[[str], bool]] = None,
        fixed_width: Optional[int] = None,
    ) -> None:
        self._items = items
        self._on_selected_cb = on_selected
        self._validate = validate if validate is not None else (lambda _: True)
        self._search_entry: Optional[Gtk.SearchEntry] = None
        self._fixed_width = fixed_width or self._calculate_optimal_width(items)

        # Generate unique ID for CSS targeting
        SearchableDropDown._instance_counter += 1
        self._dropdown_id = f"sd-{SearchableDropDown._instance_counter}"

        # ── Model ─────────────────────────────────────────────────────────────
        self._string_list = Gtk.StringList.new(items)

        self._filter = Gtk.StringFilter.new(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._filter.set_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._filter.set_ignore_case(True)

        filtered_model = Gtk.FilterListModel.new(
            self._string_list, self._filter)
        selection = Gtk.SingleSelection.new(filtered_model)
        selection.set_autoselect(False)

        # ── Factory ───────────────────────────────────────────────────────────
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)

        # ── Widget ────────────────────────────────────────────────────────────
        self._widget = Gtk.DropDown.new(selection, None)
        self._widget.set_factory(factory)
        self._widget.set_enable_search(True)
        # Don't expand; use fixed width instead
        self._widget.set_hexpand(False)
        self._widget.set_valign(Gtk.Align.CENTER)
        self._widget.set_name(self._dropdown_id)

        # Apply fixed width constraints via size request and CSS
        self._widget.set_size_request(self._fixed_width, -1)
        self._apply_width_constraints()

        self._widget.connect("notify::selected-item",
                             self._on_dropdown_selected)
        self._widget.connect("realize", self._on_realize)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def widget(self) -> Gtk.DropDown:
        """The underlying Gtk.DropDown; append this to your container."""
        return self._widget

    @property
    def search_entry(self) -> Optional[Gtk.SearchEntry]:
        """The DropDown's internal search entry (available after realise)."""
        return self._search_entry

    def select(self, value: str) -> None:
        """
        Programmatically select the item whose string equals *value* (exact).
        Clears any active filter first so StringList indices align correctly.
        """
        if not value:
            return
        self._filter.set_search("")
        if self._search_entry is not None:
            self._search_entry.handler_block_by_func(self._on_search_changed)
            self._search_entry.set_text("")
            self._search_entry.handler_unblock_by_func(self._on_search_changed)
        n = self._string_list.get_n_items()
        for i in range(n):
            item = self._string_list.get_item(i)
            if item and item.get_string() == value:
                self._widget.set_selected(i)
                return

    def set_error(self, has_error: bool) -> None:
        """Apply or remove the 'error' CSS class on the search entry."""
        if self._search_entry is None:
            return
        if has_error:
            self._search_entry.add_css_class("error")
        else:
            self._search_entry.remove_css_class("error")

    # ── Width management ──────────────────────────────────────────────────────

    @staticmethod
    def _calculate_optimal_width(items: list[str]) -> int:
        """
        Calculate fixed width based on average item text length.

        Uses Pango metrics to estimate width from character count:
        - Assumes monospace or proportional font rendering
        - Adds padding for button chrome + search icon

        Returns width in pixels (minimum 200px for usability).
        """
        if not items:
            return 200

        # Calculate average text length
        avg_length = sum(len(item) for item in items) / len(items)

        # Rough estimate: ~7-8 pixels per character in GTK4 default font
        # Adjust multiplier based on your font preferences
        char_width = 7.5
        text_width = int(avg_length * char_width)

        # Add padding for dropdown button chrome and icon space
        padding = 40
        width = text_width + padding

        # Enforce minimum and maximum for usability
        return max(200, min(width, 400))

    def _apply_width_constraints(self) -> None:
        """
        Apply width constraints to the dropdown widget using GTK4 API.
        Uses set_size_request() to maintain consistent, fixed button size.
        """
        # GTK4 native way: set size request directly on widget
        # This avoids CSS parsing issues and deprecated API calls
        # set_size_request(width, height) where -1 means natural size
        self._widget.set_size_request(self._fixed_width, -1)

    # ── GTK4 factory callbacks ────────────────────────────────────────────────

    def _on_factory_setup(self, _factory: Gtk.SignalListItemFactory,
                          list_item: Gtk.ListItem) -> None:
        label = Gtk.Label(xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(40)
        list_item.set_child(label)

    def _on_factory_bind(self, _factory: Gtk.SignalListItemFactory,
                         list_item: Gtk.ListItem) -> None:
        obj = list_item.get_item()
        label: Gtk.Label = list_item.get_child()
        if obj is not None:
            label.set_label(obj.get_string())

    # ── Internal signal handlers ──────────────────────────────────────────────

    def _on_realize(self, widget: Gtk.DropDown) -> None:
        """Wire up the DropDown's internal search entry after realisation."""
        se = _find_search_entry(widget)
        if se is not None:
            self._search_entry = se
            se.connect("search-changed", self._on_search_changed)

    def _on_search_changed(self, search_entry: Gtk.SearchEntry) -> None:
        """
        Drives the StringFilter and live error styling as the user types.
        Never writes to any ConfigEntry — that is _on_dropdown_selected's job.
        Partial search text (e.g. "asia/dha") must never become a saved value.
        """
        text = search_entry.get_text().strip()
        self._filter.set_search(text)
        is_valid = self._validate(text)
        if is_valid:
            search_entry.remove_css_class("error")
        else:
            search_entry.add_css_class("error")

    def _on_dropdown_selected(self, dropdown: Gtk.DropDown,
                              _param: object) -> None:
        """Fires when the user picks an item; clears error and invokes callback."""
        obj = dropdown.get_selected_item()
        if obj is None:
            return
        text: str = obj.get_string()
        if not text:
            return
        if self._search_entry is not None:
            self._search_entry.remove_css_class("error")
        self._on_selected_cb(text)


# ─── Rain Forecast Service ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForecastEntry:
    """A single rain forecast slot parsed from OpenWeather forecast-data.json."""
    dt: int                  # Unix epoch
    dt_txt: str              # "YYYY-MM-DD HH:MM:SS" (for display)
    temp: float              # °C
    feels_like: float        # °C
    description: str         # e.g. "light rain"
    pop: float               # 0.0–1.0 probability of precipitation


class RainForecastService:
    """
    Parses, caches, and filters rain forecast data from
    ~/.cache/weather/forecast-data.json.

    Responsibilities (Single Responsibility):
      • File reading & JSON parsing
      • Cache invalidation (by file mtime)
      • Filtering by pop threshold and lookahead count

    The service is stateless between calls except for the parsed cache,
    making it independently testable without any GTK dependency.
    """

    def __init__(self) -> None:
        self._forecast_path = Path.home() / ".cache" / "weather" / "forecast-data.json"
        self._cached_entries: list[ForecastEntry] = []
        self._cached_mtime: Optional[float] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_rain_forecasts(
        self,
        threshold: float,
        lookahead: int,
    ) -> list[ForecastEntry]:
        """
        Return up to *lookahead* upcoming rain entries with pop >= threshold,
        sorted by earliest occurrence first.

        Re-reads the file only when mtime has changed since the last call.
        Re-filters always (threshold may change between calls without a file
        change).
        """
        self._refresh_cache_if_stale()
        return self._filter(self._cached_entries, threshold, lookahead)

    def load_error(self) -> Optional[str]:
        """Return a human-readable error string if the file is unreadable."""
        try:
            self._forecast_path.stat()
            json.loads(self._forecast_path.read_text(encoding="utf-8"))
            return None
        except FileNotFoundError:
            return "forecast-data.json not found"
        except (json.JSONDecodeError, ValueError) as exc:
            return f"forecast-data.json is invalid: {exc}"
        except Exception as exc:
            return str(exc)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _refresh_cache_if_stale(self) -> None:
        """Reload and re-parse file only when mtime changed."""
        try:
            mtime = self._forecast_path.stat().st_mtime
        except Exception:
            self._cached_entries = []
            self._cached_mtime = None
            return

        if mtime == self._cached_mtime:
            return  # Cache still valid

        try:
            raw = json.loads(self._forecast_path.read_text(encoding="utf-8"))
            self._cached_entries = self._parse(raw)
            self._cached_mtime = mtime
        except Exception:
            self._cached_entries = []
            self._cached_mtime = None

    @staticmethod
    def _parse(raw: dict[str, Any]) -> list[ForecastEntry]:
        """Convert raw OpenWeather forecast JSON into a list of ForecastEntry."""
        entries: list[ForecastEntry] = []
        for item in raw.get("list", []):
            try:
                main = item["main"]
                weather = item["weather"][0]
                entry = ForecastEntry(
                    dt=int(item["dt"]),
                    dt_txt=str(item.get("dt_txt", "")),
                    temp=float(main["temp"]),
                    feels_like=float(main["feels_like"]),
                    description=str(weather.get("description", "")).title(),
                    pop=float(item.get("pop", 0.0)),
                )
                entries.append(entry)
            except (KeyError, ValueError, TypeError):
                continue  # Skip malformed slots silently
        return entries

    @staticmethod
    def _filter(
        entries: list[ForecastEntry],
        threshold: float,
        lookahead: int,
    ) -> list[ForecastEntry]:
        """
        Filter entries with pop >= threshold, keep only future timestamps,
        sort earliest-first, and return at most *lookahead* results.

        This is the single authoritative filter — never duplicated in the UI.
        """
        now_ts = int(datetime.now().timestamp())
        result = [
            e for e in entries
            if e.pop >= threshold and e.dt >= now_ts
        ]
        result.sort(key=lambda e: e.dt)
        return result[:max(0, lookahead)]


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
            val = m.group("value").split(
                "#")[0].strip()   # strip inline comment
            if key in SCHEMA_MAP:
                entries[key] = ConfigEntry(
                    schema=SCHEMA_MAP[key], raw_value=val)
        # Fill missing keys with defaults
        for schema in SCHEMA:
            if schema.key not in entries:
                default = str(
                    schema.default) if schema.default is not None else ""
                entries[schema.key] = ConfigEntry(
                    schema=schema, raw_value=default)
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

    def validate(self, entry: ConfigEntry, all_entries: Optional[dict[str, ConfigEntry]] = None) -> str:
        schema = entry.schema
        val = entry.display_value
        vt = schema.var_type

        if vt == VarType.INTEGER:
            try:
                int_val = int(val)
                # Special constraint: RAIN_FORECAST_WINDOW minimum 3 hours when API_KEY_TYPE is FREE
                if schema.key == "RAIN_FORECAST_WINDOW" and all_entries is not None:
                    api_type_entry = all_entries.get("API_KEY_TYPE")
                    if api_type_entry and api_type_entry.display_value == "FREE":
                        if int_val < 3:
                            return "FREE plan requires minimum 3-hour forecast window (3-hourly data)"
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
        elif vt == VarType.NUMERIC_OR_SENTINEL:
            if val != schema.sentinel_value:
                try:
                    int(val)
                except ValueError:
                    return f"Must be a number or \"{schema.sentinel_value}\""
        return ""


# ─── Generic File Data Monitor Base Class ────────────────────────────────────


class FileDataMonitor:
    """
    Abstract base for monitoring JSON data files.

    Responsibilities (SRP):
      • File watching: Detects file changes via Gio.FileMonitor
      • Time-based updates: Recomputes time-sensitive values via GLib.timeout
      • Callback dispatch: Notifies subscribers when data or time changes

    Subclasses must define:
      • get_file_path(): Returns Path to monitor
      • _load_data(): Loads and parses file, returns {} on error

    Error handling: Silently tolerates missing files or invalid JSON.
    """

    def __init__(self) -> None:
        self._file_monitor: Optional[Gio.FileMonitor] = None
        self._monitor_signal_id: Optional[int] = None
        self._timeout_id: Optional[int] = None
        self._data: dict[str, Any] = {}
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []

    def get_file_path(self) -> Path:
        """Return the path to the monitored file. MUST be overridden by subclass."""
        raise NotImplementedError

    def _load_data(self) -> dict[str, Any]:
        """Load and parse the file. Return {} on error. Can be overridden by subclass."""
        try:
            return json.loads(self.get_file_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def add_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register callback to be invoked when data or time changes."""
        self._callbacks.append(callback)

    def start_watching(self) -> None:
        """Start file monitoring and periodic timer."""
        if self._file_monitor is not None:
            return  # Already started

        # Load initial data
        self._data = self._load_data()
        self._dispatch_callbacks()

        # Set up file monitor
        file_path = self.get_file_path()
        gfile = Gio.File.new_for_path(str(file_path.parent))
        try:
            self._file_monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            self._monitor_signal_id = self._file_monitor.connect(
                "changed", self._on_file_changed)
        except Exception:
            # Monitor setup failed; timeout alone will keep updates flowing
            self._file_monitor = None

        # Set up periodic timer (every 1 second)
        self._timeout_id = GLib.timeout_add_seconds(1, self._on_timeout)

    def stop_watching(self) -> None:
        """Stop file monitoring and timer."""
        if self._file_monitor is not None and self._monitor_signal_id is not None:
            self._file_monitor.disconnect(self._monitor_signal_id)
            self._monitor_signal_id = None
            self._file_monitor = None

        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

        self._callbacks.clear()
        self._data.clear()

    def _on_file_changed(self, monitor: Gio.FileMonitor, file: Gio.File,
                         other_file: Optional[Gio.File],
                         event_type: Gio.FileMonitorEvent) -> None:
        """Fired when a file in the monitored directory changes."""
        # Ignore non-CHANGED events (CREATED, RENAMED, etc.)
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            return

        # Check if this is our target file
        if file.get_path() != str(self.get_file_path()):
            return

        # Reload data and dispatch
        self._data = self._load_data()
        self._dispatch_callbacks()

    def _on_timeout(self) -> bool:
        """Called every 1 second; recompute time-sensitive values."""
        # Even if file content hasn't changed, time-based values need recomputation.
        self._dispatch_callbacks()
        return True  # Keep firing

    def _dispatch_callbacks(self) -> None:
        """Invoke all registered callbacks with current data."""
        for callback in self._callbacks:
            try:
                callback(self._data)
            except Exception:
                pass  # Silently ignore callback errors

    def get_data(self) -> dict[str, Any]:
        """Return currently cached data dict."""
        return self._data.copy()


# ─── Moon Data Live Monitor (extends generic FileDataMonitor) ────────────────


class MoonDataMonitor(FileDataMonitor):
    """
    Watches ~/.cache/weather/moon-data.json for changes and computes live
    values (like lunar window progress) that update every second.

    Extends FileDataMonitor for file watching and callback dispatch,
    delegating all monitor logic to the parent class (DRY principle).
    """

    def get_file_path(self) -> Path:
        """Return path to moon-data.json."""
        return Path.home() / ".cache" / "weather" / "moon-data.json"


# ─── Rain Forecast Live Monitor (extends generic FileDataMonitor) ────────────


class RainForecastMonitor(FileDataMonitor):
    """
    Watches ~/.cache/weather/forecast-data.json for changes and triggers
    callbacks to update the rain forecast UI in real-time.

    Extends FileDataMonitor for file watching and callback dispatch,
    delegating all monitor logic to the parent class (DRY principle).

    Unlike MoonDataMonitor, this does NOT use periodic timeout updates
    since rain forecast has no time-sensitive values. Only file changes
    trigger callbacks (file monitor only, no timeout).
    """

    def get_file_path(self) -> Path:
        """Return path to forecast-data.json."""
        return Path.home() / ".cache" / "weather" / "forecast-data.json"

    def start_watching(self) -> None:
        """
        Start file monitoring only (no periodic timeout).
        Rain forecast has no time-sensitive values, so file monitor alone is sufficient.
        """
        if self._file_monitor is not None:
            return  # Already started

        # Load initial data
        self._data = self._load_data()
        self._dispatch_callbacks()

        # Set up file monitor only (skip timeout since no time-sensitive values)
        file_path = self.get_file_path()
        gfile = Gio.File.new_for_path(str(file_path.parent))
        try:
            self._file_monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            self._monitor_signal_id = self._file_monitor.connect(
                "changed", self._on_file_changed)
        except Exception:
            # Monitor setup failed; gracefully degrade
            self._file_monitor = None


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

        # Apply fixed width for consistency with searchable dropdowns
        self._apply_enum_width(choices)

        self._dropdown.connect("notify::selected", self._on_selected)
        self.add_suffix(self._dropdown)

    def _apply_enum_width(self, choices: list[str]) -> None:
        """
        Apply fixed width constraints to enum dropdown.
        Calculates width from average choice text length.
        """
        # Calculate average width similarly to SearchableDropDown
        avg_length = sum(len(c) for c in choices) / \
            len(choices) if choices else 0
        char_width = 7.5
        text_width = int(avg_length * char_width)
        padding = 40
        fixed_width = max(200, min(text_width + padding, 400))

        # Set minimum width
        self._dropdown.set_size_request(fixed_width, -1)
        self._dropdown.set_hexpand(False)

    def _on_selected(self, widget: Gtk.DropDown, _param: Any) -> None:
        idx = widget.get_selected()
        choices = self.entry.schema.choices
        if 0 <= idx < len(choices):
            self.entry.display_value = choices[idx]
            self._notify_change()

    def reset(self) -> None:
        choices = self.entry.schema.choices
        cur = self.entry.display_value
        self._dropdown.set_selected(
            choices.index(cur) if cur in choices else 0)


class LocationRow(BaseRow):
    """
    LOCATION row with:
    - Searchable preset dropdown (via SearchableDropDown) loaded from
      location_mappings.csv — mirrors TimezoneRow's search UX exactly.
    - Custom checkbox to reveal manual lat/lon entries.
    - Pin button to open Google Maps.

    When no CSV is available the checkbox and dropdown are hidden and Custom
    mode is forced so the manual entries are always shown.
    """

    def __init__(self, entry: ConfigEntry,
                 on_change: Callable[[ConfigEntry], None],
                 location_store: "LocationMappingStore") -> None:
        super().__init__(entry, on_change)

        self._location_store = location_store
        self._locations: list[LocationEntry] = []
        self._sdd: Optional[SearchableDropDown] = None

        lat, lon = self._parse_location(entry.display_value)

        # ── Layout ────────────────────────────────────────────────────────────
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_box.set_valign(Gtk.Align.CENTER)

        # Slot that holds the SearchableDropDown widget once the CSV is loaded.
        # Using a wrapper Box avoids rebuilding the whole layout when the CSV
        # is absent (the slot simply stays empty and invisible).
        self._dropdown_slot = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._dropdown_slot.set_hexpand(True)

        # Manual lat/lon (inline, hidden by default)
        self._manual_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
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

        row_box.append(self._dropdown_slot)
        row_box.append(self._manual_box)
        row_box.append(self._custom_check)
        row_box.append(self._pin_btn)

        self.add_suffix(row_box)

        # ── Load CSV and initialise state ─────────────────────────────────────
        self._load_locations()
        self._sync_initial_state(lat, lon)

    # ── CSV loading ───────────────────────────────────────────────────────────

    def _load_locations(self) -> None:
        csv_path = self._location_store.resolve_csv()
        if csv_path:
            try:
                self._locations = self._location_store.load(csv_path)
                self._location_store.save_last_csv(csv_path)
            except Exception:
                self._locations = []
        else:
            self._locations = []

        if self._locations:
            labels = [loc.display_label for loc in self._locations]
            self._sdd = SearchableDropDown(
                items=labels,
                on_selected=self._on_location_selected,
            )
            self._dropdown_slot.append(self._sdd.widget)

    # ── State sync ────────────────────────────────────────────────────────────

    def _sync_initial_state(self, lat: str, lon: str) -> None:
        """On load: match current lat/lon to a preset, else enable Custom."""
        matched_idx = next(
            (i for i, loc in enumerate(self._locations)
             if loc.lat == lat and loc.lon == lon),
            None,
        )
        if matched_idx is not None and self._sdd is not None:
            self._sdd.select(self._locations[matched_idx].display_label)
            self._set_custom_mode(False)
        else:
            self._set_custom_mode(True)

    def _set_custom_mode(self, custom: bool) -> None:
        """Toggle between searchable preset dropdown and manual entry."""
        self._custom_check.handler_block_by_func(self._on_custom_toggled)
        self._custom_check.set_active(custom)
        self._custom_check.handler_unblock_by_func(self._on_custom_toggled)

        has_presets = self._sdd is not None
        # Hide the whole dropdown slot when no CSV was loaded
        self._dropdown_slot.set_visible(has_presets and not custom)
        self._manual_box.set_visible(custom)
        # No point showing the checkbox if there are no presets to go back to
        self._custom_check.set_visible(has_presets)

    # ── Signals ───────────────────────────────────────────────────────────────

    def _on_custom_toggled(self, widget: Gtk.CheckButton) -> None:
        self._set_custom_mode(widget.get_active())
        if not widget.get_active():
            # Switching back to preset: re-apply the currently selected item
            if self._sdd is not None:
                obj = self._sdd.widget.get_selected_item()
                if obj is not None:
                    self._on_location_selected(obj.get_string())
        else:
            # Switching to custom: commit whatever is in the manual entries
            self._on_manual_changed()

    def _on_location_selected(self, label: str) -> None:
        """Called by SearchableDropDown when the user picks a preset location."""
        if self._custom_check.get_active():
            return
        loc = next(
            (l for l in self._locations if l.display_label == label), None)
        if loc is None:
            return
        self.entry.display_value = loc.location_value
        # Keep manual entries in sync so switching to Custom is seamless
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

    # ── Helpers ───────────────────────────────────────────────────────────────

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
    TIMEZONE row — searchable DropDown (via SearchableDropDown) when zone.tab
    is present, plain text entry (original StringRow behaviour) when it is not.

    DropDown mode:
    - Typing in the built-in search box filters the list via SUBSTRING match.
    - Selecting from the list sets the value immediately.
    - 'error' CSS class is applied while the typed text is not a valid timezone.
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
            self._entry: Optional[Gtk.Entry] = Gtk.Entry()
            self._entry.set_text(entry.display_value)
            self._entry.set_placeholder_text("e.g. Asia/Dhaka")
            self._entry.set_valign(Gtk.Align.CENTER)
            self._entry.set_hexpand(True)
            self._entry.connect("changed", self._on_entry_changed)
            self._sdd: Optional[SearchableDropDown] = None
            self.add_suffix(self._entry)
            self.set_activatable_widget(self._entry)
            return

        # ── GTK4-native searchable DropDown ───────────────────────────────────
        self._entry = None
        self._sdd = SearchableDropDown(
            items=self._all_timezones,
            on_selected=self._on_tz_selected,
            validate=lambda text: not text or text in self._all_timezones,
        )
        self.add_suffix(self._sdd.widget)
        self.set_activatable_widget(self._sdd.widget)
        self._sdd.select(entry.display_value)

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_tz_selected(self, text: str) -> None:
        """Called by SearchableDropDown when the user picks a timezone."""
        self.entry.display_value = text
        self._notify_change()

    def _on_entry_changed(self, widget: Gtk.Entry) -> None:
        """Plain entry handler used only when zone.tab is unavailable."""
        self.entry.display_value = widget.get_text()
        self._notify_change()

    # ── BaseRow interface ─────────────────────────────────────────────────────

    def reset(self) -> None:
        val = self.entry.display_value
        if self._sdd is not None:
            self._sdd.select(val)
            self._sdd.set_error(bool(val) and val not in self._all_timezones)
        elif self._entry is not None:
            self._entry.set_text(val)


class NumericOrSentinelRow(BaseRow):
    """
    Special row for NUMERIC_OR_SENTINEL variables.
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
             location_store: Optional["LocationMappingStore"] = None,
             tz_store: Optional["TimezoneStore"] = None) -> BaseRow:

    if entry.schema.key == "LOCATION":
        return LocationRow(entry, on_change, location_store or LocationMappingStore(None))

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

    if vt == VarType.NUMERIC_OR_SENTINEL:
        return NumericOrSentinelRow(entry, on_change)

    raise ValueError(f"Unknown VarType: {vt}")


# ─── Main Application Window ─────────────────────────────────────────────────


class WeatherConfigWindow(Adw.ApplicationWindow):
    """Main application window."""

    def _clamp_rain_forecast_if_needed(self, show_toast: bool = True) -> None:
        """
        Clamp RAIN_FORECAST_WINDOW to 3 if API_KEY_TYPE is FREE and value < 3.

        Scenario 1: If value < 3 → clamp to 3 and show "set to 3" message
        Scenario 2: If value >= 3 → keep as is and show "minimum is 3" message

        show_toast: If False, suppress the toast (avoid duplicate from side effects)
        """
        api_type = self._entries.get("API_KEY_TYPE")
        if not api_type or api_type.display_value != "FREE":
            return

        rain_window = self._entries.get("RAIN_FORECAST_WINDOW")
        if not rain_window:
            return

        try:
            current_val = int(rain_window.display_value)
            if current_val < 3:
                # Scenario 1: Value needs clamping
                rain_window.display_value = "3"
                if "RAIN_FORECAST_WINDOW" in self._rows:
                    self._rows["RAIN_FORECAST_WINDOW"].reset()
                if show_toast:
                    self._show_toast(
                        "Forecast window set to 3 hours (minimum for FREE plan)")
            else:
                # Scenario 2: Value already >= 3, just inform about the minimum
                if show_toast:
                    self._show_toast(
                        "Forecast window lower limit is now 3 hours (FREE plan)")
        except ValueError:
            pass

    def _update_dependent_states(self, changed_key: str) -> None:
        # Auto-clamp RAIN_FORECAST_WINDOW when API_KEY_TYPE changes (show toast)
        if changed_key == "API_KEY_TYPE":
            self._clamp_rain_forecast_if_needed(show_toast=True)
        # When RAIN_FORECAST_WINDOW changes directly, suppress toast to avoid duplicate
        elif changed_key == "RAIN_FORECAST_WINDOW":
            self._clamp_rain_forecast_if_needed(show_toast=False)

        if changed_key not in DEPENDENCIES:
            return

        master_value = self._entries[changed_key].display_value.lower(
        ) == "true"

        if changed_key in INVERSE_DEPENDENCIES:
            master_value = not master_value  # true → disable dependents

        for dep_key in DEPENDENCIES[changed_key]:
            if dep_key in self._rows:
                self._rows[dep_key].set_sensitive(master_value)

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app)
        self.set_title("Weather Config Editor")
        self.set_default_size(740, 900)
        self.set_size_request(740, 900)

        self._parser = ConfigParser()
        self._validator = Validator()
        self._config_path: Optional[Path] = None
        self._entries: dict[str, ConfigEntry] = {}
        self._rows: dict[str, BaseRow] = {}
        self._search_text: str = ""
        self._undo_stack: list[tuple[str, str]] = []   # (key, old_raw_value)
        # key → raw_value at load time
        self._original_values: dict[str, str] = {}
        # for in-place Moon Data refresh
        self._moon_value_labels: dict[str, Gtk.Label] = {}
        self._moon_data_group: Optional[Adw.PreferencesGroup] = None
        self._sun_data_group: Optional[Adw.PreferencesGroup] = None

        # ── Live moon data monitoring ──────────────────────────────────────
        self._moon_monitor = MoonDataMonitor()
        self._moon_monitor.add_callback(self._on_moon_data_updated)
        self._moon_alert_row: Optional[Gtk.Box] = None
        self._moon_alert_label: Optional[Gtk.Label] = None

        # ── Weather output section ─────────────────────────────────────────
        self._weather_output_group: Optional[Adw.PreferencesGroup] = None
        self._weather_output_label: Optional[Gtk.Label] = None

        # ── Rain Forecast section ──────────────────────────────────────────
        self._rain_forecast_service = RainForecastService()
        self._rain_forecast_group: Optional[Adw.PreferencesGroup] = None
        # Local UI threshold — initialised from RAIN_FORECAST_THRESHOLD on
        # first build; independent of global config thereafter.
        self._rain_forecast_threshold_ui: float = 0.7
        # Local UI lookahead count — always starts at 3, fully independent of
        # RAIN_FORECAST_WINDOW.
        self._rain_forecast_lookahead_ui: int = 3
        # Store forecast content container to update without destroying spinbuttons
        self._rain_forecast_content_row: Optional[Gtk.Widget] = None

        # ── Live rain forecast monitoring ──────────────────────────────────
        self._rain_forecast_monitor = RainForecastMonitor()
        self._rain_forecast_monitor.add_callback(
            self._on_rain_forecast_updated)

        self._build_ui()

        # Start monitoring when window is realized
        self.connect("realize", self._on_window_realized)
        self.connect("close-request", self._on_window_closed)

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

        self._main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
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

        # _path_label is assigned later by _build_weather_output_section
        # so that the path appears inside the weather output group.
        self._path_label: Optional[Gtk.Label] = None

        # Groups container
        self._groups_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._main_box.append(self._groups_box)

        self._group_widgets: dict[str,
                                  tuple[Adw.PreferencesGroup, list[BaseRow]]] = {}

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
            az_rad = float(str(value.get("azimuth", 0)))
            alt_rad = float(str(value.get("altitude", 0)))

            import math
            az_deg = math.degrees(az_rad) % 360
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
            return None

    @staticmethod
    def _resolve_tz(tz_name: str):
        """
        Return a tzinfo for *tz_name* (IANA string), trying zoneinfo then pytz.
        Returns None if the name is blank or unresolvable.
        """
        if not tz_name:
            return None
        try:
            import zoneinfo
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            pass
        try:
            import pytz  # type: ignore
            return pytz.timezone(tz_name)
        except Exception:
            return None

    @staticmethod
    def _format_moon_time(epoch_val: int, date_str: str, tz_name: str = "") -> str:
        """
        Format a moon epoch as a local time string in *tz_name*.

        If the epoch's calendar date (in the configured timezone) differs from
        *date_str* (``DD/MM/YYYY``), the date is appended: ``12:43 AM (25 April)``.

        Parameters
        ----------
        epoch_val:
            Unix timestamp for the moonrise or moonset event.
        date_str:
            The moon-data ``date`` field (``DD/MM/YYYY``) used as the
            reference day.
        tz_name:
            IANA timezone name (e.g. ``Asia/Dhaka``).  Required for a correct
            cross-date comparison -- without it the system timezone is used,
            which may produce the wrong calendar date.
        """
        tz = WeatherConfigWindow._resolve_tz(tz_name)
        dt = (datetime.fromtimestamp(epoch_val, tz=tz)
              if tz else datetime.fromtimestamp(epoch_val))
        time_str = dt.strftime("%I:%M %p").upper()

        # Cross-date check: compare calendar date of epoch vs. reference date
        try:
            ref_day, ref_month, ref_year = (int(p)
                                            for p in date_str.split("/"))
            from datetime import date as _date
            ref_date = _date(ref_year, ref_month, ref_day)
            if dt.date() != ref_date:
                day_label = f"{dt.day} {dt.strftime('%B')}"
                return f"{day_label} {time_str}"
        except Exception:
            pass  # unparseable date_str -- show plain time

        return time_str

    @staticmethod
    def _format_moon_value(key: str, value: Any, date_str: str = "", tz_name: str = "") -> str:
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
            try:
                epoch_val = int(float(text))
                if epoch_val == 0:
                    return "Not visible"
                return WeatherConfigWindow._format_moon_time(epoch_val, date_str, tz_name)
            except (ValueError, TypeError):
                return text
        if key == "retrieved_at":
            try:
                dt = datetime.fromtimestamp(int(float(text)))
                date_part = f"{dt.day} {dt.strftime('%B %Y')}"
                time_part = dt.strftime("%I:%M %p")
                return f"{date_part} {time_part.upper()}"
            except (ValueError, TypeError):
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
    def _get_sun_epochs(sun_data: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        """
        Return (sunset_epoch, sunrise_epoch) as raw Unix integers from sun_data,
        or (None, None) if either value is absent or unreadable.
        """
        try:
            return int(sun_data["sunset"]), int(sun_data["sunrise"])
        except (KeyError, ValueError, TypeError):
            return None, None

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
        illumination_raw = str(
            data.get("illumination", "0")).replace("%", "").strip()
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
        moonset_str = str(data.get("moonset", "")).strip()

        is_new_or_full = phase in ("New Moon", "Full Moon")

        alerts: list[str] = []

        # ── Currently in moon window ───────────────────────────────────────────
        # A moon window is defined as the period between moonrise and moonset.
        # Epochs of zero (or missing) mean the data is unavailable — skip silently.
        try:
            moonrise_ep = int(float(moonrise_str)) if moonrise_str else 0
            moonset_ep = int(float(moonset_str)) if moonset_str else 0

            if moonrise_ep > 0 and moonset_ep > 0:
                now_ep = int(datetime.now().timestamp())

                if moonrise_ep <= now_ep <= moonset_ep:
                    window_total = moonset_ep - moonrise_ep
                    elapsed = now_ep - moonrise_ep

                    if window_total > 0:
                        percent = int((elapsed / window_total) * 100)
                        percent = max(0, min(percent, 100))
                    else:
                        percent = 0

                    alerts.append(f"Currently in lunar window ({percent}%)")
        except Exception:
            pass  # malformed epoch — skip this check

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

        # Condition 1: illumination too low → short-circuit (mirrors bash is_moon_too_dim)
        if illumination < 5.0:
            not_visible = True

        # Condition 2: moon's arc must NOT be entirely within the daytime window.
        #
        # The moon is a pure day-moon (not visible at night) only when BOTH:
        #   • moonrise >= sunrise  (rises after dark ends)
        #   • moonset  <= sunset   (sets before dark begins)
        # If either end sticks into the night the moon is visible.
        #
        # This covers both nightly windows with a single check:
        #   – moonrise < sunrise  → was up during last night (pre-dawn visible)
        #   – moonset  > sunset   → still up during tonight (post-dusk visible)
        #
        # Unknown epochs (0 / missing) → assume visible; don't suppress.
        if not not_visible:
            try:
                moonrise_ep = int(float(moonrise_str)) if moonrise_str else 0
                moonset_ep = int(float(moonset_str)) if moonset_str else 0

                if moonrise_ep > 0 and moonset_ep > 0:
                    sun_data = WeatherConfigWindow._load_sun_data()
                    sunset_ep, sunrise_ep = WeatherConfigWindow._get_sun_epochs(
                        sun_data)

                    if sunset_ep is not None and sunrise_ep is not None:
                        if moonrise_ep >= sunrise_ep and moonset_ep <= sunset_ep:
                            not_visible = True
                    # else: sun epochs unknown → assume visible
                # else: moon epochs unknown → assume visible
            except Exception:
                pass  # malformed times – skip this check

        if not_visible:
            alerts.append("Not visible at night.")

        if not alerts:
            return None

        return ". ".join(alerts)

    def _compute_lunar_window_progress(self, data: dict[str, Any]) -> Optional[str]:
        """
        Compute the current lunar window progress percentage (live, updates every second).

        Returns a string like "Currently in lunar window (42%)" when the moon is
        between moonrise and moonset, or None when outside the window.

        This is extracted from _compute_moon_alert to enable frequent updates
        without recomputing supermoons/micromoons/visibility checks.
        """
        moonrise_str = str(data.get("moonrise", "")).strip()
        moonset_str = str(data.get("moonset", "")).strip()

        try:
            moonrise_ep = int(float(moonrise_str)) if moonrise_str else 0
            moonset_ep = int(float(moonset_str)) if moonset_str else 0

            if moonrise_ep > 0 and moonset_ep > 0:
                now_ep = int(datetime.now().timestamp())

                if moonrise_ep <= now_ep <= moonset_ep:
                    window_total = moonset_ep - moonrise_ep
                    elapsed = now_ep - moonrise_ep

                    if window_total > 0:
                        # Clamp to 0–100
                        percent = int((elapsed / window_total) * 100)
                        percent = max(0, min(percent, 100))
                    else:
                        percent = 0

                    return f"Currently in lunar window ({percent}%)"
        except Exception:
            pass

        return None

    def _compute_moon_alert_static(self, data: dict[str, Any], tz_name: str = "") -> Optional[str]:
        """
        Like _compute_moon_alert but excludes the lunar window progress check
        (which updates every second anyway). Useful for static alerts like
        supermoons and visibility warnings.

        Returns a one-line alert string, or None when nothing notable applies.
        """
        phase = str(data.get("phase", "")).strip()
        illumination_raw = str(
            data.get("illumination", "0")).replace("%", "").strip()
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
        moonset_str = str(data.get("moonset", "")).strip()

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
        MICROMOON_THRESHOLD = 401_000  # km
        if is_new_or_full and distance_km is not None and distance_km >= MICROMOON_THRESHOLD:
            alerts.append("🌑 Micromoon")

        # ── Not visible at night ──────────────────────────────────────────────
        not_visible = False

        # Condition 1: illumination too low → short-circuit (mirrors bash is_moon_too_dim)
        if illumination < 5.0:
            not_visible = True

        # Condition 2: moon's arc must NOT be entirely within the daytime window.
        #
        # The moon is a pure day-moon (not visible at night) only when BOTH:
        #   • moonrise >= sunrise  (rises after dark ends)
        #   • moonset  <= sunset   (sets before dark begins)
        # If either end sticks into the night the moon is visible.
        #
        # Unknown epochs (0 / missing) → assume visible; don't suppress.
        if not not_visible:
            try:
                moonrise_ep = int(float(moonrise_str)) if moonrise_str else 0
                moonset_ep = int(float(moonset_str)) if moonset_str else 0

                if moonrise_ep > 0 and moonset_ep > 0:
                    sun_data = WeatherConfigWindow._load_sun_data()
                    sunset_ep, sunrise_ep = WeatherConfigWindow._get_sun_epochs(
                        sun_data)

                    if sunset_ep is not None and sunrise_ep is not None:
                        if moonrise_ep >= sunrise_ep and moonset_ep <= sunset_ep:
                            not_visible = True
                    # else: sun epochs unknown → assume visible
                # else: moon epochs unknown → assume visible
            except Exception:
                pass  # malformed times – skip this check

        if not_visible:
            alerts.append("Not visible at night.")

        if not alerts:
            return None

        return ". ".join(alerts)

    @staticmethod
    def _inject_moon_epochs(data: dict[str, Any]) -> None:
        """
        Replace moonrise and moonset HH:MM strings with Unix epoch integers in-place.

        Uses the API response date (DD/MM/YYYY) as the base for both times.
        If moonset HH:MM < moonrise HH:MM (midnight crossing), moonset is anchored
        to the following calendar day — mirroring the bash call_moon_api logic.
        No-ops on fields that are already integers or not in HH:MM format.
        """
        _HHMM_RE = re.compile(r'^\d{1,2}:\d{2}$')

        def _parse_date(raw: str) -> Optional[tuple[int, int, int]]:
            try:
                d, m, y = raw.strip().split("/")
                return int(y), int(m), int(d)
            except Exception:
                return None

        def _hhmm_to_mins(hhmm: str) -> int:
            h, m = map(int, hhmm.split(":"))
            return h * 60 + m

        def _build_epoch(year: int, month: int, day: int, hhmm: str) -> int:
            h, m = map(int, hhmm.split(":"))
            return int(datetime(year, month, day, h, m).timestamp())

        api_date = str(data.get("date", "")).strip()
        moonrise_raw = str(data.get("moonrise", "")).strip()
        moonset_raw = str(data.get("moonset", "")).strip()

        ymd = _parse_date(api_date)
        if ymd is None:
            return

        year, month, day = ymd

        if not _HHMM_RE.match(moonrise_raw):
            return  # already an epoch integer or unrecognised — leave untouched

        moonrise_ep = _build_epoch(year, month, day, moonrise_raw)
        data["moonrise"] = moonrise_ep

        if _HHMM_RE.match(moonset_raw):
            # Midnight-crossing: if moonset time-of-day is earlier than moonrise,
            # the moon sets on the next calendar day.
            if _hhmm_to_mins(moonset_raw) < _hhmm_to_mins(moonrise_raw):
                next_day = datetime(year, month, day) + timedelta(days=1)
                data["moonset"] = _build_epoch(
                    next_day.year, next_day.month, next_day.day, moonset_raw)
            else:
                data["moonset"] = _build_epoch(year, month, day, moonset_raw)

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

        api_key = _get("MOON_API_KEY")
        location = _get("LOCATION")
        timezone_val = _get("TIMEZONE")

        now = datetime.now()
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

        data = json.loads(raw, parse_float=Decimal)

        if "moonrise" not in data:
            raise RuntimeError("Unexpected response: 'moonrise' field missing")

        data["retrieved_at"] = int(datetime.now().timestamp())
        self._inject_moon_epochs(data)
        return data

    def _on_moon_update_clicked(self, btn: Gtk.Button) -> None:
        if not self._entries:
            self._show_error(
                "No config file loaded. Please open a config file first.")
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
            # Live monitor detects file write and updates UI automatically
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
            try:
                dt = datetime.fromtimestamp(
                    int(float(str(retrieved_raw).strip())))
                date_part = f"{dt.day} {dt.strftime('%B %Y')}"
                time_part = dt.strftime("%I:%M %p")
                return f"{description}Retrieved: {date_part} {time_part.upper()}"
            except (ValueError, TypeError):
                pass
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

        _date_str = str(data.get("date", "")).strip()
        _tz_entry = self._entries.get("TIMEZONE", None)
        _tz_name = _tz_entry.display_value.strip() if _tz_entry else ""
        for key, val_label in self._moon_value_labels.items():
            val_label.set_label(self._format_moon_value(
                key, _get(key), _date_str, _tz_name))

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
            self._moon_data_group.set_description(
                self._moon_retrieved_description(data))

    def _build_moon_data_section(self, preloaded_data: Optional[dict[str, Any]] = None) -> Adw.PreferencesGroup:
        """
        Load ~/.cache/weather/moon-data.json and display all fields
        in a compact two-column layout.
        Times are 12h with uppercase AM/PM, while dates retain proper casing.

        If *preloaded_data* is supplied (e.g. already validated by the file
        monitor) it is used directly and the file is not re-read.  This avoids
        a race condition where the CHANGED event fires before the write is
        complete, causing an independent read here to fail even though the
        monitor already holds valid data.
        """
        group = Adw.PreferencesGroup()
        group.set_title("Moon Data")
        group.set_margin_top(4)

        # ── Update button in group header ─────────────────────────────────────
        update_btn = Gtk.Button(label="Update")
        update_btn.add_css_class("flat")
        update_btn.set_valign(Gtk.Align.CENTER)
        update_btn.set_tooltip_text(
            "Fetch latest moon data from the API and save to moon-data.json")
        update_btn.connect("clicked", self._on_moon_update_clicked)
        update_btn.set_size_request(125, -1)
        group.set_header_suffix(update_btn)

        moon_path = Path.home() / ".cache" / "weather" / "moon-data.json"

        main_row = Adw.ActionRow()
        main_row.set_activatable(False)

        # Keep a reference so we can refresh values without rebuilding
        self._moon_value_labels: dict[str, Gtk.Label] = {}

        if preloaded_data is not None:
            data: dict[str, Any] = preloaded_data
        else:
            try:
                data = json.loads(moon_path.read_text(encoding="utf-8"))
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

        _date_str = str(data.get("date", "")).strip()
        _tz_entry = self._entries.get("TIMEZONE", None)
        _tz_name = _tz_entry.display_value.strip() if _tz_entry else ""

        # ── Current time ─────────────────────────────────────────────
        tz = self._resolve_tz(_tz_name)
        now = datetime.now(tz) if tz else datetime.now()
        now_ts = int(now.timestamp())

        # ── Extract epochs once ──────────────────────────────────────
        moonrise_ep = int(float(data.get("moonrise", 0) or 0))
        moonset_ep = int(float(data.get("moonset", 0) or 0))

        # ── Compute dim flags ────────────────────────────────────────
        moonrise_dim = (moonrise_ep == 0) or (
            moonrise_ep > 0 and now_ts > moonrise_ep)
        moonset_dim = (moonset_ep == 0) or (
            moonset_ep > 0 and now_ts > moonset_ep)

        fields = list(self._MOON_FIELD_LABELS.items())

        for i, (key, label) in enumerate(fields):
            target = left_grid if i % 2 == 0 else right_grid
            row_idx = i // 2

            lbl = Gtk.Label(label=f"{label}:")
            lbl.set_halign(Gtk.Align.START)
            lbl.add_css_class("dim-label")

            raw_val = _get_moon_value(key)

            val = Gtk.Label(label=self._format_moon_value(
                key, raw_val, _date_str, _tz_name))
            val.set_halign(Gtk.Align.END)
            val.set_hexpand(True)
            val.set_selectable(False)

            # Store reference for in-place refresh

            # ── Apply dim logic ONLY to rise/set ───────────────────────
            if key == "moonrise" and moonrise_dim:
                val.add_css_class("dim-label")
            elif key == "moonset" and moonset_dim:
                val.add_css_class("dim-label")

            self._moon_value_labels[key] = val

            target.attach(lbl, 0, row_idx, 1, 1)
            target.attach(val, 1, row_idx, 1, 1)

        vsep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        vsep.set_margin_start(24)
        vsep.set_margin_end(24)

        outer_box.append(left_grid)
        outer_box.append(vsep)
        outer_box.append(right_grid)

        # ── Alert row (unchanged) ────────────────────────────────────
        alert_text = self._compute_moon_alert(data, _tz_name)

        alert_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        alert_sep.set_margin_start(16)
        alert_sep.set_margin_end(16)

        alert_label = Gtk.Label(label=alert_text or "")
        alert_label.set_halign(Gtk.Align.CENTER)
        alert_label.set_margin_top(8)
        alert_label.set_margin_bottom(8)
        alert_label.add_css_class("dim-label")
        alert_label.set_wrap(True)  # Allow wrapping for long alerts

        alert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        alert_box.append(alert_sep)
        alert_box.append(alert_label)
        alert_box.set_visible(alert_text is not None)

        # Store references for in-place refresh
        self._moon_alert_row = alert_box
        self._moon_alert_label = alert_label

        # Wrap grids + optional alert in a vertical container
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(outer_box)
        content_box.append(alert_box)

        main_row.set_child(content_box)
        group.add(main_row)

        return group

    # ── Sun Data helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _find_weather_script() -> str:
        """
        Look for linux-weather-bar.sh in the same directory as this Python script.
        Returns the absolute path if found, or an empty string if not found.
        """
        candidate = Path(__file__).resolve().parent / "linux-weather-bar.sh"
        return str(candidate) if candidate.exists() else ""

    @staticmethod
    def _load_weather_data() -> tuple[dict[str, Any], Optional[str]]:
        """
        Load ~/.cache/weather/weather-data.json.

        Returns (data, error_message).  On success error_message is None.
        On failure data is {} and error_message describes the problem.
        """
        weather_path = Path.home() / ".cache" / "weather" / "weather-data.json"
        try:
            data = json.loads(weather_path.read_text(encoding="utf-8"))
            # Validate minimal required fields
            sys_block = data.get("sys", {})
            if not isinstance(sys_block.get("sunrise"), (int, float)):
                return {}, "weather-data.json is missing sys.sunrise"
            if not isinstance(sys_block.get("sunset"), (int, float)):
                return {}, "weather-data.json is missing sys.sunset"
            return data, None
        except FileNotFoundError:
            return {}, "weather-data.json not found"
        except (json.JSONDecodeError, ValueError) as exc:
            return {}, f"weather-data.json is invalid: {exc}"
        except Exception as exc:
            return {}, str(exc)

    @staticmethod
    def _format_sun_epoch(epoch: int, tz_name: str) -> str:
        """Format a Unix epoch as '6:12 AM' in the local timezone."""
        tz = WeatherConfigWindow._resolve_tz(tz_name)
        dt = (datetime.fromtimestamp(epoch, tz=tz)
              if tz else datetime.fromtimestamp(epoch))
        return dt.strftime("%I:%M %p").upper()

    @staticmethod
    def _format_sun_date(epoch: int, tz_name: str) -> str:
        """Format a Unix epoch as '24 April 2026' in the local timezone."""
        tz = WeatherConfigWindow._resolve_tz(tz_name)
        dt = (datetime.fromtimestamp(epoch, tz=tz)
              if tz else datetime.fromtimestamp(epoch))
        return f"{dt.day} {dt.strftime('%B %Y')}"

    def _on_weather_update_clicked(self, btn: Gtk.Button) -> None:
        """Run the weather script to refresh weather-data.json."""
        script = self._find_weather_script()
        if not script:
            self._show_error("linux-weather-bar.sh not found in the script directory.")
            return

        btn.set_sensitive(False)
        btn.set_label("Updating…")

        def _worker() -> None:
            try:
                subprocess.run(["bash", "-c", script], check=True, timeout=30)
                GLib.idle_add(_on_success)
            except Exception as exc:
                GLib.idle_add(_on_error, str(exc))

        def _on_success() -> bool:
            btn.set_label("Update")
            btn.set_sensitive(True)
            # Rebuild sun data section with fresh data
            self._rebuild_sun_data_section()
            self._show_toast("Weather data updated successfully")
            return False

        def _on_error(msg: str) -> bool:
            btn.set_label("Update")
            btn.set_sensitive(True)
            self._show_error(f"Weather script failed:\n{msg}")
            return False

        threading.Thread(target=_worker, daemon=True).start()

    def _rebuild_sun_data_section(self) -> None:
        """Replace the sun data group in-place after a successful update."""
        if self._sun_data_group is None:
            return
        moon_data_group = self._moon_data_group
        old = self._sun_data_group
        new = self._build_sun_data_section()
        self._groups_box.insert_child_after(new, old)
        self._groups_box.remove(old)
        self._sun_data_group = new

    # ── Weather Output section ────────────────────────────────────────────────

    def _run_weather_script_for_output(self) -> str:
        """Run the .sh script found next to this file, return its stdout or an error message."""
        script = self._find_weather_script()
        if not script:
            return "(linux-weather-bar.sh not found in script directory)"
        try:
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip()
            return output if output else "(no output)"
        except FileNotFoundError:
            return "(script not found)"
        except subprocess.TimeoutExpired:
            return "(timed out)"
        except Exception as exc:
            return f"(error: {exc})"

    def _build_weather_output_section(self) -> Adw.PreferencesGroup:
        """
        Untitled row showing the live output of WEATHER_SCRIPT.
        Placed above the Configuration group; hidden during search.
        """
        group = Adw.PreferencesGroup()
        # No title — intentionally blank
        group.set_margin_top(4)

        row = Adw.ActionRow()
        row.set_activatable(False)

        # Output label — left-aligned, monospace-friendly, wrapping
        output_label = Gtk.Label(label="")
        output_label.set_halign(Gtk.Align.START)
        output_label.set_hexpand(True)
        output_label.set_valign(Gtk.Align.CENTER)
        output_label.set_wrap(True)
        output_label.set_xalign(0.0)
        output_label.set_margin_start(16)
        output_label.set_margin_end(8)
        output_label.set_margin_top(10)
        output_label.set_margin_bottom(10)
        self._weather_output_label = output_label

        # Update button — inline at the end of the row
        update_btn = Gtk.Button(label="Refresh")
        update_btn.add_css_class("flat")
        update_btn.set_valign(Gtk.Align.CENTER)
        update_btn.set_tooltip_text("Refresh output")
        update_btn.set_margin_end(8)
        update_btn.set_margin_top(6)
        update_btn.set_margin_bottom(6)

        def _on_update_clicked(btn: Gtk.Button) -> None:
            btn.set_sensitive(False)
            btn.set_label("Refreshing…")

            def _worker() -> None:
                output = self._run_weather_script_for_output()
                GLib.idle_add(_apply, output)

            def _apply(output: str) -> bool:
                if self._weather_output_label:
                    self._weather_output_label.set_label(output)
                btn.set_label("Update")
                btn.set_sensitive(True)
                return False

            threading.Thread(target=_worker, daemon=True).start()

        update_btn.connect("clicked", _on_update_clicked)

        # Row layout: label expands, button stays fixed on the right
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row_box.set_hexpand(True)
        row_box.append(output_label)
        row_box.append(update_btn)

        row.set_child(row_box)
        group.add(row)

        # Populate asynchronously so UI shows immediately
        def _initial_worker() -> None:
            output = self._run_weather_script_for_output()
            GLib.idle_add(_initial_apply, output)

        def _initial_apply(output: str) -> bool:
            if self._weather_output_label:
                self._weather_output_label.set_label(output)
            return False

        threading.Thread(target=_initial_worker, daemon=True).start()

        self._weather_output_group = group
        return group

    def _build_sun_data_section(self) -> Adw.PreferencesGroup:
        """
        Load ~/.cache/weather/weather-data.json and display sunrise / sunset
        in a compact two-column layout matching the Moon Data style.

        Shows an Update button only when the file is absent or invalid.
        The epoch date (e.g. '24 April 2026') is shown as group description.
        Times are formatted as 12-hour with local timezone AM/PM.
        """
        group = Adw.PreferencesGroup()
        group.set_title("Sunrise &amp; Sunset")
        group.set_margin_top(4)

        data, error = self._load_weather_data()

        if error:
            # Show update button only on error
            update_btn = Gtk.Button(label="Update")
            update_btn.add_css_class("flat")
            update_btn.set_valign(Gtk.Align.CENTER)
            update_btn.set_tooltip_text(
                "Run weather script to fetch weather-data.json")
            update_btn.connect("clicked", self._on_weather_update_clicked)
            update_btn.set_size_request(125, -1)
            group.set_header_suffix(update_btn)

            placeholder = Adw.ActionRow()
            placeholder.set_activatable(False)
            placeholder.set_title("Sun data unavailable")
            placeholder.set_subtitle(error)
            group.add(placeholder)
            return group

        sys_block = data["sys"]
        sunrise_ep = int(sys_block["sunrise"])
        sunset_ep = int(sys_block["sunset"])

        tz_entry = self._entries.get("TIMEZONE", None)
        tz_name = tz_entry.display_value.strip() if tz_entry else ""

        tz = self._resolve_tz(tz_name)
        now = datetime.now(tz) if tz else datetime.now()
        now_ts = int(now.timestamp())

        # Group description
        group.set_description(self._format_sun_date(sunrise_ep, tz_name))

        # Build the two-column row exactly as in _build_moon_data_section
        main_row = Adw.ActionRow()
        main_row.set_activatable(False)

        outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer_box.set_hexpand(True)
        outer_box.set_margin_start(16)
        outer_box.set_margin_end(16)
        outer_box.set_margin_top(12)
        outer_box.set_margin_bottom(12)

        def _make_cell(dim_text: str, value_text: str, is_past: bool) -> Gtk.Grid:
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            grid.set_hexpand(True)
            grid.set_halign(Gtk.Align.FILL)

            lbl = Gtk.Label(label=f"{dim_text}:")
            lbl.set_halign(Gtk.Align.START)
            lbl.add_css_class("dim-label")

            val = Gtk.Label(label=value_text)
            val.set_halign(Gtk.Align.END)
            val.set_hexpand(True)
            val.set_selectable(False)

            # Apply dimming if the event is in the past
            if is_past:
                val.add_css_class("dim-label")

            grid.attach(lbl, 0, 0, 1, 1)
            grid.attach(val, 1, 0, 1, 1)
            return grid

        sunrise_past = now_ts > sunrise_ep
        sunset_past = now_ts > sunset_ep

        left_grid = _make_cell(
            "Sunrise",
            self._format_sun_epoch(sunrise_ep, tz_name),
            sunrise_past
        )

        right_grid = _make_cell(
            "Sunset",
            self._format_sun_epoch(sunset_ep, tz_name),
            sunset_past
        )

        vsep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        vsep.set_margin_start(24)
        vsep.set_margin_end(24)

        outer_box.append(left_grid)
        outer_box.append(vsep)
        outer_box.append(right_grid)

        main_row.set_child(outer_box)
        group.add(main_row)
        return group

    # ── Rain Forecast helpers ─────────────────────────────────────────────────

    @staticmethod
    def _format_forecast_dt(dt_txt: str, tz_name: str) -> str:
        try:
            dt = datetime.strptime(dt_txt.strip(), "%Y-%m-%d %H:%M:%S")
            tz = WeatherConfigWindow._resolve_tz(tz_name)

            if tz:
                dt = dt.replace(tzinfo=timezone.utc).astimezone(tz)
                now = datetime.now(tz)
            else:
                now = datetime.now()

            dt_date = dt.date()
            today = now.date()
            tomorrow = today + timedelta(days=1)

            # Precompute time with forced uppercase AM/PM
            time_str = dt.strftime("%I:%M %p").upper()

            if dt_date == today:
                return f"Today, {time_str}"
            elif dt_date == tomorrow:
                return f"Tomorrow, {time_str}"

            return f"{dt.strftime('%A, %d %B %Y')} {time_str}"

        except Exception:
            return dt_txt

    def _rebuild_rain_forecast_section(self) -> None:
        """Update only the forecast content without destroying the header spinbuttons."""
        if self._rain_forecast_group is None or self._rain_forecast_content_row is None:
            return

        # Build new content (forecasts or empty message)
        new_content = self._build_rain_forecast_content()

        # Replace the content row in-place
        self._rain_forecast_group.remove(self._rain_forecast_content_row)
        self._rain_forecast_group.add(new_content)
        self._rain_forecast_content_row = new_content

    def _build_rain_forecast_content(self) -> Gtk.Widget:
        """
        Build only the forecast content (forecasts list or empty message).
        Does NOT build the header - header is stable and not rebuilt.
        """
        # ── Fetch filtered forecasts ──────────────────────────────────────
        forecasts = self._rain_forecast_service.get_rain_forecasts(
            threshold=self._rain_forecast_threshold_ui,
            lookahead=self._rain_forecast_lookahead_ui,
        )

        tz_name = ""
        if self._entries:
            tz_entry = self._entries.get("TIMEZONE")
            if tz_entry:
                tz_name = tz_entry.display_value.strip()

        feels_threshold = 10.0
        if self._entries:
            ft_entry = self._entries.get("FEELS_LIKE_THRESHOLD")
            if ft_entry:
                try:
                    feels_threshold = float(ft_entry.display_value)
                except ValueError:
                    pass

        # ── Empty state ───────────────────────────────────────────────────
        if not forecasts:
            empty_row = Adw.ActionRow()
            empty_row.set_activatable(False)
            empty_row.set_title("No upcoming rain detected.")
            empty_row.set_subtitle(
                "Try lowering the Minimum Precipitation Threshold."
            )
            return empty_row

        # ── One ActionRow containing a vertical stack of forecast blocks ──
        container_row = Adw.ActionRow()
        container_row.set_activatable(False)

        stack_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        stack_box.set_hexpand(True)
        stack_box.set_margin_start(4)
        stack_box.set_margin_end(4)
        stack_box.set_margin_top(4)
        stack_box.set_margin_bottom(4)

        for idx, entry in enumerate(forecasts):
            # Vertical spacing separator between entries (not before first)
            if idx > 0:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_margin_top(8)
                sep.set_margin_bottom(8)
                sep.set_margin_start(16)
                sep.set_margin_end(16)
                stack_box.append(sep)

            # ── Entry block ───────────────────────────────────────────────
            entry_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2)
            entry_box.set_margin_start(16)
            entry_box.set_margin_end(16)
            entry_box.set_margin_top(10)
            entry_box.set_margin_bottom(10)

            # Line 1: bold date + time
            date_label = Gtk.Label()
            date_label.set_markup(
                f"{self._format_forecast_dt(entry.dt_txt, tz_name)}"
            )
            date_label.add_css_class("dim-label")
            date_label.set_halign(Gtk.Align.START)
            date_label.set_xalign(0.0)

            # Line 2: description + temp (+ feels like if delta exceeds threshold)
            temp_rounded = round(entry.temp, 1)
            feels_rounded = round(entry.feels_like, 1)
            pop_pct = round(entry.pop * 100)

            delta = abs(entry.feels_like - entry.temp)
            if delta > feels_threshold:
                detail_text = (
                    f"{entry.description}  {temp_rounded}°C "
                    f"(Feels {feels_rounded}°C)  "
                    f"Precipitation: {pop_pct}%"
                )
            else:
                detail_text = (
                    f"{entry.description}  {temp_rounded}°C  "
                    f"Precipitation: {pop_pct}%"
                )

            detail_label = Gtk.Label(label=detail_text)
            detail_label.set_halign(Gtk.Align.START)
            detail_label.set_xalign(0.0)
            detail_label.set_wrap(True)

            entry_box.append(date_label)
            entry_box.append(detail_label)
            stack_box.append(entry_box)

        container_row.set_child(stack_box)
        return container_row

    def _on_rain_forecast_update_clicked(self, btn: Gtk.Button) -> None:
        """
        Run the weather script to refresh forecast-data.json.
        The file monitor will detect the change and automatically update the UI.
        """
        script = self._find_weather_script()
        if not script:
            self._show_error("linux-weather-bar.sh not found in the script directory.")
            return

        btn.set_sensitive(False)
        btn.set_label("Updating…")

        def _worker() -> None:
            try:
                subprocess.run(["bash", "-c", script], check=True, timeout=30)
                GLib.idle_add(_on_success)
            except Exception as exc:
                GLib.idle_add(_on_error, str(exc))

        def _on_success() -> bool:
            btn.set_label("Update")
            btn.set_sensitive(True)
            # File monitor will detect the file change and trigger _on_rain_forecast_updated
            # which will update the UI automatically. No manual cache invalidation needed.
            self._show_toast("Forecast data updated successfully")
            return False

        def _on_error(msg: str) -> bool:
            btn.set_label("Update")
            btn.set_sensitive(True)
            self._show_error(f"Weather script failed:\n{msg}")
            return False

        threading.Thread(target=_worker, daemon=True).start()

    def _build_rain_forecast_section(self) -> Adw.PreferencesGroup:
        """
        Build the Rain Forecast PreferencesGroup.

        Layout mirrors the Moon Data section:
        - Single PreferencesGroup titled "Rain Forecast"
        - Each forecast entry is a vertically stacked block (NOT a grid,
          NOT multi-column) inside a single list container.
        - A local SpinButton threshold control and lookahead count control
          in the header trigger live re-filtering.
        - An Update button runs the weather script and refreshes data.

        SOLID compliance:
        - Parsing/filtering is delegated to RainForecastService (SRP).
        - Threshold and lookahead are passed in; core logic is not modified (OCP).
        - Service is a dependency-injected instance attribute (DI).
        """
        group = Adw.PreferencesGroup()
        group.set_title("Rain Forecast")
        group.set_margin_top(4)

        # ── Initialise threshold from global config on first build ─────────
        if self._entries:
            global_entry = self._entries.get("RAIN_FORECAST_THRESHOLD")
            if global_entry:
                try:
                    self._rain_forecast_threshold_ui = float(
                        global_entry.display_value)
                except ValueError:
                    pass

        # ── Header: Update button + lookahead spinner + threshold spinner ──
        header_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.set_valign(Gtk.Align.CENTER)

        # Update button
        update_btn = Gtk.Button(label="Update")
        update_btn.add_css_class("flat")
        update_btn.set_valign(Gtk.Align.CENTER)
        update_btn.set_tooltip_text(
            "Run weather script to refresh forecast data")
        update_btn.connect("clicked", self._on_rain_forecast_update_clicked)
        update_btn.set_size_request(125, -1)

        # Lookahead spinner
        lookahead_label = Gtk.Label(label="Max Items:")
        lookahead_label.add_css_class("caption")
        lookahead_label.add_css_class("dim-label")
        lookahead_label.set_valign(Gtk.Align.CENTER)

        lookahead_adj = Gtk.Adjustment(
            value=float(self._rain_forecast_lookahead_ui),
            lower=1.0, upper=20.0,
            step_increment=1.0, page_increment=5.0,
        )
        lookahead_spin = Gtk.SpinButton(adjustment=lookahead_adj, digits=0)
        lookahead_spin.set_valign(Gtk.Align.CENTER)
        lookahead_spin.set_tooltip_text(
            "Maximum number of upcoming rain forecasts to display.\n"
            "This is independent of the global Rain Forecast Lookahead Window."
        )

        def _on_lookahead_changed(spin: Gtk.SpinButton) -> None:
            self._rain_forecast_lookahead_ui = int(spin.get_value())
            # Update content only - header spinbuttons remain stable
            self._rebuild_rain_forecast_section()

        lookahead_spin.connect("value-changed", _on_lookahead_changed)

        # Threshold spinner
        threshold_label = Gtk.Label(label="Min Precip:")
        threshold_label.add_css_class("caption")
        threshold_label.add_css_class("dim-label")
        threshold_label.set_valign(Gtk.Align.CENTER)

        threshold_adj = Gtk.Adjustment(
            value=self._rain_forecast_threshold_ui,
            lower=0.0, upper=1.0,
            step_increment=0.05, page_increment=0.1,
        )
        threshold_spin = Gtk.SpinButton(adjustment=threshold_adj, digits=2)
        threshold_spin.set_valign(Gtk.Align.CENTER)
        threshold_spin.set_tooltip_text(
            "Minimum probability of precipitation (0.0–1.0) to show a forecast entry.\n"
            "This is independent of the global Rain Probability Threshold."
        )

        def _on_threshold_changed(spin: Gtk.SpinButton) -> None:
            self._rain_forecast_threshold_ui = spin.get_value()
            # Update content only - header spinbuttons remain stable
            self._rebuild_rain_forecast_section()

        threshold_spin.connect("value-changed", _on_threshold_changed)

        header_box.append(lookahead_label)
        header_box.append(lookahead_spin)
        header_box.append(threshold_label)
        header_box.append(threshold_spin)
        header_box.append(update_btn)
        group.set_header_suffix(header_box)

        # ── Build initial content and store reference ──────────────────────
        content_row = self._build_rain_forecast_content()
        self._rain_forecast_content_row = content_row
        group.add(content_row)

        self._rain_forecast_group = group
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
                location_store = getattr(app, "location_store", None)
                tz_store = getattr(app, "tz_store", None)
                row = make_row(entry, self._on_entry_changed,
                               location_store, tz_store)

                # Add to group
                group.add(row)

                rows.append(row)
                self._rows[schema.key] = row

            # Only add group if it has rows
            if rows:
                # Suppress the "Sunrise & Sunset" title — Sun Data section
                # immediately above it already provides the visual context.
                if group_name == "Sunrise &amp; Sunset":
                    group.set_title("")

                # Inject Weather Output immediately before the Configuration group
                if group_name == "Configuration":
                    self._weather_output_group = self._build_weather_output_section()
                    self._groups_box.append(self._weather_output_group)

                    # Plain path label directly under the group, no row wrapper
                    config_path_str = str(self._config_path) if self._config_path else "No file loaded"
                    path_label = Gtk.Label(label=config_path_str)
                    path_label.set_halign(Gtk.Align.START)
                    path_label.set_hexpand(True)
                    path_label.set_wrap(True)
                    path_label.set_xalign(0.0)
                    path_label.set_margin_start(4)
                    path_label.set_margin_top(2)
                    path_label.set_margin_bottom(4)
                    path_label.add_css_class("caption")
                    path_label.add_css_class("dim-label")
                    self._groups_box.append(path_label)
                    self._path_label = path_label

                self._groups_box.append(group)
                self._group_widgets[group_name] = (group, rows)

            # Inject Rain Forecast + Sun Data immediately after Configuration
            if group_name == "Configuration":
                # 1. Rain Forecast comes next
                self._rain_forecast_group = self._build_rain_forecast_section()
                self._groups_box.append(self._rain_forecast_group)

                # 2. Then Sun Data
                self._sun_data_group = self._build_sun_data_section()
                self._groups_box.append(self._sun_data_group)

            # Inject Moon Data section immediately after Sunrise & Sunset group,
            if group_name == "Sunrise &amp; Sunset":
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

    # ── Window Lifecycle & Data Updates ────────────────────────────────────

    def _on_window_realized(self, *_: Any) -> None:
        """Start data monitoring (moon and rain forecast) when window is first shown."""
        self._moon_monitor.start_watching()
        self._rain_forecast_monitor.start_watching()

    def _on_window_closed(self, *_: Any) -> bool:
        """Clean up monitoring when window closes."""
        self._moon_monitor.stop_watching()
        self._rain_forecast_monitor.stop_watching()
        return False

    def _on_moon_data_updated(self, data: dict[str, Any]) -> None:
        """
        Callback fired when moon data changes (file reload) OR every second (timeout).
        Updates the UI in-place without rebuilding.
        """
        # If data just became available while we were still showing the
        # "Moon data unavailable" placeholder (i.e. _moon_value_labels is empty
        # because _build_moon_data_section returned early), tear down the
        # placeholder group and rebuild it so the data grid is created.
        if data and not self._moon_value_labels and self._moon_data_group is not None:
            # Pass the already-validated data dict so _build_moon_data_section
            # does not re-read the file (avoids a race with mid-write CHANGED events).
            moon_phase_tuple = self._group_widgets.get("Moon Phase")
            sibling = moon_phase_tuple[0] if moon_phase_tuple else None
            self._groups_box.remove(self._moon_data_group)
            self._moon_data_group = self._build_moon_data_section(
                preloaded_data=data)
            # Re-insert at the correct position (immediately after Moon Phase group),
            # not at the end -- append() would push it below Network / API Keys / etc.
            if sibling is not None:
                self._groups_box.insert_child_after(
                    self._moon_data_group, sibling)
            else:
                self._groups_box.append(self._moon_data_group)
            return

        if not data or not self._moon_value_labels:
            return

        # Update data value labels (from _refresh_moon_data_values)
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

        _date_str = str(data.get("date", "")).strip()
        _tz_entry = self._entries.get("TIMEZONE", None)
        _tz_name = _tz_entry.display_value.strip() if _tz_entry else ""
        for key, val_label in self._moon_value_labels.items():
            val_label.set_label(self._format_moon_value(
                key, _get(key), _date_str, _tz_name))

        # Update alert row with both lunar window progress + static alerts
        if self._moon_alert_row is not None and self._moon_alert_label is not None:
            tz_name = _tz_name

            # Combine progress (updates every second) + static alerts
            progress = self._compute_lunar_window_progress(data)
            static_alert = self._compute_moon_alert_static(data, tz_name)

            alert_parts = []
            if progress:
                alert_parts.append(progress)
            if static_alert:
                alert_parts.append(static_alert)

            alert_text = ". ".join(alert_parts)

            if alert_text:
                self._moon_alert_label.set_label(alert_text)
                self._moon_alert_row.set_visible(True)
            else:
                self._moon_alert_row.set_visible(False)

        # Refresh the group description with the new retrieved_at timestamp
        if self._moon_data_group:
            self._moon_data_group.set_description(
                self._moon_retrieved_description(data))

    def _on_rain_forecast_updated(self, data: dict[str, Any]) -> None:
        """
        Callback fired when rain forecast data changes (file monitor detection).
        Updates the rain forecast content in-place.

        Unlike moon data, rain forecast doesn't have time-sensitive values that change
        every second. Only the file monitor triggers updates; no periodic timeout.
        """
        if not self._rain_forecast_group or not self._rain_forecast_content_row:
            return

        # Rebuild forecast content when file changes
        self._rebuild_rain_forecast_section()

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
            self._original_values = {
                k: e.raw_value for k, e in self._entries.items()}
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
            self._show_error(
                "No file is loaded. Please open a config file first.")
            return
        # Validate all
        errors: list[str] = []
        for key, entry in self._entries.items():
            err = self._validator.validate(entry, self._entries)
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

            # Refresh the weather output row with fresh script output
            if self._weather_output_label is not None:
                lbl = self._weather_output_label

                def _refresh_output() -> None:
                    output = self._run_weather_script_for_output()
                    GLib.idle_add(lambda: lbl.set_label(output) or False)

                threading.Thread(target=_refresh_output, daemon=True).start()

            # Advance the baseline so Save deactivates, but keep undo stack
            # intact so the user can still undo changes made before saving.
            self._original_values = {
                k: e.raw_value for k, e in self._entries.items()}
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
        searching = bool(self._search_text)

        # Hide dynamic display-only sections (Rain Forecast, Sun Data, Moon Data)
        # when a search is active — they have no schema rows to match against and
        # would otherwise float above the filtered results confusingly.
        for special_group in (
            getattr(self, "_weather_output_group", None),
            getattr(self, "_rain_forecast_group", None),
            getattr(self, "_sun_data_group", None),
            getattr(self, "_moon_data_group", None),
        ):
            if special_group is not None:
                special_group.set_visible(not searching)

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

        self.location_store = LocationMappingStore(self.settings)
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
