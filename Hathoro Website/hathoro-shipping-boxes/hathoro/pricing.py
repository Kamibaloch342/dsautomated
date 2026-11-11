"""Hathoro pricing: partner discount -> Zoro freight rule -> target net margin -> .99 ending."""

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class Price:
    base_cost: float          # item price x quantity (Zoro list, without freight surcharge)
    discount: float           # partner discount amount
    discounted_cost: float
    surcharge: float          # Zoro freight surcharge x quantity (no discount applies)
    freight: float
    landed_cost: float
    raw_price: float          # landed / (1 - margin), before rounding
    price: float              # storefront price
    compare_at: float         # strike-through price, or None
    profit: float
    margin: float

    def to_dict(self):
        return asdict(self)


def money(value):
    """Round to cents, half up (60.525 -> 60.53)."""
    return float(Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_up_to_ending(value, ending=0.99):
    """Smallest price >= value that ends in `ending` (e.g. 46.66 -> 46.99, 47.00 -> 47.99)."""
    cents = round(value * 100)
    end = round(ending * 100)
    candidate = (cents // 100) * 100 + end
    if candidate < cents:
        candidate += 100
    return candidate / 100


def price_listing(unit_price, pack_qty, rules, surcharge=0.0):
    """unit_price: Zoro item price per unit; surcharge: Zoro freight surcharge per unit
    (the feed's sales_price is unit_price + surcharge). Zoro waives the small-order fee
    when the LIST merchandise total reaches the threshold, before the partner discount."""
    base = money(unit_price * pack_qty)
    disc_cost = money(base * (1 - rules["partner_discount"]))
    discount = money(base - disc_cost)
    surcharge_total = money(surcharge * pack_qty)
    freight = rules["freight_fee"] if base < rules["free_freight_threshold"] else 0.0
    landed = money(disc_cost + surcharge_total + freight)
    raw = landed / (1 - rules["target_margin"])
    price = round_up_to_ending(raw, rules["price_ending"])
    compare = None
    if rules.get("compare_at_ratio"):
        compare = round_up_to_ending(price / rules["compare_at_ratio"], rules["price_ending"])
    profit = money(price - landed)
    margin = profit / price
    if margin + 1e-9 < rules["target_margin"]:
        raise ValueError(f"margin {margin:.4f} below target for landed cost {landed}")
    return Price(base, discount, disc_cost, surcharge_total, freight, landed, round(raw, 4),
                 price, compare, profit, round(margin, 4))


def price_for(li, rules):
    """Price a parsed Listing (hathoro.data.Listing)."""
    return price_listing(li.unit_cost, li.order_qty, rules, surcharge=li.surcharge)
