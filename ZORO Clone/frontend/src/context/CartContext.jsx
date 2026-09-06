import { createContext, useContext, useState, useEffect, useCallback, useMemo } from "react";

const CartContext = createContext(null);

function loadCart() {
  try {
    const raw = localStorage.getItem("cart");
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function CartProvider({ children }) {
  const [items, setItems] = useState(loadCart);
  // Last product added — drives the "added to cart, please check your
  // cart" toast. Unique id per add so repeat adds re-trigger the toast.
  const [lastAdded, setLastAdded] = useState(null);

  // Persist to localStorage on every change
  useEffect(() => {
    localStorage.setItem("cart", JSON.stringify(items));
  }, [items]);

  const addItem = useCallback((product, qty = 1) => {
    setItems((prev) => {
      const idx = prev.findIndex((i) => i.zoro_code === product.zoro_code);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = { ...updated[idx], quantity: updated[idx].quantity + qty };
        return updated;
      }
      return [
        ...prev,
        {
          zoro_code: product.zoro_code,
          title: product.title,
          brand: product.brand,
          unit_price: product.unit_price,
          image_url: product.image_url,
          availability: product.availability,
          quantity: qty,
        },
      ];
    });
    setLastAdded({
      id: Date.now(),
      zoro_code: product.zoro_code,
      title: product.title,
      image_url: product.image_url,
      unit_price: product.unit_price,
      quantity: qty,
    });
  }, []);

  const dismissToast = useCallback(() => setLastAdded(null), []);

  const removeItem = useCallback((zoroCode) => {
    setItems((prev) => prev.filter((i) => i.zoro_code !== zoroCode));
  }, []);

  const updateQuantity = useCallback((zoroCode, qty) => {
    if (qty < 1) return;
    setItems((prev) =>
      prev.map((i) => (i.zoro_code === zoroCode ? { ...i, quantity: qty } : i))
    );
  }, []);

  const clearCart = useCallback(() => {
    setItems([]);
  }, []);

  const itemCount = useMemo(
    () => items.reduce((sum, i) => sum + i.quantity, 0),
    [items]
  );

  const total = useMemo(
    () => items.reduce((sum, i) => sum + (i.unit_price || 0) * i.quantity, 0),
    [items]
  );

  const value = {
    items,
    itemCount,
    total,
    addItem,
    removeItem,
    updateQuantity,
    clearCart,
    lastAdded,
    dismissToast,
  };

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used within CartProvider");
  return ctx;
}
