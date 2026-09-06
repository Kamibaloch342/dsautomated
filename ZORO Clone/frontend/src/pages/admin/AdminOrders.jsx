import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch, money, shortDate, STATUS_COLORS } from "../../lib/adminApi";
import {
  RowsIcon, TruckIcon, MailIcon, XIcon, ChevronDownIcon, CheckIcon,
} from "../../components/Icons";

const STATUSES = ["", "confirmed", "processing", "shipped", "delivered", "cancelled"];

function AdminOrders() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState(null);   // {order, emails, cases}
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [shipForm, setShipForm] = useState({ carrier: "", tracking_number: "" });
  const [notice, setNotice] = useState("");

  const status = searchParams.get("status") || "";
  const source = searchParams.get("source") || "";
  const q = searchParams.get("q") || "";
  const page = parseInt(searchParams.get("page") || "1", 10);
  const openId = searchParams.get("open");

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ page: String(page), per_page: "25" });
    if (status) params.set("status", status);
    if (source) params.set("source", source);
    if (q) params.set("q", q);
    adminFetch(token, `/orders?${params}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, status, source, q, page]);

  useEffect(load, [load]);

  // Open detail drawer when ?open=<id>
  useEffect(() => {
    if (!token || !openId) {
      setDetail(null);
      return;
    }
    adminFetch(token, `/orders/${openId}`)
      .then((d) => {
        setDetail(d);
        setShipForm({ carrier: d.order.carrier || "", tracking_number: d.order.tracking_number || "" });
      })
      .catch((e) => setError(e.message));
  }, [token, openId]);

  const updateParam = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "open" && key !== "page") next.set("page", "1");
    setSearchParams(next);
  };

  const setStatus = async (newStatus) => {
    if (!detail) return;
    if (newStatus === "cancelled" &&
        !window.confirm(`Cancel order #${detail.order.order_id}? The customer will be emailed.`)) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const payload = { status: newStatus };
      if (newStatus === "shipped") {
        if (shipForm.carrier) payload.carrier = shipForm.carrier;
        if (shipForm.tracking_number) payload.tracking_number = shipForm.tracking_number;
      }
      await adminFetch(token, `/orders/${detail.order.order_id}/status`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const d = await adminFetch(token, `/orders/${detail.order.order_id}`);
      setDetail(d);
      setNotice(`Order marked ${newStatus} — customer notified.`);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const resend = async (emailType) => {
    if (!detail) return;
    setBusy(true);
    setNotice("");
    try {
      await adminFetch(token, `/orders/${detail.order.order_id}/resend`, {
        method: "POST",
        body: JSON.stringify({ email_type: emailType }),
      });
      const d = await adminFetch(token, `/orders/${detail.order.order_id}`);
      setDetail(d);
      setNotice(`${emailType} email re-sent.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin__page" id="admin-orders">
      <div className="admin__page-head">
        <h1 className="admin__title">Orders</h1>
        {data && <span className="admin__subtitle">{data.total} total</span>}
      </div>

      {/* Filters */}
      <div className="admin-filters">
        <select
          className="admin-input"
          value={status}
          onChange={(e) => updateParam("status", e.target.value)}
          id="admin-status-filter"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s ? `Status: ${s}` : "All statuses"}</option>
          ))}
        </select>
        <select
          className="admin-input"
          value={source}
          onChange={(e) => updateParam("source", e.target.value)}
        >
          <option value="">All sources</option>
          <option value="web">Web checkout</option>
          <option value="bulk">Bulk order pad</option>
        </select>
        <input
          className="admin-input admin-filters__search"
          placeholder="Search id, email, name, batch, tracking…"
          defaultValue={q}
          onKeyDown={(e) => {
            if (e.key === "Enter") updateParam("q", e.target.value.trim());
          }}
        />
      </div>

      {error && <div className="admin__error">{error} <button onClick={() => setError("")}><XIcon size={12} /></button></div>}

      {/* Orders table */}
      <div className="admin-card">
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Order</th><th>Placed</th><th>Customer</th><th>Ship to</th>
                <th>Items</th><th>Source / batch</th><th>Total</th><th>Status</th><th />
              </tr>
            </thead>
            <tbody>
              {data?.orders.map((o) => (
                <tr key={o.order_id} className={String(o.order_id) === openId ? "admin-table__row--open" : ""}>
                  <td><span className="admin-table__link">#{o.order_id}</span></td>
                  <td>{shortDate(o.created_at)}</td>
                  <td>{o.notification_email || o.ship_email}</td>
                  <td>{o.ship_first_name} {o.ship_last_name}, {o.ship_city} {o.ship_state}</td>
                  <td>{o.items.reduce((n, i) => n + i.quantity, 0)}</td>
                  <td>
                    {o.source === "bulk" ? (
                      <span className="chip chip--neutral"><RowsIcon size={11} /> {o.bulk_batch_id}</span>
                    ) : "web"}
                  </td>
                  <td>{money(o.total)}</td>
                  <td><span className={`chip ${STATUS_COLORS[o.status]}`}>{o.status}</span></td>
                  <td>
                    <button
                      className="btn btn--secondary btn--sm"
                      onClick={() => updateParam("open", String(o.order_id) === openId ? "" : String(o.order_id))}
                    >
                      {String(o.order_id) === openId ? "Close" : "Manage"}
                    </button>
                  </td>
                </tr>
              ))}
              {data && data.orders.length === 0 && (
                <tr><td colSpan={9} className="admin-table__empty">No orders match.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {data && data.pages > 1 && (
          <div className="admin-pager">
            <button className="btn btn--secondary btn--sm" disabled={page <= 1}
              onClick={() => updateParam("page", String(page - 1))}>← Prev</button>
            <span>Page {data.page} / {data.pages}</span>
            <button className="btn btn--secondary btn--sm" disabled={page >= data.pages}
              onClick={() => updateParam("page", String(page + 1))}>Next →</button>
          </div>
        )}
      </div>

      {/* Detail drawer */}
      {detail && (
        <div className="admin-drawer" id="admin-order-drawer">
          <div className="admin-drawer__head">
            <h2>
              Order #{detail.order.order_id}
              <span className={`chip ${STATUS_COLORS[detail.order.status]}`} style={{ marginLeft: 10 }}>
                {detail.order.status}
              </span>
              {detail.order.bulk_batch_id && (
                <span className="chip chip--neutral" style={{ marginLeft: 6 }}>
                  <RowsIcon size={11} /> {detail.order.bulk_batch_id}
                </span>
              )}
            </h2>
            <button className="admin-drawer__close" onClick={() => updateParam("open", "")}>
              <XIcon size={16} />
            </button>
          </div>

          {notice && <div className="admin-notice"><CheckIcon size={14} /> {notice}</div>}

          <div className="admin-drawer__grid">
            <div>
              <h3 className="admin-drawer__section">Items</h3>
              {detail.order.items.map((i) => (
                <div className="admin-drawer__item" key={i.order_item_id}>
                  <span className="admin-drawer__item-title">{i.title}</span>
                  <span>{i.quantity} × {money(i.unit_price)}</span>
                </div>
              ))}
              <div className="admin-drawer__totals">
                <span>Subtotal {money(detail.order.subtotal)}</span>
                <span>Shipping {money(detail.order.shipping_cost)}</span>
                <span>Tax {money(detail.order.tax)}</span>
                <strong>Total {money(detail.order.total)}</strong>
              </div>

              <h3 className="admin-drawer__section">Addresses</h3>
              <div className="admin-drawer__addresses">
                <p>
                  <strong>Ship to</strong><br />
                  {detail.order.ship_first_name} {detail.order.ship_last_name}<br />
                  {detail.order.ship_address1}{detail.order.ship_address2 && <><br />{detail.order.ship_address2}</>}<br />
                  {detail.order.ship_city}, {detail.order.ship_state} {detail.order.ship_zip}<br />
                  {detail.order.ship_email}{detail.order.ship_phone && ` · ${detail.order.ship_phone}`}
                </p>
                <p>
                  <strong>Bill to</strong><br />
                  {detail.order.billing_same_as_shipping ? "Same as shipping" : (
                    <>
                      {detail.order.bill_first_name} {detail.order.bill_last_name}<br />
                      {detail.order.bill_address1}{detail.order.bill_address2 && <><br />{detail.order.bill_address2}</>}<br />
                      {detail.order.bill_city}, {detail.order.bill_state} {detail.order.bill_zip}
                    </>
                  )}
                </p>
              </div>

              {detail.cases.length > 0 && (
                <>
                  <h3 className="admin-drawer__section">Linked cases</h3>
                  {detail.cases.map((c) => (
                    <Link key={c.case_id} to={`/admin/cases?open=${c.case_id}`} className="admin-mini-case">
                      <span className={`chip ${STATUS_COLORS[c.status]}`}>{c.status}</span>
                      <span className="admin-mini-case__subject">#{c.case_id} — {c.subject}</span>
                    </Link>
                  ))}
                </>
              )}
            </div>

            <div>
              <h3 className="admin-drawer__section">Fulfillment actions</h3>
              {detail.order.tracking_number && (
                <p className="admin-drawer__tracking">
                  <TruckIcon size={14} /> {detail.order.carrier} — {detail.order.tracking_number}
                </p>
              )}
              <div className="admin-actions">
                <button className="btn btn--secondary btn--sm" disabled={busy}
                  onClick={() => setStatus("processing")}>Mark processing</button>
                <button className="btn btn--secondary btn--sm" disabled={busy}
                  onClick={() => setStatus("delivered")}>Mark delivered</button>
                <button className="btn btn--secondary btn--sm admin-actions__danger" disabled={busy}
                  onClick={() => setStatus("cancelled")}>Cancel order</button>
              </div>

              <div className="admin-ship-form">
                <span className="admin-ship-form__label">Ship with (optional — auto-generated if blank):</span>
                <div className="admin-ship-form__row">
                  <select className="admin-input" value={shipForm.carrier}
                    onChange={(e) => setShipForm((p) => ({ ...p, carrier: e.target.value }))}>
                    <option value="">Carrier (auto)</option>
                    <option>UPS</option><option>FedEx</option><option>USPS</option>
                  </select>
                  <input className="admin-input" placeholder="Tracking # (auto)"
                    value={shipForm.tracking_number}
                    onChange={(e) => setShipForm((p) => ({ ...p, tracking_number: e.target.value }))} />
                  <button className="btn btn--accent btn--sm" disabled={busy}
                    onClick={() => setStatus("shipped")} id="admin-ship-btn">
                    <TruckIcon size={14} /> Ship now
                  </button>
                </div>
              </div>

              <h3 className="admin-drawer__section">
                Email thread ({detail.emails.length})
              </h3>
              <div className="admin-actions" style={{ marginBottom: 10 }}>
                <button className="btn btn--secondary btn--sm" disabled={busy} onClick={() => resend("confirmation")}>
                  <MailIcon size={13} /> Resend confirmation
                </button>
                <button className="btn btn--secondary btn--sm" disabled={busy} onClick={() => resend("shipment")}>
                  <MailIcon size={13} /> Resend shipment
                </button>
              </div>
              <div className="admin-emails">
                {detail.emails.map((e) => (
                  <details className="admin-email" key={e.email_id}>
                    <summary>
                      <span className={`email-thread__type email-thread__type--${e.email_type}`}>
                        {e.email_type.replace("_", " ")}
                      </span>
                      <span className="admin-email__subject">{e.subject}</span>
                      <span className="admin-email__meta">{e.status} · {shortDate(e.created_at)}</span>
                      <ChevronDownIcon size={13} />
                    </summary>
                    <pre className="admin-email__body">{e.body_text}</pre>
                  </details>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default AdminOrders;
