/**
 * US Address & ZIP Code Validation with Multi-Tier LocalStorage Caching
 * ────────────────────────────────────────────────────────────────────
 * Provides:
 * 1. 50 US States + DC + Territories list for fast dropdowns / autocomplete
 * 2. Instant offline 3-digit ZIP prefix range validation per state
 * 3. Real-time ZIP Code Lookup with City & State auto-population
 * 4. LocalStorage Address & ZIP Caching for lightning-fast checkout
 */

import { api } from "./api";

export const US_STATES = [
  { code: "AL", name: "Alabama" },
  { code: "AK", name: "Alaska" },
  { code: "AZ", name: "Arizona" },
  { code: "AR", name: "Arkansas" },
  { code: "CA", name: "California" },
  { code: "CO", name: "Colorado" },
  { code: "CT", name: "Connecticut" },
  { code: "DE", name: "Delaware" },
  { code: "DC", name: "District of Columbia" },
  { code: "FL", name: "Florida" },
  { code: "GA", name: "Georgia" },
  { code: "HI", name: "Hawaii" },
  { code: "ID", name: "Idaho" },
  { code: "IL", name: "Illinois" },
  { code: "IN", name: "Indiana" },
  { code: "IA", name: "Iowa" },
  { code: "KS", name: "Kansas" },
  { code: "KY", name: "Kentucky" },
  { code: "LA", name: "Louisiana" },
  { code: "ME", name: "Maine" },
  { code: "MD", name: "Maryland" },
  { code: "MA", name: "Massachusetts" },
  { code: "MI", name: "Michigan" },
  { code: "MN", name: "Minnesota" },
  { code: "MS", name: "Mississippi" },
  { code: "MO", name: "Missouri" },
  { code: "MT", name: "Montana" },
  { code: "NE", name: "Nebraska" },
  { code: "NV", name: "Nevada" },
  { code: "NH", name: "New Hampshire" },
  { code: "NJ", name: "New Jersey" },
  { code: "NM", name: "New Mexico" },
  { code: "NY", name: "New York" },
  { code: "NC", name: "North Carolina" },
  { code: "ND", name: "North Dakota" },
  { code: "OH", name: "Ohio" },
  { code: "OK", name: "Oklahoma" },
  { code: "OR", name: "Oregon" },
  { code: "PA", name: "Pennsylvania" },
  { code: "RI", name: "Rhode Island" },
  { code: "SC", name: "South Carolina" },
  { code: "SD", name: "South Dakota" },
  { code: "TN", name: "Tennessee" },
  { code: "TX", name: "Texas" },
  { code: "UT", name: "Utah" },
  { code: "VT", name: "Vermont" },
  { code: "VA", name: "Virginia" },
  { code: "WA", name: "Washington" },
  { code: "WV", name: "West Virginia" },
  { code: "WI", name: "Wisconsin" },
  { code: "WY", name: "Wyoming" },
  { code: "PR", name: "Puerto Rico" },
  { code: "VI", name: "Virgin Islands" },
  { code: "GU", name: "Guam" },
  { code: "AS", name: "American Samoa" },
  { code: "MP", name: "Northern Mariana Islands" },
  { code: "AA", name: "Armed Forces Americas" },
  { code: "AE", name: "Armed Forces Europe" },
  { code: "AP", name: "Armed Forces Pacific" },
];

const STATE_LOOKUP = {};
US_STATES.forEach((s) => {
  STATE_LOOKUP[s.code.toLowerCase()] = s;
  STATE_LOOKUP[s.name.toLowerCase()] = s;
});

// Official USPS 3-digit prefix ranges
const STATE_ZIP_PREFIX_RANGES = {
  AL: [[350, 369]],
  AK: [[995, 999]],
  AZ: [[850, 865]],
  AR: [[716, 729], [755, 755]],
  CA: [[900, 961]],
  CO: [[800, 816]],
  CT: [[60, 69]],
  DE: [[197, 199]],
  DC: [[200, 205], [569, 569]],
  FL: [[320, 349]],
  GA: [[300, 319], [398, 399]],
  HI: [[967, 968]],
  ID: [[832, 838]],
  IL: [[600, 629]],
  IN: [[460, 479]],
  IA: [[500, 528]],
  KS: [[660, 679]],
  KY: [[400, 427]],
  LA: [[700, 714], [715, 715]],
  ME: [[39, 49]],
  MD: [[206, 219]],
  MA: [[10, 27], [55, 55]],
  MI: [[480, 499]],
  MN: [[550, 567]],
  MS: [[386, 397]],
  MO: [[630, 658]],
  MT: [[590, 599]],
  NE: [[680, 693]],
  NV: [[889, 898]],
  NH: [[30, 38]],
  NJ: [[70, 89]],
  NM: [[870, 884]],
  NY: [[100, 149], [5, 5], [63, 63]],
  NC: [[270, 289]],
  ND: [[580, 588]],
  OH: [[430, 459]],
  OK: [[730, 749]],
  OR: [[970, 979]],
  PA: [[150, 196]],
  RI: [[28, 29]],
  SC: [[290, 299]],
  SD: [[570, 577]],
  TN: [[370, 385]],
  TX: [[750, 799], [733, 733], [885, 885]],
  UT: [[840, 847]],
  VT: [[50, 59]],
  VA: [[201, 201], [220, 246]],
  WA: [[980, 994]],
  WV: [[247, 268]],
  WI: [[530, 549]],
  WY: [[820, 831]],
  PR: [[6, 9]],
  VI: [[8, 8]],
  GU: [[969, 969]],
  AS: [[967, 967]],
  MP: [[969, 969]],
  AA: [[340, 340]],
  AE: [[90, 98]],
  AP: [[962, 966]],
};

