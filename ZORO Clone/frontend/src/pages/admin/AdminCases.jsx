import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch, shortDate, STATUS_COLORS } from "../../lib/adminApi";
import { XIcon, CheckIcon, PackageIcon } from "../../components/Icons";

const CASE_STATUSES = ["open", "pending", "resolved", "closed"];

function AdminCases() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState(null); // {case, order}
  const [reply, setReply] = useState("");
  const [replyStatus, setReplyStatus] = useState("pending");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const status = searchParams.get("status") || "";
  const q = searchParams.get("q") || "";
  const page = parseInt(searchParams.get("page") || "1", 10);
  const openId = searchParams.get("open");

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ page: String(page), per_page: "25" });
    if (status) params.set("status", status);
    if (q) params.set("q", q);
    adminFetch(token, `/cases?${params}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, status, q, page]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!token || !openId) {
      setDetail(null);
      return;
    }
    adminFetch(token, `/cases/${openId}`)
      .then((d) => {
        setDetail(d);
        setReplyStatus(d.case.status === "open" ? "pending" : d.case.status);
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

  const sendReply = async () => {
    if (!reply.trim() || !detail) return;
    setBusy(true);
    setNotice("");
    try {
      const d = await adminFetch(token, `/cases/${detail.case.case_id}/reply`, {
        method: "POST",
        body: JSON.stringify({ body: reply.trim(), status: replyStatus }),
      });
      setDetail((prev) => ({ ...prev, case: d.case }));
      setReply("");
      setNotice("Reply sent — customer emailed and notified.");
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const setCaseStatus = async (newStatus) => {
    if (!detail) return;
    setBusy(true);
    try {
      const d = await adminFetch(token, `/cases/${detail.case.case_id}`, {
        method: "PUT",
        body: JSON.stringify({ status: newStatus }),
      });
      setDetail((prev) => ({ ...prev, case: d.case }));
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin__page" id="admin-cases">
      <div className="admin__page-head">
        <h1 className="admin__title">Support Cases</h1>
        {data && (
          <span className="admin__subtitle">
            {data.open_count} open · {data.total} total
          </span>
        )}
      </div>

      <div className="admin-filters">
        <select className="admin-input" value={status}
          onChange={(e) => updateParam("status", e.target.value)}>
          <option value="">All statuses</option>
          {CASE_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input
          className="admin-input admin-filters__search"
          placeholder="Search subject, email, #case, #order…"
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

      <div className="admin-card">
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Case</th><th>Subject</th><th>Customer</th><th>Category</th>
                <th>Order</th><th>Msgs</th><th>Updated</th><th>Status</th><th />
              </tr>
            </thead>
            <tbody>
              {data?.cases.map((c) => (
                <tr key={c.case_id} className={String(c.case_id) === openId ? "admin-table__row--open" : ""}>
                  <td>#{c.case_id}</td>
                  <td className="admin-table__strong">{c.subject}</td>
                  <td>{c.email}</td>
                  <td>{c.category.replace("_", " ")}</td>
                  <td>
                    {c.order_id ? (
                      <Link className="admin-table__link" to={`/admin/orders?open=${c.order_id}`}>
                        #{c.order_id}
                      </Link>
                    ) : "—"}
                  </td>
                  <td>{c.message_count}</td>
                  <td>{shortDate(c.updated_at)}</td>
                  <td><span className={`chip ${STATUS_COLORS[c.status]}`}>{c.status}</span></td>
                  <td>
                    <button className="btn btn--secondary btn--sm"
                      onClick={() => updateParam("open", String(c.case_id) === openId ? "" : String(c.case_id))}>
                      {String(c.case_id) === openId ? "Close" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
              {data && data.cases.length === 0 && (
                <tr><td colSpan={9} className="admin-table__empty">No cases match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {detail && (
        <div className="admin-drawer" id="admin-case-drawer">
          <div className="admin-drawer__head">
            <h2>
              Case #{detail.case.case_id} — {detail.case.subject}
              <span className={`chip ${STATUS_COLORS[detail.case.status]}`} style={{ marginLeft: 10 }}>
                {detail.case.status}
              </span>
            </h2>
            <button className="admin-drawer__close" onClick={() => updateParam("open", "")}>
              <XIcon size={16} />
            </button>
          </div>

          {notice && <div className="admin-notice"><CheckIcon size={14} /> {notice}</div>}

          <div className="admin-drawer__grid">
            <div>
              <h3 className="admin-drawer__section">Conversation</h3>
              <div className="admin-convo">
                {detail.case.messages.map((m) => (
                  <div key={m.case_message_id}
                    className={`admin-msg ${m.sender === "admin" ? "admin-msg--admin" : ""}`}>
                    <span className="admin-msg__sender">
                      {m.sender === "admin" ? "Support" : detail.case.name || "Customer"}
                    </span>
                    <p className="admin-msg__body">{m.body}</p>
                    <span className="admin-msg__time">{shortDate(m.created_at)}</span>
                  </div>
                ))}
              </div>

              <div className="admin-reply">
                <textarea
                  className="admin-reply__box"
                  rows={4}
                  placeholder="Write a reply — it's emailed to the customer and appears in their account…"
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  id="admin-case-reply"
                />
                <div className="admin-reply__row">
                  <select className="admin-input" value={replyStatus}
                    onChange={(e) => setReplyStatus(e.target.value)}>
                    {CASE_STATUSES.map((s) => (
                      <option key={s} value={s}>set status: {s}</option>
                    ))}
                  </select>
                  <button className="btn btn--accent btn--sm" disabled={busy || !reply.trim()}
                    onClick={sendReply} id="admin-send-reply">
                    Send reply
                  </button>
                </div>
              </div>
            </div>

            <div>
              <h3 className="admin-drawer__section">Details</h3>
              <p className="admin-drawer__facts">
                <strong>Customer:</strong> {detail.case.name || "—"} ({detail.case.email})<br />
                <strong>Category:</strong> {detail.case.category.replace("_", " ")}<br />
                <strong>Opened:</strong> {shortDate(detail.case.created_at)}<br />
                <strong>Updated:</strong> {shortDate(detail.case.updated_at)}
              </p>

              <div className="admin-actions">
                {CASE_STATUSES.filter((s) => s !== detail.case.status).map((s) => (
                  <button key={s} className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => setCaseStatus(s)}>
                    Mark {s}
                  </button>
                ))}
              </div>

              {detail.order && (
                <>
                  <h3 className="admin-drawer__section">Linked order</h3>
                  <Link to={`/admin/orders?open=${detail.order.order_id}`} className="admin-mini-case">
                    <PackageIcon size={14} />
                    <span className="admin-mini-case__subject">
                      Order #{detail.order.order_id} · {detail.order.status} ·{" "}
                      {detail.order.items.map((i) => i.title).join(", ").slice(0, 60)}
                    </span>
                  </Link>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default AdminCases;
