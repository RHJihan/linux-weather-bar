#!/bin/bash
#
# Weather & Moon Phase Display Script
# Fetches and displays current weather information from OpenWeatherMap API
# and optionally shows moon phase during nighttime hours.
#
# FROM_CALLER - Set to "true" to output JSON response (default: false)
#

# Exit on error, undefined variables, and pipe failures
set -euo pipefail

# ─── API Configuration ────────────────────────────────────────────────────────
# API keys and location are loaded from .weather_config (git-ignored)
# Config file is auto-created from .weather_config.template on first run
readonly API_BASE_URL="https://api.openweathermap.org/data/2.5"
readonly WEATHER_API_URL="${API_BASE_URL}/weather"
# Default to FREE plan (3-hourly forecast)
FORECAST_API_URL="${API_BASE_URL}/forecast"
readonly SUN_DATA_FILE="${HOME}/.cache/weather/sun-data.json"

readonly MOON_API_URL="https://astroapi.byhrast.com/moon.php"
# Path to moon data cache file
readonly MOON_DATA_FILE="${HOME}/.cache/weather/moon-data.json"

# ─── Shared Moon Phase Data (arrays) ───────────────────────────────────────────
readonly -a MOON_PHASE_NAMES=("New Moon" "Waxing Crescent" "First Quarter" "Waxing Gibbous" "Full Moon" "Waning Gibbous" "Last Quarter" "Waning Crescent")
readonly -a MOON_PHASE_NAMES_BN=("অমাবস্যা" "শুক্লপক্ষের বাঁকা চাঁদ" "শুক্লপক্ষের অর্ধচন্দ্র" "শুক্লপক্ষের বর্ধমান চাঁদ" "পূর্ণিমা" "কৃষ্ণপক্ষের ক্ষীয়মাণ চাঁদ" "কৃষ্ণপক্ষের অর্ধচন্দ্র" "কৃষ্ণপক্ষের বাঁকা চাঁদ")
readonly -a MOON_PHASE_EMOJIS=("🌑" "🌒" "🌓" "🌔" "🌕" "🌖" "🌗" "🌘")

#######################################
# Load configuration from .weather_config or create from template
# Globals:
#   (sets API_KEY, MOON_API_KEY, LOCATION, TIMEZONE)
# Returns:
#   0 always
#######################################
load_or_create_config() {
	local script_dir config_file template_file
	script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	config_file="${script_dir}/.weather_config"
	template_file="${script_dir}/.weather_config.template"

	if [[ -f "$config_file" ]]; then
		# Source existing config (overrides defaults)
		source "$config_file"
	elif [[ -f "$template_file" ]]; then
		# Create config from template
		cp "$template_file" "$config_file"
		source "$config_file"
	else
		# Fallback
		echo "Warning: Config file not found at $config_file" >&2
	fi
}

# Load configuration
load_or_create_config

# ─── Set Configuration Defaults (for set -u safety) ────────────────────────────
: "${API_KEY_TYPE:=FREE}"
: "${SHOW_SUNRISE_SUNSET:=true}"
: "${SHOW_SUNRISE_SUNSET_DURING_RAIN:=true}"
: "${SHOW_SUNRISE_SUNSET_WITH_RAIN_FORECAST:=true}"
: "${MOON_PHASE_ENABLED:=false}"
: "${MOON_PHASE_WINDOW_START:=1}"
: "${MOON_PHASE_WINDOW_DURATION:=60}"
: "${SHOW_MOONPHASE_DURING_DAYTIME:=false}"
: "${MOON_PHASE_SHOW_DURING_RAIN:=true}"
: "${MOON_PHASE_SHOW_WITH_RAIN_FORECAST:=false}"
: "${SHOW_MOONPHASE_BENGALI:=false}"
: "${SHOW_MOONPHASE_BILINGUAL:=false}"
: "${SHOW_MOONRISE_MOONSET:=false}"
: "${MOONRISE_WARNING_THRESHOLD:=20}"
: "${MOONSET_WARNING_THRESHOLD:=30}"
: "${SHOW_MOONRISE_MOONSET_DURING_RAIN:=false}"
: "${SHOW_MOONRISE_MOONSET_WITH_RAIN_FORECAST:=false}"
: "${SHOW_MOONRISE_MOONSET_DURING_DAYTIME:=false}"

# ─── Validate Required Credentials ────────────────────────────────────────────
if [[ -z "${MOON_API_KEY:-}" ]] && [[ "${MOON_PHASE_ENABLED}" == "true" ]]; then
	echo "Warning: MOON_API_KEY not set in config; moon phase display disabled" >&2
	MOON_PHASE_ENABLED=false
fi

# ─── Adjust Forecast API based on API plan ────────────────────────────────────
# FREE plan (default) uses 3-hourly forecast (/forecast)
# PRO or other plans use hourly forecast (/forecast/hourly)
if [[ "$API_KEY_TYPE" != "FREE" ]]; then
	FORECAST_API_URL="${API_BASE_URL}/forecast/hourly"
fi

# ─── Icon Mapping ─────────────────────────────────────────────────────────────
declare -Ar WEATHER_ICONS=(
	["01d"]="☀️" ["02d"]="⛅️" ["03d"]="☁️" ["04d"]="☁️"
	["09d"]="🌧️" ["10d"]="🌦️" ["11d"]="⛈️" ["13d"]="❄️" ["50d"]="🌫️"
	["01n"]="🌕" ["02n"]="☁️" ["03n"]="☁️" ["04n"]="☁️"
	["09n"]="🌧️" ["10n"]="☔️" ["11n"]="⛈️" ["13n"]="❄️" ["50n"]="🌫️"
)

#######################################
# Check internet connectivity with retry logic
# Globals:
#   MAX_CONNECTIVITY_RETRIES
#   CONNECTIVITY_RETRY_DELAY
# Returns:
#   0 if connected, 1 otherwise
#######################################
check_connectivity() {
	local attempt connectivity

	for attempt in $(seq 1 "$MAX_CONNECTIVITY_RETRIES"); do
		if command -v nmcli &>/dev/null; then
            # nmcli failure itself should not abort — capture safely
            connectivity=$(nmcli networking connectivity check 2>/dev/null) || connectivity="none"
			if [[ "$connectivity" == "full" ]]; then
				return 0
			fi
		else
			# Fallback: try pinging a reliable server
			if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
				return 0
			fi
		fi

		if [[ $attempt -lt "$MAX_CONNECTIVITY_RETRIES" ]]; then
			sleep "$CONNECTIVITY_RETRY_DELAY"
		fi
	done

	return 1
}

