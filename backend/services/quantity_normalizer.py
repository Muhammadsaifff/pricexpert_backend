"""
backend/services/quantity_normalizer.py

Centralized quantity normalization for PriceXpert.
All product quantities are stored and compared in a single canonical form:

  Volume  →  Nml      (e.g. '1000ml' for 1 L / 1 Litre / 1000 ml)
  Weight  →  Ng       (e.g. '1000g'  for 1 kg / 1000 g / 1000 gm)
  Count   →  Npcs     (e.g. '3pcs'   for 3 pieces / 3 pack)

This is the single source of truth.  Search, comparison, scraping and AI
modules all import from here — no duplicate regex logic elsewhere.
"""

import re
from typing import Optional, Tuple

# ── Lookup tables: unit string → multiplier to reach the base unit ──────────
_VOLUME_UNITS: dict = {
    'ml': 1, 'cl': 10,
    'litres': 1000, 'liters': 1000, 'litre': 1000, 'liter': 1000,
    'ltr': 1000, 'l': 1000, 'Ltr': 1000,
}
_WEIGHT_UNITS: dict = {
    'g': 1, 'gm': 1, 'gram': 1, 'grams': 1, 'G':1000, 'Gram' : 1000, 'GRAM' : 1000, 
    'kilograms': 1000, 'kilogram': 1000, 'kilo': 1000, 'kg': 1000,
}
_COUNT_UNITS: dict = {
    'dozens': 12, 'dozen': 12,
    'pairs': 2, 'pair': 2,
    'pieces': 1, 'piece': 1, 'pcs': 1, 'pc': 1,
    'packs': 1, 'pack': 1, 'pk': 1,
}

# Build sorted alternation strings (longest first avoids partial shadowing)
def _alts(d: dict) -> str:
    return '|'.join(sorted(d, key=len, reverse=True))

_VOL_ALT = _alts(_VOLUME_UNITS)
_WGT_ALT = _alts(_WEIGHT_UNITS)
_CNT_ALT = _alts(_COUNT_UNITS)

# Per-group patterns
_VOL_RE = re.compile(r'(?<!\w)(\d+(?:\.\d+)?)\s*(' + _VOL_ALT + r')(?!\w)', re.IGNORECASE)
_WGT_RE = re.compile(r'(?<!\w)(\d+(?:\.\d+)?)\s*(' + _WGT_ALT + r')(?!\w)', re.IGNORECASE)
_CNT_RE = re.compile(r'(?<!\w)(\d+)\s*(' + _CNT_ALT + r')(?!\w)', re.IGNORECASE)

# Combined pattern used only to strip quantity tokens from a search query
_ANY_QTY_RE = re.compile(
    r'(?<!\w)\d+(?:\.\d+)?\s*(?:' + _VOL_ALT + '|' + _WGT_ALT + '|' + _CNT_ALT + r')(?!\w)',
    re.IGNORECASE,
)


def _fmt(value: float) -> str:
    """'1000.0' → '1000', '1.5' → '1.5'."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip('0').rstrip('.')


# ── Public API ───────────────────────────────────────────────────────────────

def normalize_quantity(raw: Optional[str]) -> Optional[str]:
    """
    Convert *any* quantity string to a canonical representation.

    Examples
    --------
    '1L'        → '1000ml'
    '1 Liter'   → '1000ml'
    '1 Litre'   → '1000ml'
    '1000ml'    → '1000ml'
    '0.5L'      → '500ml'
    '1kg'       → '1000g'
    '1 Kg'      → '1000g'
    '500g'      → '500g'
    '500gm'     → '500g'
    '0.5kg'     → '500g'
    '3pcs'      → '3pcs'
    '3 pieces'  → '3pcs'
    '3 pack'    → '3pcs'
    '1 dozen'   → '12pcs'
    None / ''   → None
    """
    if not raw:
        return None
    text = raw.strip()

    m = _VOL_RE.search(text)
    if m:
        canonical = float(m.group(1)) * _VOLUME_UNITS[m.group(2).lower()]
        return f"{_fmt(canonical)}ml"

    m = _WGT_RE.search(text)
    if m:
        canonical = float(m.group(1)) * _WEIGHT_UNITS[m.group(2).lower()]
        return f"{_fmt(canonical)}g"

    m = _CNT_RE.search(text)
    if m:
        canonical = int(m.group(1)) * _COUNT_UNITS[m.group(2).lower()]
        return f"{canonical}pcs"

    return None


def extract_and_normalize(product_name: Optional[str]) -> Optional[str]:
    """
    Extract the first parsable quantity token from *product_name* and return
    it in canonical form.  Returns None when no quantity is found.

    Examples
    --------
    'Pepsi Cola 1 Litre Pet'  → '1000ml'
    'Ariel Detergent 500gm'   → '500g'
    'Eggs (12 pcs)'           → '12pcs'
    """
    if not product_name:
        return None
    for pat in (_VOL_RE, _WGT_RE, _CNT_RE):
        m = pat.search(product_name)
        if m:
            return normalize_quantity(m.group(0))
    return None


def split_search_query(query: str) -> Tuple[str, Optional[str]]:
    """
    Split a free-text search query into *(base_term, normalized_quantity)*.

    The quantity token is stripped from the query so that only the product
    name tokens are used for the DB ILIKE search, while the quantity is used
    for post-filtering.

    Examples
    --------
    'Pepsi 1L'          → ('Pepsi', '1000ml')
    'Pepsi 1000ml'      → ('Pepsi', '1000ml')
    'Pepsi 1 Liter'     → ('Pepsi', '1000ml')
    'Milk'              → ('Milk', None)
    'Ariel 1kg powder'  → ('Ariel powder', '1000g')
    """
    query = query.strip()
    m = _ANY_QTY_RE.search(query)
    if not m:
        return query, None

    normalized = normalize_quantity(m.group(0))
    base = _ANY_QTY_RE.sub('', query).strip()
    base = re.sub(r'\s{2,}', ' ', base).strip()
    return base, normalized


def quantities_match(qty1: Optional[str], qty2: Optional[str]) -> bool:
    """
    Return True when two quantity strings represent the same amount.

    Accepts raw *or* canonical strings.  Returns False when either value is
    None / empty so callers do not need to guard against None.

    Examples
    --------
    quantities_match('1L',     '1000ml') → True
    quantities_match('500gm',  '0.5kg')  → True
    quantities_match('3 pcs',  '3pcs')   → True
    quantities_match(None,     '1000ml') → False
    """
    if not qty1 or not qty2:
        return False
    n1 = normalize_quantity(qty1)
    n2 = normalize_quantity(qty2)
    if n1 is None or n2 is None:
        return qty1.strip().lower() == qty2.strip().lower()
    return n1 == n2
