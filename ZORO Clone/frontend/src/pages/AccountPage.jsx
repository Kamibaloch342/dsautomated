import { useState, useEffect } from "react";
import { useNavigate, Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import {
  MailIcon, TruckIcon, RowsIcon, ChevronDownIcon, PackageIcon,
  ClipboardIcon,
} from "../components/Icons";
import Net30Panel from "../components/Net30Panel";
import ApiKeysPanel from "../components/ApiKeysPanel";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const CASE_STATUS_CLASS = {
  open: "chip--danger",
  pending: "chip--warning",
  resolved: "chip--success",
  closed: "chip--neutral",
};

function AccountPage() {
  const { user, token, isAuthenticated, loading, logout, updateProfile } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [tab, setTab] = useState(searchParams.get("tab") || "profile");
  const [cases, setCases] = useState([]);
  const [casesLoading, setCasesLoading] = useState(false);
  const [openCase, setOpenCase] = useState(null);
  const [caseReply, setCaseReply] = useState("");
  const [replySending, setReplySending] = useState(false);
  const [form, setForm] = useState({});
  const [orders, setOrders] = useState([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [openThread, setOpenThread] = useState(null);   // order_id
  const [threads, setThreads] = useState({});           // order_id → emails[]

  const toggleThread = (orderId) => {
    if (openThread === orderId) {
      setOpenThread(null);
      return;
    }
    setOpenThread(orderId);
    if (!threads[orderId]) {
      fetch(`${API_BASE}/api/orders/${orderId}/emails`)
        .then((r) => (r.ok ? r.json() : { emails: [] }))
        .then((d) =>
          setThreads((prev) => ({ ...prev, [orderId]: d.emails || [] }))
        )
        .catch(() =>
          setThreads((prev) => ({ ...prev, [orderId]: [] }))
        );
    }
  };

  useEffect(() => {
    if (loading) return; // wait for the token check before redirecting
    if (!isAuthenticated) {
      navigate("/login");
      return;
    }
    setForm({
      first_name: user.first_name || "",
      last_name: user.last_name || "",
      phone: user.phone || "",
      address_line1: user.address_line1 || "",
      address_line2: user.address_line2 || "",
      city: user.city || "",
      state: user.state || "",
      zip_code: user.zip_code || "",
    });
  }, [isAuthenticated, loading, user, navigate]);

  useEffect(() => {
    if (tab === "orders" && token) {
      setOrdersLoading(true);
      fetch(`${API_BASE}/api/my-orders`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then((d) => setOrders(d.orders || []))
        .catch(() => {})
        .finally(() => setOrdersLoading(false));
    }
    if (tab === "cases" && token) {
      setCasesLoading(true);
      fetch(`${API_BASE}/api/my-cases`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then((d) => setCases(d.cases || []))
        .catch(() => {})
        .finally(() => setCasesLoading(false));
    }
  }, [tab, token]);

  const sendCaseReply = async (caseId) => {
    if (!caseReply.trim()) return;
    setReplySending(true);
    try {
      const res = await fetch(`${API_BASE}/api/cases/${caseId}/reply`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ body: caseReply.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setCases((prev) =>
          prev.map((c) => (c.case_id === caseId ? data.case : c))
        );
        setCaseReply("");
      }
    } catch {
      /* keep text so the user can retry */
    } finally {
      setReplySending(false);
    }
  };

  const update = (field) => (e) =>
    setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSave = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMsg("");
    try {
      await updateProfile(form);
      setMsg("Profile updated!");
    } catch {
      setMsg("Failed to update profile.");
    } finally {
      setSaving(false);
    }
  };

  const handleLogout = () => {
    logout();
    navigate("/");
  };

  const formatPrice = (p) => parseFloat(p || 0).toFixed(2);

  if (loading) {
    return (
      <main className="page-enter">
        <div className="container">
          <div className="loading">
            <div className="loading__spinner" />
            <span className="loading__text">Loading account...</span>
          </div>
        </div>
      </main>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <main className="page-enter">
      <section className="account-page" id="account-page">
        <div className="container">
          <div className="account-header">
            <h1 className="account-header__title">
              Welcome, {user.first_name}!
            </h1>
            <button
              className="btn btn--secondary"
              onClick={handleLogout}
              id="logout-btn"
            >
              Sign Out
            </button>
          </div>

          <div className="account-tabs">
            <button
              className={`account-tab ${tab === "profile" ? "account-tab--active" : ""}`}
              onClick={() => setTab("profile")}
            >
              Profile
            </button>
            <button
              className={`account-tab ${tab === "orders" ? "account-tab--active" : ""}`}
              onClick={() => setTab("orders")}
            >
              Order History
            </button>
            <button
              className={`account-tab ${tab === "cases" ? "account-tab--active" : ""}`}
              onClick={() => setTab("cases")}
              id="cases-tab"
            >
              Support Cases
            </button>
            <button
              className={`account-tab ${tab === "net30" ? "account-tab--active" : ""}`}
              onClick={() => setTab("net30")}
              id="net30-tab"
            >
              Net 30 &amp; Invoices
            </button>
            <button
              className={`account-tab ${tab === "api" ? "account-tab--active" : ""}`}
              onClick={() => setTab("api")}
              id="api-tab"
            >
              API
            </button>
          </div>

          {tab === "net30" && <Net30Panel />}
          {tab === "api" && <ApiKeysPanel />}

          {tab === "profile" && (
            <form className="account-form" onSubmit={handleSave}>
              {msg && <div className="account-form__msg">{msg}</div>}

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">First Name</label>
                  <input
                    type="text"
                    className="form-input"
                    value={form.first_name || ""}
                    onChange={update("first_name")}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Last Name</label>
                  <input
                    type="text"
                    className="form-input"
                    value={form.last_name || ""}
                    onChange={update("last_name")}
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Email</label>
                <input
                  type="email"
                  className="form-input"
                  value={user.email}
                  disabled
                />
              </div>

              <div className="form-group">
                <label className="form-label">Phone</label>
                <input
                  type="tel"
                  className="form-input"
                  value={form.phone || ""}
                  onChange={update("phone")}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Address Line 1</label>
                <input
                  type="text"
                  className="form-input"
                  value={form.address_line1 || ""}
                  onChange={update("address_line1")}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Address Line 2</label>
                <input
                  type="text"
                  className="form-input"
                  value={form.address_line2 || ""}
                  onChange={update("address_line2")}
                />
              </div>

              <div className="form-row form-row--3">
                <div className="form-group">
                  <label className="form-label">City</label>
                  <input
                    type="text"
                    className="form-input"
                    value={form.city || ""}
                    onChange={update("city")}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">State</label>
                  <input
                    type="text"
                    className="form-input"
                    value={form.state || ""}
                    onChange={update("state")}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">ZIP Code</label>
                  <input
                    type="text"
                    className="form-input"
                    value={form.zip_code || ""}
                    onChange={update("zip_code")}
                  />
                </div>
              </div>

              <button
                type="submit"
                className="btn btn--accent"
                disabled={saving}
              >
                {saving ? "Saving..." : "Save Changes"}
              </button>
            </form>
          )}

          {tab === "orders" && (
            <div className="account-orders">
              {ordersLoading && (
                <div className="loading">
                  <div className="loading__spinner" />
                  <span className="loading__text">Loading orders...</span>
                </div>
              )}
              {!ordersLoading && orders.length === 0 && (
                <div className="empty-state" style={{ padding: "40px 0" }}>
                  <div className="empty-state__icon">📦</div>
                  <h3 className="empty-state__title">No orders yet</h3>
                  <p className="empty-state__message">
                    Your order history will appear here.
                  </p>
                </div>
              )}
              {orders.map((order) => (
                <div className="account-order" key={order.order_id}>
                  <div className="account-order__header">
                    <div className="account-order__id-wrap">
                      <Link
                        to={`/order-confirmation/${order.order_id}`}
                        className="account-order__id-link"
                      >
                        Order #{order.order_id}
                      </Link>
                      <span className="account-order__date">
                        {order.created_at
                          ? new Date(order.created_at).toLocaleDateString()
                          : ""}
                      </span>
                      {order.bulk_batch_id && (
                        <span className="chip chip--neutral">
                          <RowsIcon size={11} /> {order.bulk_batch_id}
                        </span>
                      )}
                    </div>
                    <span
                      className={`account-order__status account-order__status--${order.status}`}
                    >
                      {order.status === "shipped" && <TruckIcon size={12} />}
                      {order.status === "processing" && <PackageIcon size={12} />}
                      {order.status}
                    </span>
                  </div>
                  <div className="account-order__items">
                    {order.items.map((item, idx) => (
                      <span key={idx}>
                        {item.title} (×{item.quantity})
                      </span>
                    ))}
                  </div>
                  {order.tracking_number && (
                    <div className="account-order__tracking">
                      <TruckIcon size={13} /> {order.carrier} —{" "}
                      <strong>{order.tracking_number}</strong>
                    </div>
                  )}
                  <div className="account-order__footer">
                    <div className="account-order__total">
                      Total: ${formatPrice(order.total)}
                    </div>
                    <button
                      className="account-order__emails-btn"
                      onClick={() => toggleThread(order.order_id)}
                    >
                      <MailIcon size={13} /> Email updates
                      <ChevronDownIcon
                        size={13}
                        style={{
                          transform: openThread === order.order_id ? "rotate(180deg)" : "none",
                          transition: "transform 150ms",
                        }}
                      />
                    </button>
                  </div>

                  {openThread === order.order_id && (
                    <div className="account-order__thread">
                      {!threads[order.order_id] && (
                        <span className="account-order__thread-loading">Loading…</span>
                      )}
                      {threads[order.order_id]?.length === 0 && (
                        <span className="account-order__thread-loading">
                          No emails yet.
                        </span>
                      )}
                      {threads[order.order_id]?.map((e) => (
                        <div className="account-order__email" key={e.email_id}>
                          <span className={`email-thread__type email-thread__type--${e.email_type}`}>
                            {e.email_type === "shipment" ? <TruckIcon size={12} /> : <MailIcon size={12} />}
                            {e.email_type.replace("_", " ")}
                          </span>
                          <span className="account-order__email-subject">{e.subject}</span>
                          <span className="account-order__email-date">
                            {e.created_at
                              ? new Date(e.created_at).toLocaleString(undefined, {
                                  month: "short", day: "numeric",
                                  hour: "numeric", minute: "2-digit",
                                })
                              : ""}
                          </span>
                        </div>
                      ))}
                      <Link
                        to={`/contact?order=${order.order_id}`}
                        className="account-order__report"
                      >
                        <ClipboardIcon size={12} /> Report an issue with this order
                      </Link>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {tab === "cases" && (
            <div className="account-cases" id="account-cases">
              {casesLoading && (
                <div className="loading">
                  <div className="loading__spinner" />
                  <span className="loading__text">Loading cases...</span>
                </div>
              )}

              {!casesLoading && cases.length === 0 && (
                <div className="empty-state" style={{ padding: "40px 0" }}>
                  <div className="empty-state__icon"><ClipboardIcon size={30} /></div>
                  <h3 className="empty-state__title">No support cases</h3>
                  <p className="empty-state__message">
                    Need help with an order?{" "}
                    <Link to="/contact">Open a support case</Link>.
                  </p>
                </div>
              )}

              {!casesLoading && cases.length > 0 && (
                <div className="account-cases__new">
                  <Link to="/contact" className="btn btn--secondary btn--sm">
                    <ClipboardIcon size={14} /> Open a new case
                  </Link>
                </div>
              )}

              {cases.map((c) => (
                <div className="account-case" key={c.case_id}>
                  <button
                    className="account-case__head"
                    onClick={() =>
                      setOpenCase(openCase === c.case_id ? null : c.case_id)
                    }
                  >
                    <span className={`chip ${CASE_STATUS_CLASS[c.status] || "chip--neutral"}`}>
                      {c.status}
                    </span>
                    <span className="account-case__subject">
                      #{c.case_id} — {c.subject}
                    </span>
                    {c.order_id && (
                      <span className="chip chip--neutral">Order #{c.order_id}</span>
                    )}
                    <span className="account-case__date">
                      {c.updated_at
                        ? new Date(c.updated_at).toLocaleDateString()
                        : ""}
                    </span>
                    <ChevronDownIcon
                      size={14}
                      style={{
                        transform: openCase === c.case_id ? "rotate(180deg)" : "none",
                        transition: "transform 150ms",
                      }}
                    />
                  </button>

                  {openCase === c.case_id && (
                    <div className="account-case__body">
                      <div className="admin-convo">
                        {c.messages.map((m) => (
                          <div
                            key={m.case_message_id}
                            className={`admin-msg ${m.sender === "admin" ? "admin-msg--admin" : ""}`}
                          >
                            <span className="admin-msg__sender">
                              {m.sender === "admin" ? "ZoroStore Support" : "You"}
                            </span>
                            <p className="admin-msg__body">{m.body}</p>
                            <span className="admin-msg__time">
                              {m.created_at
                                ? new Date(m.created_at).toLocaleString(undefined, {
                                    month: "short", day: "numeric",
                                    hour: "numeric", minute: "2-digit",
                                  })
                                : ""}
                            </span>
                          </div>
                        ))}
                      </div>

                      {["open", "pending"].includes(c.status) ? (
                        <div className="account-case__reply">
                          <textarea
                            className="form-input"
                            rows={3}
                            placeholder="Reply to support…"
                            value={openCase === c.case_id ? caseReply : ""}
                            onChange={(e) => setCaseReply(e.target.value)}
                          />
                          <button
                            className="btn btn--accent btn--sm"
                            disabled={replySending || !caseReply.trim()}
                            onClick={() => sendCaseReply(c.case_id)}
                          >
                            {replySending ? "Sending…" : "Send reply"}
                          </button>
                        </div>
                      ) : (
                        <p className="account-case__closed-note">
                          This case is {c.status}.{" "}
                          <Link to="/contact">Open a new case</Link> if you need
                          anything else.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}

export default AccountPage;
