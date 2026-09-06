import { useState, useEffect, useMemo, useRef } from "react";
import { Link, useParams, useSearchParams, useLocation, useNavigate } from "react-router-dom";
import { useCatalog } from "../context/CatalogContext";
import { api, formatCount, slugToTitle, imgUrl, handleImgError } from "../lib/api";
import { formatBrandName } from "../lib/brand";
import ProductCard from "../components/ProductCard";
import {
  ChevronDown, ChevronLeft, ChevronRight, FilterIcon, XIcon, SearchIcon,
  BoxIcon, ArrowRight,
} from "../components/Icons";

/* ── facet blocks ────────────────────────────────────────────────── */

function FacetSection({ title, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="facet">
      <button className={`facet__head ${open ? "is-open" : ""}`} onClick={() => setOpen(!open)}>
        {title} <ChevronDown size={16} />
      </button>
      {open && <div className="facet__body">{children}</div>}
    </div>
  );
}

function CategoryFacet({ facets, l1, l2, l1Name, l2Name, makeCatPath }) {
  const cats = facets?.categories || [];
  return (
    <FacetSection title="Categories">
      {l1 && (
        <Link className="facet__link facet__link--back" to="/search">
          <ChevronLeft size={14} /> All departments
        </Link>
      )}
      {l1 && l2 && (
        <Link className="facet__link facet__link--back" to={makeCatPath(l1)}>
          <ChevronLeft size={14} /> {l1Name}
        </Link>
      )}
      {l2 ? (
        <span className="facet__link facet__link--cur">{l2Name}</span>
      ) : (
        cats.map((c) => (
          <Link
            key={`${c.level}-${c.slug}`}
            className="facet__link"
            to={c.level === 1 ? makeCatPath(c.slug) : makeCatPath(l1, c.slug)}
          >
            <span>{c.name}</span>
            <span className="n">{formatCount(c.count)}</span>
          </Link>
        ))
      )}
    </FacetSection>
  );
}

