import { useRef, useState, useEffect } from "react";
import { Link } from "react-router-dom";

/**
 * CategoriesExplore — "More categories to explore" horizontal scrollable section
 * 
 * Props:
 *   categories — Array of { category_id, category_name, product_count }
 */

// Category emoji/icon mapping for visual appeal
const CATEGORY_ICONS = {
  "safety": "🦺",
  "lighting": "💡",
  "tools": "🔧",
  "electrical": "⚡",
  "plumbing": "🔩",
  "cleaning": "🧹",
  "hardware": "🔨",
  "paint": "🎨",
  "hvac": "❄️",
  "welding": "🔥",
  "outdoor": "🌳",
  "fasteners": "🔩",
  "abrasives": "⚙️",
  "adhesives": "🧴",
  "test": "📏",
  "power tools": "⚡",
  "hand tools": "🛠️",
  "gloves": "🧤",
  "tape": "📐",
  "drill": "🔩",
  "light bulbs": "💡",
  "led": "💡",
  "first aid": "🏥",
  "fire": "🔥",
  "extension": "🔌",
  "bleach": "🧪",
  "measuring": "📏",
  "personal protective": "🦺",
  "cut-resistant": "🧤",
  "impact": "🔨",
  "socket": "🔧",
};

function getIcon(name) {
  const lower = name.toLowerCase();
  for (const [key, icon] of Object.entries(CATEGORY_ICONS)) {
    if (lower.includes(key)) return icon;
  }
  return "📦";
}

function CategoriesExplore({ categories = [] }) {
  const scrollRef = useRef(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScrollState = () => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 10);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 10);
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
  }, [categories]);

  const scroll = (direction) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * 300, behavior: "smooth" });
  };

  if (!categories || categories.length === 0) return null;

  return (
    <div className="categories-explore">
      <h2 className="categories-explore__title">More categories to explore</h2>

      <div className="categories-explore__wrapper">
        {canScrollLeft && (
          <button
            className="carousel-arrow carousel-arrow--left"
            onClick={() => scroll(-1)}
            aria-label="Scroll categories left"
          >
            ←
          </button>
        )}

        <div className="categories-explore__track" ref={scrollRef}>
          {categories.map((cat) => (
            <Link
              to={`/search?q=${encodeURIComponent(cat.category_name)}`}
              className="categories-explore__item"
              key={cat.category_id}
            >
              <div className="categories-explore__icon">
                {getIcon(cat.category_name)}
              </div>
              <span className="categories-explore__name">
                {cat.category_name}
              </span>
            </Link>
          ))}
        </div>

        {canScrollRight && (
          <button
            className="carousel-arrow carousel-arrow--right"
            onClick={() => scroll(1)}
            aria-label="Scroll categories right"
          >
            →
          </button>
        )}
      </div>
    </div>
  );
}


/**
 * TrustBadges — "3 reasons you can count on us"
 */
export function TrustBadges() {
  return (
    <div className="trust-badges">
      <h2 className="trust-badges__title">3 reasons you can count on us</h2>
      <div className="trust-badges__grid">
        <div className="trust-badges__item">
          <div className="trust-badges__icon">🔧</div>
          <div className="trust-badges__text">
            <strong>Supplies for every job.</strong>
            <span>10 million items and the exact one you need.</span>
          </div>
        </div>
        <div className="trust-badges__item">
          <div className="trust-badges__icon">🚚</div>
          <div className="trust-badges__text">
            <strong>When you need it fast, count on Zoro!</strong>
            <span>2 million products ship in 1 day.</span>
          </div>
        </div>
        <div className="trust-badges__item">
          <div className="trust-badges__icon">💰</div>
          <div className="trust-badges__text">
            <strong>Stretch your budget further.</strong>
            <span>Everyday low prices on the brands you love.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CategoriesExplore;
