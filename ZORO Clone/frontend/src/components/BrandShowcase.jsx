import { Link } from "react-router-dom";
import { formatBrandName } from "../lib/brand";
import { ArrowRight, ShieldIcon } from "./Icons";

// Featured major industrial brands with stylized vector logos / badges
const FEATURED_BRANDS = [
  {
    name: "EKUIPT Select",
    tagline: "Quality MRO Essentials",
    category: "Valves, Fittings & Hardware",
    color: "#FF6B00",
    badgeType: "ekuipt",
  },
  {
    name: "Dayton",
    tagline: "Motors & Ventilation",
    category: "Electric Motors, Fans & Blowers",
    color: "#0F4C81",
    badgeType: "dayton",
  },
  {
    name: "Condor",
    tagline: "Safety & Fall Protection",
    category: "PPE, Gloves & Rigging",
    color: "#B3261E",
    badgeType: "condor",
  },
  {
    name: "Speedaire",
    tagline: "Pneumatics & Compressors",
    category: "Air Compressors & Hose Reels",
    color: "#1E545B",
    badgeType: "speedaire",
  },
  {
    name: "Westward",
    tagline: "Hand Tools & Sockets",
    category: "Wrenches, Mechanics & Storage",
    color: "#2C3E50",
    badgeType: "westward",
  },
  {
    name: "Milwaukee",
    tagline: "Heavy Duty Cordless",
    category: "M18/M12 Power Tools & PACKOUT",
    color: "#DB0000",
    badgeType: "milwaukee",
  },
  {
    name: "DeWalt",
    tagline: "Guaranteed Tough",
    category: "20V MAX Drills, Saws & Lasers",
    color: "#FEB81C",
    badgeType: "dewalt",
  },
  {
    name: "3M",
    tagline: "Science. Applied to Life.",
    category: "Abrasives, Tapes & Respirators",
    color: "#FF0000",
    badgeType: "3m",
  },
  {
    name: "Klein Tools",
    tagline: "For Professionals Since 1857",
    category: "Pliers, Cutters & Test Meters",
    color: "#FF7900",
    badgeType: "klein",
  },
  {
    name: "Proto",
    tagline: "Industrial Torque & Wrenches",
    category: "Tethered & Impact Sockets",
    color: "#C41230",
    badgeType: "proto",
  },
  {
    name: "Square D",
    tagline: "Schneider Electric",
    category: "Circuit Breakers & Panelboards",
    color: "#009530",
    badgeType: "squared",
  },
  {
    name: "Fluke",
    tagline: "Keeping Your World Up & Running",
    category: "Digital Multimeters & Thermal",
    color: "#FFC20E",
    badgeType: "fluke",
  },
  {
    name: "Loctite",
    tagline: "Henkel Adhesives",
    category: "Threadlockers & Epoxies",
    color: "#D71920",
    badgeType: "loctite",
  },
  {
    name: "Honeywell",
    tagline: "Safety & Controls",
    category: "Gas Detection & Eye Protection",
    color: "#EE3124",
    badgeType: "honeywell",
  },
  {
    name: "Parker",
    tagline: "Fluid & Hydraulic Systems",
    category: "Quick Couplers, Valves & Tubing",
    color: "#F7931E",
    badgeType: "parker",
  },
  {
    name: "Ridgid",
    tagline: "Ridge Tool Company",
    category: "Pipe Wrenches, Threaders & Vises",
    color: "#D32F2F",
    badgeType: "ridgid",
  },
];

