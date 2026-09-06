import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { CartIcon, CreditCardIcon } from "../components/Icons";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5001";
const money = (v) => `$${parseFloat(v || 0).toFixed(2)}`;

function CheckoutPage() {
  const { items, total, clearCart } = useCart();
  const { user, token } = useAuth();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    ship_first_name: user?.first_name || "",
    ship_last_name: user?.last_name || "",
    ship_email: user?.email || "",
    ship_phone: user?.phone || "",
    ship_address1: user?.address_line1 || "",
    ship_address2: user?.address_line2 || "",
    ship_city: user?.city || "",
    ship_state: user?.state || "",
    ship_zip: user?.zip_code || "",
    billing_same_as_shipping: true,
    bill_first_name: "",
    bill_last_name: "",
    bill_phone: "",
    bill_address1: "",
    bill_address2: "",
    bill_city: "",
    bill_state: "",
    bill_zip: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState("card_demo");
  const [net30, setNet30] = useState(null); // {status, available, limit}

  // Net 30 availability for the payment selector
  useEffect(() => {
    if (!token) return;
    fetch(`${API_BASE}/api/net30/status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setNet30(d))
      .catch(() => {});
  }, [token]);

  // Prefill shipping from the profile once it loads (e.g. after a hard
  // refresh the user arrives async) — only fills fields left blank.
  useEffect(() => {
    if (!user) return;
    setForm((prev) => ({
      ...prev,
      ship_first_name: prev.ship_first_name || user.first_name || "",
      ship_last_name: prev.ship_last_name || user.last_name || "",
      ship_email: prev.ship_email || user.email || "",
      ship_phone: prev.ship_phone || user.phone || "",
      ship_address1: prev.ship_address1 || user.address_line1 || "",
      ship_address2: prev.ship_address2 || user.address_line2 || "",
      ship_city: prev.ship_city || user.city || "",
      ship_state: prev.ship_state || user.state || "",
      ship_zip: prev.ship_zip || user.zip_code || "",
    }));
  }, [user]);

  const formatPrice = (p) => parseFloat(p || 0).toFixed(2);
  const shipping = total >= 50 ? 0 : 7.99;
  const tax = +(total * 0.08).toFixed(2);
  const grandTotal = +(total + shipping + tax).toFixed(2);

  const update = (field) => (e) =>
    setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;

      const res = await fetch(`${API_BASE}/api/orders`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          ...form,
          payment_method: paymentMethod,
          items: items.map((i) => ({
            zoro_code: i.zoro_code,
            title: i.title,
            unit_price: i.unit_price,
            quantity: i.quantity,
          })),
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Order failed");

      clearCart();
      navigate(`/order-confirmation/${data.order.order_id}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (items.length === 0) {
    return (
      <main className="page-enter">
        <div className="container cart-empty">
          <div className="empty-state">
            <div className="empty-state__icon"><CartIcon size={30} /></div>
            <h2 className="empty-state__title">Nothing to checkout</h2>
            <p className="empty-state__message">
              Your cart is empty. Add some items first.
            </p>
            <Link to="/" className="btn btn--accent" style={{ marginTop: 16 }}>
              Browse Products
            </Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="page-enter">
      <section className="checkout-page" id="checkout-page">
        <div className="container">
          <h1 className="checkout-page__title">Checkout</h1>

          {error && (
            <div className="auth-card__error" style={{ marginBottom: 24 }}>
              {error}
            </div>
          )}

          <div className="checkout-layout">
            {/* Shipping Form */}
            <form
              className="checkout-form"
              onSubmit={handleSubmit}
              id="checkout-form"
            >
              <h2 className="checkout-form__section-title">
                Shipping Information
              </h2>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ship-first" className="form-label">
                    First Name *
                  </label>
                  <input
                    id="ship-first"
                    type="text"
                    className="form-input"
                    value={form.ship_first_name}
                    onChange={update("ship_first_name")}
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ship-last" className="form-label">
                    Last Name *
                  </label>
                  <input
                    id="ship-last"
                    type="text"
                    className="form-input"
                    value={form.ship_last_name}
                    onChange={update("ship_last_name")}
                    required
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ship-email" className="form-label">
                    Email *
                  </label>
                  <input
                    id="ship-email"
                    type="email"
                    className="form-input"
                    value={form.ship_email}
                    onChange={update("ship_email")}
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ship-phone" className="form-label">
                    Phone
                  </label>
                  <input
                    id="ship-phone"
                    type="tel"
                    className="form-input"
                    value={form.ship_phone}
                    onChange={update("ship_phone")}
                  />
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="ship-addr1" className="form-label">
                  Address Line 1 *
                </label>
                <input
                  id="ship-addr1"
                  type="text"
                  className="form-input"
                  value={form.ship_address1}
                  onChange={update("ship_address1")}
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="ship-addr2" className="form-label">
                  Address Line 2
                </label>
                <input
                  id="ship-addr2"
                  type="text"
                  className="form-input"
                  value={form.ship_address2}
                  onChange={update("ship_address2")}
                />
              </div>

              <div className="form-row form-row--3">
                <div className="form-group">
                  <label htmlFor="ship-city" className="form-label">
                    City *
                  </label>
                  <input
                    id="ship-city"
                    type="text"
                    className="form-input"
                    value={form.ship_city}
                    onChange={update("ship_city")}
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ship-state" className="form-label">
                    State *
                  </label>
                  <input
                    id="ship-state"
                    type="text"
                    className="form-input"
                    placeholder="e.g. CA"
                    value={form.ship_state}
                    onChange={update("ship_state")}
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ship-zip" className="form-label">
                    ZIP Code *
                  </label>
                  <input
                    id="ship-zip"
                    type="text"
                    className="form-input"
                    value={form.ship_zip}
                    onChange={update("ship_zip")}
                    required
                  />
                </div>
              </div>

              {/* ── Billing address ── */}
              <h2 className="checkout-form__section-title checkout-form__section-title--billing">
                Billing Address
              </h2>

              <label className="billing-toggle" htmlFor="billing-same">
                <input
                  id="billing-same"
                  type="checkbox"
                  checked={form.billing_same_as_shipping}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      billing_same_as_shipping: e.target.checked,
                    }))
                  }
                />
                <span>Billing address is the same as shipping</span>
              </label>

              {!form.billing_same_as_shipping && (
                <div className="billing-fields" id="billing-fields">
                  <div className="form-row">
                    <div className="form-group">
                      <label htmlFor="bill-first" className="form-label">
                        First Name *
                      </label>
                      <input
                        id="bill-first"
                        type="text"
                        className="form-input"
                        value={form.bill_first_name}
                        onChange={update("bill_first_name")}
                        required
                      />
                    </div>
                    <div className="form-group">
                      <label htmlFor="bill-last" className="form-label">
                        Last Name *
                      </label>
                      <input
                        id="bill-last"
                        type="text"
                        className="form-input"
                        value={form.bill_last_name}
                        onChange={update("bill_last_name")}
                        required
                      />
                    </div>
                  </div>

                  <div className="form-row">
                    <div className="form-group">
                      <label htmlFor="bill-addr1" className="form-label">
                        Address Line 1 *
                      </label>
                      <input
                        id="bill-addr1"
                        type="text"
                        className="form-input"
                        value={form.bill_address1}
                        onChange={update("bill_address1")}
                        required
                      />
                    </div>
                    <div className="form-group">
                      <label htmlFor="bill-addr2" className="form-label">
                        Address Line 2
                      </label>
                      <input
                        id="bill-addr2"
                        type="text"
                        className="form-input"
                        value={form.bill_address2}
                        onChange={update("bill_address2")}
                      />
                    </div>
                  </div>

                  <div className="form-row form-row--3">
                    <div className="form-group">
                      <label htmlFor="bill-city" className="form-label">
                        City *
                      </label>
                      <input
                        id="bill-city"
                        type="text"
                        className="form-input"
                        value={form.bill_city}
                        onChange={update("bill_city")}
                        required
                      />
                    </div>
                    <div className="form-group">
                      <label htmlFor="bill-state" className="form-label">
                        State *
                      </label>
                      <input
                        id="bill-state"
                        type="text"
                        className="form-input"
                        placeholder="e.g. CA"
                        value={form.bill_state}
                        onChange={update("bill_state")}
                        required
                      />
                    </div>
                    <div className="form-group">
                      <label htmlFor="bill-zip" className="form-label">
                        ZIP Code *
                      </label>
                      <input
                        id="bill-zip"
                        type="text"
                        className="form-input"
                        value={form.bill_zip}
                        onChange={update("bill_zip")}
                        required
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label htmlFor="bill-phone" className="form-label">
                      Billing Phone
                    </label>
                    <input
                      id="bill-phone"
                      type="tel"
                      className="form-input"
                      value={form.bill_phone}
                      onChange={update("bill_phone")}
                    />
                  </div>
                </div>
              )}

              {/* ── Payment method ── */}
              <h2 className="checkout-form__section-title checkout-form__section-title--billing">
                Payment Method
              </h2>

              <div className="pay-methods" id="payment-methods">
                <label className={`pay-method ${paymentMethod === "card_demo" ? "pay-method--on" : ""}`}>
                  <input
                    type="radio"
                    name="payment"
                    checked={paymentMethod === "card_demo"}
                    onChange={() => setPaymentMethod("card_demo")}
                  />
                  <CreditCardIcon size={17} />
                  <span className="pay-method__text">
                    <strong>Card (demo)</strong>
                    <em>No real charge — demo checkout</em>
                  </span>
                </label>

                <label
                  className={`pay-method ${paymentMethod === "net30" ? "pay-method--on" : ""} ${net30?.status !== "approved" ? "pay-method--disabled" : ""}`}
                  title={net30?.status !== "approved" ? "Requires an approved Net 30 account" : ""}
                >
                  <input
                    type="radio"
                    name="payment"
                    disabled={net30?.status !== "approved"}
                    checked={paymentMethod === "net30"}
                    onChange={() => setPaymentMethod("net30")}
                  />
                  <CreditCardIcon size={17} />
                  <span className="pay-method__text">
                    <strong>Net 30 (invoice)</strong>
                    <em>
                      {net30?.status === "approved"
                        ? `Available credit ${money(net30.available)}`
                        : net30?.status === "pending"
                          ? "Application under review"
                          : token
                            ? <>Not approved — <Link to="/account?tab=net30">apply here</Link></>
                            : "Sign in to use Net 30 terms"}
                    </em>
                  </span>
                </label>
              </div>

              <div className="checkout-form__note">
                A confirmation email is sent to your inbox
                {paymentMethod === "net30" && ", along with a Net 30 invoice"},
                and a shipment email with tracking follows automatically.
              </div>

              <button
                type="submit"
                className="btn btn--accent checkout-form__submit"
                disabled={loading}
                id="place-order-btn"
              >
                {loading ? "Placing Order..." : `Place Order — $${formatPrice(grandTotal)}`}
              </button>
            </form>

            {/* Order Summary */}
            <aside className="cart-summary" id="checkout-summary">
              <h3 className="cart-summary__title">
                Order Summary ({items.length} item
                {items.length !== 1 ? "s" : ""})
              </h3>

              <div className="checkout-items">
                {items.map((item) => (
                  <div className="checkout-item" key={item.zoro_code}>
                    <img
                      src={item.image_url || "/placeholder.svg"}
                      alt={item.title}
                      className="checkout-item__img"
                    />
                    <div className="checkout-item__info">
                      <span className="checkout-item__title">{item.title}</span>
                      <span className="checkout-item__qty">
                        Qty: {item.quantity}
                      </span>
                    </div>
                    <span className="checkout-item__price">
                      ${formatPrice(item.unit_price * item.quantity)}
                    </span>
                  </div>
                ))}
              </div>

              <div className="cart-summary__divider" />
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
                <span>Tax</span>
                <span>${formatPrice(tax)}</span>
              </div>
              <div className="cart-summary__divider" />
              <div className="cart-summary__row cart-summary__row--total">
                <span>Total</span>
                <span>${formatPrice(grandTotal)}</span>
              </div>
            </aside>
          </div>
        </div>
      </section>
    </main>
  );
}

export default CheckoutPage;
