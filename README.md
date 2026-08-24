# Minol MQTT Bridge for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Home Assistant add-on that automatically fetches consumption data (heating, hot water, and cold water) from the Minol
customer portal and publishes it to MQTT for seamless integration with Home Assistant.

> **Note**: This is an unofficial integration and is not affiliated with or endorsed by Minol.

## Features

### Core Features

- **Automatic Authentication**: Browser-less Azure B2C / SAML sign-in via HTTP (pure `requests`) — no browser needed
- **Home Assistant Auto-Discovery**: Sensors appear automatically in Home Assistant via MQTT discovery
- **Selectable Consumption Types**: Choose which types to fetch (`HEIZUNG`, `WARMWASSER`, `KALTWASSER`) — multiple
  selections possible. Only the selected types are requested, reducing load on the Minol API.
- **Comprehensive Data**: Fetches heating (EH / Einheiten), hot water (m³), and cold water (m³) consumption
- **Room-Level Breakdown**: Individual sensors for each device/room with unique IDs
- **Periodic Updates**: Configurable data fetch interval (default: 12 hours)
- **Extended Attributes**: Every sensor includes detailed metadata:
    - Current meter reading
    - Initial meter reading
    - Evaluation factor
    - Billing period (`period_start` / `period_end`)
- **Billing-Year Timeline**: Monthly consumption from the start of the current billing year (heat cost allocators reset
  every January 1st)
- **Reset Protection**: Guards `total_increasing` sensors against false counter resets and retroactive downward
  corrections
- **DIN Average Comparison**: Compare your consumption against German DIN standards
    - Shows percentage above/below average
    - Separate sensors for each consumption type
- **Per-Room History**: Monthly consumption timeline per room (stored as attributes)

## Prerequisites

- Home Assistant with MQTT broker (e.g., Mosquitto)
- Active Minol customer portal account
- MQTT integration enabled in Home Assistant

## Installation

### Option 1: Home Assistant Add-on (Recommended)

#### Via GitHub Repository (Coming Soon)

This add-on will be available through a Home Assistant add-on repository. Stay tuned!

#### Manual Installation

1. Clone or download this repository
2. Copy the `minol_mqtt` directory to your Home Assistant `/addons` folder
    - On Home Assistant OS/Supervised: `/addons/minol_mqtt`
    - Alternative: Use the File Editor or SSH/Samba to copy files
3. Navigate to **Settings** → **Add-ons** → **Add-on Store**
4. Click the menu (⋮) → **Reload**
5. Find "Minol MQTT Bridge" in the list and click **Install**
6. Configure the add-on (see Configuration section below)
7. Start the add-on

### Option 2: Docker

```bash
cd addons/minol_mqtt
docker build -t minol-mqtt .
docker run -d \
  -e MINOL_EMAIL="your_email@example.com" \
  -e MINOL_PASSWORD="your_password" \
  -e MQTT_HOST="your_mqtt_broker" \
  -e MQTT_PORT=1883 \
  -e MQTT_USER="mqtt_user" \
  -e MQTT_PASSWORD="mqtt_password" \
  -e CONSUMPTION_TYPES="HEIZUNG,WARMWASSER,KALTWASSER" \
  minol-mqtt
```

## Configuration

### Add-on Configuration

Configure the add-on through the Home Assistant UI:

```yaml
minol_email: "your_email@example.com"
minol_password: "your_password"
mqtt_host: "core-mosquitto"
mqtt_port: 1883
mqtt_user: "mqtt_user"
mqtt_password: "mqtt_password"
scan_interval_hours: 12
base_url: "https://webservices.minol.com"
consumption_types:
  - HEIZUNG
  - WARMWASSER
  - KALTWASSER
```

### Configuration Options