function BrandLogoBadge({ brand }) {
  switch (brand.badgeType) {
    case "ekuipt":
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 21, fontWeight: 900, letterSpacing: "-0.03em", color: "var(--z-blue)" }}>
            E<span style={{ color: "var(--z-orange)" }}>K</span>UIPT
          </span>
          <span
            style={{
              fontSize: 10,
              fontWeight: 800,
              textTransform: "uppercase",
              background: "var(--z-orange)",
              color: "#fff",
              padding: "2px 6px",
              borderRadius: 4,
            }}
          >
            Select
          </span>
        </div>
      );
    case "dayton":
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ fontSize: 20, fontWeight: 900, letterSpacing: "-0.02em", color: "#0F4C81", fontStyle: "italic" }}>
            DAYTON
          </span>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#FF6B00" }} />
        </div>
      );
    case "condor":
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 20, fontWeight: 900, letterSpacing: "0.04em", color: "#B3261E" }}>
            CONDOR
          </span>
        </div>
      );
    case "speedaire":
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 18, fontWeight: 850, letterSpacing: "-0.02em", color: "#1E545B" }}>
            Speedaire
          </span>
        </div>
      );
    case "westward":
      return (
        <span style={{ fontSize: 18, fontWeight: 900, letterSpacing: "0.02em", color: "#2C3E50" }}>
          WESTWARD
        </span>
      );
    case "milwaukee":
      return (
        <div style={{ background: "#DB0000", color: "#fff", padding: "4px 10px", borderRadius: 4, fontWeight: 900, fontSize: 15, letterSpacing: "0.02em" }}>
          Milwaukee
        </div>
      );
    case "dewalt":
      return (
        <div style={{ background: "#000", color: "#FEB81C", padding: "4px 10px", borderRadius: 4, fontWeight: 900, fontSize: 16, letterSpacing: "0.04em" }}>
          DEWALT
        </div>
      );
    case "3m":
      return (
        <span style={{ fontSize: 26, fontWeight: 900, letterSpacing: "-0.05em", color: "#FF0000" }}>
          3M
        </span>
      );
    case "klein":
      return (
        <span style={{ fontSize: 18, fontWeight: 900, color: "#231F20", textTransform: "uppercase", letterSpacing: "0.02em" }}>
          KLEIN <span style={{ color: "#FF7900" }}>TOOLS</span>
        </span>
      );
    case "proto":
      return (
        <span style={{ fontSize: 22, fontWeight: 900, color: "#C41230", fontStyle: "italic", letterSpacing: "0.03em" }}>
          PROTO
        </span>
      );
    case "squared":
      return (
        <div style={{ border: "2px solid #009530", padding: "2px 8px", borderRadius: 4, color: "#009530", fontWeight: 900, fontSize: 14 }}>
          SQUARE D
        </div>
      );
    case "fluke":
      return (
        <div style={{ background: "#FFC20E", color: "#000", padding: "3px 10px", borderRadius: 4, fontWeight: 900, fontSize: 16, letterSpacing: "0.05em" }}>
          FLUKE
        </div>
      );
    case "loctite":
      return (
        <span style={{ fontSize: 19, fontWeight: 900, color: "#D71920", letterSpacing: "-0.01em" }}>
          LOCTITE
        </span>
      );
    case "honeywell":
      return (
        <span style={{ fontSize: 18, fontWeight: 800, color: "#EE3124", letterSpacing: "-0.02em" }}>
          Honeywell
        </span>
      );
    case "parker":
      return (
        <div style={{ borderLeft: "4px solid #F7931E", paddingLeft: 6, fontWeight: 900, fontSize: 17, color: "#111" }}>
          Parker
        </div>
      );
    case "ridgid":
      return (
        <div style={{ background: "#D32F2F", color: "#fff", padding: "3px 8px", borderRadius: 3, fontWeight: 900, fontSize: 15, letterSpacing: "0.04em" }}>
          RIDGID
        </div>
      );
    default:
      return (
        <span style={{ fontSize: 17, fontWeight: 800, color: "var(--z-ink)" }}>
          {brand.name}
        </span>
      );
  }
}

export default function BrandShowcase() {
  return (
    <section className="hsec" style={{ marginTop: 24, marginBottom: 32 }}>
      <div className="hsec__head" style={{ marginBottom: 18 }}>
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 800, color: "var(--z-ink)", margin: 0 }}>
            Top Trusted Industry Brands
          </h2>
          <p style={{ fontSize: 13.5, color: "var(--z-gray)", margin: "4px 0 0" }}>
            Over 7,600 certified manufacturers with direct OEM warranties and full product specifications.
          </p>
        </div>
        <Link to="/search" style={{ fontSize: 13.5, fontWeight: 700, color: "var(--z-blue)", display: "flex", alignItems: "center", gap: 4 }}>
          Shop All 7,600+ Brands <ArrowRight size={15} />
        </Link>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))",
          gap: 14,
        }}
      >
        {FEATURED_BRANDS.map((brand) => (
          <Link
            key={brand.name}
            to={`/search?brand=${encodeURIComponent(formatBrandName(brand.name))}`}
            className="brand-card-item"
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              padding: "16px",
              background: "#ffffff",
              border: "1px solid var(--z-line-soft)",
              borderRadius: 12,
              textDecoration: "none",
              transition: "transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease",
              boxShadow: "0 1px 3px rgba(0,0,0,0.03)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: 38, marginBottom: 8 }}>
              <BrandLogoBadge brand={brand} />
              <ArrowRight size={14} style={{ color: "var(--z-gray-light)" }} />
            </div>

            <div>
              <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--z-ink)", marginTop: 4 }}>
                {brand.tagline}
              </div>
              <div style={{ fontSize: 11.5, color: "var(--z-gray)", marginTop: 2 }}>
                {brand.category}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}
