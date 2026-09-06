import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import ProductCarousel from "../components/ProductCarousel";
import CategoriesExplore, { TrustBadges } from "../components/CategoriesExplore";
import RecentlyViewed, { trackProductView } from "../components/RecentlyViewed";
import { useCart } from "../context/CartContext";
import { CartIcon, TagIcon, CheckIcon, AlertIcon } from "../components/Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

function ProductPage() {
  const { zoroCode } = useParams();
  const { addItem } = useCart();
  const [product, setProduct] = useState(null);
  const [relatedProducts, setRelatedProducts] = useState([]);
  const [variations, setVariations] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedImage, setSelectedImage] = useState(0);
  const [quantity, setQuantity] = useState(1);
  const [addedMsg, setAddedMsg] = useState("");
  const [showAllSpecs, setShowAllSpecs] = useState(false);

  useEffect(() => {
    const fetchProduct = async () => {
      setLoading(true);
      setError(null);
      setQuantity(1);
      setAddedMsg("");
      setVariations([]);

      try {
        const response = await fetch(`${API_BASE}/api/products/${zoroCode}`);
        if (!response.ok) {
          if (response.status === 404) throw new Error("Product not found");
          throw new Error(`Server error: ${response.status}`);
        }
        const data = await response.json();
        setProduct(data);

        // Track view for "Recently Viewed"
        trackProductView(data);

        // Size / pack / length variations with price comparison
        const varRes = await fetch(`${API_BASE}/api/products/${zoroCode}/variations`);
        if (varRes.ok) {
          const varData = await varRes.json();
          setVariations(varData.variations || []);
        }

        // Fetch related products (same category)
        const relatedRes = await fetch(`${API_BASE}/api/products/${zoroCode}/related?limit=8`);
        if (relatedRes.ok) {
          const relatedData = await relatedRes.json();
          setRelatedProducts(relatedData.products || []);
        }

        // Fetch categories for "More categories to explore"
        const catRes = await fetch(`${API_BASE}/api/categories`);
        if (catRes.ok) {
          const catData = await catRes.json();
          setCategories(catData.categories || []);
        }

      } catch (err) {
        console.error("Fetch error:", err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchProduct();
    window.scrollTo(0, 0);
  }, [zoroCode]);

  const formatPrice = (price) => {
    if (!price && price !== 0) return "—";
    return parseFloat(price).toFixed(2);
  };

  const isInStock = product?.availability?.toLowerCase().includes("stock")
    || product?.availability?.toLowerCase().includes("in stock");

  const specs = product?.specifications || {};
  // "Item" leads (like zoro), the rest alphabetical
  const specEntries = Object.entries(specs).sort(([a], [b]) => {
    if (a === "Item") return -1;
    if (b === "Item") return 1;
    return a.localeCompare(b);
  });

  const images = product?.images || [];
  const allImages = images.length > 0
    ? images.map((img) => img.image_url || img)
    : product?.image_url
      ? [product.image_url]
      : [];

  const currentImage = allImages[selectedImage] || allImages[0] || "";

  // Category breadcrumb path from API
  const categoryPath = product?.category_path || [];

  const handleAddToCart = () => {
    if (!product) return;
    addItem(product, quantity);
    setAddedMsg(`Added ${quantity} item${quantity > 1 ? "s" : ""} to cart!`);
    setTimeout(() => setAddedMsg(""), 3000);
  };

  const handleQtyChange = (delta) => {
    setQuantity((prev) => {
      const min = product?.min_order_qty || 1;
      const next = prev + delta;
      return next < min ? min : next;
    });
  };

  // Build "shop" links for the related carousel
  const shopLinks = [];
  if (product?.category_name) {
    shopLinks.push({
      label: `Shop ${product.category_name}`,
      to: `/search?q=${encodeURIComponent(product.category_name)}`,
    });
  }
  if (product?.brand) {
    shopLinks.push({
      label: `Shop ${product.brand}`,
      to: `/search?q=${encodeURIComponent(product.brand)}`,
    });
  }

  // Loading
  if (loading) {
    return (
      <main className="page-enter">
        <div className="container">
          <div className="loading" id="product-loading">
            <div className="loading__spinner" />
            <span className="loading__text">Loading product...</span>
          </div>
        </div>
      </main>
    );
  }

  // Error
  if (error) {
    return (
      <main className="page-enter">
        <div className="container" style={{ padding: "60px 24px" }}>
          <div className="error-state" id="product-error">
            <div className="error-state__icon"><AlertIcon size={30} /></div>
            <h3 className="error-state__title">{error}</h3>
            <p className="error-state__message">
              The product with code "{zoroCode}" could not be loaded.
            </p>
            <Link to="/" className="btn btn--primary">
              ← Back to Products
            </Link>
          </div>
        </div>
      </main>
    );
  }

  if (!product) return null;

  return (
    <main className="page-enter">
      <section className="product-detail" id="product-detail">
        <div className="container">

          {/* Breadcrumb — uses real category path from API */}
          <nav className="breadcrumb" id="breadcrumb">
            <Link to="/">Home</Link>
            {categoryPath.map((crumb, i) => (
              <span key={crumb.category_id}>
                <span className="breadcrumb__sep">/</span>
                <Link to={`/search?q=${encodeURIComponent(crumb.category_name)}`}>
                  {crumb.category_name}
                </Link>
              </span>
            ))}
            <span className="breadcrumb__sep">/</span>
            <span className="breadcrumb__current">
              {product.title.length > 60
                ? product.title.substring(0, 60) + "..."
                : product.title}
            </span>
          </nav>

          {/* Two-column layout */}
          <div className="product-detail__layout">

            {/* LEFT: Image Gallery */}
            <div className="product-gallery" id="product-gallery">
              <div className="product-gallery__main">
                <img
                  src={currentImage || "/placeholder.svg"}
                  alt={product.title}
                  onError={(e) => {
                    e.target.style.opacity = "0.3";
                  }}
                />
              </div>

              {allImages.length > 1 && (
                <div className="product-gallery__thumbs">
                  {allImages.map((img, idx) => (
                    <button
                      key={idx}
                      className={`product-gallery__thumb ${
                        idx === selectedImage
                          ? "product-gallery__thumb--active"
                          : ""
                      }`}
                      onClick={() => setSelectedImage(idx)}
                    >
                      <img
                        src={img}
                        alt={`${product.title} - view ${idx + 1}`}
                      />
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* RIGHT: Product Info */}
            <div className="product-info" id="product-info">

              {product.brand && (
                <div className="product-info__brand">{product.brand}</div>
              )}

              <h1 className="product-info__title">{product.title}</h1>

              {/* Meta info */}
              <div className="product-info__meta">
                <div className="product-info__meta-item">
                  <strong>Zoro#</strong> {product.zoro_code}
                </div>
                {product.mfr_number && (
                  <div className="product-info__meta-item">
                    <strong>MFR#</strong> {product.mfr_number}
                  </div>
                )}
                {product.upc && (
                  <div className="product-info__meta-item">
                    <strong>UPC</strong> {product.upc}
                  </div>
                )}
                {product.country_of_origin && (
                  <div className="product-info__meta-item">
                    <strong>Country of Origin:</strong> {product.country_of_origin}
                  </div>
                )}
              </div>

              {/* Badges container */}
              <div className="product-info__badges" id="badges-container">
                <div
                  className={`product-info__availability ${
                    isInStock
                      ? "product-info__availability--instock"
                      : "product-info__availability--oos"
                  }`}
                  id="availability-badge"
                >
                  <span className="product-info__availability-dot" />
                  {isInStock ? "In Stock" : product.availability || "Check Availability"}
                </div>
                
                {product.lead_time && (
                  <div className="product-info__badge product-info__badge--lead">
                    {product.lead_time}
                  </div>
                )}
                {product.is_dropship && (
                  <div className="product-info__badge product-info__badge--dropship">
                    Direct from Manufacturer
                  </div>
                )}
                {product.is_ltl && (
                  <div className="product-info__badge product-info__badge--ltl">
                    LTL Freight
                  </div>
                )}
                {product.is_blind_ship && (
                  <div className="product-info__badge product-info__badge--blind">
                    Blind Ship Eligible
                  </div>
                )}
                {product.is_hazmat && (
                  <div className="product-info__badge product-info__badge--hazmat">
                    Hazmat Item
                  </div>
                )}
                {(product.restricted_states || []).length > 0 && (
                  <div
                    className="product-info__badge product-info__badge--restricted"
                    title={`Cannot ship to: ${product.restricted_states.join(", ")}`}
                  >
                    State Restrictions
                  </div>
                )}
              </div>

              {/* Price */}
              <div className="product-info__price-box" id="price-box">
                <div className="product-info__price">
                  <span className="product-info__price-currency">$</span>
                  {formatPrice(product.unit_price)}
                  {product.uom && (
                    <span style={{ fontSize: '16px', color: 'var(--text-muted)', marginLeft: '8px' }}>
                      / {product.uom.toLowerCase()}
                    </span>
                  )}
                  {product.original_price && product.original_price > product.unit_price && (
                    <span className="product-info__price-was">
                      ${formatPrice(product.original_price)}
                    </span>
                  )}
                </div>
                <div className="product-info__pack-facts">
                  <span>
                    Min. Order Qty <strong>{product.min_order_qty || 1}</strong>
                  </span>
                  <span className="product-info__pack-sep" />
                  <span>
                    Package Qty <strong>{product.package_qty || 1}</strong>
                  </span>
                  {product.freight_surcharge > 0 && (
                    <>
                      <span className="product-info__pack-sep" />
                      <span style={{ color: 'var(--danger-500)', fontWeight: 600 }}>
                        +${formatPrice(product.freight_surcharge)} Freight
                      </span>
                    </>
                  )}
                </div>
                <div className="product-info__price-label">
                  Free shipping on orders $50+
                </div>

                {/* Quantity-break pricing */}
                {(product.price_tiers || []).length > 0 && (
                  <div className="tier-pricing" id="tier-pricing">
                    <span className="tier-pricing__label">
                      <TagIcon size={13} /> Volume pricing
                    </span>
                    <div className="tier-pricing__rows">
                      <span className="tier-pricing__row">
                        <em>1+</em> ${formatPrice(product.unit_price)}
                      </span>
                      {product.price_tiers.map((t) => (
                        <span
                          className={`tier-pricing__row ${quantity >= t.min_qty ? "tier-pricing__row--hit" : ""}`}
                          key={t.min_qty}
                        >
                          <em>{t.min_qty}+</em> ${formatPrice(t.price)}
                        </span>
                      ))}
                    </div>
                    <span className="tier-pricing__note">
                      Applied automatically at checkout &amp; in the Bulk Order Pad
                    </span>
                  </div>
                )}
              </div>

              {/* ── Variations & price comparison ── */}
              {variations.length > 1 && (
                <div className="variations" id="variations-section">
                  <div className="variations__head">
                    <h3 className="variations__title">
                      Available options ({variations.length})
                    </h3>
                    <span className="variations__hint">
                      <TagIcon size={13} /> Compare pricing across sizes
                    </span>
                  </div>
                  <div className="variations__table-wrap">
                    <table className="variations__table">
                      <thead>
                        <tr>
                          <th>Option</th>
                          <th>SKU</th>
                          <th>Price</th>
                          <th>vs. lowest</th>
                          <th>Availability</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const prices = variations
                            .map((v) => v.unit_price)
                            .filter((p) => p !== null && p !== undefined);
                          const lowest = prices.length ? Math.min(...prices) : null;
                          return variations.map((v) => {
                            const isCurrent = v.zoro_code === product.zoro_code;
                            const isLowest = lowest !== null && v.unit_price === lowest;
                            return (
                              <tr
                                key={v.zoro_code}
                                className={`variations__row ${isCurrent ? "variations__row--current" : ""}`}
                              >
                                <td>
                                  <Link
                                    to={`/product/${v.zoro_code}`}
                                    className="variations__option"
                                  >
                                    <span className={`variations__radio ${isCurrent ? "variations__radio--on" : ""}`} />
                                    {v.variant_label || v.title}
                                    {isCurrent && (
                                      <span className="variations__current-tag">
                                        <CheckIcon size={11} /> Viewing
                                      </span>
                                    )}
                                  </Link>
                                </td>
                                <td className="variations__sku">{v.zoro_code}</td>
                                <td className="variations__price">
                                  ${formatPrice(v.unit_price)}
                                  {v.uom && (
                                    <span className="variations__uom">/{v.uom.toLowerCase()}</span>
                                  )}
                                  {isLowest && (
                                    <span className="chip chip--success variations__best">
                                      <TagIcon size={11} /> Lowest
                                    </span>
                                  )}
                                </td>
                                <td className="variations__diff">
                                  {isLowest || lowest === null || v.unit_price === null
                                    ? "—"
                                    : `+$${formatPrice(v.unit_price - lowest)}`}
                                </td>
                                <td>
                                  <span
                                    className={`variations__stock ${
                                      (v.availability || "").toLowerCase().includes("stock")
                                        ? "variations__stock--in"
                                        : ""
                                    }`}
                                  >
                                    {v.availability || "—"}
                                  </span>
                                </td>
                                <td>
                                  <button
                                    className="variations__add"
                                    onClick={() => {
                                      addItem(v, v.min_order_qty || 1);
                                    }}
                                    aria-label={`Add ${v.variant_label || v.title} to cart`}
                                  >
                                    <CartIcon size={14} /> Add
                                  </button>
                                </td>
                              </tr>
                            );
                          });
                        })()}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Quantity Selector + Actions */}
              <div className="product-info__actions" id="product-actions">
                <div className="qty-selector" id="qty-selector">
                  <button
                    className="qty-btn"
                    onClick={() => handleQtyChange(-1)}
                    disabled={quantity <= (product.min_order_qty || 1)}
                    aria-label="Decrease quantity"
                  >
                    −
                  </button>
                  <input
                    type="number"
                    className="qty-input"
                    value={quantity}
                    min={product.min_order_qty || 1}
                    onChange={(e) => {
                      const val = parseInt(e.target.value, 10);
                      if (!isNaN(val) && val >= (product.min_order_qty || 1)) {
                        setQuantity(val);
                      }
                    }}
                    aria-label="Quantity"
                  />
                  <button
                    className="qty-btn"
                    onClick={() => handleQtyChange(1)}
                    aria-label="Increase quantity"
                  >
                    +
                  </button>
                </div>
                <button
                  className="btn btn--accent"
                  style={{ fontSize: '16px', padding: '16px 24px' }}
                  onClick={handleAddToCart}
                  id="add-to-cart-btn"
                >
                  <CartIcon size={17} /> Add to Cart
                </button>
              </div>

              {/* Added confirmation */}
              {addedMsg && (
                <div className="product-info__added-msg" id="added-msg">
                  <CheckIcon size={15} /> {addedMsg}
                </div>
              )}

            </div>
          </div>

          {/* Promo Banners */}
          <div className="promo-banners">
            <div className="promo-banner">
              <span className="promo-banner__title">Get a 10% off coupon</span>
              <Link to="/register" className="promo-banner__link">Join our email list</Link>
            </div>
            <div className="promo-banner">
              <span className="promo-banner__title">Free 30-day returns</span>
              <Link to="/returns" className="promo-banner__link">Return Policy</Link>
            </div>
            <div className="promo-banner">
              <span className="promo-banner__title">Get 30 days to pay with Net 30</span>
              <Link to="/register" className="promo-banner__link">Apply Now</Link>
            </div>
            <div className="promo-banner">
              <span className="promo-banner__title">Unlock tax-exempt savings</span>
              <Link to="/account" className="promo-banner__link">Add Tax Exemption</Link>
            </div>
          </div>

          {/* ── Product Details (description) — zoro puts this first ── */}
          {product.description && (
            <div className="description-section" id="description-section">
              <h2 className="description-section__title">Product Details</h2>
              <div
                className="description-section__content"
                dangerouslySetInnerHTML={{
                  __html: product.description,
                }}
              />
            </div>
          )}

          {/* ── Specifications — full zoro-style striped table ── */}
          {specEntries.length > 0 && (() => {
            const VISIBLE = 12;
            const rows = showAllSpecs ? specEntries : specEntries.slice(0, VISIBLE);
            return (
              <div className="specs-section" id="specs-section">
                <div className="zoro-specs__head">
                  <h2 className="specs-section__title">
                    Specifications
                    <span className="zoro-specs__count">
                      {specEntries.length} attributes
                    </span>
                  </h2>
                  {product.category_name && (
                    <span className="zoro-specs__cat">
                      {categoryPath.map((c) => c.category_name).join(" / ") ||
                        product.category_name}
                    </span>
                  )}
                </div>
                <table className="zoro-specs" id="zoro-specs-table">
                  <tbody>
                    {rows.map(([key, value]) => (
                      <tr key={key}>
                        <th scope="row">{key}</th>
                        <td>{value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {specEntries.length > VISIBLE && (
                  <button
                    className="zoro-specs__toggle"
                    onClick={() => setShowAllSpecs((v) => !v)}
                    id="specs-toggle"
                  >
                    {showAllSpecs
                      ? "Show fewer specifications"
                      : `Show all ${specEntries.length} specifications`}
                  </button>
                )}
              </div>
            );
          })()}

          {/* ═══════════════════════════════════════════
              "You may also like" — Horizontal Carousel
              ═══════════════════════════════════════════ */}
          <ProductCarousel
            title="You may also like"
            products={relatedProducts}
            shopLinks={shopLinks}
            seeAllTo="/"
          />

          {/* ═══════════════════════════════════════════
              "More categories to explore"
              ═══════════════════════════════════════════ */}
          <CategoriesExplore categories={categories} />

          {/* ═══════════════════════════════════════════
              "3 reasons you can count on us"
              ═══════════════════════════════════════════ */}
          <TrustBadges />

          {/* ═══════════════════════════════════════════
              "Recently viewed and more"
              ═══════════════════════════════════════════ */}
          <RecentlyViewed excludeCode={zoroCode} />

        </div>
      </section>
    </main>
  );
}

export default ProductPage;