/**
 * Normalizes state input to standard object { code: 'MT', name: 'Montana' }
 */
export function normalizeState(input) {
  if (!input || typeof input !== "string") return null;
  const clean = input.trim().toLowerCase();
  return STATE_LOOKUP[clean] || null;
}

/**
 * Finds the state matching a 5-digit ZIP code prefix
 */
export function getStateFromZip(zip) {
  const clean = String(zip || "").replace(/[^\d]/g, "").slice(0, 5);
  if (clean.length !== 5) return null;
  const prefix = parseInt(clean.slice(0, 3), 10);
  if (isNaN(prefix)) return null;

  for (const [code, ranges] of Object.entries(STATE_ZIP_PREFIX_RANGES)) {
    for (const [low, high] of ranges) {
      if (prefix >= low && prefix <= high) {
        return STATE_LOOKUP[code.toLowerCase()] || { code, name: code };
      }
    }
  }
  return null;
}

/**
 * Validates if a ZIP code matches the provided state
 */
export function validateZipForState(zip, stateInput) {
  const normState = normalizeState(stateInput);
  if (!normState) return { valid: false, error: "Please select a valid US state." };

  const cleanZip = String(zip || "").replace(/[^\d]/g, "").slice(0, 5);
  if (cleanZip.length !== 5) {
    return { valid: false, error: "ZIP code must be exactly 5 digits (e.g. 59901)." };
  }

  const prefix = parseInt(cleanZip.slice(0, 3), 10);
  const ranges = STATE_ZIP_PREFIX_RANGES[normState.code] || [];
  
  for (const [low, high] of ranges) {
    if (prefix >= low && prefix <= high) {
      return { valid: true, error: null };
    }
  }

  const actualState = getStateFromZip(cleanZip);
  if (actualState) {
    return {
      valid: false,
      error: `ZIP code ${cleanZip} belongs to ${actualState.name} (${actualState.code}), not ${normState.name} (${normState.code}).`,
      suggestedState: actualState,
    };
  }

  return {
    valid: false,
    error: `ZIP code ${cleanZip} is not a valid postal code for ${normState.name}.`,
  };
}

/**
 * Real-time ZIP Code Lookup with LocalStorage Cache
 */
export async function lookupZipCode(zip) {
  const cleanZip = String(zip || "").replace(/[^\d]/g, "").slice(0, 5);
  if (cleanZip.length !== 5) {
    return { valid: false, error: "ZIP code must be 5 digits" };
  }

  // 1. Check local storage cache
  try {
    const rawCache = localStorage.getItem("ekuipt_zip_cache");
    if (rawCache) {
      const cache = JSON.parse(rawCache);
      if (cache[cleanZip]) {
        return cache[cleanZip];
      }
    }
  } catch {}

  // 2. Fetch from backend API or fallback
  let result = null;
  try {
    result = await api(`/api/lookup-zip/${cleanZip}`);
  } catch {
    // If backend unavailable, compute from client state ranges
    const stateObj = getStateFromZip(cleanZip);
    if (stateObj) {
      result = {
        zip: cleanZip,
        valid: true,
        state: stateObj.name,
        state_code: stateObj.code,
        city: "",
      };
    }
  }

  if (result && result.valid) {
    // Save to local storage cache
    try {
      const rawCache = localStorage.getItem("ekuipt_zip_cache");
      const cache = rawCache ? JSON.parse(rawCache) : {};
      cache[cleanZip] = result;
      localStorage.setItem("ekuipt_zip_cache", JSON.stringify(cache));
    } catch {}
  }

  return result || { zip: cleanZip, valid: false, error: "Unable to verify ZIP code" };
}

/**
 * Address Caching: Store and retrieve saved addresses in localStorage
 */
const ADDRESS_CACHE_KEY = "ekuipt_saved_addresses_v1";

export function getCachedAddresses() {
  try {
    const raw = localStorage.getItem(ADDRESS_CACHE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveAddressToCache(address) {
  if (!address || !address.address1 || !address.zip) return;
  try {
    const addresses = getCachedAddresses();
    // Check if duplicate
    const key = `${address.address1.toLowerCase()}_${address.zip}`;
    const filtered = addresses.filter(
      (a) => `${a.address1.toLowerCase()}_${a.zip}` !== key
    );
    const updated = [
      {
        ...address,
        id: address.id || Date.now(),
        cachedAt: new Date().toISOString(),
      },
      ...filtered,
    ].slice(0, 5); // Keep up to 5 recent addresses

    localStorage.setItem(ADDRESS_CACHE_KEY, JSON.stringify(updated));
  } catch {}
}

/**
 * Address Autocomplete: Query real-time US street address suggestions
 */
export async function fetchAddressSuggestions(query) {
  const q = String(query || "").trim();
  if (q.length < 3) return [];

  const cacheKey = `ekuipt_sug_${q.toLowerCase()}`;
  try {
    const raw = sessionStorage.getItem(cacheKey);
    if (raw) return JSON.parse(raw);
  } catch {}

  try {
    const res = await api(`/api/address-suggest?q=${encodeURIComponent(q)}&limit=6`);
    const suggestions = res.suggestions || [];
    try {
      sessionStorage.setItem(cacheKey, JSON.stringify(suggestions));
    } catch {}
    return suggestions;
  } catch (err) {
    console.debug("Address suggestion error:", err);
    return [];
  }
}
