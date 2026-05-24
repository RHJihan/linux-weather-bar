#!/usr/bin/env bash
#
# gnome-terminal --geometry=70x30 --title="Weather Location Manager"  -- bash -c "$HOME/.local/share/bin/linux-weather-bar/weather-location-manager.sh"
#
set -euo pipefail

### ========== CONFIGURATION ========== ###
WEATHER_SCRIPT="$HOME/.local/share/bin/linux-weather-bar/linux-weather-bar.sh"
CSV_FILE="$HOME/.local/share/bin/linux-weather-bar/location_mappings.csv"
LOCK_FILE="${CSV_FILE}.lock"

###############################################################################
# PRESENTATION LAYER — THEME & RENDERING
# All visual output is defined and managed here. Business logic MUST NOT
# contain any ANSI escape sequences or raw print formatting. Use the
# render_* helpers and semantic color tokens below.
###############################################################################

### ── Terminal Capability Detection ────────────────────────────────────────
_term_colors() {
	[[ -t 2 ]] && tput colors 2>/dev/null | grep -q -E '^(8|256)$' && return 0
	return 1
}

### ── Raw ANSI Palette (internal — do not use directly outside theme) ───────
if _term_colors; then
	_C_RESET="\e[0m"
	_C_BOLD="\e[1m"
	_C_DIM="\e[2m"
	_C_ITALIC="\e[3m"

	# Base palette (256-color, dark-theme tuned)
	_C_WHITE="\e[38;5;253m"
	_C_GREY="\e[38;5;242m"

	# Semantic palette
	_C_ACCENT="\e[38;5;75m"       # Steel blue — primary accent
	_C_SUCCESS="\e[38;5;114m"     # Sage green
	_C_WARNING="\e[38;5;179m"     # Amber
	_C_ERROR="\e[38;5;167m"       # Muted red
	_C_MUTED="\e[38;5;240m"       # Dark grey — de-emphasis
	_C_LABEL="\e[38;5;250m"       # Light grey — labels/keys
	_C_VALUE="\e[38;5;255m"       # Near-white — important values
	_C_SECTION="\e[38;5;69m"      # Blue-violet — section titles
	_C_CURRENT="\e[38;5;167m"     # Red — "before" state in diffs
	_C_PROPOSED="\e[38;5;114m"    # Green — "after" state in diffs
else
	# Graceful degradation — no color
	_C_RESET="" _C_BOLD="" _C_DIM="" _C_ITALIC=""
	_C_WHITE="" _C_GREY=""
	_C_ACCENT="" _C_SUCCESS="" _C_WARNING=""
	_C_ERROR="" _C_MUTED="" _C_LABEL="" _C_VALUE="" _C_SECTION=""
	_C_CURRENT="" _C_PROPOSED=""
fi

### ── Semantic Color Tokens (use these in render_* functions) ───────────────
# Each token maps a purpose to a color — change here, changes everywhere.
T_RESET="$_C_RESET"
T_BOLD="$_C_BOLD"
T_DIM="$_C_DIM"

T_ACCENT="$_C_ACCENT"         # Primary interactive elements, numbers, choices
T_MUTED="$_C_MUTED"           # De-emphasised text, parentheticals
T_LABEL="$_C_LABEL"           # Key names in key: value pairs
T_VALUE="$_C_VALUE"           # Important values (IPs, coords, names)
T_SECTION="$_C_SECTION"       # Section / menu headings
T_SUCCESS="$_C_SUCCESS"       # Confirmations, success states
T_WARNING="$_C_WARNING"       # Warnings, prompts requiring attention
T_ERROR="$_C_ERROR"           # Errors, failures
T_CURRENT="$_C_CURRENT"       # "before" state in diffs
T_PROPOSED="$_C_PROPOSED"     # "after" state in diffs

### ── Icon / Symbol Constants ───────────────────────────────────────────────
# Falls back to ASCII when terminal likely lacks Unicode support.
if [[ "${LANG:-}" =~ UTF-8 || "${LC_ALL:-}" =~ UTF-8 || "${LC_CTYPE:-}" =~ UTF-8 ]]; then
	ICO_BULLET="·"
	ICO_ARROW="→"
	ICO_CHECK="✓"
	ICO_WARN="⚠"
	ICO_CROSS="✕"
	ICO_DOT="•"
	ICO_CHOICE="›"
	ICO_DIVIDER="─"
	ICO_IP="◉"
else
	ICO_BULLET="-"
	ICO_ARROW="->"
	ICO_CHECK="[ok]"
	ICO_WARN="[!]"
	ICO_CROSS="[x]"
	ICO_DOT="*"
	ICO_CHOICE=">"
	ICO_DIVIDER="-"
	ICO_IP="#"
fi

### ── Layout Constants ──────────────────────────────────────────────────────
LAYOUT_WIDTH=64          # Nominal content width (fits 70-col window with margin)
LAYOUT_INDENT="  "       # Standard 2-space indent

### ── Core Render Primitives ────────────────────────────────────────────────

# _render_raw: lowest-level print — all other render_* functions call this.
# Always writes to stderr (UI channel).
_render_raw() {
	echo -e "$1" >&2
}

