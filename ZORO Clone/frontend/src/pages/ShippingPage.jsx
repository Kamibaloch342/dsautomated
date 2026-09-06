import { Link } from "react-router-dom";

function ShippingPage() {
  return (
    <main className="page-enter">
      <section className="static-page" id="shipping-page">
        <div className="container">
          <nav className="breadcrumb">
            <Link to="/">Home</Link>
            <span className="breadcrumb__sep">›</span>
            <span>Shipping Information</span>
          </nav>

          <h1 className="static-page__title">Shipping Information</h1>

          <div className="static-page__content">
            <h2>Free Shipping</h2>
            <p>
              Enjoy <strong>free standard shipping</strong> on all orders of $50
              or more. Orders under $50 incur a flat $7.99 shipping fee.
            </p>

            <h2>Shipping Methods</h2>
            <ul>
              <li>
                <strong>Standard Shipping (FedEx Ground):</strong> 3–7 business
                days
              </li>
              <li>
                <strong>Expedited Shipping (FedEx Express):</strong> 1–3
                business days (additional charges apply)
              </li>
              <li>
                <strong>LTL Freight:</strong> For oversized items, delivery
                typically takes 5–10 business days
              </li>
            </ul>

            <h2>Order Processing</h2>
            <p>
              Most in-stock items ship within <strong>24 hours</strong> of order
              placement. Orders placed on weekends or holidays are processed on
              the next business day.
            </p>

            <h2>Tracking Your Order</h2>
            <p>
              Once your order ships, you'll receive a confirmation email with a
              FedEx tracking number. You can track your package at{" "}
              <a
                href="https://www.fedex.com/en-us/tracking.html"
                target="_blank"
                rel="noopener noreferrer"
              >
                fedex.com
              </a>
              .
            </p>

            <h2>Questions?</h2>
            <p>
              Contact our support team at{" "}
              <Link to="/contact">our Contact page</Link> for shipping
              inquiries.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

export default ShippingPage;
