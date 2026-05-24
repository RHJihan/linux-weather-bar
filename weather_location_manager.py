#!/usr/bin/env python3
"""
Weather Location Manager — GTK4 / libadwaita GUI
Ported from: weather-location-manager.sh

Bash → Python mapping:
  get_current_ip()            → IPDetector.fetch_ip() (threaded)
  validate_ip()               → CSVManager.validate_ip()
  sanitize_csv_field()        → CSVManager.sanitize_field()
  validate_coordinate()       → validate_coordinate()
  csv_init()                  → CSVManager.__init__ + ensure_file()
  csv_read_all()              → CSVManager.get_all_entries()
  csv_get_by_ip()             → CSVManager.get_by_ip()
  csv_get_unique_locations()  → CSVManager.get_unique_locations()
  csv_add_entry()             → CSVManager.add_entry()
  csv_update_entry()          → CSVManager.update_entry()
  csv_update_location()       → CSVManager.update_location()
  handle_ip_mapping()         → MainWindow.handle_ip_mapping()
  weather_config_read_location() → WeatherConfig.read_location()
  weather_config_set_location()  → WeatherConfig.set_location()
  choose_location_menu()      → LocationListView + AddLocationDialog
  confirm()                   → AdwMessageDialog (yes/no)
  run weather script          → RunScriptDialog
"""

from gi.repository import Adw, Gdk, GLib, Gtk, Pango
import csv
import fcntl
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS / PATHS
# ──────────────────────────────────────────────────────────────────────────────

HOME = Path.home()
CSV_FILE = HOME / ".local/share/bin/linux-weather-bar/location_mappings.csv"
WEATHER_CONFIG = HOME / ".local/share/bin/linux-weather-bar/.weather_config"
WEATHER_SCRIPT = HOME / ".local/share/bin/linux-weather-bar/linux-weather-bar.sh"
CSV_HEADER = ["IP", "NAME", "LATITUDE", "LONGITUDE"]

IP_SERVICES = [
    ("ipify", "https://api.ipify.org"),
    ("ifconfig.me", "https://ifconfig.me/ip"),
    ("icanhazip", "https://icanhazip.com"),
    ("aws-checkip", "https://checkip.amazonaws.com"),
]


# ──────────────────────────────────────────────────────────────────────────────
# VALIDATION / SANITIZATION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def validate_ip(ip: str) -> bool:
    """Port of bash validate_ip() — checks IPv4 format and 0-255 range."""
    try:
        parts = ip.strip().split(".")
        if len(parts) != 4:
            return False
        for p in parts:
            if not p.isdigit():
                return False
            if len(p) > 1 and p.startswith("0"):
                return False  # reject leading zeros
            if not (0 <= int(p) <= 255):
                return False
        return True
    except Exception:
        return False


def sanitize_field(value: str) -> str:
    """Port of bash sanitize_csv_field() — strips injection characters."""
    for ch in (',', '"', '=', '+', '@', '|'):
        value = value.replace(ch, '')
    value = re.sub(r'[\n\r\t]', ' ', value)
    return value.strip()


def validate_coordinate(value: str, min_val: float, max_val: float) -> tuple[bool, str]:
    """Port of bash validate_coordinate() — format + range check."""
    if not re.match(r'^-?[0-9]+\.?[0-9]*$', value.strip()):
        return False, f"Invalid format: {value!r}"
    try:
        f = float(value)
        if not (min_val <= f <= max_val):
            return False, f"Out of range [{min_val}, {max_val}]: {f}"
        return True, ""
    except ValueError:
        return False, f"Cannot parse as number: {value!r}"


def normalize_coord(value: str) -> str:
    """Port of bash normalize_coord() — 4 decimal places."""
    return f"{float(value):.4f}"


# ──────────────────────────────────────────────────────────────────────────────
# CSV MANAGER
# ──────────────────────────────────────────────────────────────────────────────

