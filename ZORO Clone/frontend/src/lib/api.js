// Central API helper — the single place the backend base URL is defined.
// Defaults to empty string to use Vite's proxy for local & public tunnel access.
export const API_BASE = import.meta.env.VITE_API_BASE || "";

export function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function api(path, { token, ...options } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...authHeaders(token),
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const err = new Error(data?.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// ── Authenticated invoice viewer ──
// Invoice endpoints require a Bearer token (owner) or a guest ?key=.
// A plain <a href> can't send either, so fetch → blob → open.
export async function openInvoice(orderId, { token, guestKey } = {}) {
  const q = guestKey ? `?key=${encodeURIComponent(guestKey)}` : "";
  const res = await fetch(`${API_BASE}/api/orders/${orderId}/invoice-html${q}`, {
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Could not load the invoice for this order.");
  const html = await res.text();
  const blob = new Blob([html], { type: "text/html" });
  window.open(URL.createObjectURL(blob), "_blank", "noopener");
}

// ── Zoro CDN images ──
// The catalog stores full-size CDN URLs (…/static/cms/product/full/x.JPG).
// Smaller renditions exist at the same path with a different size segment.
const CDN_SIZE_RE = /(\/static\/cms\/product\/)(tiny|small|medium|large|full)(\/)/;

export function imgUrl(url, size = "large") {
  if (!url) return null;
  return url.replace(CDN_SIZE_RE, `$1${size}$3`);
}

// Fallback used by <img onError> when the browser can't hotlink the CDN —
// the backend fetches + caches the image server-side.
export function imgProxyUrl(url) {
  if (!url) return null;
  return `${API_BASE}/api/img?u=${encodeURIComponent(url)}`;
}

// Neutral placeholder shown when an image is missing from the CDN.
const IMG_PLACEHOLDER =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
      <rect width="120" height="120" fill="#fafafa"/>
      <g stroke="#cccccc" stroke-width="3" fill="none" stroke-linejoin="round">
        <rect x="28" y="34" width="64" height="52" rx="4"/>
        <circle cx="48" cy="52" r="6"/>
        <path d="m32 82 20-20 12 12 14-14 14 14"/>
      </g>
    </svg>`
  );

// Pair with className="img-fade" for a soft fade-in once the image loads.
export function fadeInImg(e) {
  e.currentTarget.classList.add("is-loaded");
}

export function handleImgError(e, originalUrl) {
  const el = e.currentTarget;
  if (!originalUrl || el.dataset.proxied) {
    el.src = IMG_PLACEHOLDER;
    return;
  }
  el.dataset.proxied = "1";
  el.src = imgProxyUrl(originalUrl);
}

// ── formatting ──
export function money(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}

export function formatCount(n, capped = false) {
  if (n === null || n === undefined) return "";
  return `${Number(n).toLocaleString("en-US")}${capped ? "+" : ""}`;
}

export function slugToTitle(slug) {
  return (slug || "")
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
