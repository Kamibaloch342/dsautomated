import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { adminFetch } from "../../lib/adminApi";
import { CheckIcon, XIcon } from "../../components/Icons";

/* Inline-editable product row */
function ProductRow({ product, token, onSaved }) {
  const [form, setForm] = useState({
    unit_price: product.unit_price ?? "",
    availability: product.availability || "In Stock",
    min_order_qty: product.min_order_qty || 1,
    lead_time: product.lead_time || "",
  });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const update = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
    setDirty(true);
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    try {
      await adminFetch(token, `/products/${product.product_id}`, {
        method: "PUT",
        body: JSON.stringify({
          unit_price: parseFloat(form.unit_price) || 0,
          availability: form.availability,
          min_order_qty: parseInt(form.min_order_qty, 10) || 1,
          lead_time: form.lead_time,
        }),
      });
      setDirty(false);
      setSaved(true);
      onSaved?.();
      setTimeout(() => setSaved(false), 2500);
    } catch {
      /* row stays dirty so the admin can retry */
    } finally {
      setSaving(false);
    }
  };

  return (
    <tr>
      <td>
        <img
          className="admin-prod__img"
          src={product.image_url || "/placeholder.svg"}
          alt=""
          onError={(e) => { e.target.style.opacity = "0.25"; }}
        />
      </td>
      <td className="admin-prod__title-cell">
        <Link to={`/product/${product.zoro_code}`} className="admin-table__link" target="_blank">
          {product.title}
        </Link>
        <span className="admin-prod__meta">
          {product.zoro_code} · {product.brand || "—"} · {product.spec_count} specs
          {product.variant_label && ` · option: ${product.variant_label}`}
        </span>
      </td>
      <td>
        <input className="admin-input admin-input--sm" type="number" step="0.01"
          value={form.unit_price} onChange={update("unit_price")} />
      </td>
      <td>
        <select className="admin-input admin-input--sm" value={form.availability}
          onChange={update("availability")}>
          <option>In Stock</option>
          <option>Out of Stock</option>
          <option>Backordered</option>
          <option>Discontinued</option>
        </select>
      </td>
      <td>
        <input className="admin-input admin-input--sm admin-input--qty" type="number" min="1"
          value={form.min_order_qty} onChange={update("min_order_qty")} />
      </td>
      <td>
        <input className="admin-input admin-input--sm" value={form.lead_time}
          onChange={update("lead_time")} placeholder="Ships in 1 day" />
      </td>
      <td>
        {saved ? (
          <span className="chip chip--success"><CheckIcon size={12} /> Saved</span>
        ) : (
          <button className="btn btn--accent btn--sm" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save"}
          </button>
        )}
      </td>
    </tr>
  );
}

function AdminProducts() {
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
    adminFetch(token, `/products?${params}`)
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
    <div className="admin__page" id="admin-products">
      <div className="admin__page-head">
        <h1 className="admin__title">Products</h1>
        {data && <span className="admin__subtitle">{data.total} in catalog</span>}
      </div>

      <div className="admin-filters">
        <input
          className="admin-input admin-filters__search"
          placeholder="Search title, brand, SKU, MFR#…"
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
          <table className="admin-table admin-table--products">
            <thead>
              <tr>
                <th /><th>Product</th><th>Price ($)</th><th>Availability</th>
                <th>Min qty</th><th>Lead time</th><th />
              </tr>
            </thead>
            <tbody>
              {data?.products.map((p) => (
                <ProductRow key={p.product_id} product={p} token={token} />
              ))}
              {data && data.products.length === 0 && (
                <tr><td colSpan={7} className="admin-table__empty">No products match.</td></tr>
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
    </div>
  );
}

export default AdminProducts;
