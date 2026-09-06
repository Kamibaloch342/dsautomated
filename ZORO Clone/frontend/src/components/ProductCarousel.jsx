import { useRef, useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { useCart } from "../context/CartContext";

/**
 * ProductCarousel — Reusable horizontal scrolling product carousel
 *
 * Props:
 *   title       — Section heading (e.g. "You may also like")
 *   products    — Array of product card objects
 *   shopLinks   — Optional array of {label, to} for category sub-links
 *   seeAllTo    — Optional link for "See All" button
 */
function ProductCarousel({ title, products, shopLinks = [], seeAllTo = "/" }) {
  const scrollRef = useRef(null);
  const { addItem } = useCart();
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  const CARD_WIDTH = 220; // approx card width + gap

  const updateScrollState = () => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 10);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 10);

    // Calculate page
    const cardsVisible = Math.floor(el.clientWidth / CARD_WIDTH) || 1;
    const total = Math.ceil(products.length / cardsVisible);
    const current = Math.min(
      Math.floor(el.scrollLeft / (cardsVisible * CARD_WIDTH)) + 1,
      total
    );
    setTotalPages(total);
    setCurrentPage(current);
  };

  useEffect(() => {
    updateScrollState();
    const el = scrollRef.current;
    if (el) {
      el.addEventListener("scroll", updateScrollState, { passive: true });
      window.addEventListener("resize", updateScrollState);
    }
    return () => {
      if (el) el.removeEventListener("scroll", updateScrollState);
      window.removeEventListener("resize", updateScrollState);
    };
  }, [products]);

  const scroll = (direction) => {
    const el = scrollRef.current;
    if (!el) return;
    const amount = el.clientWidth * 0.85;
    el.scrollBy({ left: direction * amount, behavior: "smooth" });
  };

  const formatPrice = (price) => {
    if (!price && price !== 0) return "—";
    return parseFloat(price).toFixed(2);
  };

  const handleAdd = (e, product) => {
    e.preventDefault();
    e.stopPropagation();
    addItem(product, 1);
  };

  if (!products || products.length === 0) return null;

  return (
    <div className="carousel-section" id={`carousel-${title?.replace(/\s+/g, "-").toLowerCase()}`}>
      {/* Header */}
      <div className="carousel-section__header">
        <div>
          <h2 className="carousel-section__title">{title}</h2>
          {shopLinks.length > 0 && (
            <div className="carousel-section__links">
              {shopLinks.map((link, i) => (
                <span key={link.label}>
                  {i > 0 && <span className="carousel-section__sep">|</span>}
                  <Link to={link.to} className="carousel-section__link">
                    {link.label}
                  </Link>
                </span>
              ))}
            </div>
          )}
        </div>
        {seeAllTo && (
          <Link to={seeAllTo} className="carousel-section__see-all">
            See All
          </Link>
        )}
      </div>

      {/* Carousel */}
      <div className="carousel-wrapper">
        {canScrollLeft && (
          <button
            className="carousel-arrow carousel-arrow--left"
            onClick={() => scroll(-1)}
            aria-label="Scroll left"
          >
            ←
          </button>
        )}

        <div className="carousel-track" ref={scrollRef}>
          {products.map((p) => (
            <Link
              to={`/product/${p.zoro_code}`}
              className="carousel-card"
              key={p.zoro_code}
            >
              <div className="carousel-card__image-wrap">
                <img
                  src={p.image_url || "/placeholder.svg"}
                  alt={p.title}
                  className="carousel-card__image"
                  loading="lazy"
                  onError={(e) => { e.target.style.opacity = "0.3"; }}
                />
              </div>
              <div className="carousel-card__body">
                {p.brand && (
                  <span className="carousel-card__brand">{p.brand}</span>
                )}
                <h3 className="carousel-card__title">{p.title}</h3>
                <div className="carousel-card__price-row">
                  <span className="carousel-card__price">
                    ${formatPrice(p.unit_price)}
                  </span>
                  {p.uom && (
                    <span className="carousel-card__uom">/{p.uom.toLowerCase()}</span>
                  )}
                </div>
                {p.lead_time && (
                  <span className="carousel-card__delivery">
                    {p.lead_time}
                  </span>
                )}
                <button
                  className="carousel-card__btn"
                  onClick={(e) => handleAdd(e, p)}
                >
                  Add to Cart
                </button>
              </div>
            </Link>
          ))}
        </div>

        {canScrollRight && (
          <button
            className="carousel-arrow carousel-arrow--right"
            onClick={() => scroll(1)}
            aria-label="Scroll right"
          >
            →
          </button>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="carousel-pagination">
          <span>{currentPage} of {totalPages}</span>
        </div>
      )}
    </div>
  );
}

export default ProductCarousel;
