import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import MobileMenu from "./MobileMenu";
import NotificationsBell from "./NotificationsBell";
import { SearchIcon, CartIcon, UserIcon, RowsIcon, TruckIcon, CreditCardIcon, ShieldIcon } from "./Icons";

const CATEGORIES = [
  "Tools", "Safety", "Electrical", "Lighting", "Plumbing", "Janitorial",
];

function Header() {
  const [query, setQuery] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const { isAuthenticated, user, logout } = useAuth();
  const { itemCount } = useCart();

  const handleSearch = (e) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (trimmed) {
      navigate(`/search?q=${encodeURIComponent(trimmed)}`);
    }
  };

  const handleLogout = () => {
    logout();
    navigate("/");
  };

  return (
    <>
      {/* Utility strip */}
      <div className="topbar">
        <div className="container topbar__inner">
          <span className="topbar__item">
            <TruckIcon size={13} /> Free shipping on orders $50+
          </span>
          <span className="topbar__item topbar__item--hide-sm">
            <CreditCardIcon size={13} /> Net 30 terms available
          </span>
          <span className="topbar__spacer" />
          <Link to="/contact" className="topbar__link">Help Center</Link>
          <Link to="/shipping" className="topbar__link">Shipping</Link>
          <Link to="/returns" className="topbar__link">Returns</Link>
        </div>
      </div>

      <header className="header" id="site-header">
        <div className="container header__inner">

          {/* Hamburger — mobile only */}
          <button
            className="header__hamburger"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            id="hamburger-btn"
          >
            <span /><span /><span />
          </button>

          <Link to="/" className="header__logo">
            <div className="header__logo-icon">Z</div>
            <span className="header__logo-text">
              ZoroStore
              <span className="header__logo-tag">Industrial Supply</span>
            </span>
          </Link>

          <form className="header__search" onSubmit={handleSearch}>
            <input
              id="search-input"
              type="text"
              className="header__search-input"
              placeholder="Search by product, brand, SKU or part number…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="submit"
              className="header__search-btn"
              aria-label="Search"
            >
              <SearchIcon size={17} />
            </button>
          </form>

          <nav className="header__nav">
            <Link to="/bulk-order" className="header__bulk-link" id="bulk-order-link">
              <RowsIcon size={17} />
              <span className="header__bulk-text">
                Bulk Order<em>Multi-address pad</em>
              </span>
            </Link>

            <NotificationsBell />

            {isAuthenticated ? (
              <div className="header__account">
                {user?.is_admin && (
                  <Link to="/admin" className="header__nav-link header__nav-link--admin" id="admin-link">
                    <ShieldIcon size={15} /> Admin
                  </Link>
                )}
                <Link to="/account" className="header__nav-link header__nav-link--user">
                  <UserIcon size={16} /> {user?.first_name}
                </Link>
                <button onClick={handleLogout} className="header__nav-link header__nav-btn">
                  Sign Out
                </button>
              </div>
            ) : (
              <div className="header__account">
                <Link to="/login" className="header__nav-link header__nav-link--user">
                  <UserIcon size={16} /> Sign In
                </Link>
                <Link to="/register" className="header__nav-link">Register</Link>
              </div>
            )}

            <Link to="/cart" className="header__cart-btn" id="cart-btn">
              <CartIcon size={17} />
              <span className="header__cart-text">Cart</span>
              {itemCount > 0 && (
                <span className="header__cart-badge" id="cart-badge">{itemCount}</span>
              )}
            </Link>
          </nav>
        </div>

        {/* Category subnav */}
        <div className="subnav">
          <div className="container subnav__inner">
            <NavLink to="/" end className="subnav__link">
              All Products
            </NavLink>
            {CATEGORIES.map((c) => (
              <Link key={c} to={`/search?q=${c}`} className="subnav__link">
                {c}
              </Link>
            ))}
            <span className="subnav__spacer" />
            <Link to="/bulk-order" className="subnav__link subnav__link--bulk">
              <RowsIcon size={13} /> Bulk Order Pad
            </Link>
          </div>
        </div>
      </header>

      <MobileMenu isOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
    </>
  );
}

export default Header;
