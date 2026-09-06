import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { CopyIcon, CheckIcon, PlusIcon, TrashIcon, ShieldIcon } from "./Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const shortDate = (iso) =>
  iso ? new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }) : "never";

/* Account → API keys for the /api/v1 ordering API */
function ApiKeysPanel() {
  const { token } = useAuth();
  const [keys, setKeys] = useState(null);
  const [name, setName] = useState("");
  const [newKey, setNewKey] = useState(null); // shown once
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (!token) return;
    fetch(`${API_BASE}/api/keys`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setKeys(d.keys))
      .catch(() => {});
  }, [token]);

  useEffect(load, [load]);

  const createKey = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/keys`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ name: name.trim() || "default" }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Could not create key");
      setNewKey(d.api_key);
      setName("");
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (keyId) => {
    if (!window.confirm("Revoke this key? Scripts using it will stop working.")) return;
    await fetch(`${API_BASE}/api/keys/${keyId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
    load();
  };

  const copy = () => {
    navigator.clipboard?.writeText(newKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="apikeys" id="apikeys-panel">
      <div className="apikeys__intro">
        <h3 className="net30__title"><ShieldIcon size={18} /> Ordering API</h3>
        <p className="net30__blurb">
          Place and track bulk orders programmatically — perfect for
          connecting your own automation scripts. Authenticate with the{" "}
          <code>X-API-Key</code> header.
        </p>
      </div>

      {newKey && (
        <div className="apikeys__new" id="new-key-banner">
          <p className="apikeys__new-label">
            Your new key — copy it now, it won't be shown again:
          </p>
          <div className="apikeys__new-row">
            <code className="apikeys__new-key">{newKey}</code>
            <button className="btn btn--primary btn--sm" onClick={copy}>
              {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
              {copied ? "Copied" : "Copy"}
            </button>
            <button className="btn btn--secondary btn--sm" onClick={() => setNewKey(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      {error && <div className="auth-card__error">{error}</div>}

      <form className="apikeys__create" onSubmit={createKey}>
        <input
          className="form-input"
          placeholder="Key name (e.g. order-placer-bot)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button className="btn btn--accent" disabled={busy} id="create-key-btn">
          <PlusIcon size={15} /> {busy ? "Creating…" : "Create key"}
        </button>
      </form>

      {keys === null ? (
        <div className="loading">
          <div className="loading__spinner" />
        </div>
      ) : keys.length === 0 ? (
        <p className="net30__empty">No active keys yet.</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th />
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.key_id}>
                  <td className="admin-table__strong">{k.name}</td>
                  <td><code>{k.key_prefix}…</code></td>
                  <td>{shortDate(k.created_at)}</td>
                  <td>{shortDate(k.last_used_at)}</td>
                  <td>
                    <button
                      className="btn btn--secondary btn--sm"
                      onClick={() => revoke(k.key_id)}
                    >
                      <TrashIcon size={13} /> Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="apikeys__docs">
        <h4>Quick start</h4>
        <pre className="admin-email__body">{`# sanity check
curl ${API_BASE}/api/v1/ping -H "X-API-Key: zsk_..."

# place a bulk batch (one order per row)
curl -X POST ${API_BASE}/api/v1/orders/bulk \\
  -H "X-API-Key: zsk_..." -H "Content-Type: application/json" \\
  -d '{
    "billing": {"first_name":"...","last_name":"...","address1":"...",
                "city":"...","state":"TX","zip":"78701"},
    "orders": [
      {"sku":"G1202564","quantity":2,"first_name":"John","last_name":"Doe",
       "address1":"1 Main St","city":"Dallas","state":"TX","zip":"75001"}
    ]
  }'

# track: GET /api/v1/orders/<id> · GET /api/v1/batches/<batch_id>
# catalog: GET /api/v1/products?q=gloves · GET /api/v1/products/<sku>`}</pre>
      </div>
    </div>
  );
}

export default ApiKeysPanel;
