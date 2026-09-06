import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { CheckCircleIcon, XIcon, CartIcon } from "./Icons";

const AUTO_HIDE_MS = 6000;

/* "Added to cart — please check your cart" confirmation, shown
   globally whenever any Add to Cart button is pressed. */
function CartToast() {
  const { lastAdded, dismissToast, itemCount } = useCart();
  const [leaving, setLeaving] = useState(false);
  const timerRef = useRef(null);
  const location = useLocation();

  // Auto-hide after a few seconds
  useEffect(() => {
    if (!lastAdded) return;
    setLeaving(false);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setLeaving(true), AUTO_HIDE_MS);
    return () => clearTimeout(timerRef.current);
  }, [lastAdded]);

  // Once the exit animation finishes, unmount
  useEffect(() => {
    if (!leaving) return;
    const t = setTimeout(dismissToast, 250);
    return () => clearTimeout(t);
  }, [leaving, dismissToast]);

  // Hide immediately if the user navigates to the cart
  useEffect(() => {
    if (location.pathname === "/cart") dismissToast();
  }, [location.pathname, dismissToast]);

  if (!lastAdded) return null;

  const formatPrice = (p) => (p || p === 0 ? parseFloat(p).toFixed(2) : "—");

  return (
    <div
      className={`cart-toast ${leaving ? "cart-toast--leaving" : ""}`}
      role="status"
      aria-live="polite"
      id="cart-toast"
    >
      <div className="cart-toast__top">
        <span className="cart-toast__check">
          <CheckCircleIcon size={20} />
        </span>
        <span className="cart-toast__heading">Added to cart</span>
        <button
          className="cart-toast__close"
          onClick={() => setLeaving(true)}
          aria-label="Dismiss"
        >
          <XIcon size={16} />
        </button>
      </div>

      <div className="cart-toast__body">
        <img
          className="cart-toast__img"
          src={lastAdded.image_url || "/placeholder.svg"}
          alt=""
          onError={(e) => { e.target.style.opacity = "0.25"; }}
        />
        <div className="cart-toast__info">
          <span className="cart-toast__title">{lastAdded.title}</span>
          <span className="cart-toast__meta">
            Qty {lastAdded.quantity} · ${formatPrice(lastAdded.unit_price)} each
          </span>
        </div>
      </div>

      <p className="cart-toast__note">
        Please check your cart to review your items before checkout.
      </p>

      <div className="cart-toast__actions">
        <Link
          to="/cart"
          className="btn btn--accent cart-toast__view"
          onClick={() => setLeaving(true)}
          id="toast-view-cart"
        >
          <CartIcon size={16} /> Check Cart ({itemCount})
        </Link>
        <button
          className="btn btn--secondary"
          onClick={() => setLeaving(true)}
        >
          Keep Shopping
        </button>
      </div>
    </div>
  );
}

export default CartToast;
