import json
import logging
import os
import sys
import time
from datetime import date

import paho.mqtt.client as mqtt

from minol_connector import MinolConnector

OPTIONS_PATH = "/data/options.json"

# Bridge-level availability so sensors become `unavailable` on failure
# instead of falsely reporting state 0 (which HA could read as a counter reset).
AVAILABILITY_TOPIC = "minol/bridge/availability"

# Persistent store for the last published heating values (for reset/correction
# detection across restarts). /data is persistent for HA add-ons.
STATE_STORE_PATH = (
    "/data/minol_state.json"
    if os.path.isdir("/data")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "minol_state.json")
)


def load_config():
    """Load configuration from Home Assistant options.json or .env / environment variables."""
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, "r") as f:
            return json.load(f)

    # Local development: load .env before reading environment variables.
    # `python-dotenv` is a development-only dependency and is not shipped in the
    # add-on image, so importing it is optional and best-effort.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    return {
        "minol_email": os.environ.get("MINOL_EMAIL"),
        "minol_password": os.environ.get("MINOL_PASSWORD"),
        "mqtt_host": os.environ.get("MQTT_HOST", "localhost"),
        "mqtt_port": int(os.environ.get("MQTT_PORT", 1883)),
        "mqtt_user": os.environ.get("MQTT_USER"),
        "mqtt_password": os.environ.get("MQTT_PASSWORD"),
        "scan_interval_hours": int(os.environ.get("SCAN_INTERVAL_HOURS", 12)),
        "base_url": os.environ.get("BASE_URL"),
        "consumption_types": [
            t.strip() for t in os.environ.get("CONSUMPTION_TYPES", "HEIZUNG").split(",") if t.strip()
        ],
        "log_level": os.environ.get("LOG_LEVEL", "INFO"),
    }


config = load_config()

log_level_str = config.get("log_level", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("MinolBridge")
logger.info(f"Log level set to: {log_level_str}")

if config.get("base_url") is None:
    logger.error("No base_url configured, aborting")
    sys.exit(1)

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

mqtt_user = config.get("mqtt_user")
mqtt_password = config.get("mqtt_password")
if isinstance(mqtt_user, str) and isinstance(mqtt_password, str) and mqtt_user and mqtt_password:
    mqtt_client.username_pw_set(username=mqtt_user, password=mqtt_password)


def connect_mqtt():
    """Connect to the MQTT broker."""
    try:
        # Last Will: if the bridge dies unexpectedly, sensors go unavailable.
        mqtt_client.will_set(topic=AVAILABILITY_TOPIC, payload="offline", qos=1, retain=True)
        mqtt_host_raw = config.get("mqtt_host", "localhost")
        mqtt_host = mqtt_host_raw if isinstance(mqtt_host_raw, str) and mqtt_host_raw else "localhost"
        mqtt_port_raw = config.get("mqtt_port", 1883)
        if mqtt_port_raw is None:
            mqtt_port = 1883
        elif isinstance(mqtt_port_raw, (int, float, str)):
            mqtt_port = int(mqtt_port_raw)
        else:
            raise TypeError("Invalid mqtt_port type; expected int-compatible value.")
        mqtt_client.connect(host=mqtt_host, port=mqtt_port, keepalive=60)
        mqtt_client.loop_start()
        logger.info("Connected to MQTT Broker")
    except Exception as e:
        logger.error(f"Failed to connect to MQTT: {e}")
        sys.exit(1)


def publish_discovery_config(
    sensor_type,
    unique_id,
    name,
    unit,
    icon,
    device_class,
    state_class="total_increasing",
    attributes_topic=None,
    object_id=None,
    unique_id_override=None,
):
    """Publish Home Assistant MQTT discovery configuration for automatic sensor creation."""
    topic = f"homeassistant/sensor/minol/{unique_id}/config"

    payload = {
        "name": name,
        "unique_id": (unique_id_override if unique_id_override else f"minol_{unique_id}"),
        "state_topic": f"minol/{unique_id}/state",
        "unit_of_measurement": unit,
        "state_class": state_class,
        "icon": icon,
        "platform": "mqtt",
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": ["minol_account"],
            "name": "Minol Customer Portal",
            "manufacturer": "Minol",
            "model": "Web Scraper",
        },
    }

    if device_class:
        payload["device_class"] = device_class

    if object_id:
        payload["object_id"] = object_id

    if attributes_topic:
        payload["json_attributes_topic"] = attributes_topic

    mqtt_client.publish(topic, json.dumps(payload), qos=0, retain=True)


