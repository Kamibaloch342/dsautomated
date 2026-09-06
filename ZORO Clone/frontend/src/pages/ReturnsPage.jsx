import { Link } from "react-router-dom";

function ReturnsPage() {
  return (
    <main className="page-enter">
      <section className="static-page" id="returns-page">
        <div className="container">
          <nav className="breadcrumb">
            <Link to="/">Home</Link>
            <span className="breadcrumb__sep">›</span>
            <span>Returns & Refunds</span>
          </nav>

          <h1 className="static-page__title">Returns & Refunds</h1>

          <div className="static-page__content">
            <h2>30-Day Return Policy</h2>
            <p>
              We accept returns within <strong>30 days</strong> of delivery for
              most items in their original, unused condition and packaging.
            </p>

            <h2>How to Return an Item</h2>
            <ol>
              <li>
                Contact us at{" "}
                <a href="mailto:returns@zorostore.com">returns@zorostore.com</a>{" "}
                with your order number and reason for return.
              </li>
              <li>
                You will receive a prepaid return shipping label via email within
                1–2 business days.
              </li>
              <li>Pack the item securely in its original packaging.</li>
              <li>Drop off the package at any FedEx location.</li>
            </ol>

            <h2>Refund Processing</h2>
            <p>
              Refunds are processed within <strong>5–7 business days</strong>{" "}
              after we receive and inspect the returned item. The refund will be
              issued to the original payment method.
            </p>

            <h2>Non-Returnable Items</h2>
            <ul>
              <li>Custom-ordered or special-order products</li>
              <li>Hazardous materials</li>
              <li>Items that have been used, installed, or modified</li>
              <li>Clearance items marked as final sale</li>
            </ul>

            <h2>Damaged or Defective Items</h2>
            <p>
              If you receive a damaged or defective item, please{" "}
              <Link to="/contact">contact us</Link> within 48 hours of delivery.
              We will arrange a replacement or full refund at no additional cost.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

export default ReturnsPage;
