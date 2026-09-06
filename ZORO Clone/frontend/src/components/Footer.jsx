import { Link } from "react-router-dom";

function Footer() {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="footer" id="site-footer">
      <div className="container">

        <div className="footer__grid">
          {/* Brand */}
          <div>
            <div className="footer__brand">
              <div className="header__logo-icon">Z</div>
              ZoroStore
            </div>
            <p className="footer__desc">
              Your trusted source for industrial supplies, tools, and equipment.
              Quality products at competitive prices with fast shipping.
            </p>
          </div>

          {/* Products */}
          <div>
            <h4 className="footer__col-title">Shop</h4>
            <ul className="footer__links">
              <li><Link to="/bulk-order">Bulk Order Pad</Link></li>
              <li><Link to="/search?q=Tools">Tools & Hardware</Link></li>
              <li><Link to="/search?q=Safety">Safety Equipment</Link></li>
              <li><Link to="/search?q=Lighting">Lighting</Link></li>
              <li><Link to="/search?q=Electrical">Electrical</Link></li>
              <li><Link to="/search?q=Plumbing">Plumbing</Link></li>
            </ul>
          </div>

          {/* Company */}
          <div>
            <h4 className="footer__col-title">Company</h4>
            <ul className="footer__links">
              <li><Link to="/about">About Us</Link></li>
              <li><Link to="/contact">Contact</Link></li>
              <li><Link to="/about">Careers</Link></li>
              <li><Link to="/about">Blog</Link></li>
            </ul>
          </div>

          {/* Support */}
          <div>
            <h4 className="footer__col-title">Support</h4>
            <ul className="footer__links">
              <li><Link to="/contact">Help Center</Link></li>
              <li><Link to="/shipping">Shipping Info</Link></li>
              <li><Link to="/returns">Returns</Link></li>
              <li><Link to="/account">Track Order</Link></li>
            </ul>
          </div>
        </div>

        <div className="footer__bottom">
          <span>© {currentYear} ZoroStore. All rights reserved.</span>
          <span>Industrial Supplies & Tools</span>
        </div>

      </div>
    </footer>
  );
}

export default Footer;
