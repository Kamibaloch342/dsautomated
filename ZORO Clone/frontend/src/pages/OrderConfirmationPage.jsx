import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import {
  CheckCircleIcon, MailIcon, TruckIcon, PackageIcon,
  ChevronDownIcon, RowsIcon, CreditCardIcon, AlertIcon,
} from "../components/Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const RETURN_REASONS = [
  ["damaged", "Arrived damaged"],
  ["wrong_item", "Wrong item received"],
  ["defective", "Defective / doesn't work"],
  ["no_longer_needed", "No longer needed"],
  ["other", "Other"],
];
const POLL_MS = 15000;

const STATUS_STEPS = [
  { key: "confirmed", label: "Confirmed", icon: CheckCircleIcon },
  { key: "processing", label: "Processing", icon: PackageIcon },
  { key: "shipped", label: "Shipped", icon: TruckIcon },
];

const statusIndex = (status) =>
  Math.max(STATUS_STEPS.findIndex((s) => s.key === status), 0);

function formatDateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function OrderConfirmationPage() {
  const { orderId } = useParams();
  const { token } = useAuth();
  const [order, setOrder] = useState(null);
  const [emails, setEmails] = useState([]);
  const [openEmail, setOpenEmail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnForm, setReturnForm] = useState({ reason: "damaged", details: "" });
  const [returnResult, setReturnResult] = useState(null);
  const [returnError, setReturnError] = useState("");
  const [returnBusy, setReturnBusy] = useState(false);

  const submitReturn = async () => {
    setReturnBusy(true);
    setReturnError("");
    try {
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${API_BASE}/api/returns`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          order_id: parseInt(orderId, 10),
          reason: returnForm.reason,
          details: returnForm.details,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Return request failed");
      setReturnResult(data.return);
      setReturnOpen(false);
    } catch (err) {
      setReturnError(err.message);
    } finally {
      setReturnBusy(false);
    }
  };

  const load = useCallback(() => {
    return Promise.all([
      fetch(`${API_BASE}/api/orders/${orderId}`).then((r) => {
        if (!r.ok) throw new Error("Order not found");
        return r.json();
      }),
      fetch(`${API_BASE}/api/orders/${orderId}/emails`)
        .then((r) => (r.ok ? r.json() : { emails: [] }))
        .catch(() => ({ emails: [] })),
    ])
      .then(([orderData, emailData]) => {
        setOrder(orderData.order);
        setEmails(emailData.emails || []);
      })
      .catch((err) => setError(err.message));
  }, [orderId]);

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  // Live-update until the order ships, so the processing/shipment
  // emails and the timeline appear without a refresh.
  useEffect(() => {
    if (!order || order.status === "shipped") return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [order, load]);

  const formatPrice = (p) => parseFloat(p || 0).toFixed(2);

  if (loading) {
    return (
      <main className="page-enter">
        <div className="container">
          <div className="loading">
            <div className="loading__spinner" />
            <span className="loading__text">Loading order...</span>
          </div>
        </div>
      </main>
    );
  }

  if (error || !order) {
    return (
      <main className="page-enter">
        <div className="container" style={{ padding: "60px 24px" }}>
          <div className="error-state">
            <h3 className="error-state__title">Order not found</h3>
            <Link to="/" className="btn btn--primary">
              Back to Home
            </Link>
          </div>
        </div>
      </main>
    );
  }

  const currentStep = statusIndex(order.status);

  return (
    <main className="page-enter">
      <section className="order-confirm" id="order-confirmation">
        <div className="container">
          <div className="order-confirm__card">
            <div className="order-confirm__icon">
              <CheckCircleIcon size={40} strokeWidth={1.8} />
            </div>
            <h1 className="order-confirm__title">
              {order.status === "shipped" ? "Order Shipped!" : "Order Confirmed!"}
            </h1>
            <p className="order-confirm__subtitle">
              Thank you for your order. Your order number is{" "}
              <strong>#{order.order_id}</strong>
              {order.bulk_batch_id && (
                <span className="chip chip--neutral order-confirm__batch">
                  <RowsIcon size={12} /> Batch {order.bulk_batch_id}
                </span>
              )}
            </p>

            {/* ── Status timeline ── */}
            <div className="order-timeline" id="order-timeline">
              {STATUS_STEPS.map((step, i) => {
                const Icon = step.icon;
                const done = i <= currentStep;
                return (
                  <div
                    key={step.key}
                    className={`order-timeline__step ${done ? "order-timeline__step--done" : ""}`}
                  >
                    <div className="order-timeline__dot">
                      <Icon size={15} />
                    </div>
                    <span className="order-timeline__label">{step.label}</span>
                    {step.key === "shipped" && order.shipped_at && (
                      <span className="order-timeline__date">
                        {formatDateTime(order.shipped_at)}
                      </span>
                    )}
                    {i < STATUS_STEPS.length - 1 && (
                      <span
                        className={`order-timeline__bar ${i < currentStep ? "order-timeline__bar--done" : ""}`}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {order.tracking_number && (
              <div className="order-confirm__tracking" id="order-tracking">
                <TruckIcon size={18} />
                <span>
                  Shipped via <strong>{order.carrier}</strong> — tracking{" "}
                  <strong>{order.tracking_number}</strong>
                </span>
              </div>
            )}

            {order.payment_method === "net30" && (
              <div className="order-confirm__tracking order-confirm__invoice" id="order-invoice">
                <CreditCardIcon size={18} />
                <span>
                  Net 30 invoice <strong>{order.invoice_number}</strong>
                  {order.net30_due_date && (
                    <> — due{" "}
                      <strong>
                        {new Date(order.net30_due_date).toLocaleDateString(undefined, {
                          month: "long", day: "numeric", year: "numeric",
                        })}
                      </strong>
                    </>
                  )}
                  {order.paid_at ? " · Paid" : " · Open"}
                </span>
              </div>
            )}

            <div className="order-confirm__details">
              <h3>Order Details</h3>
              <div className="order-confirm__items">
                {order.items.map((item, idx) => (
                  <div className="order-confirm__item" key={idx}>
                    <span className="order-confirm__item-title">
                      {item.title}
                    </span>
                    <span className="order-confirm__item-meta">
                      Qty: {item.quantity} × ${formatPrice(item.unit_price)}
                    </span>
                  </div>
                ))}
              </div>

              <div className="cart-summary__divider" />

              <div className="cart-summary__row">
                <span>Subtotal</span>
                <span>${formatPrice(order.subtotal)}</span>
              </div>
              <div className="cart-summary__row">
                <span>Shipping</span>
                <span>
                  {order.shipping_cost === 0 ? (
                    <span className="cart-summary__free">FREE</span>
                  ) : (
                    `$${formatPrice(order.shipping_cost)}`
                  )}
                </span>
              </div>
              <div className="cart-summary__row">
                <span>Tax</span>
                <span>${formatPrice(order.tax)}</span>
              </div>
              <div className="cart-summary__row cart-summary__row--total">
                <span>Total</span>
                <span>${formatPrice(order.total)}</span>
              </div>
            </div>

            {/* ── Addresses: shipping + billing ── */}
            <div className="order-confirm__addresses">
              <div className="order-confirm__shipping">
                <h3>Shipping To</h3>
                <p>
                  {order.ship_first_name} {order.ship_last_name}
                  <br />
                  {order.ship_address1}
                  {order.ship_address2 ? <><br />{order.ship_address2}</> : null}
                  <br />
                  {order.ship_city}, {order.ship_state} {order.ship_zip}
                </p>
              </div>
              <div className="order-confirm__shipping">
                <h3>Billing To</h3>
                {order.billing_same_as_shipping ? (
                  <p className="order-confirm__same-billing">
                    Same as shipping address
                  </p>
                ) : (
                  <p>
                    {order.bill_first_name} {order.bill_last_name}
                    <br />
                    {order.bill_address1}
                    {order.bill_address2 ? <><br />{order.bill_address2}</> : null}
                    <br />
                    {order.bill_city}, {order.bill_state} {order.bill_zip}
                  </p>
                )}
              </div>
            </div>

            {/* ── Email thread ── */}
            <div className="email-thread" id="email-thread">
              <h3 className="email-thread__title">
                <MailIcon size={16} /> Email updates
                <span className="email-thread__to">
                  → {order.notification_email || order.ship_email}
                </span>
              </h3>
              {emails.length === 0 && (
                <p className="email-thread__empty">
                  Confirmation email is on its way…
                </p>
              )}
              {emails.map((e) => (
                <div className="email-thread__item" key={e.email_id}>
                  <button
                    className="email-thread__head"
                    onClick={() =>
                      setOpenEmail(openEmail === e.email_id ? null : e.email_id)
                    }
                  >
                    <span className={`email-thread__type email-thread__type--${e.email_type}`}>
                      {e.email_type === "shipment" ? <TruckIcon size={13} /> : <MailIcon size={13} />}
                      {e.email_type.replace("_", " ")}
                    </span>
                    <span className="email-thread__subject">{e.subject}</span>
                    <span className="email-thread__meta">
                      {e.status === "sent" ? "Sent" : e.status === "simulated" ? "Logged" : "Failed"}
                      {" · "}
                      {formatDateTime(e.created_at)}
                    </span>
                    <ChevronDownIcon
                      size={15}
                      className={openEmail === e.email_id ? "email-thread__chev email-thread__chev--open" : "email-thread__chev"}
                    />
                  </button>
                  {openEmail === e.email_id && (
                    <pre className="email-thread__body">{e.body_text}</pre>
                  )}
                </div>
              ))}
              {order.status !== "shipped" && (
                <p className="email-thread__pending">
                  <TruckIcon size={14} /> Shipment email with tracking will land
                  here automatically once this order ships — the page updates live.
                </p>
              )}
            </div>

            {/* ── Return request ── */}
            {returnResult && (
              <div className="return-banner" id="return-banner">
                <CheckCircleIcon size={18} />
                Return #{returnResult.return_id} requested — we'll email{" "}
                <strong>{returnResult.email}</strong> with the RMA decision.
              </div>
            )}

            {returnOpen && !returnResult && (
              <div className="return-form" id="return-form">
                <h3 className="return-form__title">Request a return</h3>
                {returnError && (
                  <div className="auth-card__error">
                    <AlertIcon size={14} /> {returnError}
                  </div>
                )}
                <div className="return-form__row">
                  <select
                    className="form-input"
                    value={returnForm.reason}
                    onChange={(e) =>
                      setReturnForm((p) => ({ ...p, reason: e.target.value }))
                    }
                  >
                    {RETURN_REASONS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </div>
                <textarea
                  className="form-input"
                  rows={3}
                  placeholder="Tell us what happened…"
                  value={returnForm.details}
                  onChange={(e) =>
                    setReturnForm((p) => ({ ...p, details: e.target.value }))
                  }
                />
                <div className="return-form__actions">
                  <button
                    className="btn btn--accent btn--sm"
                    onClick={submitReturn}
                    disabled={returnBusy}
                    id="submit-return-btn"
                  >
                    {returnBusy ? "Submitting…" : "Submit return request"}
                  </button>
                  <button
                    className="btn btn--secondary btn--sm"
                    onClick={() => setReturnOpen(false)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <div className="order-confirm__cta-row">
              <Link to="/" className="btn btn--accent">
                Continue Shopping
              </Link>
              {order.bulk_batch_id && (
                <Link to="/bulk-order" className="btn btn--secondary">
                  <RowsIcon size={15} /> Place another bulk batch
                </Link>
              )}
              {["shipped", "delivered"].includes(order.status) && !returnResult && (
                <button
                  className="btn btn--secondary"
                  onClick={() => setReturnOpen((v) => !v)}
                  id="request-return-btn"
                >
                  Request a return
                </button>
              )}
              <Link
                to={`/contact?order=${order.order_id}`}
                className="btn btn--secondary"
                id="report-issue-btn"
              >
                Report an issue
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export default OrderConfirmationPage;
