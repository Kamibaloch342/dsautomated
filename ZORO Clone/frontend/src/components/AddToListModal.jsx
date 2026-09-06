import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, money } from "../lib/api";
import { XIcon, CheckCircle, PlusIcon, ListIcon, BoxIcon } from "./Icons";

export default function AddToListModal({ product, quantity = 1, isOpen, onClose, onAdded }) {
  const { token, isAuthenticated } = useAuth();
  const [lists, setLists] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedListId, setSelectedListId] = useState(null);
  const [mode, setMode] = useState("select"); // "select" | "new"
  const [newListName, setNewListName] = useState("");
  const [newJobSite, setNewJobSite] = useState("");
  const [qty, setQty] = useState(quantity);
  const [notes, setNotes] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setQty(quantity);
  }, [quantity]);

  useEffect(() => {
    if (!isOpen || !isAuthenticated) return;
    setError("");
    setSuccessMsg("");
    setLoading(true);
    api("/api/lists", { token })
      .then((res) => {
        const userLists = res.lists || [];
        setLists(userLists);
        if (userLists.length > 0) {
          setSelectedListId(userLists[0].list_id);
          setMode("select");
        } else {
          setMode("new");
          setNewListName("My Supply Reorder List");
        }
      })
      .catch((err) => {
        setError(err.message || "Failed to load project lists");
      })
      .finally(() => setLoading(false));
  }, [isOpen, isAuthenticated, token]);

  if (!isOpen) return null;

  const handleSave = async (e) => {
    e?.preventDefault();
    setError("");
    setSuccessMsg("");
    setSaving(true);

    try {
      let targetListId = selectedListId;

      if (mode === "new") {
        if (!newListName.trim()) {
          throw new Error("Please enter a name for the new list.");
        }
        const created = await api("/api/lists", {
          method: "POST",
          token,
          body: JSON.stringify({
            name: newListName.trim(),
            job_site: newJobSite.trim(),
          }),
        });
        targetListId = created.list.list_id;
      }

      if (!targetListId) {
        throw new Error("Please select or create a project list.");
      }

      await api(`/api/lists/${targetListId}/items`, {
        method: "POST",
        token,
        body: JSON.stringify({
          zoro_code: product.zoro_code || product.product_id,
          title: product.title,
          brand: product.brand,
          unit_price: product.unit_price,
          image_url: product.image_url,
          uom: product.uom || "EA",
          quantity: Math.max(1, parseInt(qty, 10) || 1),
          notes: notes.trim(),
        }),
      });

      setSuccessMsg("Item saved to project list successfully!");
      if (onAdded) onAdded(targetListId);
      setTimeout(() => {
        onClose();
      }, 1400);
    } catch (err) {
      setError(err.message || "Failed to save item to list.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box modal-box--md"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        style={{ maxWidth: 480 }}
      >
        <div className="modal__head">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ListIcon size={20} style={{ color: "var(--z-orange)" }} />
            <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Save to Project List</h3>
          </div>
          <button type="button" className="modal__close" onClick={onClose} aria-label="Close modal">
            <XIcon size={20} />
          </button>
        </div>

        <div className="modal__body" style={{ padding: "20px" }}>
          {!isAuthenticated ? (
            <div style={{ textAlign: "center", padding: "16px 8px" }}>
              <BoxIcon size={36} style={{ color: "var(--z-blue)", marginBottom: 12 }} />
              <h4 style={{ margin: "0 0 8px", fontSize: 16 }}>Sign in to Save Reorder Lists</h4>
              <p style={{ color: "var(--z-gray)", fontSize: 13.5, marginBottom: 20 }}>
                Create recurring job-site supply lists and reorder all items in 1 click.
              </p>
              <Link to="/login" className="z-btn z-btn--primary" style={{ width: "100%", justifyContent: "center" }}>
                Sign In to Your Account
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSave}>
              {/* Product Preview Card */}
              <div
                style={{
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                  padding: "10px 12px",
                  background: "var(--z-bg-soft)",
                  borderRadius: 8,
                  marginBottom: 18,
                  border: "1px solid var(--z-line-soft)",
                }}
              >
                {product.image_url ? (
                  <img
                    src={product.image_url}
                    alt=""
                    style={{ width: 44, height: 44, objectFit: "contain", borderRadius: 4, background: "#fff" }}
                  />
                ) : (
                  <div
                    style={{
                      width: 44,
                      height: 44,
                      borderRadius: 4,
                      background: "#fff",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <BoxIcon size={20} style={{ color: "var(--z-gray)" }} />
                  </div>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      color: "var(--z-ink)",
                    }}
                  >
                    {product.title}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--z-gray)", marginTop: 2 }}>
                    {money(product.unit_price)} / {product.uom || "EA"}
                  </div>
                </div>
              </div>

              {error && (
                <div className="z-alert z-alert--error" style={{ marginBottom: 14, fontSize: 13 }}>
                  {error}
                </div>
              )}

              {successMsg && (
                <div
                  className="z-alert z-alert--success"
                  style={{
                    marginBottom: 14,
                    fontSize: 13,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <CheckCircle size={16} />
                  <span>{successMsg}</span>
                </div>
              )}

              {/* Mode Tabs */}
              {lists.length > 0 && (
                <div
                  style={{
                    display: "flex",
                    gap: 8,
                    marginBottom: 16,
                    borderBottom: "1px solid var(--z-line-soft)",
                    paddingBottom: 8,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => setMode("select")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 6,
                      fontSize: 13,
                      fontWeight: 600,
                      background: mode === "select" ? "var(--z-blue)" : "transparent",
                      color: mode === "select" ? "#fff" : "var(--z-ink)",
                      border: "none",
                      cursor: "pointer",
                    }}
                  >
                    Existing List ({lists.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode("new")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 6,
                      fontSize: 13,
                      fontWeight: 600,
                      background: mode === "new" ? "var(--z-blue)" : "transparent",
                      color: mode === "new" ? "#fff" : "var(--z-ink)",
                      border: "none",
                      cursor: "pointer",
                    }}
                  >
                    + Create New List
                  </button>
                </div>
              )}

              {mode === "select" && lists.length > 0 ? (
                <div style={{ marginBottom: 16 }}>
                  <label style={{ display: "block", fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>
                    Select Destination List
                  </label>
                  <select
                    className="z-input"
                    value={selectedListId || ""}
                    onChange={(e) => setSelectedListId(Number(e.target.value))}
                    style={{ width: "100%", height: 40 }}
                  >
                    {lists.map((l) => (
                      <option key={l.list_id} value={l.list_id}>
                        {l.name} ({l.item_count} items {l.job_site ? `· ${l.job_site}` : ""})
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ marginBottom: 10 }}>
                    <label style={{ display: "block", fontSize: 12.5, fontWeight: 700, marginBottom: 4 }}>
                      List Name *
                    </label>
                    <input
                      type="text"
                      className="z-input"
                      placeholder="e.g. Job Site #104 — Electrical"
                      value={newListName}
                      onChange={(e) => setNewListName(e.target.value)}
                      required
                    />
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12.5, fontWeight: 700, marginBottom: 4 }}>
                      Job Site / Location (Optional)
                    </label>
                    <input
                      type="text"
                      className="z-input"
                      placeholder="e.g. Building B, 4th Floor"
                      value={newJobSite}
                      onChange={(e) => setNewJobSite(e.target.value)}
                    />
                  </div>
                </div>
              )}

              {/* Quantity and Notes */}
              <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 12, marginBottom: 20 }}>
                <div>
                  <label style={{ display: "block", fontSize: 12.5, fontWeight: 700, marginBottom: 4 }}>
                    Quantity
                  </label>
                  <input
                    type="number"
                    min="1"
                    className="z-input"
                    value={qty}
                    onChange={(e) => setQty(Math.max(1, parseInt(e.target.value, 10) || 1))}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: 12.5, fontWeight: 700, marginBottom: 4 }}>
                    Item Note (Optional)
                  </label>
                  <input
                    type="text"
                    className="z-input"
                    placeholder="e.g. Monthly replacement cycle"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                </div>
              </div>

              <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                <button type="button" className="z-btn z-btn--outline" onClick={onClose} disabled={saving}>
                  Cancel
                </button>
                <button type="submit" className="z-btn z-btn--primary" disabled={saving || loading}>
                  {saving ? "Saving..." : "Save to Project List"}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
