"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
    price_comparison(item)                          → str   (stretch feature)
"""

import os
import re
import statistics

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()


# ── Search helpers (Tool 1) ─────────────────────────────────────────────────────

# Small English stopword set dropped from the query before scoring.
STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "in", "of",
    "to", "my", "me", "i", "is", "it",
}

# Matches lowercase alphanumeric tokens.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Letter sizes that match a normalized size token by equality.
_LETTER_SIZES = {"XS", "S", "M", "L", "XL", "XXL"}


def _tokenize(text: str) -> list[str]:
    """Lowercase `text` and return its alphanumeric tokens."""
    return _TOKEN_RE.findall(str(text).lower())


def _listing_token_bag(listing: dict) -> set[str]:
    """Build the set of whole lowercased tokens for a listing.

    Tokens are drawn from title + description + style_tags + category + colors.
    """
    parts: list[str] = [
        str(listing.get("title", "")),
        str(listing.get("description", "")),
        str(listing.get("category", "")),
    ]
    parts.extend(str(tag) for tag in listing.get("style_tags", []) or [])
    parts.extend(str(color) for color in listing.get("colors", []) or [])
    bag: set[str] = set()
    for part in parts:
        bag.update(_tokenize(part))
    return bag


def _normalize_size_tokens(size: str | None) -> set[str]:
    """Split a listing's size on '/' and whitespace into UPPERCASE tokens.

    Example: "S/M" -> {"S", "M"}; "US 7" -> {"US", "7"}; "W30 L30" -> {"W30", "L30"}.
    """
    if not size:
        return set()
    raw = re.split(r"[/\s]+", str(size).strip().upper())
    return {tok for tok in raw if tok}


def _size_matches(query_size: str, listing: dict) -> bool:
    """Return True if `query_size` matches the listing's normalized size tokens.

    A letter size matches iff it equals one of the tokens ("M" matches "S/M").
    A numeric size matches iff present as a whole token. The query token must
    equal a full normalized token, never a substring (e.g. "S" never matches
    "US 7" or "W28").
    """
    q = str(query_size).strip().upper()
    if not q:
        return True
    return q in _normalize_size_tokens(listing.get("size"))


# ── LLM tool constants ──────────────────────────────────────────────────────────

# Groq chat model shared by suggest_outfit and create_fit_card.
_LLM_MODEL = "llama-3.3-70b-versatile"

# Per-request timeout (seconds) for the LLM calls; there is no automatic retry.
_LLM_TIMEOUT = 30

# Returned by create_fit_card when called without an outfit suggestion.
FIT_CARD_NO_OUTFIT_MSG = (
    "Can't write a fit card without an outfit suggestion — generate an outfit first."
)


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _summarize_item(new_item: dict) -> str:
    """Summarize a listing's key fields for an LLM prompt."""
    colors = ", ".join(new_item.get("colors") or []) or "n/a"
    style_tags = ", ".join(new_item.get("style_tags") or []) or "n/a"
    return (
        f"Title: {new_item.get('title', 'Untitled')}\n"
        f"Category: {new_item.get('category', 'n/a')}\n"
        f"Colors: {colors}\n"
        f"Style tags: {style_tags}\n"
        f"Condition: {new_item.get('condition', 'n/a')}"
    )


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    try:
        listings = load_listings()
    except Exception:
        # Never raise; an unreadable dataset yields no results.
        return []

    # Distinct query tokens after dropping stopwords.
    query_tokens = {
        tok for tok in _tokenize(description or "") if tok not in STOPWORDS
    }

    # Apply price/size filters first (shared by both branches).
    def _passes_filters(listing: dict) -> bool:
        if max_price is not None:
            price = listing.get("price")
            if price is None or price > max_price:
                return False
        if size is not None and not _size_matches(size, listing):
            return False
        return True

    filtered = [lst for lst in listings if _passes_filters(lst)]

    # Edge case: no usable description tokens but a filter was supplied —
    # skip scoring and return all filtered listings sorted by price then id.
    if not query_tokens:
        if size is not None or max_price is not None:
            return sorted(
                filtered,
                key=lambda lst: (lst.get("price", 0.0), lst.get("id", "")),
            )
        return []

    # Score by count of distinct query tokens present in the listing's bag.
    scored: list[tuple[int, dict]] = []
    for listing in filtered:
        bag = _listing_token_bag(listing)
        score = sum(1 for tok in query_tokens if tok in bag)
        if score > 0:
            scored.append((score, listing))

    # Sort by score DESC, then price ASC, then id ASC.
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1].get("price", 0.0),
            pair[1].get("id", ""),
        )
    )
    return [listing for _, listing in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    client = _get_groq_client()
    item_summary = _summarize_item(new_item)
    items = wardrobe.get("items") or []

    if not items:
        # Empty wardrobe is NOT an error: ask for general styling advice. This
        # branch still issues a real LLM call so the function never builds an
        # empty return itself.
        prompt = (
            "A shopper is considering this secondhand piece:\n"
            f"{item_summary}\n\n"
            "They have not entered any wardrobe items yet, so give general "
            "styling advice for this piece: what kinds of items pair well with "
            "it, the overall vibe and occasions it suits, and silhouettes that "
            "flatter it. Keep it friendly and concrete."
        )
    else:
        wardrobe_lines = "\n".join(
            f"- {item.get('name', 'Unnamed item')}" for item in items
        )
        prompt = (
            "A shopper is considering this secondhand piece:\n"
            f"{item_summary}\n\n"
            "Here is their current wardrobe:\n"
            f"{wardrobe_lines}\n\n"
            "Suggest 1-2 concrete outfits built around the new piece, naming "
            "specific wardrobe items from the list above for each outfit. "
            "Keep it friendly and concrete."
        )

    response = client.chat.completions.create(
        model=_LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=450,
        timeout=_LLM_TIMEOUT,
    )
    # The LLM content can be None; coerce to a string. A blank result is left
    # blank here so the planning loop can treat it as a failure (never None).
    text = (response.choices[0].message.content or "").strip()
    return text


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    if outfit is None or not str(outfit).strip():
        # Missing outfit input: return the sentinel without calling the LLM.
        return FIT_CARD_NO_OUTFIT_MSG

    title = new_item.get("title", "this piece")
    price = new_item.get("price", "n/a")
    platform = new_item.get("platform", "n/a")

    prompt = (
        "Write a casual, authentic OOTD-style caption (2-4 sentences) for a "
        "secondhand fashion find — not a product description. Capture the vibe "
        "of the outfit in specific terms.\n\n"
        f"Item: {title}\n"
        f"Price: ${price}\n"
        f"Platform: {platform}\n\n"
        f"Outfit: {outfit}\n\n"
        f"Mention the item name ({title}), its price (${price}), and the "
        f"platform ({platform}) once each, naturally."
    )

    # Higher temperature than suggest_outfit so captions vary across runs.
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=_LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.95,
        max_tokens=250,
        timeout=_LLM_TIMEOUT,
    )
    # The LLM content can be None; coerce to a string. If it comes back blank,
    # return a short fallback so the UI never renders an empty fit card.
    text = (response.choices[0].message.content or "").strip()
    if not text:
        return "Couldn't generate a caption this time — try again."
    return text