| Option                | Required | Default                       | Description                                                                                                                |
|-----------------------|----------|-------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| `minol_email`         | Yes      | -                             | Your Minol portal login email                                                                                              |
| `minol_password`      | Yes      | -                             | Your Minol portal password                                                                                                 |
| `mqtt_host`           | Yes      | -                             | MQTT broker hostname (use `core-mosquitto` for built-in broker)                                                            |
| `mqtt_port`           | No       | 1883                          | MQTT broker port                                                                                                           |
| `mqtt_user`           | Yes      | -                             | MQTT username                                                                                                              |
| `mqtt_password`       | Yes      | -                             | MQTT password                                                                                                              |
| `scan_interval_hours` | No       | 12                            | How often to fetch new data (in hours)                                                                                     |
| `base_url`            | No       | https://webservices.minol.com | Minol portal base URL                                                                                                      |
| `consumption_types`   | No       | `HEIZUNG`                     | Consumption types to fetch. Multiple selectable: `HEIZUNG`, `WARMWASSER`, `KALTWASSER`. Only selected types are requested. |
| `log_level`           | No       | INFO                          | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`                                                         |

> **Tip**: In the Home Assistant add-on UI, `consumption_types` is a list. Add one entry per type you want to fetch —
> this is how multi-selection works for add-on options.

## Usage

Once configured and started, the add-on will:

1. Authenticate with the Minol portal using your credentials
2. Fetch consumption data for the selected `consumption_types` from the start of the current billing year (January 1st)
3. Create Home Assistant sensors automatically via MQTT discovery
4. Update sensors according to the configured interval

### Created Sensors

The add-on creates sensors for each selected consumption type in Home Assistant:

#### Total Consumption Sensors

- `sensor.heizkostenverteiler_total` - Total heating consumption (EH / Einheiten)
    - **Attributes**: `monthly_history` (per-month consumption since year start), `din_comparison_percent`,
      `last_update`, `period_start`, `period_end`, `correction_detected`, `total_consumption`,
      `total_consumption_evaluated` (sum of the per-room factor-weighted/evaluated consumption)
- `sensor.minol_hot_water_total` - Total hot water consumption (m³)
    - **Attributes**: `monthly_history`, `din_comparison_percent`, `last_update`
- `sensor.minol_cold_water_total` - Total cold water consumption (m³)
    - **Attributes**: `monthly_history`, `din_comparison_percent`, `last_update`

> Sensors are only created for the consumption types selected in `consumption_types`.

#### Per-Device/Room Sensors

- **Heating** (heat cost allocators) use a dedicated naming scheme without the `minol` term:
    - `sensor.<room>_heizkostenverteiler_<device_id>` - One sensor per device
    - **Examples**:
        - `sensor.wohnzimmer_heizkostenverteiler_xxxxxxxxxx`
        - `sensor.schlafzimmer_heizkostenverteiler_yyyyyyyyyy`
- **Hot/Cold water** use the standard scheme:
    - `sensor.minol_<type>_<room>_<device_id>` (e.g. `sensor.minol_hot_water_bad_zzzzzzzzzz`)
- **Attributes** (per room):
    - `room_name`: Room name
    - `device_number`: Meter device number
    - `current_reading`: Current meter reading
    - `initial_reading`: Starting meter reading
    - `consumption`: Consumption in the current billing period
    - `consumption_evaluated`: Consumption multiplied by the evaluation factor
    - `evaluation_factor`: Conversion/evaluation factor
    - `unit_raw`: Raw unit (EH for heating, m³ for water)
    - `monthly_history`: This room's consumption per month since year start
    - `period_start` / `period_end`: Billing period (heating only)
    - `correction_detected`: Set when a retroactive downward correction was detected (heating only)

#### DIN Comparison Sensors

- `sensor.minol_heating_din_comparison` - % above/below DIN average for heating
- `sensor.minol_hot_water_din_comparison` - % above/below DIN average for hot water
- `sensor.minol_cold_water_din_comparison` - % above/below DIN average for cold water
    - **Attributes**: `interpretation` (above average / below average), `din_comparison_percent`

> **Privacy note**: Personal customer account details (name, address, customer number) are intentionally **not**
> published to MQTT.

All sensors are grouped under a single device: **Minol Customer Portal**

## How It Works

1. **Authentication**: Uses a browser-less HTTP flow (pure `requests`) to complete the Minol portal's Azure B2C SAML
   sign-in
2. **Session Management**: Extracts authentication cookies and uses them for subsequent API calls
3. **Data Fetching**: Calls the Minol API to retrieve consumption data for the selected consumption types
4. **MQTT Publishing**: Publishes data to MQTT using Home Assistant's discovery protocol
5. **Periodic Sync**: Repeats the process every configured interval

## Monitoring

### Logs

View logs in Home Assistant:

- Navigate to **Settings** → **Add-ons** → **Minol MQTT Bridge** → **Log**

Common log messages:

- `"Connected to MQTT Broker"` - Successfully connected to MQTT
- `"Authentication successful"` - Logged into Minol portal
- `"Enabled consumption types: ..."` - Shows which consumption types are being fetched
- `"Data published to MQTT successfully with all enhancements!"` - Sensors updated
- `"Sleeping for X hours..."` - Waiting until next sync

### MQTT Topics

Monitor MQTT traffic using MQTT Explorer or mosquitto_sub:

```bash
# Subscribe to all Minol topics
mosquitto_sub -h localhost -u mqtt_user -P mqtt_password -t "minol/#" -v

# Subscribe to discovery topics
mosquitto_sub -h localhost -u mqtt_user -P mqtt_password -t "homeassistant/sensor/minol/#" -v
```

## Troubleshooting

### Authentication Fails

**Problem**: "Authentication failed. Retrying next cycle."

**Solutions**:

- Verify your email and password are correct
- Try logging in manually at https://webservices.minol.com to ensure your account works
- Check the logs for more detailed error messages

### No Sensors Appearing in Home Assistant

**Problem**: Add-on runs but no sensors show up

**Solutions**:

- Ensure MQTT integration is enabled in Home Assistant
- Verify MQTT credentials are correct
- Check that the MQTT broker is running (if using Mosquitto add-on)
- Look for MQTT discovery messages on `homeassistant/sensor/minol/#` topic
- Restart Home Assistant after the first data sync

### Connection Timeout or Network Errors

**Problem**: "Failed to connect to MQTT" or timeout errors

