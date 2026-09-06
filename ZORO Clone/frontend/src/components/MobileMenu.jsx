import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import {
  XIcon, CartIcon, UserIcon, RowsIcon, SearchIcon,
} from "./Icons";

function MobileMenu({ isOpen, onClose }) {
  const { isAuthenticated, user, logout } = useAuth();
  const { itemCount } = useCart();
  const navigate = useNavigate();

  // Close on route change
  useEffect(() => {
    if (isOpen) document.body.style.overflow = "hidden";
    else document.body.style.overflow = "";
    return () => { document.body.style.overflow = ""; };
  }, [isOpen]);

  const go = (path) => {
    navigate(path);
    onClose();
  };

  const handleLogout = () => {
    logout();
    onClose();
    navigate("/");
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className={`mobile-backdrop ${isOpen ? "mobile-backdrop--open" : ""}`}
        onClick={onClose}
      />

      {/* Slide-out menu */}
      <div
        className={`mobile-menu ${isOpen ? "mobile-menu--open" : ""}`}
        id="mobile-menu"
      >
        <div className="mobile-menu__header">
          <span className="mobile-menu__title">Menu</span>
          <button
            className="mobile-menu__close"
            onClick={onClose}
            aria-label="Close menu"
          >
            <XIcon size={18} />
          </button>
        </div>

        <nav className="mobile-menu__nav">
          <button className="mobile-menu__link" onClick={() => go("/")}>
            <SearchIcon size={16} /> Browse Products
          </button>
          <button className="mobile-menu__link mobile-menu__link--bulk" onClick={() => go("/bulk-order")}>
            <RowsIcon size={16} /> Bulk Order Pad
          </button>
          <button className="mobile-menu__link" onClick={() => go("/cart")}>
            <CartIcon size={16} /> Cart {itemCount > 0 && <span className="mobile-menu__badge">{itemCount}</span>}
          </button>

          <div className="mobile-menu__divider" />

          <span className="mobile-menu__section">Categories</span>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/search?q=Lighting")}>
            Lighting
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/search?q=Tools")}>
            Tools & Hardware
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/search?q=Safety")}>
            Safety
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/search?q=Electrical")}>
            Electrical
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/search?q=Plumbing")}>
            Plumbing
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/search?q=Janitorial")}>
            Janitorial
          </button>

          <div className="mobile-menu__divider" />

          {isAuthenticated ? (
            <>
              <button className="mobile-menu__link" onClick={() => go("/account")}>
                <UserIcon size={16} /> My Account ({user?.first_name})
              </button>
              <button className="mobile-menu__link" onClick={handleLogout}>
                Sign Out
              </button>
            </>
          ) : (
            <>
              <button className="mobile-menu__link" onClick={() => go("/login")}>
                <UserIcon size={16} /> Sign In
              </button>
              <button className="mobile-menu__link" onClick={() => go("/register")}>
                Register
              </button>
            </>
          )}

          <div className="mobile-menu__divider" />
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/about")}>
            About Us
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/contact")}>
            Contact
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/shipping")}>
            Shipping Info
          </button>
          <button className="mobile-menu__link mobile-menu__link--sub" onClick={() => go("/returns")}>
            Returns
          </button>
        </nav>
      </div>
    </>
  );
}

export default MobileMenu;
