"""
US Address & ZIP Code Validator for EKUIPT
──────────────────────────────────────────
Provides comprehensive validation of:
1. US States (names and 2-letter codes for all 50 states + DC + territories + APO/FPO)
2. ZIP Codes (5-digit format and 3-digit prefix range validation per state)
3. Live / Cached ZIP code resolution to City & State
4. Server-side validation for Checkout, Registration, and Account profile updates.
"""

import re
import json
import urllib.request
import urllib.parse
import os
import logging

log = logging.getLogger(__name__)

# All 50 US States, DC, Territories, and Military Postal Codes
US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    # Territories & Armed Forces
    "PR": "Puerto Rico",
    "VI": "Virgin Islands",
    "GU": "Guam",
    "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
    "AA": "Armed Forces Americas",
    "AE": "Armed Forces Europe",
    "AP": "Armed Forces Pacific",
}

# Reverse mapping: Lowercase state names & abbreviations → 2-letter uppercase state code
STATE_LOOKUP = {}
for code, name in US_STATES.items():
    STATE_LOOKUP[code.lower()] = code
    STATE_LOOKUP[name.lower()] = code

# 3-digit ZIP code prefix ranges per state (official USPS range rules)
# Used for instant sub-millisecond offline validation
STATE_ZIP_PREFIX_RANGES = {
    "AL": [(350, 369)],
    "AK": [(995, 999)],
    "AZ": [(850, 865)],
    "AR": [(716, 729), (755, 755)],
    "CA": [(900, 961)],
    "CO": [(800, 816)],
    "CT": [(60, 69)],
    "DE": [(197, 199)],
    "DC": [(200, 205), (569, 569)],
    "FL": [(320, 349)],
    "GA": [(300, 319), (398, 399)],
    "HI": [(967, 968)],
    "ID": [(832, 838)],
    "IL": [(600, 629)],
    "IN": [(460, 479)],
    "IA": [(500, 528)],
    "KS": [(660, 679)],
    "KY": [(400, 427)],
    "LA": [(700, 714), (715, 715)],
    "ME": [(39, 49)],
    "MD": [(206, 219)],
    "MA": [(10, 27), (55, 55)],
    "MI": [(480, 499)],
    "MN": [(550, 567)],
    "MS": [(386, 397)],
    "MO": [(630, 658)],
    "MT": [(590, 599)],
    "NE": [(680, 693)],
    "NV": [(889, 898)],
    "NH": [(30, 38)],
    "NJ": [(70, 89)],
    "NM": [(870, 884)],
    "NY": [(100, 149), (5, 5), (63, 63)], # e.g. 00501, 10705
    "NC": [(270, 289)],
    "ND": [(580, 588)],
    "OH": [(430, 459)],
    "OK": [(730, 749)],
    "OR": [(970, 979)],
    "PA": [(150, 196)],
    "RI": [(28, 29)],
    "SC": [(290, 299)],
    "SD": [(570, 577)],
    "TN": [(370, 385)],
    "TX": [(750, 799), (733, 733), (885, 885)],
    "UT": [(840, 847)],
    "VT": [(50, 59)],
    "VA": [(201, 201), (220, 246)],
    "WA": [(980, 994)],
    "WV": [(247, 268)],
    "WI": [(530, 549)],
    "WY": [(820, 831)],
    "PR": [(6, 9)],
    "VI": [(8, 8)],
    "GU": [(969, 969)],
    "AS": [(967, 967)],
    "MP": [(969, 969)],
    "AA": [(340, 340)],
    "AE": [(90, 98)],
    "AP": [(962, 966)],
}

# In-memory cache for ZIP lookups
_ZIP_CACHE = {}


def normalize_state(state_input):
    """Normalize input state (e.g. 'Montana', 'MT', 'new york', 'NY') to (code, full_name).
    Returns (None, None) if invalid.
    """
    if not state_input or not isinstance(state_input, str):
        return None, None
    clean = state_input.strip().lower()
    code = STATE_LOOKUP.get(clean)
    if code:
        return code, US_STATES[code]
    return None, None