class CSVManager:
    """
    All CSV I/O with file locking (fcntl) and atomic writes.
    Replaces: csv_init, csv_read_all, csv_get_by_ip, csv_get_unique_locations,
              csv_add_entry, csv_update_entry, csv_update_location,
              csv_location_exists, csv_get_location_by_name.
    """

    def __init__(self, path: Path = CSV_FILE):
        self.path = path
        self.ensure_file()

    def ensure_file(self):
        """csv_init() — create file + directory if missing."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with open(self.path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADER)
            os.chmod(self.path, 0o600)

    # ── reading ───────────────────────────────────────────────────────────────

    def get_all_entries(self) -> list[dict]:
        """csv_read_all() — returns list of dicts (skips header)."""
        if not self.path.exists():
            return []
        with open(self.path, newline='') as f:
            reader = csv.DictReader(f)
            return [row for row in reader]

    def get_by_ip(self, ip: str) -> Optional[dict]:
        """csv_get_by_ip() — exact IP match, normalises coords."""
        for row in self.get_all_entries():
            if row.get("IP", "").strip() == ip.strip():
                try:
                    row["LATITUDE"] = normalize_coord(row["LATITUDE"])
                    row["LONGITUDE"] = normalize_coord(row["LONGITUDE"])
                except (ValueError, KeyError):
                    pass
                return row
        return None

    def get_unique_locations(self) -> list[dict]:
        """csv_get_unique_locations() — deduplicated NAME/LAT/LON triples."""
        seen = set()
        result = []
        for row in self.get_all_entries():
            name = row.get("NAME", "").strip()
            lat = row.get("LATITUDE", "").strip()
            lon = row.get("LONGITUDE", "").strip()
            if not (name and lat and lon):
                continue
            try:
                lat_n = normalize_coord(lat)
                lon_n = normalize_coord(lon)
            except ValueError:
                continue
            key = (name, lat_n, lon_n)
            if key not in seen:
                seen.add(key)
                result.append(
                    {"NAME": name, "LATITUDE": lat_n, "LONGITUDE": lon_n})
        return sorted(result, key=lambda r: r["NAME"])

    def location_exists(self, name: str) -> bool:
        """csv_location_exists()"""
        return any(r["NAME"] == name for r in self.get_unique_locations())

    def get_location_by_name(self, name: str) -> Optional[dict]:
        """csv_get_location_by_name()"""
        for r in self.get_unique_locations():
            if r["NAME"] == name:
                return r
        return None

    # ── writing (with lock + backup + atomic move) ────────────────────────────

    def _backup(self) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.path.with_suffix(f".bak.{ts}")
        shutil.copy2(self.path, backup)
        return backup

    def _atomic_write(self, rows: list[dict], backup: Path):
        """Write rows to a temp file then atomically rename → CSV path."""
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, prefix="location_mappings_")
        try:
            with os.fdopen(fd, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
                writer.writeheader()
                writer.writerows(rows)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)
            backup.unlink(missing_ok=True)
        except Exception:
            os.unlink(tmp_path)
            shutil.copy2(backup, self.path)  # restore
            raise

    def add_entry(self, ip: str, name: str, lat: str, lon: str):
        """csv_add_entry() — append after locking."""
        if not validate_ip(ip):
            raise ValueError(f"Invalid IP address: {ip}")
        name = sanitize_field(name)
        lat = normalize_coord(lat)
        lon = normalize_coord(lon)

        with open(self.path, 'a', newline='') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                backup = self._backup()
                writer = csv.writer(f)
                writer.writerow([ip, name, lat, lon])
                backup.unlink(missing_ok=True)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def update_entry(self, ip: str, name: str, lat: str, lon: str):
        """csv_update_entry() — replace row matching IP."""
        if not validate_ip(ip):
            raise ValueError(f"Invalid IP address: {ip}")
        name = sanitize_field(name)
        lat = normalize_coord(lat)
        lon = normalize_coord(lon)

        with open(self.path, 'r+', newline='') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                backup = self._backup()
                rows = [r for r in csv.DictReader(f) if r["IP"] != ip]
                rows.append({"IP": ip, "NAME": name,
                            "LATITUDE": lat, "LONGITUDE": lon})
                self._atomic_write(rows, backup)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def update_location(self, ip: str, name: str, lat: str, lon: str):
        """csv_update_location() — replace rows matching NAME, assign IP."""
        if not validate_ip(ip):
            raise ValueError(f"Invalid IP address: {ip}")
        name = sanitize_field(name)
        lat = normalize_coord(lat)
        lon = normalize_coord(lon)

        with open(self.path, 'r+', newline='') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                backup = self._backup()
                rows = [r for r in csv.DictReader(f) if r["NAME"] != name]
                rows.append({"IP": ip, "NAME": name,
                            "LATITUDE": lat, "LONGITUDE": lon})
                self._atomic_write(rows, backup)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def delete_entry_by_name(self, name: str):
        """Bonus: remove all rows with given location name."""
        with open(self.path, 'r+', newline='') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                backup = self._backup()
                rows = [r for r in csv.DictReader(f) if r["NAME"] != name]
                self._atomic_write(rows, backup)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


# ──────────────────────────────────────────────────────────────────────────────
# WEATHER CONFIG MANAGER
# ──────────────────────────────────────────────────────────────────────────────

class WeatherConfig:
    """
    Reads/writes ~/.local/share/bin/linux-weather-bar/.weather_config.
    Replaces: weather_config_read_location, weather_config_set_location.
    """

    def __init__(self, path: Path = WEATHER_CONFIG):
        self.path = path

    def read_location(self) -> Optional[str]:
        """weather_config_read_location() — returns 'lat=X&lon=Y' or None."""
        if not self.path.exists():
            return None
        for line in self.path.read_text().splitlines():
            # Match both  readonly LOCATION="..."  and  LOCATION="..."
            m = re.match(
                r'^(?:readonly\s+)?LOCATION=["\']?(lat=[^&"\']+&lon=[^"\']+)["\']?', line)
            if m:
                return m.group(1).strip()
        return None

    def parse_location(self, loc_str: str) -> tuple[str, str]:
        """Extract lat/lon from 'lat=X&lon=Y'."""
        m = re.match(r'lat=([^&]+)&lon=(.+)', loc_str)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", ""

    def set_location(self, lat: str, lon: str, name: str = "UNKNOWN") -> bool:
        """
        weather_config_set_location() — rewrite LOCATION line in config.
        Returns True on success.
        """
        if not self.path.exists():
            return False
        lat = normalize_coord(lat)
        lon = normalize_coord(lon)
        new_val = f'lat={lat}&lon={lon}'

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.path.with_suffix(f".bak.{ts}")
        shutil.copy2(self.path, backup)

        lines = self.path.read_text().splitlines(keepends=True)
        new_lines = []
        replaced = False
        for line in lines:
            if re.match(r'^(?:readonly\s+)?LOCATION=', line):
                # Preserve readonly prefix if present
                prefix = "readonly " if line.startswith("readonly ") else ""
                new_lines.append(f'{prefix}LOCATION="{new_val}"  # {name}\n')
                replaced = True
            else:
                new_lines.append(line)

        if not replaced:
            return False

        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=".weather_config_")
        try:
            with os.fdopen(fd, 'w') as f:
                f.writelines(new_lines)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            backup.unlink(missing_ok=True)
            return True
        except Exception:
            os.unlink(tmp)
            shutil.copy2(backup, self.path)
            return False


# ──────────────────────────────────────────────────────────────────────────────
# IP DETECTION (background thread)
# ──────────────────────────────────────────────────────────────────────────────

class IPDetector:
    """get_current_ip() ported to Python with fallback service list."""

    @staticmethod
    def fetch_ip() -> Optional[str]:
        for name, url in IP_SERVICES:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    ip = resp.read().decode().strip()
                    if validate_ip(ip):
                        return ip
            except Exception:
                continue
        return None


# ──────────────────────────────────────────────────────────────────────────────
# ADD / EDIT LOCATION DIALOG
# ──────────────────────────────────────────────────────────────────────────────

class LocationDialog(Adw.Dialog):
    """
    Modal dialog for adding or editing a location entry.
    Replaces: input_custom_location_name() + input_custom_coordinates().
    """

    def __init__(self, parent, title="Add Location",
                 name="", lat="", lon="", ip="", lock_ip=False):
        super().__init__(title=title)
        self.set_content_width(420)
        self.result = None  # set to (name, lat, lon, ip) on save

        # ── layout ────────────────────────────────────────────────────────────
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        self.set_child(toolbar_view)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(12)
        box.set_margin_bottom(24)
        box.set_margin_start(16)
        box.set_margin_end(16)
        toolbar_view.set_content(box)

        prefs = Adw.PreferencesGroup()
        box.append(prefs)

        # Name row
        self._name_row = Adw.EntryRow(title="Location Name")
        self._name_row.set_text(name)
        prefs.add(self._name_row)

        # Lat row
        self._lat_row = Adw.EntryRow(title="Latitude  (−90 … 90)")
        self._lat_row.set_text(lat)
        prefs.add(self._lat_row)

        # Lon row
        self._lon_row = Adw.EntryRow(title="Longitude  (−180 … 180)")
        self._lon_row.set_text(lon)
        prefs.add(self._lon_row)

        # IP row
        self._ip_row = Adw.EntryRow(title="IP Address (optional)")
        self._ip_row.set_text(ip)
        if lock_ip:
            self._ip_row.set_editable(False)
            self._ip_row.set_sensitive(False)
        prefs.add(self._ip_row)

        # Error label
        self._err = Gtk.Label(label="")
        self._err.add_css_class("error")
        self._err.set_wrap(True)
        self._err.set_margin_top(8)
        box.append(self._err)

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.set_margin_top(16)
        save_btn.set_halign(Gtk.Align.CENTER)
        save_btn.set_hexpand(False)
        save_btn.connect("clicked", self._on_save)
        box.append(save_btn)

        self.present(parent)

    def _on_save(self, _btn):
        name = self._name_row.get_text().strip()
        lat = self._lat_row.get_text().strip()
        lon = self._lon_row.get_text().strip()
        ip = self._ip_row.get_text().strip()

        # Validate
        if not name:
            self._err.set_label("Location name cannot be empty.")
            return
        name = sanitize_field(name)
        if not name:
            self._err.set_label("Name is invalid after sanitization.")
            return

        ok, msg = validate_coordinate(lat, -90, 90)
        if not ok:
            self._err.set_label(f"Latitude: {msg}")
            return

        ok, msg = validate_coordinate(lon, -180, 180)
        if not ok:
            self._err.set_label(f"Longitude: {msg}")
            return

        if ip and not validate_ip(ip):
            self._err.set_label(f"Invalid IP address: {ip!r}")
            return

        self.result = (name, normalize_coord(lat), normalize_coord(lon), ip)
        self.close()


# ──────────────────────────────────────────────────────────────────────────────
# RUN WEATHER SCRIPT DIALOG
# ──────────────────────────────────────────────────────────────────────────────

class RunScriptDialog(Adw.Dialog):
    """Runs linux-weather-bar.sh in a thread and shows its output."""

    def __init__(self, parent):
        super().__init__(title="Weather Script Output")
        self.set_content_width(640)
        self.set_content_height(480)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        self.set_child(toolbar_view)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_margin_top(12)
        scroll.set_margin_bottom(12)
        scroll.set_margin_start(12)
        scroll.set_margin_end(12)
        toolbar_view.set_content(scroll)

        self._tv = Gtk.TextView()
        self._tv.set_editable(False)
        self._tv.set_monospace(True)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._buf = self._tv.get_buffer()
        scroll.set_child(self._tv)

        self.present(parent)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        script = str(WEATHER_SCRIPT)
        if not os.access(script, os.X_OK):
            GLib.idle_add(
                self._append, f"Error: script not executable:\n{script}\n")
            return
        try:
            env = os.environ.copy()
            env["FROM_CALLER"] = "true"
            proc = subprocess.run(
                [script], capture_output=True, text=True, timeout=30, env=env
            )
            output = proc.stdout
            # Split at sentinel (same as bash script)
            sentinel = "---END-WEATHER-LINE---"
            if sentinel in output:
                weather_line, _, json_part = output.partition(sentinel)
                GLib.idle_add(self._append, weather_line.strip() + "\n\n")
                try:
                    parsed = json.loads(json_part.strip())
                    GLib.idle_add(self._append,
                                  json.dumps(parsed, indent=2) + "\n")
                except json.JSONDecodeError:
                    GLib.idle_add(self._append, json_part)
            else:
                GLib.idle_add(self._append, output or "(no output)")
            if proc.stderr:
                GLib.idle_add(self._append, "\n--- stderr ---\n" + proc.stderr)
        except subprocess.TimeoutExpired:
            GLib.idle_add(self._append, "Error: script timed out after 30 s\n")
        except Exception as e:
            GLib.idle_add(self._append, f"Error: {e}\n")

    def _append(self, text: str):
        end = self._buf.get_end_iter()
        self._buf.insert(end, text)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ──────────────────────────────────────────────────────────────────────────────

class MainWindow(Adw.ApplicationWindow):
    """
    The primary application window.
    Orchestrates all UI sections described in the task spec.
    """

    def __init__(self, app):
        super().__init__(application=app, title="Weather Location Manager")
        self.set_default_size(800, 680)
        self.set_size_request(600, 500)

        self._csv = CSVManager()
        self._wconfig = WeatherConfig()
        self._current_ip: Optional[str] = None
        self._filter_text = ""

        self._build_ui()
        self._refresh_locations()
        self._load_weather_status()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Root toast overlay (replaces all success/error/warning echoes)
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        # Top-level container
        outer = Adw.ToolbarView()
        self._toast_overlay.set_child(outer)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # Search toggle
        self._search_btn = Gtk.ToggleButton()
        self._search_btn.set_icon_name("system-search-symbolic")
        self._search_btn.set_tooltip_text("Search locations")
        self._search_btn.connect("toggled", self._on_search_toggled)
        header.pack_end(self._search_btn)

        # Run weather script button
        run_btn = Gtk.Button()
        run_btn.set_icon_name("weather-clear-symbolic")
        run_btn.set_tooltip_text("Run weather script")
        run_btn.connect("clicked", self._on_run_script)
        header.pack_end(run_btn)

        outer.add_top_bar(header)

        # Search bar (hidden initially)
        self._search_bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Filter locations…")
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_filter_changed)
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        outer.add_top_bar(self._search_bar)

        # ── main content ──────────────────────────────────────────────────────
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        outer.set_content(main_box)

        # ── status cards (IP + weather config) ───────────────────────────────
        status_group = Adw.PreferencesGroup(title="Current Status")
        main_box.append(status_group)

        # IP row
        self._ip_row = Adw.ActionRow(title="Public IP Address",
                                     subtitle="Not detected yet")
        detect_btn = Gtk.Button(label="Detect IP")
        detect_btn.set_valign(Gtk.Align.CENTER)
        detect_btn.add_css_class("suggested-action")
        detect_btn.connect("clicked", self._on_detect_ip)
        self._ip_row.add_suffix(detect_btn)
        status_group.add(self._ip_row)

        # Current location row
        self._loc_row = Adw.ActionRow(title="Active Location",
                                      subtitle="Unknown")
        status_group.add(self._loc_row)

        # Coordinates row
        self._coord_row = Adw.ActionRow(title="Coordinates",
                                        subtitle="—")
        status_group.add(self._coord_row)

        # ── location list ─────────────────────────────────────────────────────
        loc_group = Adw.PreferencesGroup(title="Saved Locations")
        loc_group.set_margin_top(16)
        main_box.append(loc_group)

        # Scrollable list
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(200)
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        loc_group.add(scroll)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_filter_func(self._filter_row)
        scroll.set_child(self._list_box)

        # ── action buttons ────────────────────────────────────────────────────
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_top(12)
        btn_bar.set_homogeneous(True)
        main_box.append(btn_bar)

        use_btn = Gtk.Button(label="Use Selected")
        use_btn.set_icon_name("emblem-default-symbolic")
        use_btn.add_css_class("suggested-action")
        use_btn.connect("clicked", self._on_use_location)
        btn_bar.append(use_btn)

        add_btn = Gtk.Button(label="Add Location")
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.connect("clicked", self._on_add_location)
        btn_bar.append(add_btn)

        edit_btn = Gtk.Button(label="Edit Selected")
        edit_btn.set_icon_name("document-edit-symbolic")
        edit_btn.connect("clicked", self._on_edit_location)
        btn_bar.append(edit_btn)

        del_btn = Gtk.Button(label="Delete")
        del_btn.set_icon_name("user-trash-symbolic")
        del_btn.add_css_class("destructive-action")
        del_btn.connect("clicked", self._on_delete_location)
        btn_bar.append(del_btn)

    # ── location list helpers ─────────────────────────────────────────────────

    def _refresh_locations(self):
        """Reload CSV and repopulate the ListBox."""
        while child := self._list_box.get_first_child():
            self._list_box.remove(child)

        locs = self._csv.get_unique_locations()
        for loc in locs:
            row = Adw.ActionRow(
                title=loc["NAME"],
                subtitle=f"Lat {loc['LATITUDE']}  ·  Lon {loc['LONGITUDE']}"
            )
            # stash data on the row widget for retrieval
            row._loc_data = loc
            self._list_box.append(row)

        if not locs:
            placeholder = Adw.ActionRow(title="No locations saved yet",
                                        subtitle='Click "Add Location" to create one')
            placeholder._loc_data = None
            self._list_box.append(placeholder)

    def _get_selected_loc(self) -> Optional[dict]:
        row = self._list_box.get_selected_row()
        if row and hasattr(row, '_loc_data') and row._loc_data:
            return row._loc_data
        return None

    def _filter_row(self, row) -> bool:
        if not self._filter_text:
            return True
        if not hasattr(row, '_loc_data') or not row._loc_data:
            return True
        needle = self._filter_text.lower()
        name = row._loc_data.get("NAME", "").lower()
        return needle in name

    # ── weather config status ─────────────────────────────────────────────────

    def _load_weather_status(self):
        """Populate coord/location rows from .weather_config on startup."""
        loc_str = self._wconfig.read_location()
        if loc_str:
            lat, lon = self._wconfig.parse_location(loc_str)
            # Try to resolve a name from CSV
            name = "Unknown"
            for r in self._csv.get_all_entries():
                try:
                    if (normalize_coord(r["LATITUDE"]) == normalize_coord(lat) and
                            normalize_coord(r["LONGITUDE"]) == normalize_coord(lon)):
                        name = r["NAME"]
                        break
                except (ValueError, KeyError):
                    pass
            self._loc_row.set_subtitle(name)
            self._coord_row.set_subtitle(f"Lat {lat}  ·  Lon {lon}")
        else:
            self._loc_row.set_subtitle("Config file not found")
            self._coord_row.set_subtitle("—")

    # ── toast helpers (replace success/error/warning echoes) ─────────────────

    def _toast(self, message: str, timeout: int = 3):
        toast = Adw.Toast(title=message, timeout=timeout)
        self._toast_overlay.add_toast(toast)

    # ── signal handlers ───────────────────────────────────────────────────────

    def _on_search_toggled(self, btn):
        self._search_bar.set_search_mode(btn.get_active())

    def _on_filter_changed(self, entry):
        self._filter_text = entry.get_text()
        self._list_box.invalidate_filter()

    def _on_detect_ip(self, _btn):
        """Replaces get_current_ip() — runs in background thread."""
        self._ip_row.set_subtitle("Detecting…")
        threading.Thread(target=self._detect_ip_thread, daemon=True).start()

    def _detect_ip_thread(self):
        ip = IPDetector.fetch_ip()
        GLib.idle_add(self._on_ip_detected, ip)

    def _on_ip_detected(self, ip: Optional[str]):
        if not ip:
            self._ip_row.set_subtitle("Detection failed")
            self._show_error_dialog(
                "IP Detection Failed",
                "Could not reach any IP-lookup service. Check your internet connection."
            )
            return
        self._current_ip = ip
        self._ip_row.set_subtitle(ip)
        self._toast(f"Detected IP: {ip}")
        # Replaces handle_ip_mapping() flow
        self._handle_ip_mapping(ip)

    def _handle_ip_mapping(self, ip: str):
        """
        handle_ip_mapping() — check CSV for this IP, auto-select or prompt.
        The dialog flow replaces the terminal choose_location_menu() + confirm().
        """
        entry = self._csv.get_by_ip(ip)
        if entry and entry.get("NAME") and entry.get("LATITUDE") and entry.get("LONGITUDE"):
            # ── IP already mapped ────────────────────────────────────────────
            name = entry["NAME"]
            lat = entry["LATITUDE"]
            lon = entry["LONGITUDE"]
            self._toast(f"IP mapped to: {name}")
            self._highlight_location(name)
            self._confirm_apply_config(lat, lon, name)
        else:
            # ── IP not in CSV → ask user to pick / add ────────────────────
            if entry:
                msg = f"IP {ip} is in the database but has incomplete location data.\nPlease select or add a location."
            else:
                msg = f"IP {ip} is not in the database.\nSelect an existing location or add a new one."
            self._show_no_mapping_dialog(ip, msg, entry is not None)

    def _highlight_location(self, name: str):
        """Auto-select the matching row in the ListBox."""
        child = self._list_box.get_first_child()
        while child:
            if hasattr(child, '_loc_data') and child._loc_data:
                if child._loc_data.get("NAME") == name:
                    self._list_box.select_row(child)
                    break
            child = child.get_next_sibling()

    def _show_no_mapping_dialog(self, ip: str, message: str, ip_in_csv: bool):
        """
        When IP has no (complete) mapping, prompt the user.
        Replaces the 'Please select or add a location' section of handle_ip_mapping().
        """
        dialog = Adw.AlertDialog(
            heading="No Location Mapping Found",
            body=message
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("select", "Use Selected Location")
        dialog.add_response("add", "Add New Location")
        dialog.set_response_appearance(
            "select", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_no_mapping_response, ip, ip_in_csv)
        dialog.present(self)

    def _on_no_mapping_response(self, dialog, response: str, ip: str, ip_in_csv: bool):
        if response == "select":
            loc = self._get_selected_loc()
            if not loc:
                self._toast("No location selected in the list.")
                return
            self._confirm_save_ip_mapping(
                ip, loc["NAME"], loc["LATITUDE"], loc["LONGITUDE"], ip_in_csv)
        elif response == "add":
            self._open_add_dialog_for_ip(ip)

    def _open_add_dialog_for_ip(self, ip: str):
        """Open LocationDialog pre-filled with the detected IP."""
        dlg = LocationDialog(
            self, title="Add Location for Detected IP", ip=ip, lock_ip=True)
        dlg.connect("closed", self._on_add_dialog_for_ip_closed, ip)

    def _on_add_dialog_for_ip_closed(self, dlg, ip: str):
        if not dlg.result:
            return
        name, lat, lon, _ip = dlg.result
        try:
            self._csv.add_entry(ip, name, lat, lon)
            self._refresh_locations()
            self._toast(f"Added: {name} → {ip}")
            self._confirm_apply_config(lat, lon, name)
        except Exception as e:
            self._toast(f"Error: {e}")

    def _confirm_save_ip_mapping(self, ip: str, name: str, lat: str, lon: str, ip_in_csv: bool):
        """
        confirm("Save IP mapping?") equivalent — AdwAlertDialog.
        Handles both update_entry and add_entry paths from handle_ip_mapping().
        """
        dialog = Adw.AlertDialog(
            heading="Save IP Mapping?",
            body=f"Map IP {ip} → {name} ({lat}, {lon})?"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance(
            "save", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_save_ip_mapping,
                       ip, name, lat, lon, ip_in_csv)
        dialog.present(self)

    def _on_save_ip_mapping(self, _dialog, response, ip, name, lat, lon, ip_in_csv):
        if response != "save":
            self._confirm_apply_config(lat, lon, name)
            return
        try:
            if ip_in_csv:
                self._csv.update_entry(ip, name, lat, lon)
            else:
                # Replicate bash handle_ip_mapping() location-check logic
                existing = self._csv.get_location_by_name(name)
                if existing:
                    entries = self._csv.get_all_entries()
                    loc_entries = [e for e in entries if e["NAME"] == name]
                    if loc_entries and loc_entries[0].get("IP"):
                        self._csv.add_entry(ip, name, lat, lon)
                    else:
                        self._csv.update_location(ip, name, lat, lon)
                else:
                    self._csv.add_entry(ip, name, lat, lon)
            self._refresh_locations()
            self._toast(f"Saved: {ip} → {name}")
        except Exception as e:
            self._toast(f"Error saving mapping: {e}")
        self._confirm_apply_config(lat, lon, name)

    def _confirm_apply_config(self, lat: str, lon: str, name: str):
        """
        confirm("Apply these coordinate changes to the weather config?") equivalent.
        Shows before/after and asks for confirmation — weather_config_set_location().
        """
        current_str = self._wconfig.read_location() or "None"
        cur_lat, cur_lon = self._wconfig.parse_location(current_str)

        # Check if already up to date
        try:
            if (normalize_coord(cur_lat) == normalize_coord(lat) and
                    normalize_coord(cur_lon) == normalize_coord(lon)):
                self._toast("Weather config already up to date.")
                return
        except ValueError:
            pass

        body = (
            f"Current:   {current_str}\n"
            f"Proposed:  lat={lat}&lon={lon}  ({name})\n\n"
            f"Apply these changes to {WEATHER_CONFIG.name}?"
        )
        dialog = Adw.AlertDialog(heading="Update Weather Config?", body=body)
        dialog.add_response("cancel", "Skip")
        dialog.add_response("apply", "Apply")
        dialog.set_response_appearance(
            "apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_apply_config, lat, lon, name)
        dialog.present(self)

    def _on_apply_config(self, _dialog, response, lat, lon, name):
        if response != "apply":
            self._toast("Config update skipped.")
            return
        ok = self._wconfig.set_location(lat, lon, name)
        if ok:
            self._loc_row.set_subtitle(name)
            self._coord_row.set_subtitle(f"Lat {lat}  ·  Lon {lon}")
            self._toast(f"Weather config updated: {name}")
        else:
            self._toast("Failed to update weather config. File missing?")

    def _on_use_location(self, _btn):
        """Use selected location → update weather config (with confirmation)."""
        loc = self._get_selected_loc()
        if not loc:
            self._toast("Select a location from the list first.")
            return
        self._confirm_apply_config(
            loc["LATITUDE"], loc["LONGITUDE"], loc["NAME"])

    def _on_add_location(self, _btn):
        dlg = LocationDialog(self, title="Add New Location")
        dlg.connect("closed", self._on_add_dialog_closed)

    def _on_add_dialog_closed(self, dlg):
        if not dlg.result:
            return
        name, lat, lon, ip = dlg.result
        if not ip:
            # No IP — store as nameless entry (empty IP)
            ip = ""
        try:
            self._csv.add_entry(
                ip, name, lat, lon) if ip else self._csv_add_no_ip(name, lat, lon)
            self._refresh_locations()
            self._toast(f"Location added: {name}")
        except Exception as e:
            self._toast(f"Error: {e}")

    def _csv_add_no_ip(self, name: str, lat: str, lon: str):
        """Add a location row with blank IP field (CSV allows it)."""
        name = sanitize_field(name)
        lat = normalize_coord(lat)
        lon = normalize_coord(lon)
        with open(self._csv.path, 'a', newline='') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                backup = self._csv._backup()
                csv.writer(f).writerow(["", name, lat, lon])
                backup.unlink(missing_ok=True)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _on_edit_location(self, _btn):
        loc = self._get_selected_loc()
        if not loc:
            self._toast("Select a location to edit.")
            return
        # Find an IP for this location (if any)
        entries = self._csv.get_all_entries()
        ip = next((e["IP"] for e in entries if e["NAME"]
                  == loc["NAME"] and e["IP"]), "")
        dlg = LocationDialog(
            self, title="Edit Location",
            name=loc["NAME"], lat=loc["LATITUDE"], lon=loc["LONGITUDE"], ip=ip
        )
        dlg.connect("closed", self._on_edit_dialog_closed, loc["NAME"])

    def _on_edit_dialog_closed(self, dlg, old_name: str):
        if not dlg.result:
            return
        name, lat, lon, ip = dlg.result
        try:
            # Remove old rows, add new
            self._csv.delete_entry_by_name(old_name)
            self._csv_add_no_ip(name, lat, lon) if not ip else self._csv.add_entry(
                ip, name, lat, lon)
            self._refresh_locations()
            self._toast(f"Location updated: {name}")
        except Exception as e:
            self._toast(f"Error updating: {e}")

    def _on_delete_location(self, _btn):
        loc = self._get_selected_loc()
        if not loc:
            self._toast("Select a location to delete.")
            return
        dialog = Adw.AlertDialog(
            heading="Delete Location?",
            body=f"Remove \"{loc['NAME']}\" and all its IP mappings from the CSV?\nThis cannot be undone."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance(
            "delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirmed, loc["NAME"])
        dialog.present(self)

    def _on_delete_confirmed(self, _dialog, response, name):
        if response != "delete":
            return
        try:
            self._csv.delete_entry_by_name(name)
            self._refresh_locations()
            self._toast(f"Deleted: {name}")
        except Exception as e:
            self._toast(f"Error deleting: {e}")

    def _on_run_script(self, _btn):
        RunScriptDialog(self)

    def _show_error_dialog(self, heading: str, body: str):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self)


# ──────────────────────────────────────────────────────────────────────────────
# APPLICATION
# ──────────────────────────────────────────────────────────────────────────────

class WeatherLocationApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.WeatherLocationManager")
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = MainWindow(app)
        win.present()


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    app = WeatherLocationApp()
    sys.exit(app.run(sys.argv))