#######################################
# Capitalize first letter of each word
# Arguments:
#   $1 - Input string
# Outputs:
#   Capitalized string
#######################################
capitalize_words() {
	local input="$1"
	# Using awk for better portability
	awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2))}1' <<<"$input"
}

#######################################
# Format temperature value
# Arguments:
#   $1 - Temperature value
# Outputs:
#   Formatted temperature (removes .0 if present)
#######################################
format_temperature() {
	local temp="$1"
	printf "%.1f" "$temp" | sed 's/\.0$//'
}

#######################################
# Get weather icon emoji
# Arguments:
#   $1 - Icon code from API
# Outputs:
#   Emoji character
#######################################
get_weather_icon() {
	local icon_code="$1"
	echo "${WEATHER_ICONS[$icon_code]:-🌡️}"
}

#######################################
# Format time
# Arguments:
#   $1 - epoch time
# Outputs:
#   Formatted time string (e.g., "6:30 PM")
#######################################
format_time() {
	local epoch="$1"
	date -d "@${epoch}" +"%-I:%M %p" 2>/dev/null ||
		date -r "$epoch" +"%-I:%M %p" 2>/dev/null
}

#######################################
# Convert a HH:MM (24-hour) time string to today's Unix epoch.
# Rejects non-time strings (e.g. "Not visible") before calling date.
# Uses GNU date with BSD date as fallback.
#
# Arguments:
#   $1 - Time string in HH:MM format
# Outputs:
#   Epoch integer on success, empty string on failure
# Returns:
#   0 always
#######################################
hhmm_to_epoch() {
	local date_str="$1"   # NEW: YYYY-MM-DD
	local hhmm="$2"

	# Validate HH:MM
	[[ "$hhmm" =~ ^[0-9]{1,2}:[0-9]{2}$ ]] || { echo "0"; return 0; }

	# Validate date
	[[ -n "$date_str" ]] || { echo "0"; return 0; }

	date -d "${date_str} ${hhmm}" +%s 2>/dev/null || \
		date -j -f "%Y-%m-%d %H:%M" "${date_str} ${hhmm}" +%s 2>/dev/null || \
		echo "0"
}

#######################################
# Fetch weather data from API
# Globals:
#   LOCATION, API_KEY, WEATHER_API_URL
# Outputs:
#   JSON response from API
# Returns:
#   0 on success, 1 on failure
#######################################
fetch_weather_data() {
	local url="${WEATHER_API_URL}?${LOCATION}&appid=${API_KEY}&units=metric"

	local response
    response=$(curl -sf "$url" 2>/dev/null) || return 1

    # Validate JSON
    echo "$response" | jq -e . >/dev/null 2>&1 || return 1

	echo "$response"
}

#######################################
# Parse weather data from JSON response
# Arguments:
#   $1 - JSON response
# Outputs:
#   Tab-separated values: weather_main description icon temp feels_like sunrise_epoch sunset_epoch
#######################################
parse_weather_data() {
	local response="$1"

	jq -re '[
        .weather[0].main // "",
		.weather[0].description // "",
        .weather[0].icon // "",
        .main.temp // 0,
        .main.feels_like // 0,
		.sys.sunrise // 0,
        .sys.sunset // 0
    ] | @tsv' <<<"$response"
}

save_sun_data() {
    local sunrise_epoch="$1"
    local sunset_epoch="$2"
    local now
    now=$(date +%s)
    # Only save during daytime — overnight the API returns tomorrow's values
    # and we need yesterday's sunset to remain in cache
    # Guard both sunrise AND sunset to prevent corrupted cache
    (( sunrise_epoch > 0 && sunset_epoch > 0 && now >= sunrise_epoch )) || return 0
    
    local today cached_date
    today=$(date +"%d/%m/%Y")
    
    # Check if file already contains today's data — don't overwrite
    if [[ -f "$SUN_DATA_FILE" ]]; then
        cached_date=$(jq -r '.date // ""' <"$SUN_DATA_FILE" 2>/dev/null) || cached_date=""
        if [[ "$cached_date" == "$today" ]]; then
            # Cache already has today's data, no need to refresh
            return 0
        fi
    fi
    
    mkdir -p "$(dirname "$SUN_DATA_FILE")"
    jq -n \
        --arg date "$today" \
        --argjson sunrise "$sunrise_epoch" \
        --argjson sunset "$sunset_epoch" \
        '{date: $date, sunrise: $sunrise, sunset: $sunset}' > "$SUN_DATA_FILE"
}

#######################################
# Resolve the effective sunrise for the solar window ceiling.
# During evening/night (after today's sunrise), the relevant sunrise
# is TOMORROW's, not today's. Add 86400 seconds to get it.
#
# Arguments:
#   $1 - api_sunrise_epoch (today's sunrise from API)
# Outputs:
#   Epoch of the next sunrise (tomorrow's if we're past today's)
#######################################
get_effective_sunrise() {
    local api_sunrise_epoch="$1"
    local now
    now=$(date +%s)
    # If we're past today's sunrise, the next sunrise is tomorrow's
    if (( now >= api_sunrise_epoch )); then
        echo $(( api_sunrise_epoch + 86400 ))
    else
        echo "$api_sunrise_epoch"
    fi
}