function BrandFacet({ facets, selected, onToggle }) {
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const brands = facets?.brands || [];
  const filtered = filter
    ? brands.filter((b) => b.name.toLowerCase().includes(filter.toLowerCase()))
    : brands;
  const shown = showAll ? filtered : filtered.slice(0, 8);
  if (!brands.length && !selected.length) return null;

  return (
    <FacetSection title="Brand">
      {brands.length > 8 && (
        <input
          className="z-input"
          style={{ height: 32, fontSize: 13, marginBottom: 6 }}
          placeholder="Search brands"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}
      {selected.filter((s) => !shown.some((b) => b.name === s)).map((s) => (
        <label key={s} className="facet__check">
          <input type="checkbox" checked onChange={() => onToggle(s)} />
          <span className="t">{formatBrandName(s)}</span>
        </label>
      ))}
      {shown.map((b) => (
        <label key={b.name} className="facet__check">
          <input
            type="checkbox"
            checked={selected.includes(b.name)}
            onChange={() => onToggle(b.name)}
          />
          <span className="t" title={b.name}>{formatBrandName(b.name)}</span>
          <span className="n">{formatCount(b.count)}</span>
        </label>
      ))}
      {filtered.length > 8 && (
        <button className="z-btn--link facet__more z-btn" onClick={() => setShowAll(!showAll)}>
          {showAll ? "See less" : `See more (${filtered.length - 8})`}
        </button>
      )}
    </FacetSection>
  );
}

function PriceFacet({ minPrice, maxPrice, onApply }) {
  const [lo, setLo] = useState(minPrice || "");
  const [hi, setHi] = useState(maxPrice || "");
  useEffect(() => { setLo(minPrice || ""); setHi(maxPrice || ""); }, [minPrice, maxPrice]);
  return (
    <FacetSection title="Price">
      <div className="facet__price-row">
        <input className="z-input" placeholder="Min" inputMode="decimal" value={lo}
          onChange={(e) => setLo(e.target.value)} aria-label="Minimum price" />
        <span className="z-muted">–</span>
        <input className="z-input" placeholder="Max" inputMode="decimal" value={hi}
          onChange={(e) => setHi(e.target.value)} aria-label="Maximum price" />
        <button className="z-btn z-btn--navy" onClick={() => onApply(lo, hi)}>Go</button>
      </div>
    </FacetSection>
  );
}

function Facets({ facets, params, l1, l2, l1Name, l2Name, makeCatPath, update, clearAll, activeCount }) {
  const selectedBrands = params.getAll("brand");
  return (
    <>
      <div className="facets__title">Filter &amp; Sort</div>
      <CategoryFacet facets={facets} l1={l1} l2={l2} l1Name={l1Name} l2Name={l2Name} makeCatPath={makeCatPath} />
      <BrandFacet
        facets={facets}
        selected={selectedBrands}
        onToggle={(name) => {
          const cur = params.getAll("brand");
          update({
            brand: cur.includes(name) ? cur.filter((b) => b !== name) : [...cur, name],
            page: null,
          });
        }}
      />
      <FacetSection title="Availability">
        <label className="facet__check">
          <input
            type="checkbox"
            checked={params.get("in_stock") === "true"}
            onChange={(e) => update({ in_stock: e.target.checked ? "true" : null, page: null })}
          />
          <span className="t">In stock only</span>
        </label>
      </FacetSection>
      <PriceFacet
        minPrice={params.get("min_price")}
        maxPrice={params.get("max_price")}
        onApply={(lo, hi) => update({
          min_price: lo || null,
          max_price: hi || null,
          page: null,
        })}
      />
      {activeCount > 0 && (
        <button className="z-btn z-btn--ghost z-btn--sm facet__clear" onClick={clearAll}>
          Clear all filters
        </button>
      )}
    </>
  );
}

/* ── pagination ──────────────────────────────────────────────────── */

function Pagination({ page, pages, onPage }) {
  if (pages <= 1) return null;
  const nums = [];
  const push = (n) => { if (!nums.includes(n) && n >= 1 && n <= pages) nums.push(n); };
  push(1); push(2);
  for (let n = page - 1; n <= page + 1; n++) push(n);
  push(pages - 1); push(pages);
  nums.sort((a, b) => a - b);

  const items = [];
  nums.forEach((n, i) => {
    if (i > 0 && n - nums[i - 1] > 1) items.push(<span key={`gap${n}`} className="gap">…</span>);
    items.push(
      <button key={n} className={n === page ? "is-cur" : ""} onClick={() => onPage(n)}>
        {n}
      </button>
    );
  });

  return (
    <nav className="pagination" aria-label="Pagination">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
        <ChevronLeft size={16} />
      </button>
      {items}
      <button disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
        <ChevronRight size={16} />
      </button>
    </nav>
  );
}

/* ── the page ────────────────────────────────────────────────────── */

export default function ListingPage() {
  const { l1, l2 } = useParams();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const topRef = useRef(null);

  const q = params.get("q") || "";
  const page = parseInt(params.get("page") || "1", 10);
  const sort = params.get("sort") || "";

  const makeCatPath = (l1Slug, l2Slug) =>
    l2Slug ? `/category/${l1Slug}/${l2Slug}` : `/category/${l1Slug}`;

  const update = (changes) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([key, value]) => {
      next.delete(key);
      if (value === null || value === undefined || value === "") return;
      if (Array.isArray(value)) value.forEach((v) => next.append(key, v));
      else next.set(key, value);
    });
    setParams(next);
  };

  const clearAll = () => {
    const next = new URLSearchParams();
    if (q) next.set("q", q);
    setParams(next);
  };

  const queryString = useMemo(() => {
    const qs = new URLSearchParams(params);
    if (l1) qs.set("l1", l1);
    if (l2) qs.set("l2", l2);
    if (!qs.get("per_page")) qs.set("per_page", "24");
    return qs.toString();
  }, [params, l1, l2]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api(`/api/products?${queryString}`)
      .then((d) => {
        if (cancelled) return;
        // If a search query was provided and it yields exactly 1 unique match on page 1, open the product page directly!
        if (q && d?.total === 1 && d?.products?.length === 1 && page === 1 && !params.get("brand") && !params.get("min_price") && !params.get("max_price")) {
          navigate(`/product/${d.products[0].zoro_code}`, { replace: true });
          return;
        }
        setData(d);
      })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [queryString, q, page, navigate, params]);

  // scroll to top when the page number changes
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [location.pathname, page]);

  const l1Name = data?.category?.l1 || slugToTitle(l1);
  const l2Name = data?.category?.l2 || slugToTitle(l2);
  const selectedBrands = params.getAll("brand");
  const activeCount =
    selectedBrands.length +
    (params.get("in_stock") === "true" ? 1 : 0) +
    (params.get("min_price") ? 1 : 0) +
    (params.get("max_price") ? 1 : 0);

  const title = q
    ? `Results for “${q}”`
    : l2 ? l2Name
    : l1 ? l1Name
    : selectedBrands.length === 1 ? formatBrandName(selectedBrands[0])
    : "All Products";

  const chips = [
    ...selectedBrands.map((b) => ({
      label: formatBrandName(b),
      clear: () => update({ brand: selectedBrands.filter((x) => x !== b), page: null }),
    })),
    ...(params.get("in_stock") === "true"
      ? [{ label: "In stock", clear: () => update({ in_stock: null, page: null }) }] : []),
    ...(params.get("min_price")
      ? [{ label: `Min $${params.get("min_price")}`, clear: () => update({ min_price: null, page: null }) }] : []),
    ...(params.get("max_price")
      ? [{ label: `Max $${params.get("max_price")}`, clear: () => update({ max_price: null, page: null }) }] : []),
  ];

  const { categories: catalogTree } = useCatalog();
  const currentL1Node = catalogTree.find((c) => c.slug === l1);
  const subcategories = useMemo(() => {
    if (currentL1Node?.children?.length) {
      return currentL1Node.children;
    }
    return data?.facets?.categories || [];
  }, [currentL1Node, data]);

  const viewMode = params.get("view");
  const isMainCategoryHub = Boolean(
    l1 &&
    !l2 &&
    !q &&
    selectedBrands.length === 0 &&
    !params.get("min_price") &&
    !params.get("max_price") &&
    !params.get("in_stock") &&
    viewMode !== "all"
  );

  const facetProps = {
    facets: data?.facets, params, l1, l2, l1Name, l2Name,
    makeCatPath, update, clearAll, activeCount,
  };

  return (
    <div className="z-page z-container" ref={topRef}>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link>
        {l1 && <><span className="sep">/</span>
          {l2 ? <Link to={makeCatPath(l1)}>{l1Name}</Link> : <span className="cur">{l1Name}</span>}</>}
        {l2 && <><span className="sep">/</span><span className="cur">{l2Name}</span></>}
        {q && <><span className="sep">/</span><span className="cur">Search: {q}</span></>}
        {!l1 && !q && <><span className="sep">/</span><span className="cur">All Products</span></>}
      </nav>

      {/* ── Main Category Hub: Show Subcategories First ──────────────── */}
      {isMainCategoryHub && subcategories.length > 0 ? (
        <div className="cathub-container">
          <div className="cathub-hero">
            <div>
              <h1 className="cathub-hero__title">{l1Name}</h1>
              <p className="cathub-hero__desc">
                Select a subcategory below to explore equipment, replacement parts, and supplies
                {data?.total ? ` (${formatCount(data.total)} items across ${subcategories.length} subcategories)` : ""}.
              </p>
            </div>
            <button
              type="button"
              className="z-btn z-btn--outline z-btn--sm"
              onClick={() => update({ view: "all" })}
              style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700 }}
            >
              Browse All {l1Name} Products ({data ? formatCount(data.total) : ""}) →
            </button>
          </div>

          <div className="cathub-grid">
            {subcategories.map((sub) => {
              const subSlug = sub.slug || sub.l2_slug;
              const subName = sub.name || sub.l2;
              const subCount = sub.count;
              const subImg = sub.image;

              return (
                <Link
                  key={subSlug}
                  to={`/category/${l1}/${subSlug}`}
                  className="cathub-card"
                >
                  <div className="cathub-card__imgwrap">
                    {subImg ? (
                      <img
                        src={imgUrl(subImg, "small")}
                        alt=""
                        loading="lazy"
                        onError={(e) => handleImgError(e, subImg)}
                      />
                    ) : (
                      <div className="noimg">
                        <BoxIcon size={32} />
                      </div>
                    )}
                  </div>
                  <div className="cathub-card__title">{subName}</div>
                  <span className="cathub-card__count">
                    {formatCount(subCount)} items
                  </span>
                </Link>
              );
            })}
          </div>

          {/* Featured / Popular Products in this Main Category */}
          {data?.products?.length > 0 && (
            <div style={{ marginTop: 36 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 16,
                  borderBottom: "1px solid var(--z-line-soft)",
                  paddingBottom: 10,
                }}
              >
                <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--z-ink)" }}>
                  Featured Products in {l1Name}
                </h3>
                <button
                  type="button"
                  className="z-btn z-btn--link"
                  onClick={() => update({ view: "all" })}
                  style={{ fontSize: 13, fontWeight: 700 }}
                >
                  View all {formatCount(data.total)} products →
                </button>
              </div>
              <div className="pgrid">
                {data.products.slice(0, 8).map((p) => (
                  <ProductCard key={p.zoro_code} product={p} />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        /* ── Standard Listing with Facets Sidebar (for Subcategories, Search, and view=all) ── */
        <div className="listing">
          <aside className="facets" aria-label="Filters">
            <Facets {...facetProps} />
          </aside>

          <div className="listing__main">
            <div className="listing__title">
              <h1>{title}</h1>
              {data && (
                <span className="count">
                  ({formatCount(data.total, data.total_capped)} item{data.total === 1 ? "" : "s"})
                </span>
              )}
            </div>

            <div className="listing__toolbar">
              <button className="z-btn z-btn--ghost z-btn--sm listing__filterbtn"
                onClick={() => setFiltersOpen(true)}>
                <FilterIcon size={15} /> Filters{activeCount > 0 ? ` (${activeCount})` : ""}
              </button>
              {chips.length > 0 && (
                <div className="listing__chips">
                  {chips.map((c) => (
                    <span key={c.label} className="chip">
                      {c.label}
                      <button onClick={c.clear} aria-label={`Remove ${c.label} filter`}>
                        <XIcon size={13} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <label className="listing__sort">
                Sort by
                <select className="z-select" value={sort}
                  onChange={(e) => update({ sort: e.target.value || null, page: null })}>
                  <option value="">Best match</option>
                  <option value="price_asc">Price: Low to High</option>
                  <option value="price_desc">Price: High to Low</option>
                  <option value="name_asc">Name: A to Z</option>
                  <option value="name_desc">Name: Z to A</option>
                  <option value="newest">Newest</option>
                </select>
              </label>
            </div>

            {error && (
              <div className={`z-alert ${error.status === 503 ? "z-alert--info" : "z-alert--error"}`}>
                {error.status === 503
                  ? "The catalog search index is still building — this page will work in a couple of minutes."
                  : `Couldn't load products: ${error.message}`}
              </div>
            )}

            {loading ? (
              <div className="pgrid">
                {Array.from({ length: 12 }, (_, i) => (
                  <div key={i} className="pcard">
                    <div className="z-skel" style={{ height: 175, marginBottom: 12 }} />
                    <div className="z-skel" style={{ height: 12, width: "40%", marginBottom: 8 }} />
                    <div className="z-skel" style={{ height: 16, marginBottom: 6 }} />
                    <div className="z-skel" style={{ height: 16, width: "70%", marginBottom: 14 }} />
                    <div className="z-skel" style={{ height: 22, width: "50%" }} />
                  </div>
                ))}
              </div>
            ) : data?.products?.length ? (
              <>
                <div className="pgrid">
                  {data.products.map((p) => (
                    <ProductCard key={p.zoro_code} product={p} />
                  ))}
                </div>
                <Pagination page={page} pages={Math.min(data.pages, 400)}
                  onPage={(n) => update({ page: n === 1 ? null : n })} />
              </>
            ) : !error && (
              <div className="listing__empty">
                <SearchIcon size={44} style={{ color: "var(--z-gray-light)" }} />
                <h3>No products found</h3>
                <p>Try a different search term, or remove some filters.</p>
                {activeCount > 0 && (
                  <button className="z-btn z-btn--outline" style={{ marginTop: 16 }} onClick={clearAll}>
                    Clear all filters
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {filtersOpen && (
        <>
          <div className="drawer-overlay" onClick={() => setFiltersOpen(false)} />
          <div className="drawer" role="dialog" aria-label="Filters">
            <div className="drawer__head">
              <span className="t">Filter &amp; Sort</span>
              <button className="drawer__close" onClick={() => setFiltersOpen(false)} aria-label="Close filters">
                <XIcon size={22} />
              </button>
            </div>
            <div className="facets facets--drawer">
              <Facets {...facetProps} />
              <button className="z-btn z-btn--primary z-btn--block" style={{ marginTop: 16 }}
                onClick={() => setFiltersOpen(false)}>
                {data ? `Show ${formatCount(data.total, data.total_capped)} results` : "Show results"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