# ── Tool 4: price_comparison (stretch feature — pure Python, no LLM) ────────────

# Returned when an item's category has fewer than 2 comparable listings.
PRICE_NO_COMPARABLES_MSG = "Not enough comparable listings to assess this price."

# Median multipliers that bound the "fairly priced" band.
_DEAL_RATIO = 0.85   # price <= this * median  -> "a great deal"
_ABOVE_RATIO = 1.15  # price >  this * median  -> "priced above comparable items"


def price_comparison(item: dict) -> str:
    """
    Assess a listing's price against comparable listings in the same category.

    Pure and deterministic — like search_listings, this makes NO LLM call and
    needs no API key. Comparables are all listings sharing the item's `category`,
    excluding the item itself (matched by `id`).

    Args:
        item: A listing dict (typically the search winner). Uses `category`,
              `id`, and `price`.

    Returns:
        A single readable sentence with the verdict and its reasoning, e.g.
        "Fairly priced — at $24.00 it's in line with the 14 comparable tops
        (range $15.00–$35.00, median $21.00)."

        Returns a graceful sentence (never raises) when there are fewer than two
        comparables, when the item price is missing/None, or when the dataset
        cannot be read.

    Verdict bands (vs. the comparables' median):
        price <= 0.85 * median            -> "a great deal"
        0.85 * median .. 1.15 * median    -> "fairly priced"
        price >  1.15 * median            -> "priced above comparable items"
    """
    item = item or {}

    # Guard the item's own price first; without it there is nothing to compare.
    price = item.get("price")
    if not isinstance(price, (int, float)):
        return PRICE_NO_COMPARABLES_MSG

    try:
        listings = load_listings()
    except Exception:
        # Never raise; an unreadable dataset means we can't assess the price.
        return PRICE_NO_COMPARABLES_MSG

    category = item.get("category")
    item_id = item.get("id")

    # Comparables: same category, excluding the item itself, with a usable price.
    comparable_prices = [
        lst["price"]
        for lst in listings
        if lst.get("category") == category
        and lst.get("id") != item_id
        and isinstance(lst.get("price"), (int, float))
    ]

    if len(comparable_prices) < 2:
        return PRICE_NO_COMPARABLES_MSG

    low = min(comparable_prices)
    high = max(comparable_prices)
    median = statistics.median(comparable_prices)

    if price <= _DEAL_RATIO * median:
        verdict = "A great deal"
        lead_in = f"at ${price:.2f} it undercuts"
    elif price > _ABOVE_RATIO * median:
        verdict = "Priced above comparable items"
        lead_in = f"at ${price:.2f} it sits above"
    else:
        verdict = "Fairly priced"
        lead_in = f"at ${price:.2f} it's in line with"

    noun = category or "items"
    count = len(comparable_prices)
    return (
        f"{verdict} — {lead_in} the {count} comparable {noun} "
        f"(range ${low:.2f}–${high:.2f}, median ${median:.2f})."
    )
