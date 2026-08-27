"""
Minol Customer Portal API Client

Handles authentication and data fetching from the Minol customer portal.

Authentication uses a lightweight, browser-less flow implemented purely with
``requests`` to complete the Azure AD B2C (SAML) sign-in. This keeps the
container image tiny (no browser required), which in turn keeps Home Assistant
backups small.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse

import requests

logger = logging.getLogger(__name__)


class _B2CAutoPostFormParser(HTMLParser):
    """Extract the single auto-submitting SAML form (action + hidden inputs).

    Azure B2C returns an HTML page whose ``<form>`` auto-posts a ``SAMLResponse``
    (and ``RelayState``) to the service provider's ACS URL. A browser would run
    the inline JavaScript that submits it; here we parse the form and submit it
    ourselves with ``requests``.
    """

    def __init__(self):
        super().__init__()
        self.action: Optional[str] = None
        self.inputs: Dict[str, str] = {}
        self._in_form = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "form":
            self._in_form = True
            self.action = attr.get("action") or self.action
        elif tag == "input" and self._in_form:
            name = attr.get("name")
            if name:
                self.inputs[name] = attr.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False


def _to_number(x, default=0):
    """Best-effort numeric coercion."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
}

# All Minol consumption types supported by this bridge and how they map to the
# internal category keys used throughout the code / MQTT topics.
ALL_CONSUMPTION_TYPES = ("HEIZUNG", "WARMWASSER", "KALTWASSER")


