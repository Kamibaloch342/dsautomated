import { useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { money, imgUrl, handleImgError } from "../lib/api";
import { BRAND, formatEqCode } from "../lib/brand";
import {
  CartIcon, XIcon, TrashIcon, ArrowRight, TruckIcon,
  PlusIcon, MinusIcon, ShieldIcon, CheckCircleIcon,
} from "./Icons";

export default function SideCartDrawer() {
  const {
    items,
    itemCount,
    total,
    removeItem,
    updateQuantity,
    isCartDrawerOpen,
    closeCartDrawer,
  } = useCart();

  const navigate = useNavigate();
  const drawerRef = useRef(null);

  // Close on Escape key
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === "Escape" && isCartDrawerOpen) {
        closeCartDrawer();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isCartDrawerOpen, closeCartDrawer]);

  // Lock body scroll when drawer is open
  useEffect(() => {
    if (isCartDrawerOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isCartDrawerOpen]);

  if (!isCartDrawerOpen) return null;

  const FREE_SHIPPING_THRESHOLD = 50;
  const remainingForFree = Math.max(0, FREE_SHIPPING_THRESHOLD - total);
  const freeProgress = Math.min(100, Math.round((total / FREE_SHIPPING_THRESHOLD) * 100));

  const handleCheckout = () => {
    closeCartDrawer();
    navigate("/checkout");
  };

  return (
    <div className="fixed inset-0 z-[100] flex justify-end p-0 sm:p-4 pointer-events-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
        onClick={closeCartDrawer}
      />

      {/* macOS Genie Popover Card */}
      <aside
        ref={drawerRef}
        className="genie-cart-modal relative w-full sm:max-w-md bg-white h-full sm:h-auto sm:max-h-[88vh] rounded-none sm:rounded-2xl shadow-2xl flex flex-col z-10 overflow-hidden border border-slate-200/80 sm:mt-14"
        aria-label="Shopping Cart Drawer"
      >
        {/* Header */}
        <div className="p-4 border-b border-slate-200 flex items-center justify-between bg-slate-50">
          <div className="flex items-center gap-2">
            <CartIcon size={20} className="text-brand-teal" />
            <h2 className="text-base font-bold text-slate-900 m-0">
              Your Cart ({itemCount} {itemCount === 1 ? "item" : "items"})
            </h2>
          </div>
          <button
            type="button"
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-200/60 rounded-full transition-colors"
            onClick={closeCartDrawer}
            aria-label="Close cart drawer"
          >
            <XIcon size={18} />
          </button>
        </div>

        {/* Free Shipping Progress Bar */}
        <div className="bg-emerald-50/70 border-b border-emerald-100 px-4 py-2.5">
          <div className="flex items-center justify-between text-xs font-semibold mb-1">
            <span className="flex items-center gap-1.5 text-emerald-900">
              <TruckIcon size={14} className="text-emerald-700" />
              {remainingForFree <= 0 ? (
                <span>🎉 <strong>FREE Shipping</strong> unlocked!</span>
              ) : (
                <span>Add <strong>{money(remainingForFree)}</strong> more for <strong>FREE Shipping</strong></span>
              )}
            </span>
            <span className="text-[11px] font-bold text-emerald-700">{freeProgress}%</span>
          </div>
          <div className="w-full h-1.5 bg-emerald-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-600 rounded-full transition-all duration-500"
              style={{ width: `${freeProgress}%` }}
            />
          </div>
        </div>

        {/* Items List */}
        <div className="flex-1 overflow-y-auto p-4 divide-y divide-slate-100">
          {items.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-500">
              <div className="w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center mb-3 text-slate-400">
                <CartIcon size={32} />
              </div>
              <h3 className="text-base font-bold text-slate-800 mb-1">Your cart is empty</h3>
              <p className="text-xs text-slate-500 mb-4 max-w-xs">
                Looks like you haven't added any industrial supplies yet.
              </p>
              <button
                type="button"
                className="z-btn z-btn--primary text-xs py-2 px-4"
                onClick={closeCartDrawer}
              >
                Continue Shopping
              </button>
            </div>
          ) : (
            items.map((item) => (
              <div key={item.zoro_code} className="py-3.5 first:pt-0 last:pb-0 flex gap-3">
                {/* Product Thumbnail */}
                <Link
                  to={`/product/${item.zoro_code}`}
                  onClick={closeCartDrawer}
                  className="w-16 h-16 rounded-md border border-slate-200 bg-white p-1 flex-shrink-0 flex items-center justify-center overflow-hidden"
                >
                  <img
                    src={imgUrl(item.image_url || `https://media.zoro.com/img/${item.zoro_code}.jpg`)}
                    onError={handleImgError}
                    alt={item.title}
                    className="w-full h-full object-contain"
                  />
                </Link>

                {/* Info & Quantity controls */}
                <div className="flex-1 min-w-0 flex flex-col justify-between">
                  <div>
                    <div className="flex justify-between items-start gap-1">
                      <Link
                        to={`/product/${item.zoro_code}`}
                        onClick={closeCartDrawer}
                        className="text-xs font-semibold text-slate-900 hover:text-brand-orange line-clamp-2 leading-tight"
                      >
                        {item.title}
                      </Link>
                      <button
                        type="button"
                        onClick={() => removeItem(item.zoro_code)}
                        className="text-slate-400 hover:text-rose-600 p-0.5 transition-colors flex-shrink-0"
                        title="Remove item"
                      >
                        <TrashIcon size={14} />
                      </button>
                    </div>

                    <div className="text-[11px] text-slate-500 font-mono mt-0.5">
                      {formatEqCode(item.zoro_code)}
                      {item.brand && <span className="font-sans text-slate-400"> · {item.brand}</span>}
                    </div>
                  </div>

                  {/* Price & Quantity Selector */}
                  <div className="flex items-center justify-between mt-2 pt-1 border-t border-slate-50">
                    {/* Inline Qty Controls */}
                    <div className="inline-flex items-center border border-slate-300 rounded bg-white overflow-hidden shadow-xs">
                      <button
                        type="button"
                        className="w-6 h-6 flex items-center justify-center text-slate-600 hover:bg-slate-100 active:bg-slate-200 disabled:opacity-40"
                        onClick={() => updateQuantity(item.zoro_code, Math.max(1, item.quantity - 1))}
                        disabled={item.quantity <= 1}
                        title="Decrease quantity"
                      >
                        <MinusIcon size={11} />
                      </button>
                      <input
                        type="number"
                        min="1"
                        className="w-10 h-6 text-center text-xs font-bold text-slate-800 border-x border-slate-200 focus:outline-none focus:bg-amber-50"
                        value={item.quantity}
                        onChange={(e) => {
                          const val = parseInt(e.target.value, 10);
                          if (!isNaN(val) && val >= 1) {
                            updateQuantity(item.zoro_code, val);
                          }
                        }}
                      />
                      <button
                        type="button"
                        className="w-6 h-6 flex items-center justify-center text-slate-600 hover:bg-slate-100 active:bg-slate-200"
                        onClick={() => updateQuantity(item.zoro_code, item.quantity + 1)}
                        title="Increase quantity"
                      >
                        <PlusIcon size={11} />
                      </button>
                    </div>

                    {/* Line Total */}
                    <div className="text-right">
                      <div className="text-xs font-bold text-slate-900">
                        {money((item.unit_price || 0) * item.quantity)}
                      </div>
                      {item.quantity > 1 && (
                        <div className="text-[10px] text-slate-400">
                          {money(item.unit_price)} each
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        {items.length > 0 && (
          <div className="p-4 border-t border-slate-200 bg-slate-50 space-y-3">
            <div className="flex justify-between items-baseline">
              <span className="text-xs font-bold text-slate-600 uppercase tracking-wide">
                Subtotal ({itemCount} {itemCount === 1 ? "item" : "items"})
              </span>
              <span className="text-lg font-extrabold text-slate-900">
                {money(total)}
              </span>
            </div>

            <div className="text-[11px] text-slate-500 text-center">
              Shipping & taxes calculated during checkout
            </div>

            <button
              type="button"
              className="z-btn z-btn--primary w-full py-3 text-sm font-bold flex items-center justify-center gap-2 shadow-md hover:shadow-lg transition-all"
              onClick={handleCheckout}
            >
              <span>Proceed to Checkout</span>
              <ArrowRight size={16} />
            </button>

            <div className="text-center">
              <Link
                to="/cart"
                onClick={closeCartDrawer}
                className="text-xs font-semibold text-slate-600 hover:text-brand-orange underline"
              >
                View and edit full cart page
              </Link>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
