import { createContext, useContext, useState, useEffect } from "react";
import { api } from "../lib/api";

// Loads the L1/L2 category tree (with counts + sample images) once and
// shares it with the header mega-menu, mobile drawer, home page and
// listing sidebar. Also tracks whether the catalog index is still building.
const CatalogContext = createContext(null);

export function CatalogProvider({ children }) {
  const [categories, setCategories] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | building | error

  useEffect(() => {
    let cancelled = false;
    let retryTimer = null;

    const load = async () => {
      try {
        const data = await api("/api/categories");
        if (cancelled) return;
        setCategories(data.categories || []);
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        if (err.status === 503) {
          setStatus("building");
          retryTimer = setTimeout(load, 15000);
        } else {
          setStatus("error");
        }
      }
    };
    load();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  return (
    <CatalogContext.Provider value={{ categories, status }}>
      {children}
    </CatalogContext.Provider>
  );
}

export function useCatalog() {
  const ctx = useContext(CatalogContext);
  if (!ctx) throw new Error("useCatalog must be used within CatalogProvider");
  return ctx;
}
