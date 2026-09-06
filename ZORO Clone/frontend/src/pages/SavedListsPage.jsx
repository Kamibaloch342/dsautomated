import { useState, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import { api, money, formatCount, imgUrl, handleImgError } from "../lib/api";
import { formatEqCode, formatBrandName, BRAND } from "../lib/brand";
import {
  ListIcon, PlusIcon, TrashIcon, CheckCircle, CartIcon,
  SearchIcon, XIcon, ArrowRight, BoxIcon, TruckIcon
} from "../components/Icons";

export default function SavedListsPage() {
  const { token, isAuthenticated } = useAuth();
  const { addItem, openCartDrawer } = useCart();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeListIdParam = searchParams.get("id");

  const [lists, setLists] = useState([]);
  const [activeList, setActiveList] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Create List Modal State
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newListName, setNewListName] = useState("");
  const [newListDesc, setNewListDesc] = useState("");
  const [newListJobSite, setNewListJobSite] = useState("");
  const [creating, setCreating] = useState(false);

  // Quick Add by SKU state
  const [quickSku, setQuickSku] = useState("");
  const [quickQty, setQuickQty] = useState(1);
  const [quickAdding, setQuickAdding] = useState(false);

  const fetchLists = async () => {
    if (!token) return;
    try {
      setLoading(true);
      const data = await api("/api/lists", { token });
      const fetched = data.lists || [];
      setLists(fetched);
      if (activeListIdParam) {
        loadListDetail(activeListIdParam);
      } else if (fetched.length > 0 && !activeList) {
        // stay on overview or pick first
      }
    } catch (err) {
      setError(err.message || "Failed to load saved project lists");
    } finally {
      setLoading(false);
    }
  };

  const loadListDetail = async (listId) => {
    if (!token) return;
    try {
      setLoadingDetail(true);
      const data = await api(`/api/lists/${listId}`, { token });
      setActiveList(data.list);
      setSearchParams({ id: listId });
    } catch (err) {
      setError(err.message || "Failed to load list details");
    } finally {
      setLoadingDetail(false);
    }
  };

  useEffect(() => {
    if (isAuthenticated) {
      fetchLists();
    } else {
      setLoading(false);
    }
  }, [isAuthenticated, token]);

  const handleCreateList = async (e) => {
    e.preventDefault();
    if (!newListName.trim()) return;
    setCreating(true);
    setError("");
    try {
      const data = await api("/api/lists", {
        method: "POST",
        token,
        body: JSON.stringify({
          name: newListName.trim(),
          description: newListDesc.trim(),
          job_site: newListJobSite.trim(),
        }),
      });
      setShowCreateModal(false);
      setNewListName("");
      setNewListDesc("");
      setNewListJobSite("");
      setSuccess(`Project list "${data.list.name}" created!`);
      await fetchLists();
      loadListDetail(data.list.list_id);
    } catch (err) {
      setError(err.message || "Failed to create list");
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteList = async (listId) => {
    if (!window.confirm("Are you sure you want to delete this project list?")) return;
    try {
      await api(`/api/lists/${listId}`, { method: "DELETE", token });
      setSuccess("Project list deleted.");
      setActiveList(null);
      setSearchParams({});
      fetchLists();
    } catch (err) {
      setError(err.message || "Failed to delete list");
    }
  };

  const handleUpdateItemQty = async (itemId, newQty) => {
    if (newQty < 1) {
      handleRemoveItem(itemId);
      return;
    }
    try {
      await api(`/api/lists/${activeList.list_id}/items/${itemId}`, {
        method: "PUT",
        token,
        body: JSON.stringify({ quantity: newQty }),
      });
      loadListDetail(activeList.list_id);
    } catch (err) {
      setError(err.message || "Failed to update quantity");
    }
  };

  const handleRemoveItem = async (itemId) => {
    try {
      await api(`/api/lists/${activeList.list_id}/items/${itemId}`, {
        method: "DELETE",
        token,
      });
      loadListDetail(activeList.list_id);
    } catch (err) {
      setError(err.message || "Failed to remove item");
    }
  };

  const handleQuickAddSku = async (e) => {
    e.preventDefault();
    if (!quickSku.trim() || !activeList) return;
    setQuickAdding(true);
    setError("");
    try {
      await api(`/api/lists/${activeList.list_id}/items`, {
        method: "POST",
        token,
        body: JSON.stringify({
          zoro_code: quickSku.trim(),
          quantity: Math.max(1, parseInt(quickQty, 10) || 1),
        }),
      });
      setQuickSku("");
      setQuickQty(1);
      setSuccess("Item added to project list!");
      loadListDetail(activeList.list_id);
    } catch (err) {
      setError(err.message || "Product code not found in catalog.");
    } finally {
      setQuickAdding(false);
    }
  };

  const handleAddAllToCart = (listToCart) => {
    const target = listToCart || activeList;
    if (!target || !target.items || target.items.length === 0) return;
    let count = 0;
    target.items.forEach((it) => {
      addItem({
        zoro_code: it.zoro_code,
        title: it.title,
        brand: it.brand,
        unit_price: it.unit_price,
        image_url: it.image_url,
        uom: it.uom,
      }, it.quantity);
      count += it.quantity;
    });
    setSuccess(`⚡ Added ${count} items from "${target.name}" to your cart!`);
    openCartDrawer();
  };

  if (!isAuthenticated) {
    return (
      <div className="z-page z-container" style={{ padding: "60px 16px", textAlign: "center" }}>
        <ListIcon size={48} style={{ color: "var(--z-orange)", marginBottom: 16 }} />
        <h1 style={{ fontSize: 28, fontWeight: 800, color: "var(--z-ink)" }}>Saved Project Lists &amp; Reorder Pad</h1>
        <p style={{ color: "var(--z-gray)", fontSize: 16, maxWidth: 500, margin: "12px auto 24px" }}>
          Sign in to organize job-site recurring supply lists, collaborate on commercial procurement,
          and reorder frequently needed items in one click.
        </p>
        <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
          <Link to="/login" className="z-btn z-btn--primary z-btn--lg">
            Sign In to Access Lists
          </Link>
          <Link to="/register" className="z-btn z-btn--outline z-btn--lg">
            Create Business Account
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="z-page z-container" id="saved-lists-page" style={{ paddingBottom: 60 }}>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link>
        <span className="sep">/</span>
        <Link to="/account">Account</Link>
        <span className="sep">/</span>
        <span className="cur">Project Lists &amp; Reorder Pad</span>
      </nav>

      <div
        className="z-page-head"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div>
          <h1>Saved Project Lists &amp; Reorder Pad</h1>
          <p className="sub">
            Organize materials by job-site, facility, or recurring maintenance cycle with instant 1-click reordering.
          </p>
        </div>
        <button
          type="button"
          className="z-btn z-btn--primary"
          onClick={() => setShowCreateModal(true)}
          style={{ display: "flex", alignItems: "center", gap: 8 }}
        >
          <PlusIcon size={18} /> + Create New Project List
        </button>
      </div>

      {error && (
        <div className="z-alert z-alert--error" style={{ marginBottom: 18 }}>
          {error}
        </div>
      )}

      {success && (
        <div
          className="z-alert z-alert--success"
          style={{
            marginBottom: 18,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <CheckCircle size={17} />
          <span>{success}</span>
        </div>
      )}

      {/* Main Content: Split or Single View */}
      {activeList ? (
        /* Detailed List View */
        <div>
          <button
            type="button"
            className="z-btn z-btn--outline"
            onClick={() => {
              setActiveList(null);
              setSearchParams({});
            }}
            style={{ marginBottom: 18, fontSize: 13 }}
          >
            ← Back to All Lists ({lists.length})
          </button>

          {loadingDetail ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--z-gray)" }}>
              Loading project list items...
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 28, alignItems: "start" }}>
              {/* Left Column: Items */}
              <div>
                <div
                  style={{
                    background: "#fff",
                    border: "1px solid var(--z-line-soft)",
                    borderRadius: "var(--z-radius-lg)",
                    padding: "24px",
                    marginBottom: 24,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      borderBottom: "1px solid var(--z-line-soft)",
                      paddingBottom: 16,
                      marginBottom: 20,
                    }}
                  >
                    <div>
                      <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "var(--z-ink)" }}>
                        {activeList.name}
                      </h2>
                      {activeList.job_site && (
                        <span
                          style={{
                            display: "inline-block",
                            background: "var(--z-bg-soft)",
                            border: "1px solid var(--z-line)",
                            padding: "2px 8px",
                            borderRadius: 4,
                            fontSize: 12,
                            fontWeight: 600,
                            color: "var(--z-blue)",
                            marginTop: 6,
                          }}
                        >
                          📍 {activeList.job_site}
                        </span>
                      )}
                      {activeList.description && (
                        <p style={{ color: "var(--z-gray)", fontSize: 14, margin: "6px 0 0" }}>
                          {activeList.description}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => handleDeleteList(activeList.list_id)}
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--z-gray)",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: 13,
                      }}
                      title="Delete this list"
                    >
                      <TrashIcon size={16} /> Delete List
                    </button>
                  </div>

                  {/* Live Quick Add SKU Box */}
                  <form
                    onSubmit={handleQuickAddSku}
                    style={{
                      display: "flex",
                      gap: 10,
                      alignItems: "center",
                      background: "var(--z-bg-soft)",
                      padding: "12px 16px",
                      borderRadius: 8,
                      marginBottom: 24,
                      border: "1px solid var(--z-line-soft)",
                    }}
                  >
                    <span style={{ fontSize: 13, fontWeight: 700, whiteSpace: "nowrap" }}>
                      + Quick Add SKU:
                    </span>
                    <input
                      type="text"
                      className="z-input"
                      placeholder="Enter EQ Code or Zoro SKU (e.g. E4341486)"
                      value={quickSku}
                      onChange={(e) => setQuickSku(e.target.value)}
                      style={{ flex: 1, height: 38 }}
                      required
                    />
                    <input
                      type="number"
                      min="1"
                      className="z-input"
                      value={quickQty}
                      onChange={(e) => setQuickQty(Math.max(1, parseInt(e.target.value, 10) || 1))}
                      style={{ width: 70, height: 38 }}
                      title="Quantity"
                    />
                    <button
                      type="submit"
                      className="z-btn z-btn--secondary"
                      disabled={quickAdding}
                      style={{ height: 38, whiteSpace: "nowrap" }}
                    >
                      {quickAdding ? "Adding..." : "Add to List"}
                    </button>
                  </form>

                  {/* Items List */}
                  {activeList.items && activeList.items.length > 0 ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                      {activeList.items.map((item) => (
                        <div
                          key={item.item_id}
                          style={{
                            display: "grid",
                            gridTemplateColumns: "70px 1fr 110px 110px 40px",
                            gap: 16,
                            alignItems: "center",
                            padding: "14px",
                            border: "1px solid var(--z-line-soft)",
                            borderRadius: 8,
                            background: "#fff",
                          }}
                        >
                          {/* Image */}
                          <Link to={`/product/${item.zoro_code}`}>
                            {item.image_url ? (
                              <img
                                src={imgUrl(item.image_url, "small")}
                                alt=""
                                style={{
                                  width: 64,
                                  height: 64,
                                  objectFit: "contain",
                                  borderRadius: 6,
                                  background: "#fdfdfd",
                                }}
                                onError={(e) => handleImgError(e, item.image_url)}
                              />
                            ) : (
                              <div
                                style={{
                                  width: 64,
                                  height: 64,
                                  borderRadius: 6,
                                  background: "var(--z-bg-soft)",
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                }}
                              >
                                <BoxIcon size={24} style={{ color: "var(--z-gray)" }} />
                              </div>
                            )}
                          </Link>

                          {/* Info */}
                          <div>
                            <div style={{ fontSize: 11.5, color: "var(--z-orange)", fontWeight: 700 }}>
                              {item.brand} · {BRAND.codeLabel}: {item.eq_code}
                            </div>
                            <Link
                              to={`/product/${item.zoro_code}`}
                              style={{
                                display: "block",
                                fontSize: 14,
                                fontWeight: 600,
                                color: "var(--z-ink)",
                                textDecoration: "none",
                                marginTop: 3,
                              }}
                            >
                              {item.title}
                            </Link>
                            {item.notes && (
                              <div style={{ fontSize: 12, color: "var(--z-gray)", marginTop: 3, fontStyle: "italic" }}>
                                Note: {item.notes}
                              </div>
                            )}
                          </div>

                          {/* Unit Price */}
                          <div style={{ textAlign: "right" }}>
                            <div style={{ fontSize: 15, fontWeight: 700, color: "var(--z-ink)" }}>
                              {money(item.unit_price)}
                            </div>
                            <div style={{ fontSize: 11.5, color: "var(--z-gray)" }}>per {item.uom || "EA"}</div>
                          </div>

                          {/* Quantity Selector */}
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                            <input
                              type="number"
                              min="1"
                              className="z-input"
                              value={item.quantity}
                              onChange={(e) =>
                                handleUpdateItemQty(item.item_id, parseInt(e.target.value, 10) || 1)
                              }
                              style={{ width: 64, height: 34, textAlign: "center", padding: "0 6px" }}
                            />
                          </div>

                          {/* Delete Item Button */}
                          <button
                            type="button"
                            onClick={() => handleRemoveItem(item.item_id)}
                            style={{
                              background: "none",
                              border: "none",
                              color: "var(--z-gray-light)",
                              cursor: "pointer",
                              padding: 4,
                            }}
                            title="Remove item"
                          >
                            <XIcon size={18} />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--z-gray)" }}>
                      <BoxIcon size={40} style={{ color: "var(--z-gray-light)", marginBottom: 10 }} />
                      <p style={{ fontSize: 15, fontWeight: 600 }}>This project list is currently empty.</p>
                      <p style={{ fontSize: 13 }}>Use the Quick Add SKU box above or browse products to save items here.</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Right Column: Summary & 1-Click Action */}
              <div
                style={{
                  background: "#fff",
                  border: "1px solid var(--z-line-soft)",
                  borderRadius: "var(--z-radius-lg)",
                  padding: "24px",
                  position: "sticky",
                  top: 90,
                }}
              >
                <h3 style={{ margin: "0 0 16px", fontSize: 17, fontWeight: 700 }}>List Summary</h3>

                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, fontSize: 14 }}>
                  <span style={{ color: "var(--z-gray)" }}>Unique SKUs:</span>
                  <span style={{ fontWeight: 600 }}>{activeList.item_count || 0}</span>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, fontSize: 14 }}>
                  <span style={{ color: "var(--z-gray)" }}>Total Units:</span>
                  <span style={{ fontWeight: 600 }}>{activeList.total_quantity || 0}</span>
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: 16,
                    paddingTop: 16,
                    borderTop: "1px solid var(--z-line-soft)",
                    fontSize: 18,
                    fontWeight: 800,
                  }}
                >
                  <span>Estimated Total:</span>
                  <span style={{ color: "var(--z-blue)" }}>{money(activeList.total_value)}</span>
                </div>

                <button
                  type="button"
                  className="z-btn z-btn--primary z-btn--lg"
                  onClick={() => handleAddAllToCart(activeList)}
                  disabled={!activeList.items || activeList.items.length === 0}
                  style={{
                    width: "100%",
                    marginTop: 24,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    gap: 8,
                    fontSize: 15,
                  }}
                >
                  ⚡ Add All ({activeList.total_quantity || 0}) to Cart
                </button>

                <p style={{ fontSize: 12, color: "var(--z-gray)", textAlign: "center", marginTop: 14 }}>
                  Orders placed before 4:00 PM CT start processing same day.
                </p>
              </div>
            </div>
          )}
        </div>
      ) : (
        /* Lists Overview Grid */
        <div>
          {loading ? (
            <div style={{ textAlign: "center", padding: "60px 20px", color: "var(--z-gray)" }}>
              Loading project lists...
            </div>
          ) : lists.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: "60px 20px",
                background: "#fff",
                borderRadius: "var(--z-radius-lg)",
                border: "1px solid var(--z-line-soft)",
              }}
            >
              <ListIcon size={48} style={{ color: "var(--z-orange)", marginBottom: 14 }} />
              <h3 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 8px" }}>No Saved Project Lists Yet</h3>
              <p style={{ color: "var(--z-gray)", fontSize: 14, maxWidth: 440, margin: "0 auto 20px" }}>
                Create recurring supply lists for job sites, routine maintenance, or departmental purchasing.
              </p>
              <button
                type="button"
                className="z-btn z-btn--primary"
                onClick={() => setShowCreateModal(true)}
              >
                + Create Your First Project List
              </button>
            </div>
          ) : (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
                gap: 20,
              }}
            >
              {lists.map((l) => (
                <div
                  key={l.list_id}
                  style={{
                    background: "#fff",
                    border: "1px solid var(--z-line-soft)",
                    borderRadius: "var(--z-radius-lg)",
                    padding: "20px",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    boxShadow: "var(--z-shadow-sm)",
                    transition: "border-color 0.18s ease, transform 0.18s ease",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
                      <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "var(--z-ink)" }}>
                        {l.name}
                      </h3>
                      {l.job_site && (
                        <span
                          style={{
                            background: "var(--z-bg-soft)",
                            border: "1px solid var(--z-line)",
                            padding: "2px 6px",
                            borderRadius: 4,
                            fontSize: 11,
                            fontWeight: 600,
                            color: "var(--z-blue)",
                            whiteSpace: "nowrap",
                          }}
                        >
                          📍 {l.job_site}
                        </span>
                      )}
                    </div>

                    {l.description && (
                      <p style={{ color: "var(--z-gray)", fontSize: 13, margin: "6px 0 14px", lineHeight: 1.4 }}>
                        {l.description}
                      </p>
                    )}

                    {/* Preview Images Strip */}
                    <div style={{ display: "flex", gap: 6, margin: "14px 0", minHeight: 48 }}>
                      {l.preview_images && l.preview_images.length > 0 ? (
                        l.preview_images.map((img, idx) => (
                          <img
                            key={idx}
                            src={imgUrl(img, "tiny")}
                            alt=""
                            style={{
                              width: 44,
                              height: 44,
                              objectFit: "contain",
                              borderRadius: 4,
                              background: "var(--z-bg-soft)",
                              border: "1px solid var(--z-line-soft)",
                            }}
                            onError={(e) => handleImgError(e, img)}
                          />
                        ))
                      ) : (
                        <div style={{ fontSize: 12.5, color: "var(--z-gray-light)", alignSelf: "center" }}>
                          No items added yet
                        </div>
                      )}
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13.5, margin: "10px 0" }}>
                      <span style={{ color: "var(--z-gray)" }}>{l.item_count} items ({l.total_quantity} units)</span>
                      <span style={{ fontWeight: 700, color: "var(--z-ink)" }}>{money(l.total_value)}</span>
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 8, marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--z-line-soft)" }}>
                    <button
                      type="button"
                      className="z-btn z-btn--secondary"
                      onClick={() => loadListDetail(l.list_id)}
                      style={{ flex: 1, justifyContent: "center", fontSize: 13 }}
                    >
                      View &amp; Edit Items
                    </button>
                    <button
                      type="button"
                      className="z-btn z-btn--primary"
                      onClick={() => {
                        api(`/api/lists/${l.list_id}`, { token }).then((data) => {
                          handleAddAllToCart(data.list);
                        });
                      }}
                      disabled={l.item_count === 0}
                      style={{ justifyContent: "center", padding: "0 14px" }}
                      title="1-Click Add All to Cart"
                    >
                      ⚡ Reorder
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Create New List Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div
            className="modal-box modal-box--md"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="modal__head">
              <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>+ Create New Project List</h3>
              <button
                type="button"
                className="modal__close"
                onClick={() => setShowCreateModal(false)}
                aria-label="Close modal"
              >
                <XIcon size={20} />
              </button>
            </div>
            <form onSubmit={handleCreateList}>
              <div className="modal__body" style={{ padding: "20px" }}>
                <div style={{ marginBottom: 14 }}>
                  <label style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                    List Name *
                  </label>
                  <input
                    type="text"
                    className="z-input"
                    placeholder="e.g. Facility Maintenance — HVAC Restock"
                    value={newListName}
                    onChange={(e) => setNewListName(e.target.value)}
                    required
                  />
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                    Job Site / Facility Location (Optional)
                  </label>
                  <input
                    type="text"
                    className="z-input"
                    placeholder="e.g. Warehouse 4, Bay 12"
                    value={newListJobSite}
                    onChange={(e) => setNewListJobSite(e.target.value)}
                  />
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                    Description (Optional)
                  </label>
                  <input
                    type="text"
                    className="z-input"
                    placeholder="e.g. Recurring bi-weekly restock list"
                    value={newListDesc}
                    onChange={(e) => setNewListDesc(e.target.value)}
                  />
                </div>
              </div>

              <div className="modal__foot" style={{ display: "flex", gap: 10, justifyContent: "flex-end", padding: "14px 20px" }}>
                <button
                  type="button"
                  className="z-btn z-btn--outline"
                  onClick={() => setShowCreateModal(false)}
                  disabled={creating}
                >
                  Cancel
                </button>
                <button type="submit" className="z-btn z-btn--primary" disabled={creating}>
                  {creating ? "Creating..." : "Create List"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