class MinolConnector:
    """Minol customer portal API client with requests-based authentication."""

    def __init__(
        self,
        email: str,
        password: str,
        base_url: str = "https://webservices.minol.com",
        consumption_types: Optional[List[str]] = None,
        saml2idp: str = "B2C-Minol-Tenant",
    ):
        """Initialize the connector with user credentials.

        Args:
            email: Minol login (email / customer number).
            password: Minol password.
            base_url: Base URL of the Minol portal.
            consumption_types: Optional list of Minol consumption types to fetch
                (subset of ``HEIZUNG``, ``WARMWASSER``, ``KALTWASSER``). When
                ``None`` or empty, all supported types are fetched. Restricting
                this reduces the number of requests made against the Minol API.
            saml2idp: The Azure B2C IdP identifier used to initiate SAML SSO.
                Defaults to ``"B2C-Minol-Tenant"`` (Mieter/Eigentümer login).
        """
        self.email = email
        self.password = password

        self.base_url = base_url
        self.login_url = f"{base_url}/"
        self.acs_url = f"{base_url}/saml2/sp/acs"
        self.saml2idp = saml2idp or "B2C-Minol-Tenant"

        # Normalize the requested consumption types: keep only known values and
        # fall back to "all" when nothing valid was provided.
        if consumption_types:
            normalized = [t.strip().upper() for t in consumption_types if isinstance(t, str) and t.strip()]
            selected = [t for t in ALL_CONSUMPTION_TYPES if t in normalized]
            self.consumption_types = selected or list(ALL_CONSUMPTION_TYPES)
        else:
            self.consumption_types = list(ALL_CONSUMPTION_TYPES)
        logger.info(f"Enabled consumption types: {', '.join(self.consumption_types)}")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
            }
        )

        self.user_tenants = None
        self.user_num = None
        self.csrf_token = None
        self._authenticated = False
        self._last_data: Optional[Dict] = None
        self._last_update: Optional[datetime] = None
        self._cache_duration = timedelta(hours=1)

    def login(self):
        """Browser-less Azure AD B2C (SAML) authentication using requests only.

        Steps:
          1. Load the portal monitoring URL. The portal first shows an SAP
             logon page, so we explicitly initiate the SAML SSO to Azure B2C
             (the same request the page's ``callTenantLogin()`` triggers) and
             read the ``SETTINGS`` object (csrf token, transId, tenant, policy).
          2. POST the credentials to the ``SelfAsserted`` endpoint.
          3. GET the ``confirmed`` endpoint and follow the auto-posting SAML
             form(s) to the service provider's ACS URL, establishing the
             session cookies (e.g. ``MYSAPSSO2``).
        """
        logger.info("Starting authentication...")

        monitoring_url = (
            f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/"
            f"index.html?isMieter=true&redirect2=true"
        )

        resp = self.session.get(monitoring_url, allow_redirects=True)
        resp.raise_for_status()

        settings = self._extract_b2c_settings(resp.text)
        if settings is None:
            # The portal returned the SAP logon page (not the B2C page yet).
            # Initiate the SAML SSO to Azure B2C exactly like the page's
            # callTenantLogin() -> callSamlLogin("B2C-Minol-Tenant") does.
            target_url = (
                f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/" f"index.html?isMieter=true"
            )
            saml_login_url = (
                f"{self.base_url}/minol.com~kundenportal~login~saml/"
                f"?logonTargetUrl={quote(target_url, safe='')}"
                f"&saml2idp={self.saml2idp}"
            )
            logger.debug(f"Initiating SAML SSO via {saml_login_url}")
            resp = self.session.get(saml_login_url, allow_redirects=True)
            resp.raise_for_status()
            settings = self._extract_b2c_settings(resp.text)

        if settings is None:
            # No B2C page -> we may already hold a valid session.
            if any(c.name == "MYSAPSSO2" for c in self.session.cookies):
                logger.info("Already authenticated (existing session cookie present).")
                self._authenticated = True
                return
            raise RuntimeError("Could not locate Azure B2C SETTINGS on the sign-in page.")

        try:
            csrf = settings["csrf"]
            trans_id = settings["transId"]
            hosts = settings.get("hosts", {})
            tenant = hosts["tenant"]
            policy = hosts["policy"]
        except KeyError as e:
            raise RuntimeError(f"B2C SETTINGS missing expected field: {e}") from e
        api = settings.get("api", "CombinedSigninAndSignup")

        parsed = urlparse(resp.url)
        b2c_origin = f"{parsed.scheme}://{parsed.netloc}"

        # 1) Submit credentials to the SelfAsserted endpoint.
        self_asserted_url = f"{b2c_origin}{tenant}/SelfAsserted"
        r_self = self.session.post(
            self_asserted_url,
            params={"tx": trans_id, "p": policy},
            data={
                "request_type": "RESPONSE",
                "signInName": self.email,
                "password": self.password,
            },
            headers={
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": resp.url,
            },
        )
        r_self.raise_for_status()
        try:
            status = str(r_self.json().get("status"))
        except json.JSONDecodeError:
            status = None
        logger.debug(f"SelfAsserted status={status}")
        if status not in (None, "200"):
            raise RuntimeError(f"Credential submission failed (status={status}): {r_self.text[:200]}")

        # 2) Confirm the sign-in. B2C returns an auto-submitting HTML form that
        #    carries the SAMLResponse. Depending on the policy this can be a
        #    short chain of auto-posting forms (B2C -> ... -> SP ACS), so we
        #    follow them until the service-provider session is established.
        confirmed_url = f"{b2c_origin}{tenant}/api/{api}/confirmed"
        r_step = self.session.get(
            confirmed_url,
            params={
                "rememberMe": "false",
                "csrf_token": csrf,
                "tx": trans_id,
                "p": policy,
            },
            headers={"Referer": resp.url},
        )
        r_step.raise_for_status()
        logger.debug(
            f"confirmed: status={r_step.status_code} "
            f"content-type={r_step.headers.get('Content-Type')} url={r_step.url}"
        )

        page_text = r_step.text
        current_url = r_step.url
        posted_saml = False
        for hop in range(6):
            form = _B2CAutoPostFormParser()
            form.feed(page_text)
            if not self._is_auto_post_form(form, page_text):
                logger.debug(f"No further auto-post form after hop {hop} (url={current_url}).")
                break
            action_url = urljoin(current_url, form.action) if form.action else self.acs_url
            has_saml = any(k in form.inputs for k in ("SAMLResponse", "SAMLRequest"))
            logger.debug(f"Auto-posting SSO form (hop {hop + 1}) to {action_url} " f"(fields={sorted(form.inputs)})")
            r_step = self.session.post(action_url, data=form.inputs, allow_redirects=True)
            r_step.raise_for_status()
            page_text = r_step.text
            current_url = r_step.url
            posted_saml = posted_saml or has_saml
            if any(c.name == "MYSAPSSO2" for c in self.session.cookies):
                break

        # 3) Mirror the browser flow: navigate back to the portal so the SP can
        #    finalize its session on the target page.
        target_monitoring_url = (
            f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/" f"index.html?isMieter=true"
        )
        try:
            self.session.get(target_monitoring_url, allow_redirects=True)
        except requests.exceptions.RequestException as e:
            logger.debug(f"Post-login portal GET failed (non-fatal): {e}")

        cookie_names = sorted({c.name for c in self.session.cookies})
        has_sso = "MYSAPSSO2" in cookie_names
        if has_sso:
            logger.info("MYSAPSSO2 cookie obtained")
        else:
            self._dump_debug("http_login_last_page.html", page_text)
            logger.warning(
                "MYSAPSSO2 cookie not found after HTTP login "
                f"(posted_saml={posted_saml}, final_url={current_url}, "
                f"cookies={cookie_names}). Saved last page to "
                "http_login_last_page.html for diagnostics."
            )

        logger.info("HTTP login flow completed.")
        self._authenticated = True

    @staticmethod
    def _extract_b2c_settings(html_text: str) -> Optional[Dict]:
        """Extract the Azure B2C ``SETTINGS`` JSON object from the sign-in page."""
        match = re.search(r"SETTINGS\s*=\s*(\{.*?})\s*;", html_text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse B2C SETTINGS JSON: {e}")
            return None

    @staticmethod
    def _is_auto_post_form(form: "_B2CAutoPostFormParser", page_text: str) -> bool:
        """Heuristic: is this an SSO/SAML auto-submitting form worth posting?"""
        if not form.action and not form.inputs:
            return False
        sso_keys = {
            "SAMLResponse",
            "SAMLRequest",
            "id_token",
            "state",
            "code",
            "wa",
            "wresult",
        }
        if any(k in form.inputs for k in sso_keys):
            return True
        if form.action and re.search(r"saml|/acs|b2clogin|SelfAsserted|authresp", form.action, re.I):
            return True
        # A body that auto-submits its form via JS onload is also a strong hint.
        if form.inputs and re.search(r"onload\s*=\s*[\"'][^\"']*submit", page_text, re.I):
            return True
        return False

    def _dump_debug(self, filename: str, text: str):
        """Best-effort write of a debug page next to the app (never fatal)."""
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            logger.debug(f"Could not write debug file {filename}: {e}")

    def get_user_tenants(self):
        """Fetch user tenants to extract the userNum."""
        logger.info("Fetching user tenants...")
        url = f"{self.base_url}/minol.com~kundenportal~em~web/rest/EMData/getUserTenants"
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=utf-8",
            "Referer": f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true",
            "X-Requested-With": "XMLHttpRequest",
        }
        logger.debug(f"Fetching user tenants from URL: {url}")
        logger.debug(f"Request Headers: {json.dumps(headers, indent=2)}")
        response = None
        try:
            logger.debug(f"Session cookies before getUserTenants: {self.session.cookies}")
            response = self.session.get(url, headers=headers)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            logger.debug(f"getUserTenants response URL: {response.url}")
            logger.debug(f"getUserTenants response headers: {dict(response.headers)}")
            if "application/json" not in content_type:
                raise ValueError(f"Expected JSON response, but got Content-Type: {content_type}")

            self.user_tenants = response.json()
            logger.debug(f"User tenants response: {json.dumps(self.user_tenants, indent=2)}")
            if self.user_tenants and len(self.user_tenants) > 0:
                self.user_num = self.user_tenants[0].get("userNumber")
                logger.info(f"userNum found: {self.user_num}")
            else:
                raise ValueError("User tenants not found or empty.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching user tenants: {e}")
            raise
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Error processing user tenants response: {e}")
            if response is not None:
                with open("user_tenants_error_response.html", "w", encoding="utf-8") as f:
                    f.write(response.text)
                logger.error("Response content saved to user_tenants_error_response.html")
            raise

    def fetch_em_data(
        self,
        timeline_start,
        timeline_end,
        cons_type="HEIZUNG",
        dlg_key="100EH",
        values_in_kwh=True,
    ):
        """
        Fetch eMonitoring data for a specific consumption type.

        Args:
            timeline_start (str): Start period in format YYYYMM (e.g., "202411")
            timeline_end (str): End period in format YYYYMM (e.g., "202510")
            cons_type (str): Type of consumption - "HEIZUNG", "WARMWASSER", or "KALTWASSER"
            dlg_key (str): Dialog key, default "100EH" for heating
            values_in_kwh (bool): If True, heat cost allocator values are converted to
                kWh. If False, the raw units ("EH") are returned.

        Returns:
            dict: JSON response containing table (per room) and chart (timeline) data
        """
        logger.info(f"Fetching eMonitoring data for {cons_type} from {timeline_start} to {timeline_end}")
        url = f"{self.base_url}/minol.com~kundenportal~em~web/rest/EMData/readData"
        payload = {
            "userNum": self.user_num,
            "layer": "NE",
            "scale": "CALMONTH",
            "chartRefUnit": "ABS",
            "refObject": "DIN_AVG",
            "consType": cons_type,
            "dashBoardKey": "PE",
            "timelineStart": timeline_start,
            "timelineStartTxt": f"{timeline_start[4:]}.{timeline_start[:4]}",
            "timelineEnd": timeline_end,
            "timelineEndTxt": f"{timeline_end[4:]}.{timeline_end[:4]}",
            "valuesInKWH": values_in_kwh,
            "dlgKey": dlg_key,
        }
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=UTF-8",
            "Referer": f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true",
            "X-Requested-With": "XMLHttpRequest",
        }

        logger.debug(f"Fetching EM data from URL: {url}")
        logger.debug(f"Request Payload: {json.dumps(payload, indent=2)}")
        logger.debug(f"Request Headers: {json.dumps(headers, indent=2)}")

        response = None
        try:
            response = self.session.post(url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            logger.debug(f"EM data response status: {response.status_code}")
            logger.debug(f"EM data response content: {response.text[:200]}...")
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error fetching EM data: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding EM data response: {e}")
            if response is not None:
                with open("em_data_error_response.html", "w", encoding="utf-8") as f:
                    f.write(response.text)
                logger.error("Response content saved to em_data_error_response.html")
            raise

    def get_all_consumption_data(self, timeline_start, timeline_end):
        """
        Fetch all consumption data types (heating, hot water, cold water) organized by room.

        Args:
            timeline_start (str): Start period in format YYYYMM (e.g., "202411")
            timeline_end (str): End period in format YYYYMM (e.g., "202510")

        Returns:
            dict: Structured consumption data with the following format:
                {
                    "heating": {
                        "by_room": [list of room consumption data],
                        "timeline": [list of monthly data],
                        "total_consumption": float
                    },
                    "hot_water": {
                        "by_room": [list of room consumption data],
                        "timeline": [list of monthly data],
                        "total_consumption": float
                    },
                    "cold_water": {
                        "by_room": [list of room consumption data],
                        "timeline": [list of monthly data],
                        "total_consumption": float
                    },
                    "timestamp": "ISO timestamp",
                    "period": {"start": "YYYYMM", "end": "YYYYMM"}
                }
        """
        from datetime import datetime

        logger.info(f"Fetching all consumption data from {timeline_start} to {timeline_end}")

        consumption_data: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "period": {"start": timeline_start, "end": timeline_end},
        }

        # Mapping of internal category key -> (Minol consType, dlgKey, valuesInKWH).
        category_config = {
            "heating": ("HEIZUNG", "100EH", False),
            "hot_water": ("WARMWASSER", "100WW", True),
            "cold_water": ("KALTWASSER", "100KW", True),
        }

        # Only fetch the consumption types that were selected in the config to
        # reduce the number of requests against the Minol API.
        for category, (
            cons_type,
            dlg_key,
            values_in_kwh,
        ) in category_config.items():
            if cons_type not in self.consumption_types:
                logger.debug(f"Skipping {cons_type} ({category}); not in enabled consumption types.")
                continue
            try:
                raw = self.fetch_em_data(
                    timeline_start,
                    timeline_end,
                    cons_type=cons_type,
                    dlg_key=dlg_key,
                    values_in_kwh=values_in_kwh,
                )
                consumption_data[category] = self._process_consumption_data(
                    raw, cons_type, timeline_start, timeline_end
                )
            except Exception as e:
                logger.error(f"Error fetching {category} data: {e}")
                consumption_data[category] = {"error": str(e)}

        # Replace the aggregate/evaluated chart timeline with an accurate
        # per-month breakdown in the same (raw) unit as the totals, and attach
        # per-room monthly consumption. This is done by querying each month of
        # the billing period individually (the Minol API returns per-room
        # consumption for a single month when timelineStart == timelineEnd).
        for category, (
            cons_type,
            dlg_key,
            values_in_kwh,
        ) in category_config.items():
            if cons_type not in self.consumption_types:
                continue
            category_data = consumption_data.get(category)
            if not isinstance(category_data, dict) or "error" in category_data:
                continue
            try:
                self._augment_with_monthly_breakdown(
                    category_data,
                    timeline_start,
                    timeline_end,
                    cons_type,
                    dlg_key,
                    values_in_kwh,
                )
            except Exception as e:
                logger.warning(f"Could not build monthly breakdown for {category}: {e}")

        return consumption_data

    @staticmethod
    def _month_range(timeline_start, timeline_end):
        """Yield every YYYYMM period from timeline_start to timeline_end (inclusive)."""
        start_year, start_month = int(timeline_start[:4]), int(timeline_start[4:6])
        end_year, end_month = int(timeline_end[:4]), int(timeline_end[4:6])
        year, month = start_year, start_month
        while (year, month) <= (end_year, end_month):
            yield f"{year:04d}{month:02d}"
            month += 1
            if month > 12:
                month = 1
                year += 1

    @staticmethod
    def _evaluated_value(consumption, factor, api_value=None):
        """
        Determine the factor-weighted (bewertet) consumption for a single entry.

        The derivation ``consumption * evaluation_factor`` is preferred so that the
        published value stays fully reproducible. If no factor is available, the
        value reported by the API (``consumptionBew``) is used as a fallback.
        """
        if factor is not None:
            return round(_to_number(consumption) * _to_number(factor), 3)
        if api_value is not None:
            return round(_to_number(api_value), 3)
        return round(_to_number(consumption), 3)

    def _augment_with_monthly_breakdown(
        self,
        processed: Dict[str, Any],
        timeline_start,
        timeline_end,
        cons_type,
        dlg_key,
        values_in_kwh,
    ):
        """
        Build a true per-month consumption timeline (overall and per room) by
        querying each month individually, and merge it into ``processed``.

        - ``processed["timeline"]`` is replaced with the per-month total
          consumption (sum of all rooms) in the same raw unit as the totals.
        - Each room in ``processed["by_room"]`` gets a ``monthly`` list holding
          that room's consumption per month.
        """
        is_heating = cons_type == "HEIZUNG"

        overall_timeline = []
        by_room_monthly = {}  # room key -> {period_int: {consumption, evaluation_factor, consumption_evaluated}}

        for period in self._month_range(timeline_start, timeline_end):
            try:
                raw = self.fetch_em_data(
                    period,
                    period,
                    cons_type=cons_type,
                    dlg_key=dlg_key,
                    values_in_kwh=values_in_kwh,
                )
            except Exception as e:
                logger.warning(f"Could not fetch {cons_type} data for {period}: {e}")
                continue

            table = raw.get("table") or []
            month_total = 0.0
            month_total_evaluated = 0.0
            has_value = False

            for room_data in table:
                consumption = _to_number(room_data.get("consumption", 0))
                if is_heating:
                    consumption = int(round(consumption))

                factor = room_data.get("bewertung")
                consumption_evaluated = self._evaluated_value(consumption, factor, room_data.get("consumptionBew"))

                month_total += consumption
                month_total_evaluated += _to_number(consumption_evaluated)
                has_value = True

                key = room_data.get("gerNr") or room_data.get("raumKey") or room_data.get("raum")
                by_room_monthly.setdefault(key, {})[period] = {
                    "consumption": consumption,
                    "evaluation_factor": factor,
                    "consumption_evaluated": consumption_evaluated,
                }

            if not has_value:
                continue

            if is_heating:
                month_total = int(round(month_total))
            month_total_evaluated = round(month_total_evaluated, 3)

            overall_timeline.append(
                {
                    "period": f"{period[4:6]}.{period[:4]}",
                    "period_int": period,
                    "consumption": month_total,
                    "consumption_evaluated": month_total_evaluated,
                }
            )

        processed["timeline"] = overall_timeline

        for room in processed.get("by_room", []):
            key = room.get("device_number") or room.get("room_key") or room.get("room_name_orig")
            monthly = by_room_monthly.get(key, {})
            room["monthly"] = [
                {
                    "period": f"{p[4:6]}.{p[:4]}",
                    "period_int": p,
                    "consumption": monthly[p]["consumption"],
                    "evaluation_factor": monthly[p]["evaluation_factor"],
                    "consumption_evaluated": monthly[p]["consumption_evaluated"],
                }
                for p in sorted(monthly)
            ]

    @staticmethod
    def _process_consumption_data(raw_data, consumption_type, timeline_start, timeline_end):
        """
        Process raw consumption data into a structured format.

        Note: The Minol API currently only provides timeline data on aggregate level,
        not per individual room/device. Room timeline data is not available from the API.

        Args:
            raw_data (dict): Raw API response
            consumption_type (str): Type identifier (HEIZUNG, WARMWASSER, KALTWASSER)
            timeline_start (str): Start period (for documentation)
            timeline_end (str): End period (for documentation)

        Returns:
            dict: Processed data with by_room, overall timeline, and total_consumption
        """
        processed = {"by_room": [], "timeline": [], "total_consumption": 0.0, "total_consumption_evaluated": 0.0}

        is_heating = consumption_type == "HEIZUNG"

        total_consumption = 0.0
        total_consumption_evaluated = 0.0
        if "table" in raw_data and raw_data["table"]:
            for room_data in raw_data["table"]:
                unit = room_data.get("unit", "kWh")
                if unit == "KWH":
                    unit = "kWh"

                # Minol reports heat cost allocator readings in whole "Einheiten".
                consumption = room_data.get("consumption", 0)
                reading = room_data.get("ablesung", 0)
                initial_reading = room_data.get("anfangsstand", 0)
                if is_heating:
                    consumption = int(round(_to_number(consumption)))
                    reading = int(round(_to_number(reading)))
                    initial_reading = int(round(_to_number(initial_reading)))

                consumption_evaluated = room_data.get("consumptionBew", 0)
                room_info = {
                    "room_name_orig": room_data.get("raum", "Unknown"),
                    "room_key": room_data.get("raumKey"),
                    "device_number": room_data.get("gerNr"),
                    "consumption": consumption,
                    "unit": unit,
                    "consumption_evaluated": consumption_evaluated,
                    "evaluation_score": room_data.get("bewertung"),
                    "reading": reading,
                    "initial_reading": initial_reading,
                    # Note: Per-room timeline not available from API
                }
                processed["by_room"].append(room_info)
                total_consumption += _to_number(consumption)
                # The "evaluated" value is already factor-weighted per room, so
                # summing the per-room values yields the unit's total weighted
                # (bewertet) consumption used for cost distribution.
                total_consumption_evaluated += _to_number(consumption_evaluated)

        if is_heating:
            processed["total_consumption"] = int(round(total_consumption))
        else:
            processed["total_consumption"] = total_consumption
        processed["total_consumption_evaluated"] = round(total_consumption_evaluated, 2)

        if "chart" in raw_data and raw_data["chart"]:
            for entry in raw_data["chart"]:
                if entry.get("keyFigure") != "REF":
                    timeline_entry = {
                        "period": entry.get("category"),
                        "period_int": entry.get("categoryInt"),
                        "value": entry.get("value", 0),
                        "label": entry.get("label"),
                        "num_values": entry.get("anzValues", 0),
                    }
                    processed["timeline"].append(timeline_entry)

        return processed

    def authenticate(self) -> bool:
        """
        Authenticate with the Minol portal.

        This is a convenience wrapper around login() + get_user_tenants().

        Returns:
            bool: True if authentication successful, False otherwise
        """
        try:
            logger.info("Authenticating with Minol portal...")
            self.login()
            self.get_user_tenants()
            logger.info("Authentication successful")
            return True
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            self._authenticated = False
            return False

    def get_consumption_data(self, months_back: int = 12, force_update: bool = False) -> Optional[Dict]:
        """
        Fetch all consumption data (heating, hot water, cold water) with caching.

        Args:
            months_back: Deprecated/ignored. Data is always fetched from the
                start of the current billing year (January 1st) so the reading
                matches the physical device, which resets annually.
            force_update: Force data refresh even if cached data exists

        Returns:
            Dict with consumption data or None on error
        """
        if not force_update and self._last_data and self._last_update:
            age = datetime.now() - self._last_update
            if age < self._cache_duration:
                logger.debug(f"Returning cached data (age: {age})")
                return self._last_data

        if not self._authenticated:
            if not self.authenticate():
                return None

        try:
            end_date = datetime.now()
            # Query from the start of the current billing year (January 1st).
            # Minol heat cost allocators reset to 0 on January 1st, so aligning
            # the query window to the billing year makes the per-room reading
            # match the physical device and increase monotonically (which is
            # required for Home Assistant's total_increasing / Utility Meter).
            start_date = end_date.replace(month=1, day=1)

            timeline_start = start_date.strftime("%Y%m")
            timeline_end = end_date.strftime("%Y%m")

            logger.info(f"Fetching consumption data from {timeline_start} to {timeline_end}")

            data = self.get_all_consumption_data(timeline_start, timeline_end)
            self._last_data = data
            self._last_update = datetime.now()

            return data

        except Exception as e:
            logger.error(f"Error fetching consumption data: {e}")
            return None

    def get_heating_total(self) -> Optional[float]:
        """
        Get total heating consumption in kWh.

        Returns:
            float: Total heating consumption or None on error
        """
        data = self.get_consumption_data()
        if data and "heating" in data and "error" not in data["heating"]:
            return data["heating"]["total_consumption"]
        return None

    def get_hot_water_total(self) -> Optional[float]:
        """
        Get total hot water consumption in m³.

        Returns:
            float: Total hot water consumption or None on error
        """
        data = self.get_consumption_data()
        if data and "hot_water" in data and "error" not in data["hot_water"]:
            return data["hot_water"]["total_consumption"]
        return None

    def get_cold_water_total(self) -> Optional[float]:
        """
        Get total cold water consumption in m³.

        Returns:
            float: Total cold water consumption or None on error
        """
        data = self.get_consumption_data()
        if data and "cold_water" in data and "error" not in data["cold_water"]:
            return data["cold_water"]["total_consumption"]
        return None

    def get_rooms_data(self, consumption_type: str = "heating") -> Optional[List[Dict]]:
        """
        Get per-room consumption data for a specific type.

        Args:
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            List of room data dictionaries or None on error
        """
        data = self.get_consumption_data()
        if data and consumption_type in data and "error" not in data[consumption_type]:
            return data[consumption_type]["by_room"]
        return None

    def get_room_consumption(self, room_name_orig: str, consumption_type: str = "heating") -> Optional[float]:
        """
        Get consumption for a specific room.

        Args:
            room_name_orig: Original room name from Minol
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            Consumption value or None if not found
        """
        rooms = self.get_rooms_data(consumption_type)
        if rooms:
            for room in rooms:
                if room["room_name_orig"] == room_name_orig:
                    return room["consumption"]
        return None

    def get_timeline(self, consumption_type: str = "heating") -> Optional[List[Dict]]:
        """
        Get overall timeline data for a consumption type.

        Args:
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            List of timeline entries or None on error
        """
        data = self.get_consumption_data()
        if data and consumption_type in data and "error" not in data[consumption_type]:
            return data[consumption_type]["timeline"]
        return None

    @staticmethod
    def get_room_timeline(room_name: str, consumption_type: str = "heating") -> Optional[List[Dict]]:
        """
        Get timeline data for a specific room.

        Note: The Minol API currently does not provide per-room timeline data.
        This method returns None. Use get_timeline() for overall consumption timeline.

        Args:
            room_name: Name of the room
            consumption_type: One of "heating", "hot_water", "cold_water"

        Returns:
            None (per-room timeline not available from API)
        """
        logger.warning("Per-room timeline data is not available from the Minol API")
        return None

    def get_user_details(self) -> Optional[Dict]:
        """
        Fetch detailed user information from the portal.

        Returns:
            dict: User details including customer number, property, address, etc.
                 Returns None on error.
        """
        if not self._authenticated:
            logger.error("Not authenticated. Call authenticate() first.")
            return None

        logger.info("Fetching user details...")
        url = f"{self.base_url}/minol.com~util~framework~ui5~common~web/rest/UserInfo/getUserDetail"

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=utf-8",
            "Referer": f"{self.base_url}/minol.com~kundenportal~em~web/resources/monitoring/index.html?isMieter=true",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()

            user_details = response.json()
            logger.debug(f"User details response: {json.dumps(user_details, indent=2)}")
            return user_details
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching user details: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding user details response: {e}")
            return None

    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        return self._authenticated

    @property
    def user_info(self) -> Optional[Dict]:
        """Get user tenant information."""
        if self.user_tenants:
            return self.user_tenants[0] if self.user_tenants else None
        return None
