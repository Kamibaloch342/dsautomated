import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import {
  RowsIcon, PlusIcon, TrashIcon, XIcon, UploadIcon, DownloadIcon,
  CopyIcon, CheckCircleIcon, AlertIcon, MailIcon, BellIcon, TruckIcon,
  ClipboardIcon, CheckIcon, InfoIcon, CreditCardIcon,
} from "../components/Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";
const MAX_ROWS = 100;
const DRAFT_KEY = "bulk_order_draft_v1";

const ROW_FIELDS = [
  "sku", "quantity", "first_name", "last_name",
  "address1", "address2", "city", "state", "zip", "phone",
];

let rowSeq = 1;
const blankRow = () => ({
  id: `r${Date.now()}-${rowSeq++}`,
  sku: "", quantity: "1",
  first_name: "", last_name: "",
  address1: "", address2: "", city: "", state: "", zip: "", phone: "",
  info: null,     // catalog info from validate (title, unit_price, ...)
  error: null,
});

const blankBilling = () => ({
  first_name: "", last_name: "", phone: "",
  address1: "", address2: "", city: "", state: "", zip: "",
});

/* Minimal CSV parser — handles quoted fields and CRLF. */
function parseCSV(text) {
  const rows = [];
  let row = [], field = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field); field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((f) => f.trim() !== "")) rows.push(row);
      row = [];
    } else {
      field += c;
    }
  }
  row.push(field);
  if (row.some((f) => f.trim() !== "")) rows.push(row);
  return rows;
}

const CSV_HEADER = "sku,quantity,first_name,last_name,address1,address2,city,state,zip,phone";

const SAMPLE_ROWS = [
  ["G1202564", "1", "John", "Carter", "410 Elm Street", "", "Dallas", "TX", "75001", "214-555-0132"],
  ["G7804513", "3", "Maria", "Lopez", "88 Bayshore Dr", "Apt 4B", "Miami", "FL", "33101", ""],
  ["G6637946", "2", "David", "Kim", "1200 Harbor Ave", "", "Seattle", "WA", "98101", "206-555-0177"],
];

const money = (v) => `$${parseFloat(v || 0).toFixed(2)}`;

