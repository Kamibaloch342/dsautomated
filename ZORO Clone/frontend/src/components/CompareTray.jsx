import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useCompare } from "../context/CompareContext";
import { useCart } from "../context/CartContext";
import { CompareIcon, XIcon, TagIcon, CartIcon } from "./Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

const formatPrice = (p) => (p || p === 0 ? parseFloat(p).toFixed(2) : "—");

/* ── Sticky bottom bar ── */
function CompareBar() {
  const { items, count, max, remove, clear, openCompare, limitHit } = useCompare();

  if (count === 0) return null;

  return (
    <div className="compare-bar" id="compare-bar">
      <div className="compare-bar__inner container">
        <div className="compare-bar__label">
          <CompareIcon size={18} />
          <span>
            Compare <strong>{count}</strong>/{max}
          </span>
          {limitHit && (
            <span className="compare-bar__limit">Up to {max} products</span>
          )}
        </div>

        <div className="compare-bar__thumbs">
          {items.map((p) => (
            <div className="compare-bar__thumb" key={p.zoro_code} title={p.title}>
              <img
                src={p.image_url || "/placeholder.svg"}
                alt={p.title}
                onError={(e) => { e.target.style.opacity = "0.25"; }}
              />
              <button
                className="compare-bar__thumb-remove"
                onClick={() => remove(p.zoro_code)}
                aria-label={`Remove ${p.title} from compare`}
              >
                <XIcon size={11} strokeWidth={2.5} />
              </button>
            </div>
          ))}
        </div>

        <div className="compare-bar__actions">
          <button
            className="btn btn--accent compare-bar__cta"
            onClick={openCompare}
            disabled={count < 2}
            id="compare-open-btn"
          >
            Compare prices{count < 2 ? " (pick 2+)" : ""}
          </button>
          <button className="compare-bar__clear" onClick={clear}>
            Clear all
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Full-screen comparison modal ── */
function CompareModal() {
  const { items, isOpen, closeCompare, remove } = useCompare();
  const { addItem } = useCart();
  const [details, setDetails] = useState({});
  const [loading, setLoading] = useState(false);

  // Pull full product records (specs etc.) when the modal opens
  useEffect(() => {
    if (!isOpen || items.length === 0) return;
    let cancelled = false;
    setLoading(true);
    Promise.all(
      items.map((p) =>
        fetch(`${API_BASE}/api/products/${p.zoro_code}`)
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    ).then((results) => {
      if (cancelled) return;
      const map = {};
      results.forEach((full, i) => {
        if (full) map[items[i].zoro_code] = full;
      });
      setDetails(map);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [isOpen, items]);

  useEffect(() => {
    document.body.style.overflow = isOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [isOpen]);

  if (!isOpen || items.length < 2) return null;

  const full = (code) => details[code] || {};
  const prices = items
    .map((p) => (p.unit_price === null || p.unit_price === undefined ? null : +p.unit_price))
    .filter((v) => v !== null);
  const lowest = prices.length ? Math.min(...prices) : null;

  // Union of spec keys across compared products (most common first)
  const specCounts = {};
  items.forEach((p) => {
    Object.keys(full(p.zoro_code).specifications || {}).forEach((k) => {
      specCounts[k] = (specCounts[k] || 0) + 1;
    });
  });
  const specKeys = Object.keys(specCounts)
    .sort((a, b) => specCounts[b] - specCounts[a])
    .slice(0, 12);

  const infoRows = [
    { label: "Brand", get: (p) => full(p.zoro_code).brand || p.brand || "—" },
    { label: "SKU", get: (p) => p.zoro_code },
    { label: "Availability", get: (p) => full(p.zoro_code).availability || "—" },
    { label: "Unit", get: (p) => (full(p.zoro_code).uom || "each").toLowerCase() },
    { label: "Min order", get: (p) => full(p.zoro_code).min_order_qty || 1 },
    { label: "Lead time", get: (p) => full(p.zoro_code).lead_time || "—" },
  ];

  return (
    <div className="compare-modal" role="dialog" aria-modal="true" id="compare-modal">
      <div className="compare-modal__backdrop" onClick={closeCompare} />
      <div className="compare-modal__panel">
        <div className="compare-modal__header">
          <h2 className="compare-modal__title">
            <CompareIcon size={20} /> Price &amp; spec comparison
          </h2>
          <button
            className="compare-modal__close"
            onClick={closeCompare}
            aria-label="Close comparison"
          >
            <XIcon size={18} />
          </button>
        </div>

        <div className="compare-modal__scroll">
          <table className="compare-table">
            <thead>
              <tr>
                <th className="compare-table__side" />
                {items.map((p) => (
                  <th key={p.zoro_code} className="compare-table__col">
                    <button
                      className="compare-table__remove"
                      onClick={() => remove(p.zoro_code)}
                      aria-label="Remove from comparison"
                    >
                      <XIcon size={13} />
                    </button>
                    <Link
                      to={`/product/${p.zoro_code}`}
                      onClick={closeCompare}
                      className="compare-table__product"
                    >
                      <img
                        src={p.image_url || "/placeholder.svg"}
                        alt={p.title}
                        className="compare-table__img"
                        onError={(e) => { e.target.style.opacity = "0.25"; }}
                      />
                      <span className="compare-table__name">{p.title}</span>
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="compare-table__price-row">
                <td className="compare-table__side">Price</td>
                {items.map((p) => {
                  const price = p.unit_price === null || p.unit_price === undefined
                    ? null : +p.unit_price;
                  const isLowest = lowest !== null && price === lowest && prices.length > 1;
                  return (
                    <td key={p.zoro_code}>
                      <div className={`compare-price ${isLowest ? "compare-price--best" : ""}`}>
                        <span className="compare-price__value">
                          ${formatPrice(price)}
                        </span>
                        {isLowest && (
                          <span className="compare-price__badge">
                            <TagIcon size={12} /> Lowest price
                          </span>
                        )}
                        {!isLowest && price !== null && lowest !== null && (
                          <span className="compare-price__diff">
                            +${formatPrice(price - lowest)} vs lowest
                          </span>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>

              {infoRows.map((row) => (
                <tr key={row.label}>
                  <td className="compare-table__side">{row.label}</td>
                  {items.map((p) => (
                    <td key={p.zoro_code}>{loading ? "…" : row.get(p)}</td>
                  ))}
                </tr>
              ))}

              {specKeys.map((key) => (
                <tr key={key}>
                  <td className="compare-table__side">{key}</td>
                  {items.map((p) => (
                    <td key={p.zoro_code}>
                      {(full(p.zoro_code).specifications || {})[key] || "—"}
                    </td>
                  ))}
                </tr>
              ))}

              <tr className="compare-table__cta-row">
                <td className="compare-table__side" />
                {items.map((p) => (
                  <td key={p.zoro_code}>
                    <button
                      className="btn btn--accent compare-table__add"
                      onClick={() => addItem({ ...p, ...full(p.zoro_code) }, 1)}
                    >
                      <CartIcon size={15} /> Add to Cart
                    </button>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function CompareTray() {
  return (
    <>
      <CompareBar />
      <CompareModal />
    </>
  );
}

export default CompareTray;
