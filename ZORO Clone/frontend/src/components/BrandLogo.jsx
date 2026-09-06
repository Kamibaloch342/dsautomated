import { BRAND } from "../lib/brand";
import { LogoMark } from "./Icons";

export default function BrandLogo({ variant = "light", size = "default", showTagline = true }) {
  return (
    <div className={`brand-logo brand-logo--${variant} brand-logo--${size}`}>
      <LogoMark size={size === "small" ? 30 : size === "large" ? 44 : 38} />
      <div className="brand-logo__text">
        <div className="brand-logo__wordmark">
          <span className="brand-logo__letter brand-logo__letter--e">E</span>
          <span className="brand-logo__k-root">
            <span className="brand-logo__letter brand-logo__letter--k">K</span>
            <svg
              className="brand-logo__k-track"
              viewBox="0 0 100 8"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              aria-hidden="true"
            >
              <defs>
                <linearGradient id={`k-line-grad-${variant}`} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#FF6B00" />
                  <stop offset="70%" stopColor="#FF851B" />
                  <stop offset="100%" stopColor="#FFA043" />
                </linearGradient>
              </defs>
              <path
                d="M 0 1.5 L 94 1.5 L 99 4.5 L 0 4.5 Z"
                fill={`url(#k-line-grad-${variant})`}
              />
            </svg>
          </span>
          <span className="brand-logo__letter brand-logo__letter--uipt">UIPT</span>
        </div>
        {showTagline && (
          <span className="brand-logo__tagline">
            {BRAND.tagline}
          </span>
        )}
      </div>
    </div>
  );
}