function BulkOrderPage() {
  const { user, token, isAuthenticated } = useAuth();

  const [rows, setRows] = useState(() => [blankRow(), blankRow(), blankRow()]);
  const [billing, setBilling] = useState(blankBilling);
  const [notifEmail, setNotifEmail] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [validating, setValidating] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [formError, setFormError] = useState("");
  const [results, setResults] = useState(null); // response of POST /bulk
  const [copied, setCopied] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState("card_demo");
  const [net30, setNet30] = useState(null);
  const fileRef = useRef(null);
  const restoredRef = useRef(false);

  // Net 30 availability for the payment selector
  useEffect(() => {
    if (!token) return;
    fetch(`${API_BASE}/api/net30/status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setNet30(d))
      .catch(() => {});
  }, [token]);

  /* ── Draft persistence ── */
  useEffect(() => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const d = JSON.parse(raw);
        if (d.rows?.length) {
          setRows(d.rows.map((r) => ({ ...blankRow(), ...r, info: null, error: null })));
        }
        if (d.billing) setBilling((prev) => ({ ...prev, ...d.billing }));
        if (d.notifEmail) setNotifEmail(d.notifEmail);
      }
    } catch { /* corrupt draft — start fresh */ }
    restoredRef.current = true;
  }, []);

  useEffect(() => {
    if (!restoredRef.current) return;
    const slim = rows.map(({ info: _info, error: _error, ...r }) => r);
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ rows: slim, billing, notifEmail }));
    } catch { /* storage full — draft skipped */ }
  }, [rows, billing, notifEmail]);

  /* Prefill notification email from the signed-in account */
  useEffect(() => {
    if (user?.email) setNotifEmail((prev) => prev || user.email);
  }, [user]);

  /* ── Row helpers ── */
  const updateRow = (id, field, value) => {
    setRows((prev) =>
      prev.map((r) =>
        r.id === id
          ? { ...r, [field]: value, ...(field === "sku" ? { info: null, error: null } : {}) }
          : r
      )
    );
  };

  const addRow = () => {
    setRows((prev) =>
      prev.length >= MAX_ROWS ? prev : [...prev, blankRow()]
    );
  };

  const removeRow = (id) =>
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.id !== id) : [blankRow()]));

  const clearRows = () => setRows([blankRow(), blankRow(), blankRow()]);

  const rowsWithSku = useMemo(() => rows.filter((r) => r.sku.trim()), [rows]);

  const estTotal = useMemo(
    () =>
      rows.reduce((sum, r) => {
        if (r.info?.unit_price && r.quantity) {
          return sum + r.info.unit_price * (parseInt(r.quantity, 10) || 0);
        }
        return sum;
      }, 0),
    [rows]
  );

  /* ── Catalog lookup (single row, on SKU blur) ── */
  const lookupRow = useCallback(async (row) => {
    if (!row.sku.trim()) return;
    try {
      const res = await fetch(`${API_BASE}/api/orders/bulk/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          orders: [{ sku: row.sku, quantity: parseInt(row.quantity, 10) || 1 }],
        }),
      });
      const data = await res.json();
      const result = data.results?.[0];
      if (!result) return;
      setRows((prev) =>
        prev.map((r) =>
          r.id === row.id
            ? {
                ...r,
                info: result.found ? result : null,
                error: result.valid ? null : result.error,
              }
            : r
        )
      );
    } catch { /* backend offline — validate-all will surface it */ }
  }, []);

  /* ── Validate everything (also checks addresses) ── */
  const validateAll = async () => {
    setFormError("");
    if (rowsWithSku.length === 0) {
      setFormError("Add at least one row with a SKU.");
      return null;
    }
    setValidating(true);
    try {
      const res = await fetch(`${API_BASE}/api/orders/bulk/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          check_address: true,
          orders: rowsWithSku.map((r) => ({
            ...Object.fromEntries(ROW_FIELDS.map((f) => [f, r[f]])),
            quantity: parseInt(r.quantity, 10) || 0,
          })),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Validation failed");

      const bySku = {};
      data.results.forEach((result, i) => {
        bySku[rowsWithSku[i].id] = result;
      });
      setRows((prev) =>
        prev.map((r) => {
          const result = bySku[r.id];
          if (!result) return r;
          return {
            ...r,
            info: result.found ? result : null,
            error: result.valid ? null : result.error,
          };
        })
      );
      return data;
    } catch (err) {
      setFormError(err.message || "Could not reach the server.");
      return null;
    } finally {
      setValidating(false);
    }
  };

  /* ── Place the batch ── */
  const placeOrders = async () => {
    setFormError("");

    if (!notifEmail.trim() || !notifEmail.includes("@")) {
      setFormError("Enter a valid notification email — order IDs and shipment emails go there.");
      return;
    }
    const missingBilling = ["first_name", "last_name", "address1", "city", "state", "zip"]
      .filter((f) => !billing[f].trim());
    if (missingBilling.length) {
      setFormError("Complete the billing address (name, address, city, state, ZIP).");
      return;
    }

    const validation = await validateAll();
    if (!validation) return;
    if (validation.invalid_count > 0) {
      setFormError(
        `${validation.invalid_count} row${validation.invalid_count > 1 ? "s need" : " needs"} attention — fix the highlighted rows, then place again.`
      );
      return;
    }

    setPlacing(true);
    try {
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${API_BASE}/api/orders/bulk`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          notification_email: notifEmail.trim(),
          billing,
          payment_method: paymentMethod,
          orders: rowsWithSku.map((r) => ({
            ...Object.fromEntries(ROW_FIELDS.map((f) => [f, r[f]])),
            quantity: parseInt(r.quantity, 10) || 0,
          })),
        }),
      });
      const data = await res.json();
      if (!res.ok && !data.batch_id) throw new Error(data.error || "Bulk placement failed");
      setResults(data);
      clearRows();
      localStorage.removeItem(DRAFT_KEY);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      setFormError(err.message || "Bulk placement failed.");
    } finally {
      setPlacing(false);
    }
  };

  /* ── CSV import/export ── */
  const importRows = (parsed) => {
    if (!parsed.length) return;
    let data = parsed;
    if (data[0][0]?.toLowerCase().trim() === "sku") data = data.slice(1); // header row
    const imported = data.slice(0, MAX_ROWS).map((cols) => {
      const r = blankRow();
      ROW_FIELDS.forEach((f, i) => { r[f] = (cols[i] || "").trim(); });
      if (!r.quantity) r.quantity = "1";
      return r;
    });
    if (imported.length) setRows(imported);
    setPasteOpen(false);
    setPasteText("");
  };

  const handleFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => importRows(parseCSV(String(reader.result || "")));
    reader.readAsText(file);
    e.target.value = "";
  };

  const downloadTemplate = () => {
    const blob = new Blob(
      [CSV_HEADER + "\n" + SAMPLE_ROWS.map((r) => r.join(",")).join("\n")],
      { type: "text/csv" }
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "bulk-order-template.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const loadSample = () => {
    importRows(SAMPLE_ROWS);
  };

  const downloadResultsCSV = () => {
    if (!results) return;
    const header = "row,sku,status,order_id,title,quantity,unit_price,total,ship_to,error";
    const lines = results.results.map((r) =>
      [
        r.row, r.sku, r.status, r.order_id || "",
        `"${(r.title || "").replace(/"/g, '""')}"`,
        r.quantity || "", r.unit_price || "", r.total || "",
        `"${(r.ship_to || "").replace(/"/g, '""')}"`,
        `"${(r.error || "").replace(/"/g, '""')}"`,
      ].join(",")
    );
    const blob = new Blob([header + "\n" + lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${results.batch_id}-results.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const copyOrderIds = () => {
    if (!results) return;
    const ids = results.results
      .filter((r) => r.status === "created")
      .map((r) => `#${r.order_id}`)
      .join(", ");
    navigator.clipboard?.writeText(ids).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const useProfileBilling = () => {
    if (!user) return;
    setBilling({
      first_name: user.first_name || "",
      last_name: user.last_name || "",
      phone: user.phone || "",
      address1: user.address_line1 || "",
      address2: user.address_line2 || "",
      city: user.city || "",
      state: user.state || "",
      zip: user.zip_code || "",
    });
  };

  const updateBilling = (field) => (e) =>
    setBilling((prev) => ({ ...prev, [field]: e.target.value }));

  const validCount = rows.filter((r) => r.sku.trim() && r.info && !r.error).length;
  const errorCount = rows.filter((r) => r.error).length;

  return (
    <main className="page-enter">
      <section className="bulk-page" id="bulk-order-page">

        {/* ── Page header ── */}
        <div className="bulk-hero">
          <div className="container bulk-hero__inner">
            <div>
              <span className="bulk-hero__eyebrow">
                <RowsIcon size={14} /> Exclusive — Zoro doesn't offer this
              </span>
              <h1 className="bulk-hero__title">Bulk Order Pad</h1>
              <p className="bulk-hero__subtitle">
                Zoro only takes one order at a time. Here, add a row per order —
                SKU, quantity and its own ship-to address — and place them all in
                one shot. You get every Order&nbsp;ID instantly, plus confirmation
                email threads, bell notifications and shipment-tracking emails as
                each order moves.
              </p>
            </div>
            <ol className="bulk-hero__steps">
              <li><strong>1.</strong> Add rows or import CSV</li>
              <li><strong>2.</strong> Validate against the catalog</li>
              <li><strong>3.</strong> Place all orders at once</li>
              <li><strong>4.</strong> Get IDs, emails &amp; tracking</li>
            </ol>
          </div>
        </div>

        <div className="container bulk-body">

          {/* ── Results banner (after placing) ── */}
          {results && (
            <div className="bulk-results" id="bulk-results">
              <div className="bulk-results__banner">
                <CheckCircleIcon size={26} />
                <div>
                  <h2 className="bulk-results__title">
                    Batch {results.batch_id} placed — {results.created_count}{" "}
                    order{results.created_count !== 1 ? "s" : ""} created
                    {results.failed_count > 0 && `, ${results.failed_count} failed`}
                  </h2>
                  <p className="bulk-results__sub">
                    Batch total {money(results.batch_total)} · A summary email and
                    per-order confirmations were sent to{" "}
                    <strong>{results.notification_email}</strong>. Shipment emails
                    with tracking follow automatically as each order ships.
                  </p>
                </div>
              </div>

              <div className="bulk-results__comms">
                <span><MailIcon size={15} /> Confirmation email thread started</span>
                <span><BellIcon size={15} /> Notification sent to your bell</span>
                <span><TruckIcon size={15} /> Shipment emails on the way</span>
              </div>

              <div className="bulk-results__table-wrap">
                <table className="bulk-results__table">
                  <thead>
                    <tr>
                      <th>Row</th><th>SKU</th><th>Product</th><th>Qty</th>
                      <th>Order ID</th><th>Total</th><th>Ship To</th><th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.results.map((r) => (
                      <tr key={r.row} className={r.status === "error" ? "bulk-results__row--error" : ""}>
                        <td>{r.row}</td>
                        <td className="bulk-results__mono">{r.sku}</td>
                        <td className="bulk-results__product">{r.title || "—"}</td>
                        <td>{r.quantity || "—"}</td>
                        <td>
                          {r.order_id ? (
                            <Link
                              className="bulk-results__order-link"
                              to={`/order-confirmation/${r.order_id}`}
                            >
                              #{r.order_id}
                            </Link>
                          ) : "—"}
                        </td>
                        <td>{r.total ? money(r.total) : "—"}</td>
                        <td className="bulk-results__ship">{r.ship_to || "—"}</td>
                        <td>
                          {r.status === "created" ? (
                            <span className="chip chip--success"><CheckIcon size={12} /> Created</span>
                          ) : (
                            <span className="chip chip--danger" title={r.error}>
                              <AlertIcon size={12} /> {r.error}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="bulk-results__actions">
                <button className="btn btn--primary" onClick={copyOrderIds} id="copy-ids-btn">
                  {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
                  {copied ? "Copied!" : "Copy Order IDs"}
                </button>
                <button className="btn btn--secondary" onClick={downloadResultsCSV}>
                  <DownloadIcon size={15} /> Download results CSV
                </button>
                <button className="btn btn--secondary" onClick={() => setResults(null)}>
                  <PlusIcon size={15} /> Start another batch
                </button>
              </div>
            </div>
          )}

          {/* ── Batch settings: notification email + billing ── */}
          <div className="bulk-card" id="bulk-billing-card">
            <div className="bulk-card__head">
              <h2 className="bulk-card__title">
                <CreditCardTitle />
              </h2>
              {isAuthenticated && (
                <button className="bulk-card__use-profile" onClick={useProfileBilling}>
                  Use my profile address
                </button>
              )}
            </div>

            <div className="bulk-billing">
              <div className="form-group bulk-billing__email">
                <label className="form-label" htmlFor="bulk-notif-email">
                  Notification email * <InfoHint text="Order IDs, confirmations and shipment-tracking emails are sent here." />
                </label>
                <input
                  id="bulk-notif-email"
                  type="email"
                  className="form-input"
                  placeholder="orders@yourcompany.com"
                  value={notifEmail}
                  onChange={(e) => setNotifEmail(e.target.value)}
                />
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Billing first name *</label>
                  <input className="form-input" value={billing.first_name} onChange={updateBilling("first_name")} />
                </div>
                <div className="form-group">
                  <label className="form-label">Billing last name *</label>
                  <input className="form-input" value={billing.last_name} onChange={updateBilling("last_name")} />
                </div>
                <div className="form-group">
                  <label className="form-label">Phone</label>
                  <input className="form-input" value={billing.phone} onChange={updateBilling("phone")} />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group" style={{ flex: 2 }}>
                  <label className="form-label">Billing address line 1 *</label>
                  <input className="form-input" value={billing.address1} onChange={updateBilling("address1")} />
                </div>
                <div className="form-group">
                  <label className="form-label">Line 2</label>
                  <input className="form-input" value={billing.address2} onChange={updateBilling("address2")} />
                </div>
              </div>

              <div className="form-row form-row--3">
                <div className="form-group">
                  <label className="form-label">City *</label>
                  <input className="form-input" value={billing.city} onChange={updateBilling("city")} />
                </div>
                <div className="form-group">
                  <label className="form-label">State *</label>
                  <input className="form-input" placeholder="TX" value={billing.state} onChange={updateBilling("state")} />
                </div>
                <div className="form-group">
                  <label className="form-label">ZIP *</label>
                  <input className="form-input" value={billing.zip} onChange={updateBilling("zip")} />
                </div>
              </div>

              <div className="pay-methods pay-methods--bulk">
                <label className={`pay-method ${paymentMethod === "card_demo" ? "pay-method--on" : ""}`}>
                  <input
                    type="radio" name="bulk-payment"
                    checked={paymentMethod === "card_demo"}
                    onChange={() => setPaymentMethod("card_demo")}
                  />
                  <CreditCardIcon size={16} />
                  <span className="pay-method__text">
                    <strong>Card (demo)</strong>
                    <em>Pay per batch</em>
                  </span>
                </label>
                <label
                  className={`pay-method ${paymentMethod === "net30" ? "pay-method--on" : ""} ${net30?.status !== "approved" ? "pay-method--disabled" : ""}`}
                  title={net30?.status !== "approved" ? "Requires an approved Net 30 account" : ""}
                >
                  <input
                    type="radio" name="bulk-payment"
                    disabled={net30?.status !== "approved"}
                    checked={paymentMethod === "net30"}
                    onChange={() => setPaymentMethod("net30")}
                  />
                  <CreditCardIcon size={16} />
                  <span className="pay-method__text">
                    <strong>Net 30 (invoice)</strong>
                    <em>
                      {net30?.status === "approved"
                        ? `Available ${money(net30.available)} — each order gets its own invoice`
                        : net30?.status === "pending"
                          ? "Application under review"
                          : isAuthenticated
                            ? <>Not approved — <Link to="/account?tab=net30">apply</Link></>
                            : "Sign in to use Net 30"}
                    </em>
                  </span>
                </label>
              </div>
            </div>
          </div>

          {/* ── Order rows ── */}
          <div className="bulk-card">
            <div className="bulk-card__head">
              <h2 className="bulk-card__title">
                <RowsIcon size={18} /> Order rows
                <span className="bulk-card__count">{rowsWithSku.length}/{MAX_ROWS}</span>
              </h2>
              <div className="bulk-toolbar">
                <button className="btn btn--secondary btn--sm" onClick={() => setPasteOpen((v) => !v)}>
                  <ClipboardIcon size={14} /> Paste CSV
                </button>
                <button className="btn btn--secondary btn--sm" onClick={() => fileRef.current?.click()}>
                  <UploadIcon size={14} /> Upload CSV
                </button>
                <button className="btn btn--secondary btn--sm" onClick={downloadTemplate}>
                  <DownloadIcon size={14} /> Template
                </button>
                <button className="btn btn--secondary btn--sm" onClick={loadSample}>
                  Load sample
                </button>
                <button className="btn btn--secondary btn--sm bulk-toolbar__clear" onClick={clearRows}>
                  <TrashIcon size={14} /> Clear
                </button>
                <input
                  ref={fileRef} type="file" accept=".csv,text/csv"
                  style={{ display: "none" }} onChange={handleFile}
                />
              </div>
            </div>

            {pasteOpen && (
              <div className="bulk-paste">
                <p className="bulk-paste__hint">
                  One order per line: <code>{CSV_HEADER}</code>
                </p>
                <textarea
                  className="bulk-paste__textarea"
                  rows={5}
                  placeholder={`G1202564,1,John,Carter,410 Elm Street,,Dallas,TX,75001,214-555-0132`}
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                />
                <div className="bulk-paste__actions">
                  <button className="btn btn--primary btn--sm" onClick={() => importRows(parseCSV(pasteText))}>
                    Import rows
                  </button>
                  <button className="btn btn--secondary btn--sm" onClick={() => { setPasteOpen(false); setPasteText(""); }}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <div className="bulk-grid-wrap">
              <table className="bulk-grid">
                <thead>
                  <tr>
                    <th className="bulk-grid__num">#</th>
                    <th style={{ minWidth: 130 }}>SKU *</th>
                    <th className="bulk-grid__qty-col">Qty *</th>
                    <th style={{ minWidth: 230 }}>Product</th>
                    <th style={{ minWidth: 110 }}>First name *</th>
                    <th style={{ minWidth: 110 }}>Last name *</th>
                    <th style={{ minWidth: 170 }}>Address 1 *</th>
                    <th style={{ minWidth: 110 }}>Address 2</th>
                    <th style={{ minWidth: 120 }}>City *</th>
                    <th style={{ minWidth: 64 }}>State *</th>
                    <th style={{ minWidth: 90 }}>ZIP *</th>
                    <th style={{ minWidth: 120 }}>Phone</th>
                    <th className="bulk-grid__remove-col" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, idx) => (
                    <tr key={row.id} className={row.error ? "bulk-grid__row--error" : ""}>
                      <td className="bulk-grid__num">{idx + 1}</td>
                      <td>
                        <input
                          className="bulk-grid__input bulk-grid__input--sku"
                          value={row.sku}
                          placeholder="G1234567"
                          onChange={(e) => updateRow(row.id, "sku", e.target.value.toUpperCase())}
                          onBlur={() => lookupRow({ ...row, sku: row.sku })}
                        />
                      </td>
                      <td>
                        <input
                          className="bulk-grid__input bulk-grid__input--qty"
                          type="number" min="1"
                          value={row.quantity}
                          onChange={(e) => updateRow(row.id, "quantity", e.target.value)}
                        />
                      </td>
                      <td className="bulk-grid__product">
                        {row.info ? (
                          <div className="bulk-grid__product-info">
                            <img
                              src={row.info.image_url || "/placeholder.svg"} alt=""
                              onError={(e) => { e.target.style.opacity = "0.25"; }}
                            />
                            <div>
                              <span className="bulk-grid__product-title" title={row.info.title}>
                                {row.info.title}
                              </span>
                              <span className="bulk-grid__product-meta">
                                {money(row.info.unit_price)}
                                {row.quantity && ` × ${row.quantity} = ${money(row.info.unit_price * (parseInt(row.quantity, 10) || 0))}`}
                              </span>
                            </div>
                          </div>
                        ) : row.error ? (
                          <span className="bulk-grid__error-text" title={row.error}>
                            <AlertIcon size={13} /> {row.error}
                          </span>
                        ) : (
                          <span className="bulk-grid__product-empty">
                            {row.sku ? "Validating on blur…" : "Enter a SKU"}
                          </span>
                        )}
                      </td>
                      <td><input className="bulk-grid__input" value={row.first_name} onChange={(e) => updateRow(row.id, "first_name", e.target.value)} /></td>
                      <td><input className="bulk-grid__input" value={row.last_name} onChange={(e) => updateRow(row.id, "last_name", e.target.value)} /></td>
                      <td><input className="bulk-grid__input" value={row.address1} onChange={(e) => updateRow(row.id, "address1", e.target.value)} /></td>
                      <td><input className="bulk-grid__input" value={row.address2} onChange={(e) => updateRow(row.id, "address2", e.target.value)} /></td>
                      <td><input className="bulk-grid__input" value={row.city} onChange={(e) => updateRow(row.id, "city", e.target.value)} /></td>
                      <td><input className="bulk-grid__input bulk-grid__input--state" maxLength={2} placeholder="TX" value={row.state} onChange={(e) => updateRow(row.id, "state", e.target.value.toUpperCase())} /></td>
                      <td><input className="bulk-grid__input" value={row.zip} onChange={(e) => updateRow(row.id, "zip", e.target.value)} /></td>
                      <td><input className="bulk-grid__input" value={row.phone} onChange={(e) => updateRow(row.id, "phone", e.target.value)} /></td>
                      <td className="bulk-grid__remove-col">
                        <button
                          className="bulk-grid__remove"
                          onClick={() => removeRow(row.id)}
                          aria-label={`Remove row ${idx + 1}`}
                        >
                          <XIcon size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <button className="bulk-add-row" onClick={addRow} id="bulk-add-row">
              <PlusIcon size={15} /> Add row
            </button>
          </div>

          {/* ── Footer bar: summary + actions ── */}
          {formError && (
            <div className="bulk-error" id="bulk-error" role="alert">
              <AlertIcon size={16} /> {formError}
            </div>
          )}

          <div className="bulk-footer" id="bulk-footer">
            <div className="bulk-footer__summary">
              <span className="bulk-footer__stat">
                <strong>{rowsWithSku.length}</strong> row{rowsWithSku.length !== 1 ? "s" : ""}
              </span>
              <span className="bulk-footer__stat bulk-footer__stat--ok">
                <CheckIcon size={13} /> <strong>{validCount}</strong> validated
              </span>
              {errorCount > 0 && (
                <span className="bulk-footer__stat bulk-footer__stat--bad">
                  <AlertIcon size={13} /> <strong>{errorCount}</strong> with issues
                </span>
              )}
              {estTotal > 0 && (
                <span className="bulk-footer__stat">
                  Est. merchandise <strong>{money(estTotal)}</strong>
                </span>
              )}
            </div>
            <div className="bulk-footer__actions">
              <button
                className="btn btn--primary"
                onClick={validateAll}
                disabled={validating || placing}
                id="bulk-validate-btn"
              >
                {validating ? "Validating…" : "Validate rows"}
              </button>
              <button
                className="btn btn--accent bulk-footer__place"
                onClick={placeOrders}
                disabled={placing || validating || rowsWithSku.length === 0}
                id="bulk-place-btn"
              >
                {placing
                  ? "Placing orders…"
                  : `Place ${rowsWithSku.length || ""} order${rowsWithSku.length !== 1 ? "s" : ""}`}
              </button>
            </div>
          </div>

          <p className="bulk-note">
            <InfoIcon size={14} /> Each row becomes its own order with its own
            Order ID and tracking. All orders in the batch share the billing
            address above; confirmations and shipment emails go to the
            notification email{isAuthenticated ? " and your notification bell" : ""}.
            {!isAuthenticated && (
              <> <Link to="/login">Sign in</Link> to also get bell notifications
              and see the batch in your order history.</>
            )}
          </p>
        </div>
      </section>
    </main>
  );
}

/* Small helpers kept out of the main JSX */
function CreditCardTitle() {
  return (
    <>
      <MailIcon size={18} /> Billing &amp; notifications
    </>
  );
}

function InfoHint({ text }) {
  return (
    <span className="info-hint" title={text}>
      <InfoIcon size={13} />
    </span>
  );
}

export default BulkOrderPage;
