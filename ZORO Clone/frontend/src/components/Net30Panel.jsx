import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { CreditCardIcon, CheckCircleIcon, AlertIcon } from "./Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const money = (v) => `$${parseFloat(v || 0).toLocaleString(undefined, {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
})}`;

const shortDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—";

/* Account → Net 30 & Invoices */
function Net30Panel() {
  const { token, user } = useAuth();
  const [data, setData] = useState(null);
  const [company, setCompany] = useState(user?.company || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (!token) return;
    fetch(`${API_BASE}/api/net30/status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setData(d))
      .catch(() => {});
  }, [token]);

  useEffect(load, [load]);

  const apply = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/net30/apply`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ company }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Application failed");
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return (
      <div className="loading">
        <div className="loading__spinner" />
        <span className="loading__text">Loading Net 30…</span>
      </div>
    );
  }

  const pct = data.limit > 0 ? Math.min((data.used / data.limit) * 100, 100) : 0;

  return (
    <div className="net30" id="net30-panel">
      {data.status === "none" || data.status === "rejected" ? (
        <div className="net30__apply-card">
          <h3 className="net30__title">
            <CreditCardIcon size={18} /> Apply for Net 30 terms
          </h3>
          <p className="net30__blurb">
            Order now, pay in 30 days. Approved accounts get a credit line and
            per-order invoices — usable at checkout and in the Bulk Order Pad.
          </p>
          {data.status === "rejected" && (
            <div className="auth-card__error">
              A previous application wasn't approved — you may apply again.
            </div>
          )}
          {error && <div className="auth-card__error">{error}</div>}
          <form onSubmit={apply} className="net30__form">
            <div className="form-group" style={{ flex: 1 }}>
              <label className="form-label">Company name *</label>
              <input
                className="form-input"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="Your company LLC"
                required
              />
            </div>
            <button className="btn btn--accent" disabled={busy} id="net30-apply-btn">
              {busy ? "Submitting…" : "Apply for Net 30"}
            </button>
          </form>
        </div>
      ) : data.status === "pending" ? (
        <div className="net30__pending">
          <AlertIcon size={22} />
          <div>
            <strong>Application under review</strong>
            <p>
              We're reviewing the Net 30 application for{" "}
              <strong>{data.company}</strong>. You'll get a bell notification
              and email once it's decided.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="net30__summary">
            <div className="net30__stat">
              <span className="net30__stat-label">Status</span>
              <span className="chip chip--success">
                <CheckCircleIcon size={12} /> Approved
              </span>
            </div>
            <div className="net30__stat">
              <span className="net30__stat-label">Credit limit</span>
              <span className="net30__stat-value">{money(data.limit)}</span>
            </div>
            <div className="net30__stat">
              <span className="net30__stat-label">Open balance</span>
              <span className="net30__stat-value">{money(data.used)}</span>
            </div>
            <div className="net30__stat">
              <span className="net30__stat-label">Available</span>
              <span className="net30__stat-value net30__stat-value--good">
                {money(data.available)}
              </span>
            </div>
          </div>

          <div className="net30__bar">
            <div className="net30__bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <p className="net30__bar-note">
            {data.company} · {pct.toFixed(0)}% of credit line in use
          </p>

          <h3 className="net30__invoices-title">Invoices ({data.invoices.length})</h3>
          {data.invoices.length === 0 && (
            <p className="net30__empty">
              No invoices yet — pick "Net 30 (invoice)" at checkout or in the{" "}
              <Link to="/bulk-order">Bulk Order Pad</Link>.
            </p>
          )}
          {data.invoices.length > 0 && (
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Invoice</th><th>Order</th><th>Issued</th>
                    <th>Due</th><th>Amount</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.invoices.map((inv) => (
                    <tr key={inv.order_id}>
                      <td className="admin-table__strong">{inv.invoice_number}</td>
                      <td>
                        <Link className="admin-table__link" to={`/order-confirmation/${inv.order_id}`}>
                          #{inv.order_id}
                        </Link>
                      </td>
                      <td>{shortDate(inv.created_at)}</td>
                      <td>{shortDate(inv.due_date)}</td>
                      <td>{money(inv.total)}</td>
                      <td>
                        {inv.paid_at ? (
                          <span className="chip chip--success">Paid</span>
                        ) : inv.overdue ? (
                          <span className="chip chip--danger">Overdue</span>
                        ) : (
                          <span className="chip chip--warning">Open</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default Net30Panel;
