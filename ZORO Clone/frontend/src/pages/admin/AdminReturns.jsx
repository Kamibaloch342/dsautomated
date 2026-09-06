import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch, money, shortDate } from "../../lib/adminApi";
import { XIcon, CheckIcon } from "../../components/Icons";

const RETURN_STATUS_CLASS = {
  requested: "chip--danger",
  approved: "chip--warning",
  denied: "chip--neutral",
  received: "chip--warning",
  refunded: "chip--success",
};

const STATUSES = ["", "requested", "approved", "denied", "received", "refunded"];

function AdminReturns() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const status = searchParams.get("status") || "";
  const q = searchParams.get("q") || "";
  const page = parseInt(searchParams.get("page") || "1", 10);

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ page: String(page), per_page: "25" });
    if (status) params.set("status", status);
    if (q) params.set("q", q);
    adminFetch(token, `/returns?${params}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, status, q, page]);

  useEffect(load, [load]);

  const updateParam = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.set("page", "1");
    setSearchParams(next);
  };

  const act = async (ret, action) => {
    let payload = { action };
    if (action === "approve" || action === "refund") {
      const amount = window.prompt(
        `${action === "approve" ? "Approve return" : "Issue refund"} — amount ($):`,
        ret.refund_amount ?? ret.order_total ?? ""
      );
      if (amount === null) return;
      payload.refund_amount = parseFloat(amount) || ret.order_total;
    }
    if (action === "deny") {
      const note = window.prompt("Reason for denial (sent to the customer):", "");
      if (note === null) return;
      payload.note = note;
    }
    setBusyId(ret.return_id);
    setNotice("");
    try {
      await adminFetch(token, `/returns/${ret.return_id}/action`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setNotice(`Return #${ret.return_id} ${action}d — customer emailed.`.replace("dd", "d"));
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="admin__page" id="admin-returns">
      <div className="admin__page-head">
        <h1 className="admin__title">Returns (RMA)</h1>
        {data && (
          <span className="admin__subtitle">
            {data.open_count} open · {data.total} total
          </span>
        )}
      </div>

      <div className="admin-filters">
        <select className="admin-input" value={status}
          onChange={(e) => updateParam("status", e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s ? `Status: ${s}` : "All statuses"}</option>
          ))}
        </select>
        <input
          className="admin-input admin-filters__search"
          placeholder="Search email, RMA, #order…"
          defaultValue={q}
          onKeyDown={(e) => {
            if (e.key === "Enter") updateParam("q", e.target.value.trim());
          }}
        />
      </div>

      {error && (
        <div className="admin__error">
          {error} <button onClick={() => setError("")}><XIcon size={12} /></button>
        </div>
      )}
      {notice && <div className="admin-notice"><CheckIcon size={14} /> {notice}</div>}

      <div className="admin-card">
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>RMA</th><th>Order</th><th>Items</th><th>Customer</th>
                <th>Reason</th><th>Refund</th><th>Updated</th><th>Status</th><th />
              </tr>
            </thead>
            <tbody>
              {data?.returns.map((r) => (
                <tr key={r.return_id}>
                  <td className="admin-table__strong">
                    {r.rma_number || `#${r.return_id}`}
                  </td>
                  <td>
                    <Link className="admin-table__link" to={`/admin/orders?open=${r.order_id}`}>
                      #{r.order_id}
                    </Link>
                  </td>
                  <td className="bulk-results__product" title={r.items_summary}>
                    {r.items_summary || "—"}
                  </td>
                  <td>{r.email}</td>
                  <td>
                    {r.reason.replace("_", " ")}
                    {r.details && (
                      <span className="admin-prod__meta" title={r.details}>
                        {r.details.slice(0, 48)}{r.details.length > 48 ? "…" : ""}
                      </span>
                    )}
                  </td>
                  <td>{r.refund_amount != null ? money(r.refund_amount) : "—"}</td>
                  <td>{shortDate(r.updated_at)}</td>
                  <td>
                    <span className={`chip ${RETURN_STATUS_CLASS[r.status]}`}>
                      {r.status}
                    </span>
                  </td>
                  <td>
                    <div className="admin-actions" style={{ marginBottom: 0 }}>
                      {r.status === "requested" && (
                        <>
                          <button className="btn btn--accent btn--sm"
                            disabled={busyId === r.return_id}
                            onClick={() => act(r, "approve")}>
                            Approve
                          </button>
                          <button className="btn btn--secondary btn--sm admin-actions__danger"
                            disabled={busyId === r.return_id}
                            onClick={() => act(r, "deny")}>
                            Deny
                          </button>
                        </>
                      )}
                      {r.status === "approved" && (
                        <button className="btn btn--secondary btn--sm"
                          disabled={busyId === r.return_id}
                          onClick={() => act(r, "received")}>
                          Mark received
                        </button>
                      )}
                      {["approved", "received"].includes(r.status) && (
                        <button className="btn btn--primary btn--sm"
                          disabled={busyId === r.return_id}
                          onClick={() => act(r, "refund")}>
                          Refund
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {data && data.returns.length === 0 && (
                <tr><td colSpan={9} className="admin-table__empty">No returns match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default AdminReturns;
