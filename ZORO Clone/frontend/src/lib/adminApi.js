const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

/** Minimal fetch helper for the admin API — always sends the token. */
export async function adminFetch(token, path, options = {}) {
  const res = await fetch(`${API_BASE}/api/admin${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export const money = (v) =>
  `$${parseFloat(v || 0).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

export const shortDate = (iso) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

export const STATUS_COLORS = {
  confirmed: "chip--neutral",
  processing: "chip--warning",
  shipped: "chip--success",
  delivered: "chip--success",
  cancelled: "chip--danger",
  open: "chip--danger",
  pending: "chip--warning",
  resolved: "chip--success",
  closed: "chip--neutral",
};