def get_state_from_zip(zip_code):
    """Determine the likely US state code from a 5-digit ZIP code prefix range."""
    clean_zip = re.sub(r"[^\d]", "", str(zip_code or ""))[:5]
    if len(clean_zip) != 5:
        return None
    try:
        prefix = int(clean_zip[:3])
    except ValueError:
        return None

    for code, ranges in STATE_ZIP_PREFIX_RANGES.items():
        for low, high in ranges:
            if low <= prefix <= high:
                return code
    return None


def is_zip_valid_for_state(zip_code, state_input):
    """Validate if the given 5-digit ZIP code belongs to the specified state."""
    state_code, state_name = normalize_state(state_input)
    if not state_code:
        return False, "Invalid US state."

    clean_zip = re.sub(r"[^\d]", "", str(zip_code or ""))[:5]
    if len(clean_zip) != 5:
        return False, "ZIP code must be 5 digits (e.g. 59901)."

    try:
        prefix = int(clean_zip[:3])
    except ValueError:
        return False, "ZIP code must contain numbers."

    ranges = STATE_ZIP_PREFIX_RANGES.get(state_code, [])
    for low, high in ranges:
        if low <= prefix <= high:
            return True, None

    expected_state_code = get_state_from_zip(clean_zip)
    if expected_state_code:
        expected_name = US_STATES.get(expected_state_code, expected_state_code)
        return False, f"ZIP code {clean_zip} belongs to {expected_name} ({expected_state_code}), not {state_name} ({state_code})."
    
    return False, f"ZIP code {clean_zip} does not belong to {state_name}."


