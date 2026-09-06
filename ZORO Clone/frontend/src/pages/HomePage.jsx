import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import ProductCard from "../components/ProductCard";
import Pagination from "../components/Pagination";
import SortFilter from "../components/SortFilter";
import { RowsIcon, CompareIcon, TruckIcon, MailIcon, AlertIcon, PackageIcon } from "../components/Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";

function HomePage() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [totalProducts, setTotalProducts] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);

  const [searchParams, setSearchParams] = useSearchParams();
  const searchQuery = searchParams.get("q");
  const currentPage = parseInt(searchParams.get("page") || "1", 10);
  const currentSort = searchParams.get("sort") || "";
  const currentInStock = searchParams.get("in_stock") === "true";

  useEffect(() => {
    const fetchProducts = async () => {
      setLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams();
        params.set("page", currentPage);
        params.set("per_page", "20");
        if (currentSort) params.set("sort", currentSort);
        if (currentInStock) params.set("in_stock", "true");

        let url;
        if (searchQuery) {
          params.set("q", searchQuery);
          url = `${API_BASE}/api/products/search?${params}`;
        } else {
          url = `${API_BASE}/api/products?${params}`;
        }

        const response = await fetch(url);

        if (!response.ok) {
          throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();
        setProducts(data.products || []);
        setTotalProducts(data.total || data.count || 0);
        setTotalPages(data.pages || 1);
        setHasNext(data.has_next || false);
        setHasPrev(data.has_prev || false);
      } catch (err) {
        console.error("Fetch error:", err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchProducts();
  }, [searchQuery, currentPage, currentSort, currentInStock]);

  const updateParams = (updates) => {
    const newParams = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value === null || value === "" || value === false) {
        newParams.delete(key);
      } else {
        newParams.set(key, value);
      }
    });
    setSearchParams(newParams);
  };

  const handlePageChange = (page) => {
    updateParams({ page });
    window.scrollTo(0, 0);
  };

  const handleSortChange = (sort) => {
    updateParams({ sort: sort || null, page: "1" });
  };

  const handleInStockChange = (checked) => {
    updateParams({ in_stock: checked ? "true" : null, page: "1" });
  };

  return (
    <main className="page-enter">

      {/* Hero Banner — only on home, not search */}
      {!searchQuery && (
        <>
          <section className="hero" id="hero-banner">
            <div className="container hero__inner">
              <div className="hero__content">
                <span className="hero__badge">Industrial Supplies &amp; Tools</span>
                <h1 className="hero__title">
                  Professional tools.<br />
                  <span>Ordered your way.</span>
                </h1>
                <p className="hero__subtitle">
                  Thousands of industrial supplies from trusted brands — with
                  the bulk, multi-address ordering that Zoro won't give you.
                </p>
                <div className="hero__cta-row">
                  <Link to="/bulk-order" className="btn btn--accent hero__cta">
                    <RowsIcon size={16} /> Open Bulk Order Pad
                  </Link>
                  <a href="#products-section" className="btn hero__cta-ghost">
                    Browse catalog
                  </a>
                </div>
                <div className="hero__stats">
                  <div className="hero__stat">
                    <div className="hero__stat-value">{totalProducts}+</div>
                    <div className="hero__stat-label">Products</div>
                  </div>
                  <div className="hero__stat">
                    <div className="hero__stat-value">500+</div>
                    <div className="hero__stat-label">Brands</div>
                  </div>
                  <div className="hero__stat">
                    <div className="hero__stat-value">24h</div>
                    <div className="hero__stat-label">Shipping</div>
                  </div>
                </div>
              </div>
            </div>
          </section>

          {/* Feature strip */}
          <section className="feature-strip">
            <div className="container feature-strip__inner">
              <Link to="/bulk-order" className="feature-tile feature-tile--link">
                <span className="feature-tile__icon"><RowsIcon size={20} /></span>
                <div>
                  <span className="feature-tile__title">Bulk Order Pad</span>
                  <span className="feature-tile__desc">
                    Place many orders at once — each row its own SKU, qty &amp; address
                  </span>
                </div>
              </Link>
              <div className="feature-tile">
                <span className="feature-tile__icon"><CompareIcon size={20} /></span>
                <div>
                  <span className="feature-tile__title">Compare pricing</span>
                  <span className="feature-tile__desc">
                    Tick "Compare" on listings, view sizes &amp; prices side-by-side
                  </span>
                </div>
              </div>
              <div className="feature-tile">
                <span className="feature-tile__icon"><MailIcon size={20} /></span>
                <div>
                  <span className="feature-tile__title">Live email threads</span>
                  <span className="feature-tile__desc">
                    Order IDs instantly, confirmations &amp; notifications built in
                  </span>
                </div>
              </div>
              <div className="feature-tile">
                <span className="feature-tile__icon"><TruckIcon size={20} /></span>
                <div>
                  <span className="feature-tile__title">Tracking emails</span>
                  <span className="feature-tile__desc">
                    Shipment emails with carrier tracking as each order ships
                  </span>
                </div>
              </div>
            </div>
          </section>
        </>
      )}

      {/* Products Section */}
      <section className="products-section" id="products-section">
        <div className="container">
          
          <aside className="sidebar">
            <h3 className="sidebar__title">Categories</h3>
            <ul className="sidebar__list">
              <li><Link to="/search?q=Lighting" className="sidebar__link">Lighting</Link></li>
              <li><Link to="/search?q=Tools" className="sidebar__link">Tools & Hardware</Link></li>
              <li><Link to="/search?q=Safety" className="sidebar__link">Safety</Link></li>
              <li><Link to="/search?q=Electrical" className="sidebar__link">Electrical</Link></li>
              <li><Link to="/search?q=Plumbing" className="sidebar__link">Plumbing</Link></li>
              <li><Link to="/search?q=Janitorial" className="sidebar__link">Janitorial</Link></li>
            </ul>
          </aside>

          <div className="products-content">
            <div className="section-header">
              <h2 className="section-header__title">
                {searchQuery
                  ? `Search results for "${searchQuery}"`
                  : "Featured Products"
                }
              </h2>
              <span className="section-header__count">
                {totalProducts} product{totalProducts !== 1 ? "s" : ""}
              </span>
            </div>

            {/* Sort & Filter */}
            <SortFilter
              sort={currentSort}
              onSortChange={handleSortChange}
              inStock={currentInStock}
              onInStockChange={handleInStockChange}
            />

            {/* Loading */}
            {loading && (
              <div className="loading" id="loading-state">
                <div className="loading__spinner" />
                <span className="loading__text">Loading products...</span>
              </div>
            )}

            {/* Error */}
            {error && !loading && (
              <div className="error-state" id="error-state">
                <div className="error-state__icon"><AlertIcon size={30} /></div>
                <h3 className="error-state__title">Connection Error</h3>
                <p className="error-state__message">
                  Could not reach the backend server. Make sure Flask is running on port 5001.
                </p>
                <p className="error-state__message">
                  <code>{error}</code>
                </p>
                <button
                  className="btn btn--primary"
                  onClick={() => window.location.reload()}
                >
                  Retry
                </button>
              </div>
            )}

            {/* Empty */}
            {!loading && !error && products.length === 0 && (
              <div className="empty-state" id="empty-state">
                <div className="empty-state__icon"><PackageIcon size={30} /></div>
                <h3 className="empty-state__title">No products found</h3>
                <p className="empty-state__message">
                  {searchQuery
                    ? `No results for "${searchQuery}". Try a different search.`
                    : "Run the seed script to populate the database."
                  }
                </p>
              </div>
            )}

            {/* Product Grid */}
            {!loading && !error && products.length > 0 && (
              <>
                <div className="product-grid" id="product-grid">
                  {products.map((product) => (
                    <ProductCard
                      key={product.zoro_code}
                      product={product}
                    />
                  ))}
                </div>

                {/* Pagination */}
                <Pagination
                  page={currentPage}
                  pages={totalPages}
                  hasNext={hasNext}
                  hasPrev={hasPrev}
                  onPageChange={handlePageChange}
                />
              </>
            )}
          </div>

        </div>
      </section>
    </main>
  );
}

export default HomePage;
