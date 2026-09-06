import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch } from "../../lib/adminApi";
import {
  PackageIcon, MailIcon, UserIcon, ClipboardIcon,
  TruckIcon, ShieldIcon, TagIcon,
} from "../../components/Icons";

/* Admin shell: guard + sidebar + content outlet. */
function AdminLayout() {
  const { user, token, loading, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [openCases, setOpenCases] = useState(0);

  useEffect(() => {
    if (loading) return;
    if (!isAuthenticated) {
      navigate("/login");
    } else if (!user?.is_admin) {
      navigate("/");
    }
  }, [loading, isAuthenticated, user, navigate]);

  // Open-cases badge for the sidebar
  useEffect(() => {
    if (!token || !user?.is_admin) return;
    const load = () =>
      adminFetch(token, "/cases?per_page=1")
        .then((d) => setOpenCases(d.open_count || 0))
        .catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [token, user]);

  if (loading || !user?.is_admin) {
    return (
      <main className="page-enter">
        <div className="container">
          <div className="loading">
            <div className="loading__spinner" />
            <span className="loading__text">Loading admin…</span>
          </div>
        </div>
      </main>
    );
  }

  const items = [
    { to: "/admin", end: true, icon: <ShieldIcon size={16} />, label: "Dashboard" },
    { to: "/admin/orders", icon: <PackageIcon size={16} />, label: "Orders" },
    { to: "/admin/cases", icon: <ClipboardIcon size={16} />, label: "Cases", badge: openCases },
    { to: "/admin/products", icon: <TagIcon size={16} />, label: "Products" },
    { to: "/admin/customers", icon: <UserIcon size={16} />, label: "Customers" },
    { to: "/admin/emails", icon: <MailIcon size={16} />, label: "Email Log" },
  ];

  return (
    <div className="admin" id="admin-panel">
      <aside className="admin__sidebar">
        <div className="admin__brand">
          <span className="admin__brand-icon"><ShieldIcon size={17} /></span>
          <div>
            <span className="admin__brand-title">Admin Panel</span>
            <span className="admin__brand-sub">ZoroStore Control</span>
          </div>
        </div>

        <nav className="admin__nav">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `admin__nav-link ${isActive ? "admin__nav-link--active" : ""}`
              }
            >
              {item.icon}
              <span>{item.label}</span>
              {item.badge > 0 && (
                <span className="admin__nav-badge">{item.badge}</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="admin__sidebar-foot">
          <Link to="/" className="admin__back">
            <TruckIcon size={14} /> Back to storefront
          </Link>
          <span className="admin__whoami">
            <UserIcon size={13} /> {user.first_name} {user.last_name}
          </span>
        </div>
      </aside>

      <div className="admin__main">
        <Outlet />
      </div>
    </div>
  );
}

export default AdminLayout;