# render_blank: emit an empty line
render_blank() {
	_render_raw ""
}

# render_separator: a full-width dim rule
render_separator() {
	local width="${1:-$LAYOUT_WIDTH}"
	local rule
	rule=$(printf "${ICO_DIVIDER}%.0s" $(seq 1 "$width"))
	_render_raw "${T_MUTED}${rule}${T_RESET}"
}

# render_thin_sep: a shorter, lighter rule (used within sections)
render_thin_sep() {
	local width="${1:-$((LAYOUT_WIDTH / 2))}"
	local rule
	rule=$(printf "${ICO_DIVIDER}%.0s" $(seq 1 "$width"))
	_render_raw "${T_MUTED}${T_DIM}${rule}${T_RESET}"
}

### ── Semantic Message Renderers ────────────────────────────────────────────

render_error() {
	_render_raw "${T_ERROR}${T_BOLD}${ICO_CROSS}${T_RESET} ${T_ERROR}$1${T_RESET}"
}

render_success() {
	_render_raw "${T_SUCCESS}${ICO_CHECK}${T_RESET}  $1"
}

render_warning() {
	_render_raw "${T_WARNING}${ICO_WARN}${T_RESET}  ${T_WARNING}$1${T_RESET}"
}

render_info() {
	_render_raw "${T_MUTED}${ICO_BULLET}${T_RESET}  $1"
}

render_dim() {
	_render_raw "${T_MUTED}${T_DIM}$1${T_RESET}"
}

### ── Composite Layout Components ───────────────────────────────────────────

# render_banner: application title banner
render_banner() {
	render_blank
	render_separator
	_render_raw "${LAYOUT_INDENT}${T_SECTION}${T_BOLD}Weather Location Manager${T_RESET}"
	render_separator
	render_blank
}

# render_section_header: a named section break
render_section_header() {
	local title="$1"
	render_blank
	_render_raw "${LAYOUT_INDENT}${T_SECTION}${T_BOLD}${title}${T_RESET}"
	render_thin_sep
}

# render_kv: render a key - value pair
# Usage: render_kv "Label" "value" [indent]
render_kv() {
	local label="$1"
	local value="$2"
	local indent="${3:-$LAYOUT_INDENT}"
	_render_raw "${indent}${T_LABEL}${label}:${T_RESET}  ${T_VALUE}${value}${T_RESET}"
}

# render_kv_dim: a dimmed variant for secondary metadata
render_kv_dim() {
	local label="$1"
	local value="$2"
	local indent="${3:-$LAYOUT_INDENT}"
	_render_raw "${indent}${T_MUTED}${T_DIM}${label}:${T_RESET}  ${T_MUTED}${value}${T_RESET}"
}

# render_change_row: render a before/after diff row
render_change_row() {
	local label="$1"
	local value="$2"
	local color="$3"
	local prefix="$4"
	_render_raw "${LAYOUT_INDENT}${color}${prefix}${T_RESET}  ${T_LABEL}${label}${T_RESET}  ${T_VALUE}${value}${T_RESET}"
}

# render_menu_item: render a numbered menu entry
render_menu_item() {
	local number="$1"
	local label="$2"
	local meta="${3:-}"
	local meta_str=""
	[[ -n "$meta" ]] && meta_str="  ${T_MUTED}${meta}${T_RESET}"
	_render_raw "${LAYOUT_INDENT}${T_ACCENT}${number})${T_RESET}  ${T_VALUE}${label}${T_RESET}${meta_str}"
}

# render_prompt_str: styled inline prompt prefix (returns string for read -rp)
render_prompt_str() {
	echo -e "${LAYOUT_INDENT}${T_ACCENT}${ICO_CHOICE}${T_RESET} $1 "
}

# render_config_summary: the final "all done" configuration block
render_config_summary() {
	local name="$1"
	local lat="$2"
	local lon="$3"
	local ip="$4"

	render_blank
	render_separator
	_render_raw "${LAYOUT_INDENT}${T_SUCCESS}${T_BOLD}${ICO_CHECK}  Active Configuration${T_RESET}"
	render_thin_sep
	render_kv "Location   " "$name"
	render_kv "Latitude   " "$lat"
	render_kv "Longitude  " "$lon"
	render_kv "IP Address " "$ip"
	render_separator
	render_blank
}

# render_config_diff: show pending config change (current vs proposed)
render_config_diff() {
	local config_file="$1"
	local current_name="$2"
	local current_vals="$3"
	local proposed_name="$4"
	local proposed_vals="$5"

	render_section_header "Pending Config Update"
	render_kv_dim "File" "$config_file"
	render_blank
	render_change_row "Current " "${current_name}  (${current_vals:-none})" "$T_CURRENT" "${ICO_CROSS}"
	render_change_row "Proposed" "${proposed_name}  (${proposed_vals})" "$T_PROPOSED" "${ICO_CHECK}"
	render_blank
}

# render_weather_section: heading before weather output
render_weather_section() {
	render_section_header "Live Weather Output"
}

# render_close_prompt: the "press enter to close" footer
render_close_prompt() {
	render_blank
	render_dim "  Press Enter to close..."
}

###############################################################################
# END OF PRESENTATION LAYER
###############################################################################

