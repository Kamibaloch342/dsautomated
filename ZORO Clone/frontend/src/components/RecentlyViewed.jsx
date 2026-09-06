import { useState, useEffect } from "react";
import ProductCarousel from "./ProductCarousel";

const STORAGE_KEY = "zoro_recently_viewed";
const MAX_ITEMS = 20;

/**
 * Track a product view in localStorage.
 * Call this from ProductPage on mount.
 */
export function trackProductView(product) {
  if (!product || !product.zoro_code) return;

  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");

    // Remove if already exists (we'll re-add at front)
    const filtered = stored.filter((p) => p.zoro_code !== product.zoro_code);

    // Add to front
    filtered.unshift({
      zoro_code: product.zoro_code,
      title: product.title,
      brand: product.brand,
      unit_price: product.unit_price,
      image_url: product.image_url,
      uom: product.uom,
      lead_time: product.lead_time,
      mfr_number: product.mfr_number,
      availability: product.availability,
      category_name: product.category_name,
    });

    // Trim to max
    const trimmed = filtered.slice(0, MAX_ITEMS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // localStorage full or unavailable — silently ignore
  }
}

/**
 * RecentlyViewed — Shows a carousel of recently viewed products.
 * Excludes the current product (pass its zoro_code as excludeCode).
 */
function RecentlyViewed({ excludeCode }) {
  const [products, setProducts] = useState([]);

  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      // Exclude the current product
      const filtered = stored.filter((p) => p.zoro_code !== excludeCode);
      setProducts(filtered);
    } catch {
      setProducts([]);
    }
  }, [excludeCode]);

  if (products.length === 0) return null;

  // Build shop links from unique category names
  const uniqueCategories = [...new Set(
    products
      .map((p) => p.category_name)
      .filter(Boolean)
  )].slice(0, 3);

  const shopLinks = uniqueCategories.map((name) => ({
    label: `Shop ${name}`,
    to: `/search?q=${encodeURIComponent(name)}`,
  }));

  return (
    <ProductCarousel
      title="Recently viewed and more"
      products={products}
      shopLinks={shopLinks}
      seeAllTo="/"
    />
  );
}

export default RecentlyViewed;