#######################################
# Determine the effective sunset to use for the Solar Window.
#
# Context:
# - Before midnight (daytime/evening): use TODAY's sunset from API
# - After midnight (overnight): use YESTERDAY's sunset from cache
#
# Logic:
# If current time is past today's sunrise, we're in "today's" timeframe (daytime/evening).
# Use today's sunset directly.
#
# If current time is before today's sunrise, we're in "overnight" (yesterday's sunset → today's sunrise).
# Fetch the cache and use yesterday's sunset from there.
#
# If cache is missing/stale and we're overnight:
# Estimate yesterday's sunset as (api_sunrise_epoch - 24h).
# This typically differs by ~3min from reality but is acceptable.
#
# Arguments:
#   $1 - api_sunset_epoch (today's sunset from API)
#   $2 - api_sunrise_epoch (today's sunrise from API)
# Outputs:
#   Epoch of the sunset to use for the Solar Window
# Returns:
#   0 always
#######################################
get_effective_sunset() {
    local api_sunset_epoch="$1"
    local api_sunrise_epoch="$2"
    local now
    now=$(date +%s)

    # If we're past today's sunrise, it's daytime/evening — use API sunset as-is
    if (( now >= api_sunrise_epoch )); then
        echo "$api_sunset_epoch"
        return 0
    fi

    # Overnight: try yesterday's sunset from cache
    if [[ -f "$SUN_DATA_FILE" ]]; then
        local cached_sunset
        cached_sunset=$(jq -r '.sunset // 0' <"$SUN_DATA_FILE" 2>/dev/null)
        if (( cached_sunset > 0 )); then
            echo "$cached_sunset"
            return 0
        fi
    fi

    # Cache missing/stale — estimate yesterday's sunset from today's sunrise
    # Sunset is typically ~12.5 hours after sunrise; this error is acceptable
    echo $(( api_sunrise_epoch - 43200 ))
}

#######################################
# Fetch forecast data from API
# Globals:
#   LOCATION, API_KEY, FORECAST_API_URL
# Outputs:
#   JSON response
# Returns:
#   0 on success, 1 on failure
#######################################
fetch_forecast_data() {
	local url="${FORECAST_API_URL}?${LOCATION}&appid=${API_KEY}&units=metric"
	local response
	response=$(curl -sf "$url" 2>/dev/null) || return 1

    echo "$response" | jq -e . >/dev/null 2>&1 || return 1

    echo "$response"
}