### ========== HELPER FUNCTIONS ========== ###
# Low-level message functions — delegate to render_* (no inline ANSI here)

msg() {
	echo -e "$1" >&2
}

error() {
	render_error "$1"
}

success() {
	render_success "$1"
}

info() {
	render_info "$1"
}

warning() {
	render_warning "$1"
}

separator() {
	render_separator
}

### ========== INPUT SANITIZATION ========== ###
#######################################
# Sanitizes input for CSV fields
# Removes/replaces dangerous characters that could cause CSV injection
# Arguments:
#   $1 - Field value to sanitize
# Returns:
#   Sanitized field value
#######################################
sanitize_csv_field() {
	local field="$1"

	# Remove/replace dangerous characters
	field="${field//,/;}"     # Replace commas with semicolons
	field="${field//\"/''}"   # Replace double quotes with single quotes
	field="${field//=/}"      # Remove equals (CSV injection via formulas)
	field="${field//+/}"      # Remove plus (CSV injection)
	field="${field//@/}"      # Remove at sign (CSV injection)
	field="${field//|/}"      # Remove pipes
	field="${field//$'\n'/ }" # Replace newlines with spaces
	field="${field//$'\r'/ }" # Replace carriage returns with spaces
	field="${field//$'\t'/ }" # Replace tabs with spaces

	# Trim leading/trailing whitespace
	field="${field#"${field%%[![:space:]]*}"}"
	field="${field%"${field##*[![:space:]]}"}"

	echo "$field"
}

### ========== IP VALIDATION ========== ###
#######################################
# Validates IPv4 address format and range
# Arguments:
#   $1 - IP address to validate
# Returns:
#   0 if valid, 1 if invalid
#######################################
validate_ip() {
	local ip="$1"
	local IFS='.'
	local -a octets=($ip)

	# Must have exactly 4 octets
	[[ ${#octets[@]} -eq 4 ]] || return 1

	# Validate each octet
	for octet in "${octets[@]}"; do
		# Check if numeric
		[[ "$octet" =~ ^[0-9]+$ ]] || return 1

		# Check range (0-255)
		((octet >= 0 && octet <= 255)) || return 1

		# Reject leading zeros (except for "0" itself)
		if [[ ${#octet} -gt 1 && "$octet" == 0* ]]; then
			return 1
		fi
	done

	return 0
}

### ========== FILE LOCKING ========== ###
#######################################
# Acquires an exclusive lock on the CSV file
# Globals:
#   LOCK_FILE
# Returns:
#   0 on success, 1 on failure
#######################################
acquire_lock() {
	local timeout=10

	# Create lock file if it doesn't exist
	touch "$LOCK_FILE" 2>/dev/null || {
		error "Cannot create lock file: $LOCK_FILE"
		return 1
	}

	# Open file descriptor 200 for locking
	exec 200>"$LOCK_FILE"

	# Try to acquire exclusive lock with timeout
	if ! flock -w "$timeout" 200; then
		error "Could not acquire lock on CSV file after ${timeout}s"
		error "Another instance may be running or lock is stale"
		return 1
	fi

	return 0
}

#######################################
# Releases the lock on the CSV file
# Returns:
#   0 on success
#######################################
release_lock() {
	# Release lock on file descriptor 200
	if [[ -n "${LOCK_FILE:-}" ]] && [[ -e "/proc/$$/fd/200" ]] 2>/dev/null; then
		flock -u 200 2>/dev/null || true
		exec 200>&- 2>/dev/null || true
	fi
	return 0
}

### ========== CLEANUP ========== ###
#######################################
# Cleanup function for script exit
# Releases locks and removes temporary files
#######################################
cleanup() {
	release_lock
	rm -f "${CSV_FILE}.tmp" 2>/dev/null || true
	rm -f "${LOCK_FILE}" 2>/dev/null || true

	# Wait before closing
	render_close_prompt
	read -rp "" < /dev/tty 2>/dev/null || true
	exit 0
}

# Set up cleanup trap
trap cleanup EXIT
trap 'error "Script interrupted"; exit 130' INT TERM

### ========== CSV FILE MANAGER ========== ###
#######################################
# Initializes CSV file with header if it doesn't exist
# Globals:
#   CSV_FILE
#######################################
csv_init() {
	if [[ ! -f "$CSV_FILE" ]]; then
		info "Creating CSV file: $CSV_FILE"

		# Ensure directory exists
		local csv_dir
		csv_dir=$(dirname "$CSV_FILE")
		mkdir -p "$csv_dir"

		echo "IP,NAME,LATITUDE,LONGITUDE" >"$CSV_FILE"

		# Set restrictive permissions
		chmod 600 "$CSV_FILE"

		success "CSV file created"
	else
		# Validate header
		local header
		header=$(head -n1 "$CSV_FILE" 2>/dev/null || echo "")

		if [[ "$header" != "IP,NAME,LATITUDE,LONGITUDE" ]]; then
			warning "CSV header mismatch. Expected: IP,NAME,LATITUDE,LONGITUDE"
			warning "Found: $header"

			if confirm "Fix CSV header?"; then
				acquire_lock || return 1
				sed -i '1s/.*/IP,NAME,LATITUDE,LONGITUDE/' "$CSV_FILE"
				release_lock
				success "CSV header fixed"
			fi
		fi
	fi
}

#######################################
# Reads all CSV entries (excluding header)
# Returns:
#   CSV content without header
#######################################
csv_read_all() {
	[[ -f "$CSV_FILE" ]] || return 1
	tail -n +2 "$CSV_FILE" # Skip header
}

#######################################
# Gets CSV entry by IP address
# Arguments:
#   $1 - IP address to search for
# Returns:
#   0 and CSV line if found, 1 if not found
#######################################
csv_get_by_ip() {
    local ip="$1"
    [[ -f "$CSV_FILE" ]] || return 1
    grep "^${ip}," "$CSV_FILE" 2>/dev/null | head -n1 | awk -F',' '
        {
            norm_lat = sprintf("%.4f", $3)
            norm_lon = sprintf("%.4f", $4)
            print $1 "," $2 "," norm_lat "," norm_lon
        }
    ' || return 1
}

#######################################
# Gets unique location entries from CSV
# Returns:
#   Unique NAME,LATITUDE,LONGITUDE combinations
#######################################
csv_get_unique_locations() {
    if [[ ! -f "$CSV_FILE" ]]; then
        return 1
    fi
    csv_read_all | awk -F',' '
        $2 != "" && $3 != "" && $4 != "" {
            norm_lat = sprintf("%.4f", $3)
            norm_lon = sprintf("%.4f", $4)
            key = $2 "," norm_lat "," norm_lon
            if (!seen[key]++) print $2 "," norm_lat "," norm_lon
        }
    ' | sort -u
}

#######################################
# Adds new entry to CSV file with file locking
# Arguments:
#   $1 - IP address (will be validated)
#   $2 - Location name (will be sanitized)
#   $3 - Latitude
#   $4 - Longitude
# Returns:
#   0 on success, 1 on failure
#######################################
csv_add_entry() {
	local ip="$1"
	local name="$(sanitize_csv_field "$2")"
	local lat="$3"
	local lon="$4"

	# Validate IP
	if ! validate_ip "$ip"; then
		error "Invalid IP address: $ip"
		return 1
	fi

	# Acquire lock
	acquire_lock || return 1

	# Create backup
	local backup="${CSV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
	if ! cp "$CSV_FILE" "$backup" 2>/dev/null; then
		error "Failed to create backup"
		release_lock
		return 1
	fi

	# Append new entry
	if ! echo "${ip},${name},${lat},${lon}" >>"$CSV_FILE"; then
		error "Failed to write to CSV file"
		cp "$backup" "$CSV_FILE" # Restore backup
		rm -f "$backup"
		release_lock
		return 1
	fi

	# Remove backup on success
	rm -f "$backup"

	# Release lock
	release_lock

	success "Entry added  ${ICO_ARROW}  ${ip}  ${ICO_ARROW}  ${name}"
	return 0
}

#######################################
# Updates existing CSV entry with file locking
# Arguments:
#   $1 - IP address (will be validated)
#   $2 - Location name (will be sanitized)
#   $3 - Latitude
#   $4 - Longitude
# Returns:
#   0 on success, 1 on failure
#######################################
csv_update_entry() {
	local ip="$1"
	local name="$(sanitize_csv_field "$2")"
	local lat="$3"
	local lon="$4"

	# Validate IP
	if ! validate_ip "$ip"; then
		error "Invalid IP address: $ip"
		return 1
	fi

	# Acquire lock
	acquire_lock || return 1

	# Create backup
	local backup="${CSV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
	if ! cp "$CSV_FILE" "$backup" 2>/dev/null; then
		error "Failed to create backup"
		release_lock
		return 1
	fi

	# Create temporary file securely
	local tmpfile
	tmpfile=$(mktemp "${CSV_FILE}.XXXXXX") || {
		error "Failed to create temporary file"
		rm -f "$backup"
		release_lock
		return 1
	}

	# Remove old entry for this IP and write to temp file
	if ! grep -v "^${ip}," "$CSV_FILE" >"$tmpfile"; then
		error "Failed to process CSV file"
		rm -f "$tmpfile" "$backup"
		release_lock
		return 1
	fi

	# Add new entry
	if ! echo "${ip},${name},${lat},${lon}" >>"$tmpfile"; then
		error "Failed to write to temporary file"
		rm -f "$tmpfile" "$backup"
		release_lock
		return 1
	fi

	# Atomic move
	if ! mv "$tmpfile" "$CSV_FILE"; then
		error "Failed to update CSV file"
		cp "$backup" "$CSV_FILE" # Restore backup
		rm -f "$tmpfile" "$backup"
		release_lock
		return 1
	fi

	# Remove backup on success
	rm -f "$backup"

	# Release lock
	release_lock

	success "Entry updated  ${ICO_ARROW}  ${ip}  ${ICO_ARROW}  ${name}"
	return 0
}

#######################################
# Checks if location name exists in CSV
# Arguments:
#   $1 - Location name
# Returns:
#   0 if exists, 1 if not found
#######################################
csv_location_exists() {
	local name="$1"
	csv_get_unique_locations | grep -q "^${name}," 2>/dev/null
}

#######################################
# Gets location by name from CSV
# Arguments:
#   $1 - Location name
# Returns:
#   First matching location as NAME,LAT,LON
#######################################
csv_get_location_by_name() {
	local name="$1"
	csv_get_unique_locations | grep "^${name}," | head -n1
}

#######################################
# Updates location entry in CSV by replacing all rows with matching location name
# This function should only be used when a location exists WITHOUT an IP
# Arguments:
#   $1 - IP address (will be validated)
#   $2 - Location name (will be sanitized)
#   $3 - Latitude
#   $4 - Longitude
# Returns:
#   0 on success, 1 on failure
#######################################
csv_update_location() {
	local ip="$1"
	local name="$(sanitize_csv_field "$2")"
	local lat="$3"
	local lon="$4"

	# Validate IP
	if ! validate_ip "$ip"; then
		error "Invalid IP address: $ip"
		return 1
	fi

	# Acquire lock
	acquire_lock || return 1

	# Create backup
	local backup="${CSV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
	if ! cp "$CSV_FILE" "$backup" 2>/dev/null; then
		error "Failed to create backup"
		release_lock
		return 1
	fi

	# Create temporary file securely
	local tmpfile
	tmpfile=$(mktemp "${CSV_FILE}.XXXXXX") || {
		error "Failed to create temporary file"
		rm -f "$backup"
		release_lock
		return 1
	}

	# Keep header
	head -n1 "$CSV_FILE" >"$tmpfile"

	# Remove all rows with this location name (regardless of IP)
	tail -n +2 "$CSV_FILE" | grep -v ",${name}," >>"$tmpfile" || true

	# Add new entry with IP
	if ! echo "${ip},${name},${lat},${lon}" >>"$tmpfile"; then
		error "Failed to write to temporary file"
		rm -f "$tmpfile" "$backup"
		release_lock
		return 1
	fi

	# Atomic move
	if ! mv "$tmpfile" "$CSV_FILE"; then
		error "Failed to update CSV file"
		cp "$backup" "$CSV_FILE" # Restore backup
		rm -f "$tmpfile" "$backup"
		release_lock
		return 1
	fi

	# Remove backup on success
	rm -f "$backup"

	# Release lock
	release_lock

	success "Updated existing location '${name}' with IP ${ip}"
	return 0
}

### ========== INTERNET CHECK ========== ###
#######################################
# Checks internet connectivity
# Returns:
#   0 if connected, 1 if not connected
#######################################
check_internet() {
	info "Checking internet connection..."

	local test_hosts=("1.1.1.1" "8.8.8.8")

	for host in "${test_hosts[@]}"; do
		if ping -c 1 -W 2 "$host" &>/dev/null; then
			success "Internet connection active"
			return 0
		fi
	done

	error "No internet connection detected"
	exit 1
}

### ========== IP DETECTION ========== ###
#######################################
# Detects current public IP address
# Returns:
#   0 and IP address on success, 1 on failure
# Outputs:
#   IP address to stdout
#######################################
get_current_ip() {
	local ip service url

	local -a services=(
		"ipify|https://api.ipify.org"
		"ifconfig.me|https://ifconfig.me/ip"
		"icanhazip|https://icanhazip.com"
		"aws-checkip|https://checkip.amazonaws.com"
	)

	for entry in "${services[@]}"; do
		service="${entry%%|*}"
		url="${entry##*|}"

		ip=$(curl -s --max-time 3 "$url" 2>/dev/null | tr -d '[:space:]')

		# Validate IP address properly
		if validate_ip "$ip"; then
			render_dim "${LAYOUT_INDENT}  via ${service}"
			echo "$ip"
			return 0
		fi
	done

	error "Failed to detect public IP from all services"
	return 1
}

#######################################
# Normalizes a coordinate to 4 decimal places
# Arguments:
#   $1 - Coordinate value
# Returns:
#   Normalized coordinate string
#######################################
normalize_coord() {
    printf "%.4f" "$1"
}

### ========== WEATHER CONFIG MANAGEMENT ========== ###
#######################################
# Reads current location from .weather_config file
# Returns:
#    0 and location as LAT&LON on success, 1 if not set
# Outputs:
#    Location string in format: lat=XX.XXXX&lon=YY.YYYY
#######################################
weather_config_read_location() {
    local config_file="$HOME/.local/share/bin/linux-weather-bar/.weather_config"

    if [[ ! -f "$config_file" ]]; then
        warning "Config file not found: $config_file"
        return 1
    fi

    # Extract LOCATION variable from config
    local location
    location=$(grep "^readonly LOCATION=" "$config_file" | head -n1 | cut -d'"' -f2 || echo "")

    if [[ -z "$location" ]]; then
        warning "No location found in config file"
        return 1
    fi

    echo "$location"
    return 0
}

#######################################
# Updates location in .weather_config file
# Arguments:
#    $1 - Latitude
#    $2 - Longitude
# Returns:
#    0 on success, 1 on failure
#######################################
weather_config_set_location() {
    local lat lon
    lat=$(normalize_coord "$1")
    lon=$(normalize_coord "$2")
    local proposed_vals="lat=$lat&lon=$lon"

    local config_file="$HOME/.local/share/bin/linux-weather-bar/.weather_config"

    if [[ ! -f "$config_file" ]]; then
        error "Config file not found: $config_file"
        return 1
    fi

    # Read current location using the dedicated function
    local current_line
    current_line=$(weather_config_read_location 2>/dev/null || echo "")

    # Normalize current coords before comparing
    local current_lat="" current_lon=""
    local normalized_current_line=""

    if [[ -n "$current_line" ]]; then
        current_lat=$(echo "$current_line" | sed -E 's/lat=([^&]+)&.*/\1/')
        current_lon=$(echo "$current_line" | sed -E 's/.*lon=(.*)/\1/')
        current_lat=$(normalize_coord "$current_lat")
        current_lon=$(normalize_coord "$current_lon")
        normalized_current_line="lat=$current_lat&lon=$current_lon"
    fi

    if [[ "$normalized_current_line" == "$proposed_vals" ]]; then
        info "Weather config already matches current location. Skipping update."
        return 0
    fi

    local current_name="UNKNOWN"
    local proposed_name="UNKNOWN"

    # Try to find location names from CSV
    if [[ -n "$current_lat" && -n "$current_lon" ]]; then
        local match
        match=$(csv_read_all | awk -F',' -v la="$current_lat" -v lo="$current_lon" '
            BEGIN { split(la, a, "."); split(lo, b, ".") }
            {
                split($3, c, "."); split($4, d, ".")
                ca = sprintf("%.4f", $3); cb = sprintf("%.4f", $4)
                if (ca == la && cb == lo) { print $2; exit }
            }
        ')
        [[ -n "$match" ]] && current_name="$match"
    fi

    local prop_match
    prop_match=$(csv_read_all | awk -F',' -v la="$lat" -v lo="$lon" '
        {
            ca = sprintf("%.4f", $3); cb = sprintf("%.4f", $4)
            if (ca == la && cb == lo) { print $2; exit }
        }
    ')
    [[ -n "$prop_match" ]] && proposed_name="$prop_match"

    render_config_diff \
        "$config_file" \
        "$current_name" "${normalized_current_line:-none}" \
        "$proposed_name" "$proposed_vals"

    if ! confirm "Apply these coordinate changes to the weather config?"; then
        warning "Update skipped by user."
        return 0
    fi

    # Create backup
    local backup="${config_file}.bak.$(date +%Y%m%d_%H%M%S)"
    if ! cp "$config_file" "$backup" 2>/dev/null; then
        error "Failed to create backup"
        return 1
    fi

    local tmpfile
    tmpfile=$(mktemp "${config_file}.XXXXXX")

    # Update LOCATION variable in config file
    awk -v lat="$lat" -v lon="$lon" -v name="$proposed_name" '
        /^readonly LOCATION=/ {
            print "readonly LOCATION=\"lat=" lat "&lon=" lon "\"  # " name
            next
        }
        {print}
    ' "$config_file" >"$tmpfile"

    # Preserve original permissions
    chmod 600 "$tmpfile" 2>/dev/null || true

    if ! mv "$tmpfile" "$config_file"; then
        error "Failed to update config file"
        cp "$backup" "$config_file" # Restore backup
        rm -f "$tmpfile"
        return 1
    fi

    # Remove backup on success
    rm -f "$backup"

    success "Weather config updated  ${ICO_ARROW}  ${proposed_name}"
    return 0
}

### ========== USER INTERACTION ========== ###
#######################################
# Prompts user for confirmation
# Arguments:
#   $1 - Prompt message
# Returns:
#   0 if yes, 1 if no
#######################################
confirm() {
	local prompt="$1"
	local response
	read -rp "$(echo -e "${LAYOUT_INDENT}${T_WARNING}${prompt}${T_RESET}  ${T_MUTED}[Y/n]${T_RESET} ")" response
	response="${response,,}"
	[[ -z "$response" || "$response" =~ ^(y|yes)$ ]]
}

#######################################
# Validates coordinate value
# Arguments:
#   $1 - Coordinate value
#   $2 - Minimum value
#   $3 - Maximum value
#   $4 - Coordinate name (for error messages)
# Returns:
#   0 if valid, 1 if invalid
#######################################
validate_coordinate() {
	local value="$1"
	local min="$2"
	local max="$3"
	local name="$4"

	# Check format (allow negative, decimal)
	if [[ ! "$value" =~ ^-?[0-9]+\.?[0-9]*$ ]]; then
		error "$name has invalid format: $value"
		return 1
	fi

	# Check range using awk (more portable than bc)
	if awk -v val="$value" -v min="$min" -v max="$max" \
		'BEGIN { exit !(val >= min && val <= max) }'; then
		return 0
	else
		error "$name out of range [$min, $max]: $value"
		return 1
	fi
}

#######################################
# Prompts user for custom coordinates
# Returns:
#   0 and coordinates as LAT|LON on success, 1 on failure
# Outputs:
#   Coordinates to stdout in format: LAT|LON
#######################################
input_custom_coordinates() {
	{
		render_section_header "Custom Coordinates"
		render_dim "${LAYOUT_INDENT}  Format: latitude, longitude  e.g. 25.7457, 89.2589"
		render_blank
	} >&2

	local coords_input lat lon
	read -rp "$(render_prompt_str "Coordinates")" coords_input

	coords_input=$(echo "$coords_input" | tr -d ' ')

	if [[ "$coords_input" == *","* ]]; then
		lat="${coords_input%%,*}"
		lon="${coords_input##*,}"
	else
		warning "No comma found. Please enter coordinates separately." >&2
		read -rp "$(render_prompt_str "Latitude ")" lat
		read -rp "$(render_prompt_str "Longitude")" lon
	fi

	# Validate coordinates
	if ! validate_coordinate "$lat" -90 90 "Latitude"; then
		return 1
	fi

	if ! validate_coordinate "$lon" -180 180 "Longitude"; then
		return 1
	fi

	echo "$lat|$lon"
	return 0
}

#######################################
# Prompts user for location name
# Returns:
#   0 and sanitized name on success, 1 on failure
# Outputs:
#   Sanitized location name to stdout
#######################################
input_custom_location_name() {
	local name
	read -rp "$(render_prompt_str "Location name")" name

	if [[ -z "$name" ]]; then
		error "Location name cannot be empty"
		return 1
	fi

	# Sanitize the name
	name=$(sanitize_csv_field "$name")

	if [[ -z "$name" ]]; then
		error "Location name invalid after sanitization"
		return 1
	fi

	echo "$name"
	return 0
}

#######################################
# Displays location selection menu
# Returns:
#   0 and selected location as NAME|LAT|LON on success
# Outputs:
#   Selected location to stdout in format: NAME|LAT|LON
#######################################
choose_location_menu() {
	{
		render_section_header "Location Selection"
	} >&2

	local i=1
	local choice
	local -a location_list=()

	# Check if CSV file exists
	if [[ ! -f "$CSV_FILE" ]]; then
		warning "CSV file not found: $CSV_FILE" >&2
		info "Initializing new CSV file..." >&2
		csv_init
	fi

	# Read unique locations from CSV and display them to stderr
	if [[ -f "$CSV_FILE" ]]; then
		while IFS=',' read -r name lat lon; do
			if [[ -n "$name" && -n "$lat" && -n "$lon" ]]; then
				render_menu_item "$i" "$name" "(${lat}, ${lon})" >&2
				location_list+=("$name|$lat|$lon")
				((i++))
			fi
		done < <(csv_get_unique_locations 2>/dev/null || true)
	fi

	# Show message if no locations found
	if [[ ${#location_list[@]} -eq 0 ]]; then
		render_info "No saved locations found." >&2
		render_blank >&2
	fi

	# Custom option
	{
		render_menu_item "$i" "Custom" "enter new location"
		render_blank
	} >&2

	read -rp "$(render_prompt_str "Choice [1-${i}]")" choice

	if [[ ! "$choice" =~ ^[0-9]+$ ]]; then
		error "Invalid input. Please enter a number."
		choose_location_menu
		return
	fi

	if [[ "$choice" -ge 1 && "$choice" -lt "$i" ]]; then
		# Existing location selected
		local selected="${location_list[$((choice - 1))]}"
		local name="${selected%%|*}"
		local temp="${selected#*|}"
		local lat="${temp%%|*}"
		local lon="${temp##*|}"

		# Only return the selection to stdout
		echo "$name|$lat|$lon"

	elif [[ "$choice" -eq "$i" ]]; then
		# Custom location
		local name="" lat="" lon="" coords_result=""

		name=$(input_custom_location_name) || {
			choose_location_menu
			return
		}

		coords_result=$(input_custom_coordinates) || {
			choose_location_menu
			return
		}

		lat="${coords_result%%|*}"
		lon="${coords_result##*|}"

		# Only return the selection to stdout
		echo "$name|$lat|$lon"
	else
		error "Invalid choice. Please select a valid option."
		choose_location_menu
	fi
}

### ========== IP MAPPING HANDLER ========== ###
#######################################
# Handles IP mapping logic
# Arguments:
#   $1 - Current IP address
# Returns:
#   0 and location as NAME|LAT|LON on success
# Outputs:
#   Location data to stdout in format: NAME|LAT|LON
#######################################
handle_ip_mapping() {
	local current_ip="$1"
	local csv_entry=""

	# Validate IP before processing
	if ! validate_ip "$current_ip"; then
		error "Invalid IP address format: $current_ip"
		return 1
	fi

	# Try to get CSV entry for this IP
	if csv_entry=$(csv_get_by_ip "$current_ip" 2>/dev/null); then
		# IP found in CSV
		local ip="" name="" lat="" lon=""
		IFS=',' read -r ip name lat lon <<<"$csv_entry"

		if [[ -n "$name" && -n "$lat" && -n "$lon" ]]; then
			# Complete entry found
			info "IP mapping found:  ${ip}  ${ICO_ARROW}  ${name}  (${lat}, ${lon})"
			echo "$name|$lat|$lon"
			return 0
		else
			# Incomplete entry (IP exists but no location data)
			warning "IP $current_ip found in CSV but missing location data"
			info "Please select or add a location:"
			render_blank >&2

			local selected=""
			selected=$(choose_location_menu)

			local sel_name="" sel_lat="" sel_lon=""
			sel_name="${selected%%|*}"
			local temp="${selected#*|}"
			sel_lat="${temp%%|*}"
			sel_lon="${temp##*|}"

			render_blank >&2
			if confirm "Update CSV entry for IP $current_ip with location '$sel_name'?"; then
				csv_update_entry "$current_ip" "$sel_name" "$sel_lat" "$sel_lon"
			fi >&2

			echo "$sel_name|$sel_lat|$sel_lon"
			return 0
		fi
	else
		# IP not found in CSV
		warning "IP $current_ip not found in CSV database"
		info "Please select or add a location:"
		render_blank >&2

		local selected=""
		selected=$(choose_location_menu)

		local sel_name="" sel_lat="" sel_lon=""
		sel_name="${selected%%|*}"
		local temp="${selected#*|}"
		sel_lat="${temp%%|*}"
		sel_lon="${temp##*|}"

		render_blank >&2
		if confirm "Save IP $current_ip with location '$sel_name' to CSV?"; then
			# Check if this location already exists in CSV
			local existing_location
			existing_location=$(csv_get_location_by_name "$sel_name")

			if [[ -n "$existing_location" ]]; then
				# Location exists - check if any entry has an IP
				local existing_entry
				existing_entry=$(csv_read_all | awk -F',' -v name="$sel_name" '$2 == name {print; exit}')

				if [[ -n "$existing_entry" ]]; then
					local existing_ip
					existing_ip=$(echo "$existing_entry" | cut -d',' -f1)

					if [[ -z "$existing_ip" || "$existing_ip" == "" ]]; then
						# Location exists but has no IP - update it
						info "Location '$sel_name' exists without IP, updating..." >&2
						csv_update_location "$current_ip" "$sel_name" "$sel_lat" "$sel_lon"
					else
						# Location exists with a different IP - add new entry instead of replacing
						info "Location '$sel_name' already mapped to IP $existing_ip" >&2
						info "Adding new entry for current IP $current_ip..." >&2
						csv_add_entry "$current_ip" "$sel_name" "$sel_lat" "$sel_lon"
					fi
				else
					# Shouldn't happen, but add new entry
					csv_add_entry "$current_ip" "$sel_name" "$sel_lat" "$sel_lon"
				fi
			else
				# New location, just add it
				csv_add_entry "$current_ip" "$sel_name" "$sel_lat" "$sel_lon"
			fi
		fi >&2

		echo "$sel_name|$sel_lat|$sel_lon"
		return 0
	fi
}

### ========== MAIN LOGIC ========== ###
#######################################
# Main function
# Arguments:
#   All command line arguments
#######################################
main() {
	# Header
	render_banner

	# Initialize CSV
	csv_init

	# Check internet
	check_internet
	render_blank

	# Get current IP
	info "Detecting public IP address..."
	local current_ip
	current_ip=$(get_current_ip) || exit 1

	if [[ -z "$current_ip" ]]; then
		error "Could not detect public IP address"
		exit 1
	fi

	render_blank
	render_kv "${ICO_IP}  Public IP" "$current_ip"
	render_blank
	render_separator

	# Handle IP mapping and get location
	local location_data
	location_data=$(handle_ip_mapping "$current_ip")

	local name lat lon
	name="${location_data%%|*}"
	local temp="${location_data#*|}"
	lat="${temp%%|*}"
	lon="${temp##*|}"

	# Set location in weather config
	weather_config_set_location "$lat" "$lon" || {
		error "Failed to update weather config"
		exit 1
	}

	# Display final configuration
	render_config_summary "$name" "$lat" "$lon" "$current_ip"

	# Launch weather script
	render_weather_section
	render_blank

	if [[ -x "$WEATHER_SCRIPT" ]]; then
		# Capture output with a separator
		output=$(EMIT_JSON_OUTPUT=true "$WEATHER_SCRIPT")

		# Split at separator
		weather_line=$(echo "$output" | sed -n '1,/^---END-WEATHER-LINE---$/ { /^---END-WEATHER-LINE---$/!p }')
		json_response=$(echo "$output" | sed -n '/^---END-WEATHER-LINE---$/,$ { /^---END-WEATHER-LINE---$/!p }')

		echo -e "$weather_line"
		render_blank

		# Check if JSON is valid
		if echo "$json_response" | jq empty >/dev/null 2>&1; then
			if read -rp "$(render_prompt_str "Print Weather JSON?  [y/N]")" response && [[ "${response,,}" =~ ^(y|yes)$ ]]; then
				render_section_header "Weather JSON"
				echo "$json_response" | jq '.weather'
				render_thin_sep
				if read -rp "$(render_prompt_str "Print Forecast JSON? [y/N]")" response && [[ "${response,,}" =~ ^(y|yes)$ ]]; then
					render_section_header "Forecast JSON"
					echo "$json_response" | jq '.forecast'
				fi
			fi
		else
			warning "JSON response is invalid"
		fi

	else
		error "Weather script is not executable: $WEATHER_SCRIPT"
		exit 1
	fi
}

### ========== ENTRY POINT ========== ###
main "$@"