def lookup_zip(zip_code):
    """Lookup city and state for a 5-digit US ZIP code with multi-tier caching.
    Returns dict: {"zip": "59901", "city": "Kalispell", "state": "Montana", "state_code": "MT", "valid": True}
    """
    clean_zip = re.sub(r"[^\d]", "", str(zip_code or ""))[:5]
    if len(clean_zip) != 5:
        return {"zip": clean_zip, "valid": False, "error": "ZIP code must be 5 digits"}

    # 1. Check in-memory cache
    if clean_zip in _ZIP_CACHE:
        return _ZIP_CACHE[clean_zip]

    # 2. Derive state from prefix table as initial high-speed baseline
    state_code = get_state_from_zip(clean_zip)
    state_name = US_STATES.get(state_code, "") if state_code else ""

    result = {
        "zip": clean_zip,
        "valid": bool(state_code),
        "state_code": state_code or "",
        "state": state_name or "",
        "city": "",
    }

    # 3. Query Zippopotam API for precise city resolution with timeout
    try:
        url = f"https://api.zippopotam.us/us/{clean_zip}"
        req = urllib.request.Request(url, headers={"User-Agent": "EKUIPT-Store/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            places = data.get("places", [])
            if places:
                place = places[0]
                result["city"] = place.get("place name", "").title()
                result["state"] = place.get("state", state_name)
                result["state_code"] = place.get("state abbreviation", state_code)
                result["valid"] = True
    except Exception as e:
        log.debug(f"Zippopotam lookup failed for {clean_zip}: {e}")

    if result["valid"]:
        _ZIP_CACHE[clean_zip] = result

    return result


def validate_us_address(address_dict, prefix=""):
    """Comprehensive validation for an address dictionary.
    Keys expected (with optional prefix like 'ship_' or 'bill_'):
    - first_name, last_name, address1, city, state, zip (or zip_code)
    
    Returns (is_valid, normalized_dict, error_message)
    """
    p = prefix
    first = (address_dict.get(f"{p}first_name") or address_dict.get("first_name") or "").strip()
    last = (address_dict.get(f"{p}last_name") or address_dict.get("last_name") or "").strip()
    addr1 = (address_dict.get(f"{p}address1") or address_dict.get(f"{p}address_line1") or address_dict.get("address_line1") or "").strip()
    addr2 = (address_dict.get(f"{p}address2") or address_dict.get(f"{p}address_line2") or address_dict.get("address_line2") or "").strip()
    city = (address_dict.get(f"{p}city") or address_dict.get("city") or "").strip()
    state = (address_dict.get(f"{p}state") or address_dict.get("state") or "").strip()
    zip_code = (address_dict.get(f"{p}zip") or address_dict.get(f"{p}zip_code") or address_dict.get("zip_code") or "").strip()

    if not first:
        return False, None, f"{'Shipping' if 'ship' in p else 'Billing'} recipient first name is required."
    if not last:
        return False, None, f"{'Shipping' if 'ship' in p else 'Billing'} recipient last name is required."
    if not addr1:
        return False, None, f"{'Shipping' if 'ship' in p else 'Billing'} street address is required."
    if not city:
        return False, None, f"{'Shipping' if 'ship' in p else 'Billing'} city is required."
    if not state:
        return False, None, f"{'Shipping' if 'ship' in p else 'Billing'} state is required."

    state_code, state_name = normalize_state(state)
    if not state_code:
        return False, None, f"'{state}' is not a valid US state or territory."

    clean_zip = re.sub(r"[^\d]", "", zip_code)[:5]
    if len(clean_zip) != 5:
        return False, None, "ZIP code must be 5 digits (e.g. 59901)."

    zip_ok, zip_err = is_zip_valid_for_state(clean_zip, state_code)
    if not zip_ok:
        return False, None, zip_err

    normalized = {
        f"{p}first_name": first,
        f"{p}last_name": last,
        f"{p}address1": addr1,
        f"{p}address2": addr2,
        f"{p}city": city.title(),
        f"{p}state": state_name,
        f"{p}state_code": state_code,
        f"{p}zip": clean_zip,
    }
    return True, normalized, None


_SUGGEST_CACHE = {}

def suggest_addresses(query, limit=5):
    """Real-time US address autocomplete using OpenStreetMap Photon geocoder.
    Returns a list of structured US address suggestion dicts.
    """
    q = (query or "").strip()
    if len(q) < 3:
        return []

    cache_key = q.lower()
    if cache_key in _SUGGEST_CACHE:
        return _SUGGEST_CACHE[cache_key]

    results = []
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Append USA bias if not already in query
        encoded_q = urllib.parse.quote(q)
        url = f"https://photon.komoot.io/api/?q={encoded_q}&limit=8&lang=en"
        req = urllib.request.Request(url, headers={"User-Agent": "EKUIPT-Industrial/1.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            features = data.get("features", [])
            for f in features:
                props = f.get("properties", {})
                country = (props.get("country") or "").upper()
                countrycode = (props.get("countrycode") or "").upper()
                if country and country not in ("UNITED STATES", "UNITED STATES OF AMERICA", "US", "USA") and countrycode not in ("US", "USA"):
                    continue

                housenumber = (props.get("housenumber") or "").strip()
                street_name = (props.get("street") or props.get("name") or "").strip()
                street = f"{housenumber} {street_name}".strip() if housenumber else street_name
                city = (props.get("city") or props.get("town") or props.get("village") or props.get("county") or "").strip()
                raw_state = (props.get("state") or "").strip()
                postcode = (props.get("postcode") or "").strip()

                state_code, state_name = normalize_state(raw_state)
                if not state_code and postcode:
                    state_code = get_state_from_zip(postcode)
                    if state_code:
                        state_name = US_STATES.get(state_code, state_code)

                if street and (city or state_name or postcode):
                    formatted_parts = [street]
                    if city:
                        formatted_parts.append(city)
                    if state_name or state_code:
                        st = state_code or state_name
                        if postcode:
                            formatted_parts.append(f"{st} {postcode}")
                        else:
                            formatted_parts.append(st)
                    elif postcode:
                        formatted_parts.append(postcode)

                    results.append({
                        "address1": street,
                        "city": city.title() if city else "",
                        "state": state_name or raw_state,
                        "state_code": state_code or "",
                        "zip": postcode[:5] if postcode else "",
                        "formatted": ", ".join(formatted_parts),
                    })
                    if len(results) >= limit:
                        break
    except Exception as e:
        log.debug(f"Address suggestion error for '{q}': {e}")

    _SUGGEST_CACHE[cache_key] = results
    return results
