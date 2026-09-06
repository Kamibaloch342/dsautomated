import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch, shortDate } from "../../lib/adminApi";
import { ChevronDownIcon } from "../../components/Icons";

const TYPES = ["", "confirmation", "processing", "shipment", "batch_summary", "case_reply", "cancellation"];

function AdminEmails() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const type = searchParams.get("type") || "";
  const q = searchParams.get("q") || "";
  const page = parseInt(searchParams.get("page") || "1", 10);

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ page: String(page), per_page: "30" });
    if (type) params.set("type", type);
    if (q) params.set("q", q);
    adminFetch(token, `/emails?${params}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, type, q, page]);

  useEffect(load, [load]);

  const updateParam = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.set("page", "1");
    setSearchParams(next);
  };

  return (
    <div className="admin__page" id="admin-emails">
      <div className="admin__page-head">
        <h1 className="admin__title">Email Log</h1>
        {data && <span className="admin__subtitle">{data.total} sent/logged</span>}
      </div>

      <div className="admin-filters">
        <select className="admin-input" value={type}
          onChange={(e) => updateParam("type", e.target.value)}>
          {TYPES.map((t) => (
            <option key={t} value={t}>{t ? t.replace("_", " ") : "All types"}</option>
          ))}
        </select>
        <input
          className="admin-input admin-filters__search"
          placeholder="Search recipient or subject…"
          defaultValue={q}
          onKeyDown={(e) => {
            if (e.key === "Enter") updateParam("q", e.target.value.trim());
          }}
        />
      </div>

      {error && <div className="admin__error">{error}</div>}

      <div className="admin-card">
        <div className="admin-emails admin-emails--log">
          {data?.emails.map((e) => (
            <details className="admin-email" key={e.email_id}>
              <summary>
                <span className={`email-thread__type email-thread__type--${e.email_type}`}>
                  {e.email_type.replace("_", " ")}
                </span>
                <span className="admin-email__subject">{e.subject}</span>
                <span className="admin-email__meta">
                  → {e.recipient} · {e.status} · {shortDate(e.created_at)}
                  {e.order_id && (
                    <>
                      {" · "}
                      <Link className="admin-table__link" to={`/admin/orders?open=${e.order_id}`}>
                        order #{e.order_id}
                      </Link>
                    </>
                  )}
                </span>
                <ChevronDownIcon size={13} />
              </summary>
              <pre className="admin-email__body">{e.body_text}</pre>
            </details>
          ))}
          {data && data.emails.length === 0 && (
            <p className="admin-card__empty">No emails match.</p>
          )}
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
    </div>
  );
}

export default AdminEmails;
