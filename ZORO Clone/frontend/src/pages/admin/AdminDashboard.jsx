import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch, money, shortDate, STATUS_COLORS } from "../../lib/adminApi";
import {
  PackageIcon, MailIcon, UserIcon, ClipboardIcon, TagIcon, RowsIcon,
} from "../../components/Icons";

function AdminDashboard() {
  const { token } = useAuth();
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) return;
    adminFetch(token, "/stats").then(setStats).catch((e) => setError(e.message));
    const t = setInterval(
      () => adminFetch(token, "/stats").then(setStats).catch(() => {}),
      30000
    );
    return () => clearInterval(t);
  }, [token]);

  if (error) return <div className="admin__error">{error}</div>;
  if (!stats) {
    return (
      <div className="loading">
        <div className="loading__spinner" />
        <span className="loading__text">Loading dashboard…</span>
      </div>
    );
  }

  const kpis = [
    { label: "Total revenue", value: money(stats.total_revenue), icon: <TagIcon size={18} /> },
    { label: "Orders", value: stats.total_orders, icon: <PackageIcon size={18} />, to: "/admin/orders" },
    { label: "Today", value: `${stats.today_orders} · ${money(stats.today_revenue)}`, icon: <RowsIcon size={18} /> },
    { label: "Open cases", value: stats.open_cases, icon: <ClipboardIcon size={18} />, to: "/admin/cases", alert: stats.open_cases > 0 },
    { label: "Products", value: stats.products, icon: <TagIcon size={18} />, to: "/admin/products" },
    { label: "Customers", value: stats.customers, icon: <UserIcon size={18} />, to: "/admin/customers" },
    { label: "Bulk batches", value: stats.bulk_batches, icon: <RowsIcon size={18} /> },
    { label: "Emails sent", value: stats.emails_sent, icon: <MailIcon size={18} />, to: "/admin/emails" },
  ];

  return (
    <div className="admin__page" id="admin-dashboard">
      <div className="admin__page-head">
        <h1 className="admin__title">Dashboard</h1>
        <span className="admin__subtitle">Live store overview — refreshes automatically</span>
      </div>

      <div className="admin-kpis">
        {kpis.map((kpi) => {
          const inner = (
            <>
              <span className={`admin-kpi__icon ${kpi.alert ? "admin-kpi__icon--alert" : ""}`}>
                {kpi.icon}
              </span>
              <div>
                <span className="admin-kpi__value">{kpi.value}</span>
                <span className="admin-kpi__label">{kpi.label}</span>
              </div>
            </>
          );
          return kpi.to ? (
            <Link className="admin-kpi admin-kpi--link" to={kpi.to} key={kpi.label}>
              {inner}
            </Link>
          ) : (
            <div className="admin-kpi" key={kpi.label}>{inner}</div>
          );
        })}
      </div>

      <div className="admin-dash-grid">
        <div className="admin-card">
          <div className="admin-card__head">
            <h2>Orders by status</h2>
          </div>
          <div className="admin-status-list">
            {["confirmed", "processing", "shipped", "delivered", "cancelled"].map((s) => {
              const row = stats.by_status[s];
              if (!row) return null;
              return (
                <Link
                  to={`/admin/orders?status=${s}`}
                  className="admin-status-row"
                  key={s}
                >
                  <span className={`chip ${STATUS_COLORS[s] || "chip--neutral"}`}>{s}</span>
                  <span className="admin-status-row__count">{row.count} orders</span>
                  <span className="admin-status-row__rev">{money(row.revenue)}</span>
                </Link>
              );
            })}
          </div>
        </div>

        <div className="admin-card">
          <div className="admin-card__head">
            <h2>Latest cases</h2>
            <Link to="/admin/cases" className="admin-card__more">View all</Link>
          </div>
          {stats.recent_cases.length === 0 && (
            <p className="admin-card__empty">No cases yet.</p>
          )}
          {stats.recent_cases.map((c) => (
            <Link to={`/admin/cases?open=${c.case_id}`} className="admin-mini-case" key={c.case_id}>
              <span className={`chip ${STATUS_COLORS[c.status]}`}>{c.status}</span>
              <span className="admin-mini-case__subject">
                #{c.case_id} — {c.subject}
              </span>
              <span className="admin-mini-case__meta">{shortDate(c.updated_at)}</span>
            </Link>
          ))}
        </div>
      </div>

      <div className="admin-card">
        <div className="admin-card__head">
          <h2>Recent orders</h2>
          <Link to="/admin/orders" className="admin-card__more">View all</Link>
        </div>
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Order</th><th>Placed</th><th>Customer</th><th>Ship to</th>
                <th>Source</th><th>Total</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_orders.map((o) => (
                <tr key={o.order_id}>
                  <td>
                    <Link className="admin-table__link" to={`/admin/orders?open=${o.order_id}`}>
                      #{o.order_id}
                    </Link>
                  </td>
                  <td>{shortDate(o.created_at)}</td>
                  <td>{o.notification_email || o.ship_email}</td>
                  <td>{o.ship_first_name} {o.ship_last_name}, {o.ship_city} {o.ship_state}</td>
                  <td>
                    {o.source === "bulk"
                      ? <span className="chip chip--neutral"><RowsIcon size={11} /> bulk</span>
                      : "web"}
                  </td>
                  <td>{money(o.total)}</td>
                  <td><span className={`chip ${STATUS_COLORS[o.status]}`}>{o.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default AdminDashboard;
