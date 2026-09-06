import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch, money, shortDate } from "../../lib/adminApi";
import { ShieldIcon } from "../../components/Icons";

function AdminCustomers() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const q = searchParams.get("q") || "";
  const page = parseInt(searchParams.get("page") || "1", 10);

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ page: String(page), per_page: "30" });
    if (q) params.set("q", q);
    adminFetch(token, `/customers?${params}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, q, page]);

  useEffect(load, [load]);

  const updateParam = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.set("page", "1");
    setSearchParams(next);
  };

  return (
    <div className="admin__page" id="admin-customers">
      <div className="admin__page-head">
        <h1 className="admin__title">Customers</h1>
        {data && <span className="admin__subtitle">{data.total} registered</span>}
      </div>

      <div className="admin-filters">
        <input
          className="admin-input admin-filters__search"
          placeholder="Search name or email…"
          defaultValue={q}
          onKeyDown={(e) => {
            if (e.key === "Enter") updateParam("q", e.target.value.trim());
          }}
        />
      </div>

      {error && <div className="admin__error">{error}</div>}

      <div className="admin-card">
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Customer</th><th>Email</th><th>Location</th>
                <th>Joined</th><th>Orders</th><th>Total spent</th>
              </tr>
            </thead>
            <tbody>
              {data?.customers.map((u) => (
                <tr key={u.user_id}>
                  <td className="admin-table__strong">
                    {u.first_name} {u.last_name}
                    {u.is_admin && (
                      <span className="chip chip--neutral" style={{ marginLeft: 8 }}>
                        <ShieldIcon size={11} /> admin
                      </span>
                    )}
                  </td>
                  <td>{u.email}</td>
                  <td>{[u.city, u.state].filter(Boolean).join(", ") || "—"}</td>
                  <td>{shortDate(u.created_at)}</td>
                  <td>
                    {u.order_count > 0 ? (
                      <Link className="admin-table__link" to={`/admin/orders?q=${encodeURIComponent(u.email)}`}>
                        {u.order_count}
                      </Link>
                    ) : 0}
                  </td>
                  <td>{money(u.total_spent)}</td>
                </tr>
              ))}
              {data && data.customers.length === 0 && (
                <tr><td colSpan={6} className="admin-table__empty">No customers match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default AdminCustomers;
