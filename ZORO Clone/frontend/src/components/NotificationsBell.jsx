import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { BellIcon, PackageIcon, TruckIcon, RowsIcon, CheckCircleIcon } from "./Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";
const POLL_MS = 25000;

function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function notifIcon(type) {
  if (type === "order_shipped") return <TruckIcon size={16} />;
  if (type === "batch_placed") return <RowsIcon size={16} />;
  if (type === "order_processing") return <PackageIcon size={16} />;
  return <CheckCircleIcon size={16} />;
}

/* Header bell — live order/batch notifications for signed-in users. */
function NotificationsBell() {
  const { token, isAuthenticated } = useAuth();
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [unread, setUnread] = useState(0);
  const wrapRef = useRef(null);

  const load = useCallback(() => {
    if (!token) return;
    fetch(`${API_BASE}/api/notifications`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setNotifications(d.notifications || []);
        setUnread(d.unread_count || 0);
      })
      .catch(() => {});
  }, [token]);

  // Initial load + poll
  useEffect(() => {
    if (!isAuthenticated) return;
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [isAuthenticated, load]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const markAllRead = () => {
    fetch(`${API_BASE}/api/notifications/mark-read`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ all: true }),
    })
      .then(() => {
        setUnread(0);
        setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      })
      .catch(() => {});
  };

  if (!isAuthenticated) return null;

  return (
    <div className="notif" ref={wrapRef}>
      <button
        className="notif__bell"
        onClick={() => setOpen((v) => !v)}
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
        id="notif-bell"
      >
        <BellIcon size={19} />
        {unread > 0 && (
          <span className="notif__badge" id="notif-badge">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="notif__panel" id="notif-panel">
          <div className="notif__head">
            <span className="notif__head-title">Notifications</span>
            {unread > 0 && (
              <button className="notif__mark-read" onClick={markAllRead}>
                Mark all read
              </button>
            )}
          </div>

          <div className="notif__list">
            {notifications.length === 0 && (
              <div className="notif__empty">
                <BellIcon size={22} />
                <p>No notifications yet.</p>
                <span>Order confirmations and shipment updates land here.</span>
              </div>
            )}

            {notifications.map((n) => {
              const inner = (
                <>
                  <span className={`notif__icon notif__icon--${n.notif_type}`}>
                    {notifIcon(n.notif_type)}
                  </span>
                  <span className="notif__content">
                    <span className="notif__title">{n.title}</span>
                    {n.body && <span className="notif__body">{n.body}</span>}
                    <span className="notif__time">{timeAgo(n.created_at)}</span>
                  </span>
                  {!n.is_read && <span className="notif__dot" />}
                </>
              );
              const cls = `notif__item ${n.is_read ? "" : "notif__item--unread"}`;
              return n.order_id ? (
                <Link
                  key={n.notification_id}
                  to={`/order-confirmation/${n.order_id}`}
                  className={cls}
                  onClick={() => setOpen(false)}
                >
                  {inner}
                </Link>
              ) : (
                <div key={n.notification_id} className={cls}>
                  {inner}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default NotificationsBell;
