import { Link } from "react-router-dom";

function AboutPage() {
  return (
    <main className="page-enter">
      <section className="static-page" id="about-page">
        <div className="container">
          <nav className="breadcrumb">
            <Link to="/">Home</Link>
            <span className="breadcrumb__sep">›</span>
            <span>About Us</span>
          </nav>

          <h1 className="static-page__title">About ZoroStore</h1>

          <div className="static-page__content">
            <p>
              ZoroStore is your trusted partner for industrial supplies, tools,
              and equipment. We provide businesses and professionals with access
              to over thousands of products from top brands — all at competitive
              prices with fast, reliable shipping.
            </p>

            <h2>Our Mission</h2>
            <p>
              We believe every business deserves easy access to the products they
              need to get the job done. Our mission is to simplify procurement
              and deliver the best value in industrial supplies.
            </p>

            <h2>Why Choose Us?</h2>
            <ul>
              <li>
                <strong>Extensive Selection:</strong> Thousands of products
                across tools, safety, electrical, plumbing, janitorial, and more.
              </li>
              <li>
                <strong>Competitive Pricing:</strong> We work directly with
                manufacturers and distributors to offer the best prices.
              </li>
              <li>
                <strong>Fast Shipping:</strong> Most in-stock items ship within
                24 hours via FedEx.
              </li>
              <li>
                <strong>Expert Support:</strong> Our team is here to help you
                find the right products for your needs.
              </li>
            </ul>

            <h2>Contact Us</h2>
            <p>
              Have questions or need help? Visit our{" "}
              <Link to="/contact">Contact page</Link> or email us at{" "}
              <a href="mailto:support@zorostore.com">support@zorostore.com</a>.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

export default AboutPage;
