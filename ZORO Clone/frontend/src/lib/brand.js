// Single source of truth for the store's brand identity.
// Rename the store by editing this file (and Backend(Task)/brand.py).
export const BRAND = {
  name: "EKUIPT",
  fullName: "EKUIPT Industrial Supply",
  tagline: "Everything for business, industry & trade",
  codeLabel: "EQ Code",     // compact SKU label, e.g. "EQ Code: E4341486"
  codeName: "EQ Part Code", // long-form label used on the product page
  privateLabel: "EKUIPT Select", // Private label brand name
  supportEmail: "support@ekuipt.com",
  phone: "1-855-666-4649",
  colors: {
    primary: "#19454B",     // Eagle Green / Midnight Teal
    primaryDark: "#0E282C",
    primarySoft: "#E9F1F2",
    accent: "#964C14",      // Saddle Brown / Rust Copper
    accentDark: "#783B0E",
    accentSoft: "#FAF3EE",
  }
};

/**
 * Format any product code to EQ Code format (e.g. G4341486 -> E4341486)
 */
export function formatEqCode(code) {
  if (!code) return "";
  const s = String(code).trim();
  if (s.startsWith("G") || s.startsWith("g")) {
    return "E" + s.slice(1);
  }
  if (s.startsWith("E") || s.startsWith("e")) {
    return "E" + s.slice(1);
  }
  if (/^\d+$/.test(s)) {
    return "E" + s;
  }
  return s.toUpperCase();
}

/**
 * Rebrand Zoro Select to EKUIPT Select across all UI surfaces
 */
export function formatBrandName(brand) {
  if (!brand) return "";
  const s = String(brand).trim();
  if (
    s.toLowerCase() === "zoro select" ||
    s.toLowerCase() === "zoro" ||
    s.toLowerCase() === "zoro_select" ||
    s.toLowerCase() === "zoroselect"
  ) {
    return BRAND.privateLabel;
  }
  return s;
}

