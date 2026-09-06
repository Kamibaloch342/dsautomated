import { Link } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { CartIcon } from "../components/Icons";

function CartPage() {
  const { items, itemCount, total, removeItem, updateQuantity, clearCart } =
    useCart();

  const formatPrice = (p) => (p ? parseFloat(p).toFixed(2) : "0.00");
  const shipping = total >= 50 ? 0 : 7.99;
  const tax = +(total * 0.08).toFixed(2);
  const grandTotal = +(total + shipping + tax).toFixed(2);

  if (items.length === 0) {
    return (
      <main className="page-enter">
        <div className="container cart-empty" id="cart-empty">
          <div className="empty-state">
            <div className="empty-state__icon"><CartIcon size={30} /></div>
            <h2 className="empty-state__title">Your cart is empty</h2>
            <p className="empty-state__message">
              Browse our products and add items to get started.
            </p>
            <Link to="/" className="btn btn--accent" style={{ marginTop: 16 }}>
              Continue Shopping
            </Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="page-enter">
      <section className="cart-page" id="cart-page">
        <div className="container">
          <h1 className="cart-page__title">
            Shopping Cart
            <span className="cart-page__count">
              ({itemCount} item{itemCount !== 1 ? "s" : ""})
            </span>
          </h1>

          <div className="cart-layout">
            {/* Items */}
            <div className="cart-items">
              {items.map((item) => (
                <div
                  className="cart-item"
                  key={item.zoro_code}
                  id={`cart-item-${item.zoro_code}`}
                >
                  <div className="cart-item__image">
                    <img
                      src={item.image_url || "/placeholder.svg"}
                      alt={item.title}
                      onError={(e) => {
                        e.target.style.opacity = "0.3";
                      }}
                    />
                  </div>
                  <div className="cart-item__info">
                    <Link
                      to={`/product/${item.zoro_code}`}
                      className="cart-item__title"
                    >
                      {item.title}
                    </Link>
                    {item.brand && (
                      <span className="cart-item__brand">{item.brand}</span>
                    )}
                    <span className="cart-item__code">
                      Zoro# {item.zoro_code}
                    </span>
                  </div>
                  <div className="cart-item__qty">
                    <button
                      className="qty-btn"
                      onClick={() =>
                        updateQuantity(item.zoro_code, item.quantity - 1)
                      }
                      disabled={item.quantity <= 1}
                      aria-label="Decrease quantity"
                    >
                      −
                    </button>
                    <span className="qty-value">{item.quantity}</span>
                    <button
                      className="qty-btn"
                      onClick={() =>
                        updateQuantity(item.zoro_code, item.quantity + 1)
                      }
                      aria-label="Increase quantity"
                    >
                      +
                    </button>
                  </div>
                  <div className="cart-item__price">
                    ${formatPrice(item.unit_price * item.quantity)}
                  </div>
                  <button
                    className="cart-item__remove"
                    onClick={() => removeItem(item.zoro_code)}
                    aria-label="Remove item"
                  >
                    ✕
                  </button>
                </div>
              ))}

              <div className="cart-items__actions">
                <button
                  className="btn btn--secondary"
                  onClick={clearCart}
                  id="clear-cart-btn"
                >
                  Clear Cart
                </button>
                <Link to="/" className="btn btn--secondary">
                  Continue Shopping
                </Link>
              </div>
            </div>

            {/* Summary */}
            <aside className="cart-summary" id="cart-summary">
              <h3 className="cart-summary__title">Order Summary</h3>
              <div className="cart-summary__row">
                <span>Subtotal</span>
                <span>${formatPrice(total)}</span>
              </div>
              <div className="cart-summary__row">
                <span>Shipping</span>
                <span>
                  {shipping === 0 ? (
                    <span className="cart-summary__free">FREE</span>
                  ) : (
                    `$${formatPrice(shipping)}`
                  )}
                </span>
              </div>
              <div className="cart-summary__row">
                <span>Estimated Tax</span>
                <span>${formatPrice(tax)}</span>
              </div>
              <div className="cart-summary__divider" />
              <div className="cart-summary__row cart-summary__row--total">
                <span>Total</span>
                <span>${formatPrice(grandTotal)}</span>
              </div>
              {total < 50 && (
                <p className="cart-summary__note">
                  Add ${formatPrice(50 - total)} more for free shipping!
                </p>
              )}
              <Link
                to="/checkout"
                className="btn btn--accent cart-summary__checkout"
                id="checkout-btn"
              >
                Proceed to Checkout
              </Link>
            </aside>
          </div>
        </div>
      </section>
    </main>
  );
}

export default CartPage;
