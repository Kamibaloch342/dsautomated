import { createContext, useContext, useState, useCallback, useMemo } from "react";

/* Compare list for listings — up to 4 products side-by-side. */

const MAX_COMPARE = 4;
const CompareContext = createContext(null);

function loadCompare() {
  try {
    const raw = sessionStorage.getItem("compare_items");
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function persist(items) {
  try {
    sessionStorage.setItem("compare_items", JSON.stringify(items));
  } catch {
    /* storage unavailable — compare still works in-memory */
  }
}

export function CompareProvider({ children }) {
  const [items, setItems] = useState(loadCompare);
  const [isOpen, setIsOpen] = useState(false);
  const [limitHit, setLimitHit] = useState(false);

  const toggle = useCallback((product) => {
    setItems((prev) => {
      const exists = prev.some((p) => p.zoro_code === product.zoro_code);
      let next;
      if (exists) {
        next = prev.filter((p) => p.zoro_code !== product.zoro_code);
      } else {
        if (prev.length >= MAX_COMPARE) {
          setLimitHit(true);
          setTimeout(() => setLimitHit(false), 2500);
          return prev;
        }
        next = [
          ...prev,
          {
            zoro_code: product.zoro_code,
            title: product.title,
            brand: product.brand,
            unit_price: product.unit_price,
            image_url: product.image_url,
          },
        ];
      }
      persist(next);
      return next;
    });
  }, []);

  const remove = useCallback((zoroCode) => {
    setItems((prev) => {
      const next = prev.filter((p) => p.zoro_code !== zoroCode);
      persist(next);
      if (next.length < 2) setIsOpen(false);
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setItems([]);
    persist([]);
    setIsOpen(false);
  }, []);

  const isCompared = useCallback(
    (zoroCode) => items.some((p) => p.zoro_code === zoroCode),
    [items]
  );

  const value = useMemo(
    () => ({
      items,
      count: items.length,
      max: MAX_COMPARE,
      limitHit,
      isOpen,
      openCompare: () => setIsOpen(true),
      closeCompare: () => setIsOpen(false),
      toggle,
      remove,
      clear,
      isCompared,
    }),
    [items, limitHit, isOpen, toggle, remove, clear, isCompared]
  );

  return (
    <CompareContext.Provider value={value}>{children}</CompareContext.Provider>
  );
}

export function useCompare() {
  const ctx = useContext(CompareContext);
  if (!ctx) throw new Error("useCompare must be used within CompareProvider");
  return ctx;
}