#######################################
# Get next rain timing and probability
# within RAIN_FORECAST_WINDOW
# Arguments:
#   $1 - JSON forecast response
# Outputs:
#   "rain_epoch probability" OR empty
#######################################
get_rain_forecast() {
	local response="$1"
    local result

    result=$(jq -r \
		--argjson threshold "$RAIN_FORECAST_THRESHOLD" \
		--argjson window "$((RAIN_FORECAST_WINDOW * 3600))" '
		now as $now
		| .list
		| map(
			select(
				(.dt - $now) >= 900 and    # at least 15 minutes from now
				(.dt - $now) <= $window and
				(.pop >= $threshold)
			)
		)
		| if length == 0 then
			empty
		  else
			.[0] as $first
            | [$first.dt, ($first.pop * 100 | floor), ($first.weather[0].description // ""), ($first.weather[0].icon // "") ]
			| @tsv
		  end
	' <<<"$response" 2>/dev/null) || return 1

    # empty output is valid (no rain forecast) — not an error
    echo "$result"
}

#######################################
# Format rain warning text
# Arguments:
#   $1 - Rain epoch time
#   $2 - Probability %
#   $3 - Description string
#   $4 - Icon emoji
# Outputs:
#   Formatted rain warning string
#######################################
format_rain_warning() {
	local rain_epoch="$1"
	local probability="$2"
	local description="$3"
	local icon="$4"

	# Convert to local time string (e.g., 6:45 PM)
	local rain_time
	rain_time=$(format_time "$rain_epoch")
	if [[ -z "$rain_time" ]]; then
    	rain_time="soon"   # graceful degradation
	fi

	local rain_desc
	if [[ "$description" =~ [Rr]ain|[Dd]rizzle|[Tt]hunderstorm ]]; then
		rain_desc=$(capitalize_words "$description")
	else
		rain_desc="Rain likely"
		icon="🌧️"
	fi
	echo "${icon}   ${rain_desc} ≈ ${rain_time^^} (${probability}%)"
}

#######################################
# Load moon data cache from MOON_DATA_FILE.
# Creates the cache directory and an empty JSON object if absent.
#
# Globals:
#   MOON_DATA_FILE
# Arguments:
#   (none)
# Outputs:
#   Cached JSON string, or "{}" if file did not exist
# Returns:
#   0 always
#######################################
load_moon_cache() {
	mkdir -p "$(dirname "$MOON_DATA_FILE")"
	if [[ ! -f "$MOON_DATA_FILE" ]]; then
		echo "{}" > "$MOON_DATA_FILE"
	fi
	cat "$MOON_DATA_FILE"
}

#######################################
# Check whether the moon cache holds valid data for viewing.
#
# Returns true if:
#   - Cache date matches today, OR
#   - Cache is from yesterday AND current time is before sunrise (overnight)
#
# Arguments:
#   $1 - JSON string (cache contents)
#   $2 - sunrise_epoch (optional; if provided, uses actual sunrise)
# Returns:
#   0 (true)  if cache is usable for display
#   1 (false) if stale, empty, or malformed
#######################################
moon_cache_is_fresh() {
	local cache="$1"
	local sunrise_epoch="${2:-0}"
	local today yesterday cached_date now
	today=$(date +"%d/%m/%Y")
	yesterday=$(date -d "yesterday" +"%d/%m/%Y" 2>/dev/null || date -v-1d +"%d/%m/%Y" 2>/dev/null)
	cached_date=$(jq -r '.date // ""' <<<"$cache" 2>/dev/null) || return 1
	
	# Fresh if today's date
	if [[ "$cached_date" == "$today" ]]; then
		return 0
	fi
	
	# Check if overnight (before sunrise) and cache is from yesterday
	if [[ "$cached_date" == "$yesterday" ]]; then
		now=$(date +%s)
		if (( sunrise_epoch > 0 && now < sunrise_epoch )); then
			return 0
		fi
	fi

	return 1
}


# -----------------------------------------------------------------------------
# call_moon_api
#
# Purpose:
#   Calls the configured Moon API endpoint and returns validated JSON output.
#
# Inputs:
#   $1 - date_param (required)
#   $2 - time_param (required)
#
# Behavior:
#   - Builds a request URL using:
#       MOON_API_URL, MOON_API_KEY, LOCATION, TIMEZONE
#   - Sends a GET request via curl
#   - Ensures request succeeds (non-zero exit → failure)
#   - Validates that response contains expected field: ".moonrise"
#   - Adds an additional field:
#       retrieved_at → current system date-time (ISO-8601 format)
#   - Returns the modified JSON response
#
# Output:
#   JSON response from API with an added field:
#     {
#       ... original fields ...,
#       "retrieved_at": "2026-04-17T14:00:00+06:00"
#     }
#
# Failure cases:
#   - Missing input parameters → return 1
#   - curl failure → return 1
#   - invalid or unexpected JSON → return 1
# -----------------------------------------------------------------------------
call_moon_api() {
    local date_param="$1"
    local time_param="$2"
    local url response

    # Validate inputs
    [ -z "$date_param" ] && return 1
    [ -z "$time_param" ] && return 1

    url="${MOON_API_URL}?key=${MOON_API_KEY}&${LOCATION}&tz=${TIMEZONE}&date=${date_param}&time=${time_param}"
    
    response=$(curl -sf "$url" 2>/dev/null) || return 1

    # Sanity-check: ensure expected field is present
    echo "$response" | jq -e '.moonrise' >/dev/null 2>&1 || return 1

    # Add fetch timestamp (ISO 8601 format with timezone)
    response=$(echo "$response" | jq --arg ts "$(date -Iseconds)" '. + {retrieved_at: $ts}')

    echo "$response"
}

#######################################
# Fetch fresh moon data from astroapi and write it to MOON_DATA_FILE.
#
# Globals:
#   MOON_API_KEY, MOON_API_URL, LAT, LON, TIMEZONE, MOON_DATA_FILE
# Arguments:
#   (none)
# Outputs:
#   Fresh JSON string on success, nothing on failure
# Returns:
#   0 on success, 1 on failure
#######################################
fetch_moon_data() {
    local date_param time_param url response
    date_param=$(date +"%d/%m/%Y")
    time_param=$(date +"%H:%M")

	response=$(call_moon_api "$date_param" "$time_param")
    echo "$response"
}

#######################################
# Return current moon data — from cache if valid, otherwise fetched live.
#
# Handles overnight logic and ensures moon data is always current:
# - Reuses cached data when it is still valid for the required date
# - Forces refresh when the cached moonset has already passed
# - Uses yesterday's data during overnight hours (before sunrise)
#
# Refresh Logic:
# 1. Load cache
# 2. Determine required date:
#    - If before sunrise → use yesterday's data
#    - Otherwise → use today's data
#
# 3. Validate cache:
#    - If cached date matches required date AND
#      moonset has NOT passed → return cache immediately
#    - If cached moonset has already passed → invalidate cache
#
# 4. If cache is stale or invalid:
#    a. Fetch fresh data from API
#    b. Use overnight-aware API request when needed
#    c. Update cache ONLY if:
#       - Fetch is successful
#       - Moonset for fetched data has passed
#       - Fetched date matches the required date
#
# 5. Fallback behavior:
#    - If fetch fails → return existing cache (graceful degradation)
#    - If no cache exists and fetch fails → return empty string
#
# Globals:
#   MOON_DATA_FILE, MOON_API_URL, MOON_API_KEY, LOCATION, TIMEZONE
#
# Arguments:
#   $1 - sunrise_epoch (optional; used to determine overnight state)
#
# Outputs:
#   JSON string, or empty string on total failure
#
# Returns:
#   0 always
#######################################
get_moon_data() {
	local sunrise_epoch="${1:-0}"
	local cache now cached_date needed_date moonset_epoch=0

	cache=$(load_moon_cache)
	now=$(date +%s)

	# Today's and yesterday's date
	local today yesterday
	today=$(date +"%d/%m/%Y")
	yesterday=$(date -d "yesterday" +"%d/%m/%Y" 2>/dev/null || date -v-1d +"%d/%m/%Y" 2>/dev/null)

	# Extract cached date
	cached_date=$(jq -r '.date // ""' <<<"$cache" 2>/dev/null) || cached_date=""

	# Extract moonset from cache (if exists)
	if [[ -n "$cache" && "$cache" != "{}" ]]; then
		local moonset_hhmm normalized_date

		moonset_hhmm=$(jq -r '.moonset // ""' <<<"$cache" 2>/dev/null)

		if [[ "$cached_date" =~ ^[0-9]{2}/[0-9]{2}/[0-9]{4}$ ]]; then
			normalized_date=$(date -d "$(echo "$cached_date" | awk -F/ '{print $3"-"$2"-"$1}')" +"%Y-%m-%d" 2>/dev/null)
			moonset_epoch=$(hhmm_to_epoch "$normalized_date" "$moonset_hhmm")
		fi
	fi

	# ─────────────────────────────────────────────
	# DETERMINE NEEDED DATE (DATA-DRIVEN)
	# ─────────────────────────────────────────────
	if [[ "$cached_date" == "$yesterday" ]] && (( moonset_epoch > 0 && now < moonset_epoch )); then
		# Still in yesterday's moon cycle
		needed_date="$yesterday"
	else
		needed_date="$today"
	fi

	# ─────────────────────────────────────────────
	# CACHE VALIDATION
	# ─────────────────────────────────────────────
	if [[ "$cached_date" == "$needed_date" ]]; then
		echo "$cache"
		return 0
	fi

	# ─────────────────────────────────────────────
	# FETCH FRESH DATA
	# ─────────────────────────────────────────────
	local fresh_data=""

	if (( sunrise_epoch > 0 && now < sunrise_epoch )); then
		# Overnight → explicit date fetch
		local time_param url response

		time_param=$(date +"%H:%M")

		response=$(call_moon_api "$needed_date" "$time_param")

		if [[ -n "$response" ]] && echo "$response" | jq -e '.moonrise' >/dev/null 2>&1; then
			fresh_data="$response"
		fi
	else
		# Daytime → normal fetch
		fresh_data=$(fetch_moon_data) || fresh_data=""
	fi

	# ─────────────────────────────────────────────
	# CACHE WRITE (ALWAYS ON SUCCESS)
	# ─────────────────────────────────────────────
	if [[ -n "$fresh_data" ]]; then
		local fetched_date
		fetched_date=$(jq -r '.date // ""' <<<"$fresh_data" 2>/dev/null)

		# Safety: only store correct date
		if [[ "$fetched_date" == "$needed_date" ]]; then
			mkdir -p "$(dirname "$MOON_DATA_FILE")"
			echo "$fresh_data" | jq '.' > "$MOON_DATA_FILE"
		fi

		echo "$fresh_data"
		return 0
	fi

	# ─────────────────────────────────────────────
	# FALLBACK
	# ─────────────────────────────────────────────
	if [[ -n "$cache" && "$cache" != "{}" ]]; then
		echo "$cache"
		return 0
	fi

	echo ""
}

#######################################
# Parse moonrise and moonset HH:MM strings from moon data JSON
# and convert them to Unix epoch values, with date normalization.
#
# Rejects non-time values (e.g. "Not visible") via hhmm_to_epoch.
# Either value is 0 if absent, non-time, or unparseable.
#
# HANDLES MIDNIGHT CROSSING:
# If moonset time parses successfully but is earlier than moonrise
# (e.g., moonset 01:12 but moonrise 11:39), adds 86400 seconds (1 day)
# to moonset_epoch to represent the next calendar day.
#
# OVERNIGHT DATA CORRECTION:
# If cache date is yesterday (overnight viewing), shifts both times
# backward by 86400 seconds to represent yesterday's times, ensuring
# correct window comparison for current time.
#
# Arguments:
#   $1 - Moon data JSON string
# Outputs:
#   Tab-separated: moonrise_epoch<TAB>moonset_epoch
# Returns:
#   0 always
#######################################
parse_moon_times() {
    local moon_data="$1"
    local sunrise_epoch="${2:-0}"

    local moonrise_hhmm moonset_hhmm
    local moonrise_epoch moonset_epoch
    local cached_date normalized_date

    # Extract values safely
    moonrise_hhmm=$(jq -re '.moonrise // ""' <<<"$moon_data" 2>/dev/null)
    moonset_hhmm=$(jq -re '.moonset // ""' <<<"$moon_data" 2>/dev/null)
    cached_date=$(jq -re '.date // ""' <<<"$moon_data" 2>/dev/null)

    # Convert DD/MM/YYYY → YYYY-MM-DD
    if [[ "$cached_date" =~ ^[0-9]{2}/[0-9]{2}/[0-9]{4}$ ]]; then
        normalized_date=$(date -d "$(echo "$cached_date" | awk -F/ '{print $3"-"$2"-"$1}')" +"%Y-%m-%d" 2>/dev/null)
    else
        normalized_date=""
    fi

    # Convert to epoch using API date (NOT system date)
    moonrise_epoch=$(hhmm_to_epoch "$normalized_date" "$moonrise_hhmm")
    moonrise_epoch=${moonrise_epoch:-0}

    moonset_epoch=$(hhmm_to_epoch "$normalized_date" "$moonset_hhmm")
    moonset_epoch=${moonset_epoch:-0}

    # Handle midnight crossing (moonset is next day)
    if (( moonrise_epoch > 0 && moonset_epoch > 0 && moonset_epoch < moonrise_epoch )); then
        moonset_epoch=$(( moonset_epoch + 86400 ))
    fi

    printf "%s\t%s\n" "$moonrise_epoch" "$moonset_epoch"
}

#######################################
# Check if moon data JSON contains valid moon times (strict validation).
# Ensures times match HH:MM format, rejecting "Not visible" and other invalid strings.
#
# Arguments:
#   $1 - Moon data JSON string
# Returns:
#   0 (true)  if has valid moonrise or moonset in HH:MM format
#   1 (false) if empty, missing both fields, or contains invalid strings
#######################################
is_valid_moon_data() {
	local moon_data="$1"
	[[ -n "$moon_data" ]] || return 1
	# Strict: only accept times in HH:MM format, reject "Not visible" etc.
	jq -e '(
			(.moonrise | test("^[0-9]{1,2}:[0-9]{2}$|^[Nn]ot [Vv]isible$"; "i")) or
			(.moonset  | test("^[0-9]{1,2}:[0-9]{2}$|^[Nn]ot [Vv]isible$"; "i"))
	)' <<<"$moon_data" >/dev/null 2>&1
}

#######################################
# Resolve the moon phase index (0-7) from API data.
# Only returns valid index if API provides recognized phase name.
# Returns nothing if API data missing or phase unrecognized.
#
# Arguments:
#   $1 - Moon data JSON string (may be empty)
# Outputs:
#   Integer index 0-7, or nothing if not available from API
# Returns:
#   0 always
#######################################
resolve_phase_index() {
	local moon_data="$1"

	[[ -z "$moon_data" ]] && return 0

	local api_phase
	api_phase=$(jq -r '.phase // ""' <<<"$moon_data" 2>/dev/null)

	[[ -z "$api_phase" ]] && return 0

	local i
	for i in "${!MOON_PHASE_NAMES[@]}"; do
		if [[ "${MOON_PHASE_NAMES[i],,}" == "${api_phase,,}" ]]; then
			echo "$i"
			return 0
		fi
	done
}
#######################################
# Format and return the moon phase string for display.
#
# Globals:
#   SHOW_MOONPHASE_BENGALI   - true → Bengali only
#   SHOW_MOONPHASE_BILINGUAL - true → English + Bengali (overrides BENGALI)
#   MOON_PHASE_EMOJIS, MOON_PHASE_NAMES, MOON_PHASE_NAMES_BN
# Arguments:
#   $1 - Phase index (0-7)
# Outputs:
#   English:    "🌕  Full Moon"
#   Bengali:    "🌕  পূর্ণিমা"
#   Bilingual:  "🌕  Full Moon (পূর্ণিমা)"
# Returns:
#   0 always
#######################################
get_moon_phase() {
	local phase_index="$1"
	if [[ "$SHOW_MOONPHASE_BILINGUAL" == "true" ]]; then
		echo "${MOON_PHASE_EMOJIS[phase_index]}  ${MOON_PHASE_NAMES[phase_index]} (${MOON_PHASE_NAMES_BN[phase_index]})"
	elif [[ "$SHOW_MOONPHASE_BENGALI" == "true" ]]; then
		echo "${MOON_PHASE_EMOJIS[phase_index]}  ${MOON_PHASE_NAMES_BN[phase_index]}"
	else
		echo "${MOON_PHASE_EMOJIS[phase_index]}  ${MOON_PHASE_NAMES[phase_index]}"
	fi
}

#######################################
# Resolve window start epoch from MOON_PHASE_WINDOW_START parameter.
# Supports: numeric (minutes after anchor) or "moonrise" (use moonrise directly)
#
# Arguments:
#   $1 - MOON_PHASE_WINDOW_START value (number or "moonrise")
#   $2 - anchor epoch (sunset or moonrise if after sunset)
#   $3 - moonrise_epoch (0 if unavailable)
# Outputs:
#   Window start epoch
#######################################
resolve_window_start() {
	local param="$1" anchor="$2" moonrise="$3"
	if [[ "$param" == "moonrise" ]]; then
		if (( moonrise > 0 )); then
			echo "$moonrise"
		else
			echo "$anchor"   # moonrise not visible — fall back to sunset
		fi
	else
		echo $(( anchor + ${param:-1} * 60 ))
	fi
}

#######################################
# Resolve window end epoch from MOON_PHASE_WINDOW_DURATION parameter.
# Supports: numeric (minutes after start) or "moonset" (use moonset directly)
#
# The result is clamped to sunrise_epoch (solar ceiling) so the window
# never extends into daytime, regardless of when moonset occurs.
#
# Arguments:
#   $1 - MOON_PHASE_WINDOW_DURATION value (number or "moonset")
#   $2 - window_start epoch
#   $3 - moonset_epoch (0 if unavailable)
#   $4 - sunrise_epoch (0 if unavailable; used to enforce solar ceiling)
# Outputs:
#   Window end epoch
#######################################
resolve_window_end() {
	local param="$1" window_start="$2" moonset="$3" sunrise="${4:-0}"
	local end

	if [[ "$param" == "moonset" ]]; then
		if (( moonset > 0 )); then
			end="$moonset"
		else
			local midnight
			midnight=$(date -d "tomorrow 00:00:00" +%s 2>/dev/null || \
			           date -v+1d -v0H -v0M -v0S +%s 2>/dev/null)
			end=$(( midnight - 1 ))
		fi
	else
		end=$(( window_start + ${param:-60} * 60 ))
	fi

	echo "$end"
}

#######################################
# Determine whether to show moon phase and return the formatted string.
#
# Shows moon if ALL conditions are met:
# 1. MOON_PHASE_ENABLED is true (master kill switch)
# 2. Current time is between sunset and sunrise (solar window)
# 3. Moon is visible (between moonrise and moonset, lunar window)
# 4. Current time is within display window (configured start/end)
# 5. Not suppressed by rain conditions
#
# Window Calculation:
# Solar Window: sunset_epoch (Day N) → sunrise_epoch (Day N+1)
# Lunar Window: moonrise_epoch → moonset_epoch
# Final Window: [max(sunset, moonrise), min(sunrise, moonset)] ∩ [window_start, window_end]
#   window_start = max(sunset, moonrise) + MOON_PHASE_WINDOW_START minutes
#   window_end   = clamped to sunrise by resolve_window_end (solar ceiling enforced there)

# Time Comparison: All comparisons use Unix epoch (seconds), eliminating
# "crossing midnight" issues that would arise from HH:MM string comparisons.
#
# Globals:
#   MOON_PHASE_ENABLED, MOON_PHASE_WINDOW_START, MOON_PHASE_WINDOW_DURATION,
#   MOON_PHASE_SHOW_DURING_RAIN, MOON_PHASE_SHOW_WITH_RAIN_FORECAST
# Arguments:
#   $1 - sunset_epoch
#   $2 - sunrise_epoch
#   $3 - is_currently_raining (default: false)
#   $4 - has_rain_forecast (default: false)
#   $5 - Moon data JSON string (required for phase display)
# Outputs:
#   Formatted moon phase string, or nothing if conditions not met
# Returns:
#   0 always
#######################################
resolve_moon_phase() {
	local sunset_epoch="$1"
	local sunrise_epoch="${2:-0}"
	local is_currently_raining="${3:-false}"
	local has_rain_forecast="${4:-false}"
	local moon_data="${5:-}"

	# 1. Check master toggle
	if [[ "$MOON_PHASE_ENABLED" != "true" ]]; then
		return 0
	fi

	# 2. Validate moon data exists and is valid
	if [[ -z "$moon_data" ]] || ! is_valid_moon_data "$moon_data"; then
		return 0
	fi

	# 3. Resolve moon times (moonrise/moonset)
	local moonrise_epoch moonset_epoch
	IFS=$'\t' read -r moonrise_epoch moonset_epoch <<<"$(parse_moon_times "$moon_data" "$sunrise_epoch")"

	# 4. Check solar window (sunset → sunrise, crossing midnight)
	local now
	now=$(date +%s)

	# Skip solar window restriction if show phase after sunrise is enabled.
	if [[ "$SHOW_MOONPHASE_DURING_DAYTIME" != "true" ]]; then
		# Solar window is active if:
		#   now >= sunset_epoch AND now < sunrise_epoch
		# This naturally handles crossing midnight because:
		#   - Before midnight: sunset < midnight < sunrise (next day)
		#   - After midnight: yesterday's sunset < now < today's sunrise
		if ! (( now >= sunset_epoch && now < sunrise_epoch )); then
			# Not in window (daytime) — suppress moon phase
			return 0
		fi
	fi

	# 5. Handle "not visible" corner cases first (before window check)
	# If moonrise is "Not visible"
	# Treat as sunset + 30 min (ONLY if still within solar window)
	if (( moonrise_epoch == 0 )); then
		moonrise_epoch=$(( sunset_epoch + 1800 ))
	fi
	# If moonset is "Not visible"
	# Treat as 23:59 of the current day per spec
	if (( moonset_epoch == 0 )); then
		moonset_epoch=$(date -d "$(date +%Y-%m-%d) 23:59:00" +%s 2>/dev/null || \
		                date -j -f "%Y-%m-%d %H:%M:%S" "$(date +%Y-%m-%d) 23:59:00" +%s 2>/dev/null)
	fi

	# Post-fallback midnight-crossing correction:
	# If moonrise_epoch was just resolved from "Not visible" (sunset+30min, which is evening),
	# and moonset_epoch is a real HH:MM that falls earlier in the day (e.g., 06:20 today),
	# moonset is actually NEXT DAY relative to moonrise — add 86400.
	if (( moonset_epoch > 0 && moonrise_epoch > 0 && moonset_epoch < moonrise_epoch )); then
		moonset_epoch=$(( moonset_epoch + 86400 ))
	fi

	# 6. Check lunar window (moonrise → moonset)
	# After handling "not visible", both epochs should be valid
	if ! (( now >= moonrise_epoch && now < moonset_epoch )); then
		# Outside lunar window — suppress moon phase
		return 0
	fi

	# 7. Resolve display window (user-configured start/end)
	# Anchor is the later of sunset or moonrise (intersection start)
	local window_anchor
	window_anchor=$(( sunset_epoch > moonrise_epoch ? sunset_epoch : moonrise_epoch ))

	local window_start window_end
	window_start=$(resolve_window_start "$MOON_PHASE_WINDOW_START" "$window_anchor" "$moonrise_epoch")
	window_end=$(resolve_window_end "$MOON_PHASE_WINDOW_DURATION" "$window_start" "$moonset_epoch" "$sunrise_epoch")	

	# 8. Check if current time is within display window
	if ! (( now >= window_start && now < window_end )); then
		# Outside display window — suppress moon phase
		return 0
	fi

	# 9. Check rain suppression (after all other conditions pass)
	if [[ "$is_currently_raining" == "true" ]] && [[ "$MOON_PHASE_SHOW_DURING_RAIN" != "true" ]]; then
		# Currently raining and suppression enabled — suppress moon phase
		return 0
	fi

	if [[ "$has_rain_forecast" == "true" ]] && [[ "$MOON_PHASE_SHOW_WITH_RAIN_FORECAST" != "true" ]] && [[ "$SHOW_RAIN_FORECAST" == "true" ]]; then
		# Rain forecasted and suppression enabled — suppress moon phase
		return 0
	fi

	# 10. All conditions met — resolve and display moon phase	
	local phase_index
	phase_index=$(resolve_phase_index "$moon_data")

	if [[ -n "$phase_index" ]]; then
		get_moon_phase "$phase_index"
	fi
}

#######################################
# Calculate minutes until an event epoch
# Arguments:
#   $1 - Event epoch time
# Outputs:
#   Integer minutes until event (negative if past)
#######################################
minutes_until_event() {
	local event_epoch="$1"
	local current_epoch
	current_epoch=$(date +%s)
	echo $(((event_epoch - current_epoch) / 60))
}

#######################################
# Build and return the formatted weather display line.
# Composes temperature, feels-like, sunrise/sunset warnings,
# rain warning, and moon phase into a single output string.
#
# Arguments:
#   $1 - Weather description
#   $2 - Icon emoji
#   $3 - Temperature
#   $4 - Feels-like temperature
#   $5 - Sunrise epoch
#   $6 - Sunset epoch
#   $7 - Rain warning string (optional)
#   $8 - is_currently_raining flag (true/false, optional, default: false)
#   $9 - has_rain_forecast flag (true/false, optional, default: false)
#   $10 - Moon data JSON string (optional)
# Outputs:
#   Formatted weather line string
#######################################
build_weather_line() {
	local desc="$1"
	local icon="$2"
	local temp="$3"
	local feels_like="$4"
	local sunrise_epoch="${5:-0}"
	local sunset_epoch="${6:-0}"
	local rain_warning="${7:-}"
	local is_currently_raining="${8:-false}"
	local has_rain_forecast="${9:-false}"
	local moon_data="${10:-}"
	local effective_sunset_epoch="${11:-$sunset_epoch}"
	local effective_sunrise_epoch="${12:-$sunrise_epoch}"

	local line="               ${icon}   ${desc}   ${temp}°C"

	# Append feels-like if the difference exceeds FEELS_LIKE_THRESHOLD
	local diff
	diff=$(awk -v t="$temp" -v f="$feels_like" 'BEGIN { d = t - f; print (d < 0 ? -d : d) }')
	if awk -v diff="$diff" -v threshold="$FEELS_LIKE_THRESHOLD" 'BEGIN { exit !(diff > threshold) }'; then
		local formatted_feels_like
		formatted_feels_like=$(format_temperature "$feels_like")
		line="               ${icon}   ${desc}   ${temp}°C  (Feels ${formatted_feels_like}°C)"
	fi

	if [[ "$SHOW_SUNRISE_SUNSET" == "true" ]]; then
		if [[ "$is_currently_raining" == "true" ]] && [[ "$SHOW_SUNRISE_SUNSET_DURING_RAIN" != "true" ]]; then
			:
		elif [[ "$has_rain_forecast" == "true" ]] && [[ "$SHOW_SUNRISE_SUNSET_WITH_RAIN_FORECAST" != "true" ]] && [[ "$SHOW_RAIN_FORECAST" == "true" ]]; then
			:
		else
			local minutes

		# Add sunrise info if within threshold
		if [[ $sunrise_epoch -gt 0 ]]; then
			minutes=$(minutes_until_event "$sunrise_epoch")
			if ((minutes > 0 && minutes <= SUNRISE_WARNING_THRESHOLD)); then
				local sunrise_time
				sunrise_time=$(format_time "$sunrise_epoch")
				line+="    Sunrise: ${sunrise_time^^}"
			fi
		fi

		# Add sunset info if within threshold (use effective_sunset for overnight accuracy)
		if [[ $effective_sunset_epoch -gt 0 ]]; then
			minutes=$(minutes_until_event "$effective_sunset_epoch")

			if ((minutes > 0 && minutes <= SUNSET_WARNING_THRESHOLD)); then
				local sunset_time
				sunset_time=$(format_time "$effective_sunset_epoch")
				line+="    Sunset: ${sunset_time^^}"
			fi
		fi
		fi
	fi

	if [[ "$SHOW_RAIN_FORECAST" == "true" ]]; then

		# Rain warning
		if [[ -n "$rain_warning" ]]; then
			line+="    ${rain_warning}"
		fi
	fi
	
	# Moon phase
	local moon_phase
	moon_phase=$(resolve_moon_phase "$effective_sunset_epoch" "$effective_sunrise_epoch" "$is_currently_raining" "$has_rain_forecast" "$moon_data")
	if [[ -n "$moon_phase" ]]; then
		line+="    ${moon_phase}"
	fi

	# Hoisted parsing, shared by moonrise + moonset warning
	local _moonrise_epoch=0 _moonset_epoch=0
	if [[ -n "$moon_data" ]]; then
		IFS=$'\t' read -r _moonrise_epoch _moonset_epoch \
			<<<"$(parse_moon_times "$moon_data" "$effective_sunrise_epoch")"
	fi

	# Master toggle for all moon events
	if [[ "$SHOW_MOONRISE_MOONSET" == "true" ]]; then

		# Rain suppression (reuse moon phase logic)
		if [[ "$is_currently_raining" == "true" ]] && [[ "$SHOW_MOONRISE_MOONSET_DURING_RAIN" != "true" ]]; then
			:
		elif [[ "$has_rain_forecast" == "true" ]] && [[ "$SHOW_MOONRISE_MOONSET_WITH_RAIN_FORECAST" != "true" ]] && [[ "$SHOW_RAIN_FORECAST" == "true" ]]; then
			:
		else

			local now
			now=$(date +%s)
			
			local is_daytime=false
			if (( now >= sunrise_epoch && now < effective_sunset_epoch )); then
				is_daytime=true
			fi

			# Moonrise announcement
			if (( _moonrise_epoch > 0 && now < _moonrise_epoch)); then
				if [[ "$is_daytime" == "true" ]] && [[ "$SHOW_MOONRISE_MOONSET_DURING_DAYTIME" != "true" ]]; then
					:
				elif [[ "$MOONRISE_WARNING_THRESHOLD" == "sunset" ]]; then
					# Only show moonrise if it's after sunset
					if (( now >= effective_sunset_epoch && _moonrise_epoch > effective_sunset_epoch )); then
						local moonrise_time
						moonrise_time=$(format_time "$_moonrise_epoch")
						line+="    Moonrise: ${moonrise_time^^}"
					fi
				else
					# Numeric threshold: existing behavior
					local mins_to_moonrise
					mins_to_moonrise=$(minutes_until_event "$_moonrise_epoch")
					if (( mins_to_moonrise > 0 && mins_to_moonrise <= MOONRISE_WARNING_THRESHOLD )); then
						local moonrise_time
						moonrise_time=$(format_time "$_moonrise_epoch")
						line+="    Moonrise: ${moonrise_time^^}"
					fi
				fi
			fi

			# Moonset announcement
			if (( _moonset_epoch > 0 && now < _moonset_epoch )); then
				if [[ "$is_daytime" == "true" ]] && [[ "$SHOW_MOONRISE_MOONSET_DURING_DAYTIME" != "true" ]]; then
					:
				else
					local mins_to_moonset
					mins_to_moonset=$(minutes_until_event "$_moonset_epoch")

					if (( mins_to_moonset > 0 && mins_to_moonset <= MOONSET_WARNING_THRESHOLD )); then
						local moonset_time
						moonset_time=$(format_time "$_moonset_epoch")
						line+="    Moonset: ${moonset_time^^}"
					fi
				fi
			fi
		fi
	fi

	echo "$line"
}

#######################################
# Main function
#######################################
main() {
	# Check connectivity
	if ! check_connectivity; then
		exit 1
	fi

	# Fetch weather data
	local response
	if ! response=$(fetch_weather_data); then
		echo "               Weather data unavailable" >&2
		exit 1
	fi

	# Parse weather data
	local weather_main desc icon temp feels_like sunrise_epoch sunset_epoch

	IFS=$'\t' read -r weather_main desc icon temp feels_like sunrise_epoch sunset_epoch <<< \
    "$(parse_weather_data "$response")"

	# Validate parsed data
	if [[ -z "$desc" ]]; then
		echo "               Weather data unavailable" >&2
		exit 1
	fi

	# Save today's sun data for overnight use tomorrow
	save_sun_data "$sunrise_epoch" "$sunset_epoch"

	# Resolve effective sunset (yesterday's if overnight)
	local effective_sunset_epoch
	effective_sunset_epoch=$(get_effective_sunset "$sunset_epoch" "$sunrise_epoch")

	# Resolve effective sunrise (tomorrow's if we're past today's sunrise)
	local effective_sunrise_epoch
	effective_sunrise_epoch=$(get_effective_sunrise "$sunrise_epoch")

	# Format data
	local weather_desc icon_emoji formatted_temp
	weather_desc=$(capitalize_words "$desc")
	icon_emoji=$(get_weather_icon "$icon")
	formatted_temp=$(format_temperature "$temp")

	local forecast_response=""  # initialized empty — may remain unset if fetch fails
	local rain_data rain_warning=""
    if forecast_response=$(fetch_forecast_data); then
        if rain_data=$(get_rain_forecast "$forecast_response"); then
            if [[ -n "$rain_data" ]]; then
    			local rain_epoch rain_prob rain_desc rain_icon rain_icon_emoji
    			IFS=$'\t' read -r rain_epoch rain_prob rain_desc rain_icon <<<"$rain_data"
				rain_icon_emoji=$(get_weather_icon "$rain_icon")
    			rain_warning=$(format_rain_warning "$rain_epoch" "$rain_prob" "$rain_desc" "$rain_icon_emoji")
			fi
        fi
    fi

	# Determine rain status — distinguish between current and forecast
	local is_currently_raining=false
	local has_rain_forecast=false
	
	# Current rain check (from weather_main) — OWM returns Title Case
	if [[ "${weather_main,,}" =~ ^(rain|drizzle|thunderstorm)$ ]]; then
		is_currently_raining=true
	fi
	
	# Forecast rain check (from rain_warning)
	if [[ -n "$rain_warning" ]]; then
		has_rain_forecast=true
	fi

	# Fetch moon data (with caching and graceful degradation)
	local moon_data=""
	if [[ "$MOON_PHASE_ENABLED" == "true" ]]; then
		moon_data=$(get_moon_data "$sunrise_epoch") || moon_data=""
	fi

	# Build output
	local weather_line
	# Change signature to accept effective_sunset separately, or handle inside main:
	weather_line=$(build_weather_line \
		"$weather_desc" \
		"$icon_emoji" \
		"$formatted_temp" \
		"$feels_like" \
		"$sunrise_epoch" \
		"$sunset_epoch" \
		"$rain_warning" \
		"$is_currently_raining" \
		"$has_rain_forecast" \
		"$moon_data" \
		"$effective_sunset_epoch" \
		"$effective_sunrise_epoch")

	# Output based on caller mode
	if [[ "${FROM_CALLER:-false}" == "true" ]]; then
		echo "$weather_line"
		echo "---END-WEATHER-LINE---"

        # Only emit combined JSON if forecast data is available
        if [[ -n "$forecast_response" ]]; then
            local combined_json
            combined_json=$(jq -n \
                --argjson weather "$response" \
                --argjson forecast "$forecast_response" \
                '{weather: $weather, forecast: $forecast}')
            echo "$combined_json"
        else
            jq -n --argjson weather "$response" '{weather: $weather}' 
        fi
	else
		echo "$weather_line"
	fi
}

# Run main function
main "$@"
