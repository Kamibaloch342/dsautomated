import { Link } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { useCompare } from "../context/CompareContext";
import { CheckIcon, CartIcon } from "./Icons";

function ProductCard({ product }) {
  const { addItem } = useCart();
  const { toggle, isCompared } = useCompare();

  const {
    zoro_code,
    title,
    brand,
    unit_price,
    availability,
    image_url,
    mfr_number,
    uom,
    variant_group,
  } = product;

  const isInStock = availability?.toLowerCase().includes("stock")
    || availability?.toLowerCase().includes("in stock");

  const compared = isCompared(zoro_code);

  const formatPrice = (price) => {
    if (!price && price !== 0) return "—";
    return parseFloat(price).toFixed(2);
  };

  const handleAddToCart = (e) => {
    e.preventDefault();
    e.stopPropagation();
    addItem(product, 1);
  };

  const handleCompare = (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggle(product);
  };

  return (
    <Link
      to={`/product/${zoro_code}`}
      className="product-card"
      id={`product-card-${zoro_code}`}
    >
      {/* Image */}
      <div className="product-card__image-wrap">
        <span
          className={`product-card__badge ${
            isInStock
              ? "product-card__badge--stock"
              : "product-card__badge--oos"
          }`}
        >
          {isInStock ? "In Stock" : "Out of Stock"}
        </span>
        <img
          className="product-card__image"
          src={image_url || "/placeholder.svg"}
          alt={title}
          loading="lazy"
          onError={(e) => {
            e.target.style.opacity = "0.3";
          }}
        />
        {product.lead_time && (
          <span className="product-card__lead-time">
            {product.lead_time}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="product-card__body">
        {brand && (
          <span className="product-card__brand">{brand}</span>
        )}

        <h3 className="product-card__title">{title}</h3>

        {variant_group && (
          <span className="product-card__variants">
            More sizes &amp; options available
          </span>
        )}

        <div className="product-card__footer">
          <div>
            <div className="product-card__price">
              <span className="product-card__price-currency">$</span>
              {formatPrice(unit_price)}
              {uom && (
                <span className="product-card__uom">/{uom.toLowerCase()}</span>
              )}
            </div>
            {mfr_number && (
              <span className="product-card__mfr">
                MFR# {mfr_number}
              </span>
            )}
          </div>

          <button
            className="product-card__action"
            onClick={handleAddToCart}
            aria-label={`Add ${title} to cart`}
          >
            <CartIcon size={14} /> Add
          </button>
        </div>

        {/* Compare toggle */}
        <button
          className={`product-card__compare ${compared ? "product-card__compare--on" : ""}`}
          onClick={handleCompare}
          aria-pressed={compared}
          aria-label={`${compared ? "Remove" : "Add"} ${title} ${compared ? "from" : "to"} comparison`}
        >
          <span className="product-card__compare-box">
            {compared && <CheckIcon size={11} strokeWidth={3} />}
          </span>
          Compare
        </button>
      </div>
    </Link>
  );
}

export default ProductCard;