def publish_state(unique_id, value):
    """Publish sensor state value to MQTT."""
    topic = f"minol/{unique_id}/state"
    mqtt_client.publish(topic, str(value), qos=0, retain=True)


def publish_attributes(unique_id, attributes):
    """Publish sensor JSON attributes to MQTT."""
    topic = f"minol/{unique_id}/attributes"
    mqtt_client.publish(topic, json.dumps(attributes), qos=0, retain=True)


def publish_availability(available):
    """Publish bridge availability. When offline, HA marks all sensors unavailable."""
    payload = "online" if available else "offline"
    mqtt_client.publish(AVAILABILITY_TOPIC, payload, qos=1, retain=True)
    logger.debug(f"Published availability: {payload}")


def load_state_store():
    """Load the persisted last-known heating values from disk."""
    try:
        if os.path.exists(STATE_STORE_PATH):
            with open(STATE_STORE_PATH, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load state store ({STATE_STORE_PATH}): {e}")
    return {}


def save_state_store(store):
    """Persist the last-known heating values to disk."""
    try:
        with open(STATE_STORE_PATH, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save state store ({STATE_STORE_PATH}): {e}")


state_store = load_state_store()


def _to_number(x, default=0):
    """Best-effort numeric coercion."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def apply_reset_protection(uid, val, billing_year, factor=None):
    """
    Protect a total_increasing value against false counter resets and
    retroactive downward corrections.

    Returns (published_val, correction_detected):
    - First sighting or new billing year -> accept the value (reset allowed).
    - Same year, value >= last -> accept the value.
    - Same year, value < last -> hold the last value (avoid a false reset),
      flag the correction and log a warning. The caller should expose the true
      value separately (e.g. via consumption).
    Also warns if the evaluation factor changes mid billing year.
    """
    prev = state_store.get(uid)
    correction_detected = False
    published_val = val

    if prev is None:
        published_val = val
    elif prev.get("billing_year") != billing_year:
        logger.info(
            f"{uid}: new billing year {billing_year} " f"(was {prev.get('billing_year')}); accepting reset to {val}."
        )
        published_val = val
    else:
        prev_val = prev.get("value", val)
        if _to_number(val) < _to_number(prev_val):
            correction_detected = True
            logger.warning(
                f"{uid}: value decreased within billing year {billing_year} "
                f"({prev_val} -> {val}). Holding state at {prev_val} to avoid a "
                f"false total_increasing reset."
            )
            published_val = prev_val
        else:
            published_val = val

    if factor is not None and prev is not None and prev.get("billing_year") == billing_year:
        prev_factor = prev.get("evaluation_factor")
        if prev_factor is not None and prev_factor != factor:
            logger.warning(
                f"{uid}: evaluation_factor changed mid-billing-year "
                f"({prev_factor} -> {factor}). This retroactively changes any "
                f"factor-weighted cumulative value."
            )

    state_store[uid] = {
        "value": published_val,
        "billing_year": billing_year,
        "evaluation_factor": factor,
    }
    return published_val, correction_detected


def run_sync():
    """
    Main sync cycle: authenticate, fetch data, and publish to MQTT.

    Publishes total consumption sensors, per-room/device sensors,
    DIN comparison sensors, and customer info.
    """
    minol_email = config.get("minol_email")
    minol_password = config.get("minol_password")
    base_url = config.get("base_url")
    if not isinstance(minol_email, str) or not isinstance(minol_password, str) or not isinstance(base_url, str):
        logger.error("Missing or invalid Minol credentials/base_url configuration.")
        publish_availability(False)
        return

    raw_consumption_types = config.get("consumption_types")
    if isinstance(raw_consumption_types, list):
        consumption_types = [
            entry.strip() for entry in raw_consumption_types if isinstance(entry, str) and entry.strip()
        ]
        if not consumption_types:
            consumption_types = ["HEIZUNG"]
    else:
        consumption_types = ["HEIZUNG"]

    connector = MinolConnector(
        minol_email,
        minol_password,
        base_url,
        consumption_types=consumption_types,
    )

    logger.info("Starting authentication...")
    if not connector.authenticate():
        logger.error("Authentication failed. Retrying next cycle.")
        publish_availability(False)
        return

    logger.info("Fetching consumption data...")
    data = connector.get_consumption_data(months_back=12, force_update=True)

    if not data:
        logger.error("No data received.")
        publish_availability(False)
        return

    user_info = connector.user_info
    if user_info:
        logger.info("Publishing customer data sensor...")

        address_parts = [
            user_info.get("addrStreet", ""),
            user_info.get("addrHouseNum", ""),
            user_info.get("addrPostalCode", ""),
            user_info.get("addrCity", ""),
        ]
        full_address = " ".join([p for p in address_parts if p]).strip()

        customer_attrs = {
            "email": user_info.get("email", ""),
            "customer_number": user_info.get("userNumber", ""),
            "tenant_number": user_info.get("nenr", "").strip(),
            "property_number": user_info.get("lgnr", "").strip(),
            "floor": user_info.get("geschossText", ""),
            "position": user_info.get("lageText", ""),
            "address": full_address,
            "name": user_info.get("name", ""),
            "move_in_date": user_info.get("einzugMieter", ""),
        }

        # I don't want to publish personal customer info to MQTT for privacy
        # reasons, so this is commented out.

        # publish_discovery_config(
        #     "info",
        #     "customer_info",
        #     "Minol Customer Info",
        #     "",
        #     "mdi:account",
        #     None,
        #     state_class=None,
        #     attributes_topic="minol/customer_info/attributes"
        # )
        # publish_state(
        #     "customer_info", customer_attrs.get("customer_number", "N/A")
        # )
        # publish_attributes("customer_info", customer_attrs)

    def calculate_din_comparison(timeline):
        """Calculate percentage above/below DIN average."""
        if not timeline:
            return None

        try:
            total_actual = sum(
                float(entry.get("value", 0) or 0) for entry in timeline if entry and entry.get("label") != "REF"
            )
            total_ref = sum(
                float(entry.get("value", 0) or 0) for entry in timeline if entry and entry.get("label") == "REF"
            )

            if total_ref > 0:
                diff_percent = ((total_actual - total_ref) / total_ref) * 100
                return round(diff_percent, 1)
        except (TypeError, ValueError) as e:
            logger.warning(f"Error calculating DIN comparison: {e}")

        return None

    if "heating" in data and "total_consumption" in data["heating"]:
        val = data["heating"]["total_consumption"]
        # The sensor state exposes the evaluated (factor-weighted) consumption so
        # that Home Assistant can build monthly/yearly statistics without an extra
        # template sensor. The raw consumption stays available via attributes.
        val_evaluated = data["heating"].get("total_consumption_evaluated", val)
        timeline = data["heating"].get("timeline", [])
        din_comparison = calculate_din_comparison(timeline)

        # Build timeline attributes
        timeline_attrs = {
            "monthly_history": [{"period": entry.get("period"), "value": entry.get("value", 0)} for entry in timeline],
            "din_comparison_percent": din_comparison,
            "last_update": data.get("timestamp", ""),
        }

        heating_total_uid = "heizkostenverteiler_total"
        publish_discovery_config(
            "heating",
            heating_total_uid,
            "Heizkostenverteiler Total",
            "EH",
            "mdi:sigma",
            None,
            state_class="total_increasing",
            attributes_topic=f"minol/{heating_total_uid}/attributes",
            object_id=heating_total_uid,
            unique_id_override=heating_total_uid,
        )
        heating_total_year = date.today().year
        published_heating_total, heating_total_corrected = apply_reset_protection(
            heating_total_uid, val_evaluated, heating_total_year
        )
        timeline_attrs["period_start"] = f"{heating_total_year}-01-01"
        timeline_attrs["period_end"] = f"{heating_total_year}-12-31"
        timeline_attrs["correction_detected"] = heating_total_corrected
        timeline_attrs["total_consumption"] = val
        timeline_attrs["total_consumption_evaluated"] = val_evaluated
        publish_state(heating_total_uid, published_heating_total)
        publish_attributes(heating_total_uid, timeline_attrs)

    if "hot_water" in data and "total_consumption" in data["hot_water"]:
        val = data["hot_water"]["total_consumption"]
        timeline = data["hot_water"].get("timeline", [])
        din_comparison = calculate_din_comparison(timeline)

        timeline_attrs = {
            "monthly_history": [{"period": entry.get("period"), "value": entry.get("value", 0)} for entry in timeline],
            "din_comparison_percent": din_comparison,
            "last_update": data.get("timestamp", ""),
        }

        publish_discovery_config(
            "water",
            "hot_water_total",
            "Minol Hot Water Total",
            "m³",
            "mdi:water-thermometer",
            "water",
            state_class="total_increasing",
            attributes_topic="minol/hot_water_total/attributes",
        )
        publish_state("hot_water_total", val)
        publish_attributes("hot_water_total", timeline_attrs)

    if "cold_water" in data and "total_consumption" in data["cold_water"]:
        val = data["cold_water"]["total_consumption"]
        timeline = data["cold_water"].get("timeline", [])
        din_comparison = calculate_din_comparison(timeline)

        timeline_attrs = {
            "monthly_history": [{"period": entry.get("period"), "value": entry.get("value", 0)} for entry in timeline],
            "din_comparison_percent": din_comparison,
            "last_update": data.get("timestamp", ""),
        }

        publish_discovery_config(
            "water",
            "cold_water_total",
            "Minol Cold Water Total",
            "m³",
            "mdi:water-pump",
            "water",
            state_class="total_increasing",
            attributes_topic="minol/cold_water_total/attributes",
        )
        publish_state("cold_water_total", val)
        publish_attributes("cold_water_total", timeline_attrs)

    def process_rooms_extended(category_key, category_name, unit, icon, device_class):
        """Process room data and publish sensors with extended attributes and monthly history."""
        if category_key not in data or "by_room" not in data[category_key]:
            return

        for room in data[category_key]["by_room"]:
            r_name = room.get("room_name", "Unknown")
            device_num = room.get("device_number", "")

            safe_room = (
                "".join(c for c in r_name if c.isalnum())
                .lower()
                .replace("ä", "ae")
                .replace("ö", "oe")
                .replace("ü", "ue")
                .replace("ß", "ss")
            )
            safe_device = "".join(c for c in str(device_num) if c.isalnum())

            # For heating (heat cost allocators) use a custom identifier scheme:
            #   <room>_heizkostenverteiler_<device_id>
            # This drives the unique_id, MQTT topics and the entity_id.
            if category_key == "heating":
                if safe_device:
                    uid = f"{safe_room}_heizkostenverteiler_{safe_device}".lower()
                else:
                    uid = f"{safe_room}_heizkostenverteiler".lower()
                custom_object_id = uid
                custom_unique_id = uid
            else:
                uid = f"{category_key}_{safe_room}_{safe_device}" if safe_device else f"{category_key}_{safe_room}"
                custom_object_id = None
                custom_unique_id = None

            val = room.get("consumption", 0)
            val_evaluated = room.get("consumption_evaluated", 0)

            device_suffix = f"({device_num})" if device_num else ""
            if category_key == "heating":
                # Clean, readable friendly name without the "Minol" prefix.
                # Use safe_room so umlauts are transliterated (e.g. "Küche" ->
                # "kueche") and capitalize it (e.g. "Kueche").
                sensor_name = f"{safe_room.capitalize()} Heizkostenverteiler {device_suffix}"
            else:
                sensor_name = f"Minol {safe_room.capitalize()} {category_name} {device_suffix}"

            reading = room.get("reading", 0)
            initial = room.get("initial_reading", 0)
            factor = room.get("evaluation_score", 0)

            extended_attrs = {
                "room_name": r_name,
                "device_number": device_num,
                "current_reading": reading,
                "initial_reading": initial,
                "consumption": val,
                "evaluation_factor": factor,
                "consumption_evaluated": val_evaluated,
                "monthly_history": [
                    {
                        "period": entry.get("period"),
                        "value": entry.get("value", 0),
                    }
                    for entry in room.get("monthly", [])
                ],
            }

            published_val = val

            # For heating (heat cost allocators): add billing-period attributes and
            # protect the total_increasing state against false resets / corrections.
            if category_key == "heating":
                billing_year = date.today().year
                period_consumption = int(round(_to_number(reading) - _to_number(initial)))

                extended_attrs["period_start"] = f"{billing_year}-01-01"
                extended_attrs["period_end"] = f"{billing_year}-12-31"
                extended_attrs["consumption"] = period_consumption

                # The sensor state exposes the evaluated (factor-weighted)
                # consumption so Home Assistant can build long-term statistics
                # without an extra template sensor. The raw consumption stays
                # available via the "consumption" attribute above.
                published_val, correction_detected = apply_reset_protection(
                    uid, val_evaluated, billing_year, factor=factor
                )
                # The true (possibly corrected/lower) reading stays visible via
                # consumption above, even when the state is held.
                extended_attrs["correction_detected"] = correction_detected

            publish_discovery_config(
                category_key,
                uid,
                sensor_name,
                unit,
                icon,
                device_class,
                state_class="total_increasing",
                attributes_topic=f"minol/{uid}/attributes",
                object_id=custom_object_id,
                unique_id_override=custom_unique_id,
            )
            publish_state(uid, published_val)
            publish_attributes(uid, extended_attrs)

    process_rooms_extended(
        category_key="heating",
        category_name="Heating",
        unit="EH",
        icon="mdi:radiator",
        device_class=None,
    )
    process_rooms_extended(
        category_key="hot_water",
        category_name="Hot Water",
        unit="m³",
        icon="mdi:water-thermometer",
        device_class="water",
    )
    process_rooms_extended(
        category_key="cold_water",
        category_name="Cold Water",
        unit="m³",
        icon="mdi:water-pump",
        device_class="water",
    )

    def publish_din_comparison(category_key, category_name, unit):
        """Publish dedicated DIN comparison sensor."""
        if category_key not in data or "timeline" not in data[category_key]:
            return

        timeline = data[category_key]["timeline"]
        din_comparison = calculate_din_comparison(timeline)

        if din_comparison is not None:
            uid = f"{category_key}_din_comparison"
            sensor_name = f"Minol {category_name} DIN Comparison"

            publish_discovery_config(
                sensor_type=category_key,
                unique_id=uid,
                name=sensor_name,
                unit="%",
                icon="mdi:chart-line",
                device_class=None,
                state_class="measurement",
                attributes_topic=f"minol/{uid}/attributes",
            )
            publish_state(uid, din_comparison)

            interpretation = "above average" if din_comparison > 0 else "below average"
            attrs = {
                "interpretation": interpretation,
                "din_comparison_percent": din_comparison,
            }
            publish_attributes(uid, attrs)

    publish_din_comparison(category_key="heating", category_name="Heating", unit="EH")
    publish_din_comparison(category_key="hot_water", category_name="Hot Water", unit="m³")
    publish_din_comparison(category_key="cold_water", category_name="Cold Water", unit="m³")

    save_state_store(state_store)
    publish_availability(True)
    logger.info("Data published to MQTT successfully with all enhancements!")


if __name__ == "__main__":
    connect_mqtt()

    while True:
        try:
            run_sync()
        except Exception as e:
            logger.error(f"Critical error in main loop: {e}")
            try:
                publish_availability(False)
            except Exception:
                pass

        interval_raw = config.get("scan_interval_hours", 12)
        if isinstance(interval_raw, (int, float, str)):
            interval = int(interval_raw)
        else:
            raise TypeError("Invalid scan_interval_hours type; expected int-compatible value.")
        logger.info(f"Sleeping for {interval} hours...")
        time.sleep(interval * 3600)