**Solutions**:

- Verify `mqtt_host` is correct (use `core-mosquitto` for built-in broker)
- Ensure MQTT broker is running and accessible
- Check firewall settings if using external MQTT broker
- Verify network connectivity from the add-on container

### Docker/Container Issues

**Problem**: Add-on fails to start or crashes

**Solutions**:

- Ensure sufficient memory is available (the browser-less `requests` login is very lightweight)
- Check Docker logs for detailed error messages
- Verify `/data/options.json` exists and is properly formatted
- Try rebuilding the Docker image

## Technical Details

### Architecture

- **Language**: Python 3
- **Authentication**: Browser-less Azure B2C SAML flow via `requests`
- **HTTP Client**: requests
- **MQTT Client**: paho-mqtt
- **Base Image**: `python:3.14-alpine` (no browser)

### Data Refresh

- Consumption data is cached for 1 hour to reduce API load
- Data is fetched from the start of the current billing year (January 1st), matching the physical devices which reset
  annually
- The monthly timeline is built by querying each month individually, so per-month and per-room values reflect the true
  monthly consumption
- Per-room timeline data is derived per month (the Minol API only returns an aggregate chart timeline directly)

### Security

- Credentials are stored in Home Assistant's options system
- SAML tokens and session cookies are kept in memory only
- All communication with Minol portal uses HTTPS
- MQTT credentials can be configured for secure broker access

## Contributing

### Commit Messages

This project **requires** [Conventional Commits](https://www.conventionalcommits.org/). Every commit message must start
with the correct type prefix, because release-please derives the next version and the changelog directly from these
prefixes:

```
<type>: <short description>
```

Common types:

| Prefix     | When to use it                                              | Version impact |
|------------|-------------------------------------------------------------|----------------|
| `feat:`    | A new feature                                               | minor bump     |
| `fix:`     | A bug fix                                                   | patch bump     |
| `chore:`   | Tooling, dependencies, maintenance (no user-facing change)  | patch bump     |
| `docs:`    | Documentation-only changes                                  | patch bump     |
| `refactor:`| Code change that neither fixes a bug nor adds a feature     | patch bump     |
| `test:`    | Adding or updating tests                                    | patch bump     |
| `ci:`      | CI/CD configuration changes                                 | patch bump     |

Examples:

```
feat: add support for KALTWASSER consumption type
fix: handle empty response from Minol API
chore: bump requests to 2.34.2
```

> **Every change ships a new version**: This is a private, single-user add-on, so **every** Conventional Commit type
> triggers at least a version bump and appears in the changelog — that way every small change reliably surfaces as an
> update in Home Assistant. `feat:` → **minor**, everything else (`fix:`, `chore:`, `docs:`, `refactor:`, `test:`,
> `ci:`, …) → **patch**. This is release-please's default versioning; all commit types are additionally made visible in
> the changelog via `changelog-sections` in `release-please-config.json`.

> **Breaking changes**: Add a `!` after the type (e.g. `feat!: ...`) or a `BREAKING CHANGE:` footer to trigger a major
> version bump.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/Gr4ph1xZ/Minol-MQTT-Bridge.git
cd Minol-MQTT-Bridge

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development dependencies (includes runtime deps + tooling like black)
pip install -r minol_mqtt_bridge/requirements-dev.txt

# Run locally
export MINOL_EMAIL="your_email"
export MINOL_PASSWORD="your_password"
export MQTT_HOST="localhost"
export MQTT_USER="mqtt_user"
export MQTT_PASSWORD="mqtt_password"
export CONSUMPTION_TYPES="HEIZUNG,WARMWASSER,KALTWASSER"
python minol_mqtt_bridge/main.py
```

> **Dependency files**:
> - `minol_mqtt_bridge/requirements.txt` — runtime dependencies only. These are the packages installed into the
>   add-on Docker image, so keep this list as lean as possible to keep the image (and Home Assistant backups) small.
> - `minol_mqtt_bridge/requirements-dev.txt` — development-only tooling (e.g. `black`). It pulls in `requirements.txt`
>   and adds the dev tools on top. This file is **not** installed into the add-on image. Use it for local development.

### Releasing a New Version

Versioning and changelogs are automated with [release-please](https://github.com/googleapis/release-please) based on
[Conventional Commits](https://www.conventionalcommits.org/):

1. Write commits using the Conventional Commits format (e.g. `feat: ...`, `fix: ...`, `chore: ...`) and merge them
   into `main`.
2. On each merge to `main`, release-please automatically opens (or updates) a **release PR**. This PR bumps the add-on
   version in `minol_mqtt_bridge/config.yaml` and updates the changelog.
3. When you are ready to publish, simply **merge that release PR**. No manual version bumping is required.
4. After the merge, `minol_mqtt_bridge/CHANGELOG.md` contains **only the changes of that version bump** (the entries
   accumulated since the previous release), and a matching release/tag is created for the new add-on version.

## Disclaimer

This project is provided as-is without warranty. Use at your own risk. This is an unofficial integration and is not
affiliated with or endorsed by Minol.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
