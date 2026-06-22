"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re
import sys

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    price_comparison,
)


# ── query parsing (Step 2: rule-based) ──────────────────────────────────────────

# Wardrobe / styling cue phrases (case-insensitive). Description is the query
# text BEFORE the first occurrence of any of these.
_CUE_PHRASES = (
    "i mostly wear",
    "i usually wear",
    "i wear",
    "i have",
    "i own",
    "how would i style",
    "how do i",
    "what's out there",
    "what is out there",
    "style it",
)

# Price patterns (first match wins). A bare "$NN(.NN)?" is a ceiling, as are the
# explicit "under/below/less than/max" forms with an optional "$".
_PRICE_PATTERNS = (
    re.compile(r"under\s+\$?(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"below\s+\$?(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"less\s+than\s+\$?(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"max\s+\$?(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$(\d+(?:\.\d+)?)"),
)

# Phrases to strip from the description once a price has been parsed.
_PRICE_STRIP_RE = re.compile(
    r"(?:under|below|less\s+than|max)\s+\$?\d+(?:\.\d+)?|\$\d+(?:\.\d+)?",
    re.IGNORECASE,
)

# Explicit "size X" token (letter or number) — preferred over a bare token.
_SIZE_LABELLED_RE = re.compile(r"\bsize\s+([A-Za-z]+|\d+)\b", re.IGNORECASE)

# Standalone whole-word letter size (word boundaries, never inside a word).
_LETTER_SIZES = ("XS", "S", "M", "L", "XL", "XXL")
_SIZE_BARE_RE = re.compile(
    r"\b(" + "|".join(_LETTER_SIZES) + r")\b", re.IGNORECASE
)

# Strip a parsed "size X" phrase from the description.
_SIZE_LABELLED_STRIP_RE = re.compile(
    r"\bsize\s+(?:[A-Za-z]+|\d+)\b", re.IGNORECASE
)


def _parse_max_price(text: str) -> float | None:
    """Return the price ceiling parsed from `text`, or None.

    First matching pattern wins; a bare "$NN" counts as a ceiling.
    """
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    return None


def _parse_size(text: str) -> str | None:
    """Return the size parsed from `text`, uppercased, or None.

    A labelled "size X" token (letter or number) is preferred; otherwise a
    standalone whole-word letter size from {XS,S,M,L,XL,XXL}. A letter inside a
    word (e.g. the "s" in "jeans") is never matched, thanks to word boundaries.
    """
    labelled = _SIZE_LABELLED_RE.search(text)
    if labelled:
        return labelled.group(1).upper()
    bare = _SIZE_BARE_RE.search(text)
    if bare:
        return bare.group(1).upper()
    return None


def _strip_filters(text: str) -> str:
    """Remove parsed price + size phrases from `text` and tidy whitespace."""
    text = _PRICE_STRIP_RE.sub(" ", text)
    text = _SIZE_LABELLED_STRIP_RE.sub(" ", text)
    # Drop bare standalone letter sizes too, so they don't leak into search terms.
    text = _SIZE_BARE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.;:-")


def _leading_segment(query: str) -> str:
    """Return the query text before the first wardrobe/styling cue phrase.

    Falls back to the full query when no cue phrase is present.
    """
    lowered = query.lower()
    cut = len(query)
    for phrase in _CUE_PHRASES:
        idx = lowered.find(phrase)
        if idx != -1 and idx < cut:
            cut = idx
    return query[:cut]


def _parse_query(query: str) -> dict:
    """Rule-based parse of a user query into search parameters.

    Returns {"description", "size", "max_price"}:
    - description: text before the first wardrobe/styling cue, with price + size
      phrases stripped; falls back to the full query (stripped) when empty.
    - max_price:   first price pattern match as a float, else None.
    - size:        labelled "size X" preferred, else a whole-word letter size,
      uppercased; else None.
    """
    query = query or ""
    max_price = _parse_max_price(query)
    size = _parse_size(query)

    description = _strip_filters(_leading_segment(query))
    if not description:
        description = _strip_filters(query)

    return {"description": description, "size": size, "max_price": max_price}


# ── search retry ladder (Step 3 fallback, stretch feature) ──────────────────────

def _format_filters(size: str | None, max_price: float | None) -> str:
    """Render the active size/price filters as a short human phrase.

    Example: size "M" + 30.0 -> "size M under $30"; only a price -> "under $30".
    Returns "" when neither filter is set.
    """
    parts: list[str] = []
    if size is not None:
        parts.append(f"size {size}")
    if max_price is not None:
        parts.append(f"under ${max_price:g}")
    return " ".join(parts)


def _search_with_fallback(parsed: dict) -> tuple[list[dict], str | None]:
    """Search, then progressively relax only the filters that were actually set.

    Returns (results, retry_note). The initial unrelaxed search has no note. The
    relaxation order is: drop size, then drop max_price, then drop both, taking
    the FIRST relaxation that yields results. `retry_note` is non-None ONLY when
    a relaxed retry succeeded; on a true no-results query it stays None and
    results is [].
    """
    description = parsed.get("description")
    size = parsed.get("size")
    max_price = parsed.get("max_price")

    # Initial, fully-constrained search — no relaxation, no note.
    results = search_listings(description=description, size=size, max_price=max_price)
    if results:
        return results, None

    original = _format_filters(size, max_price)

    # Build the relaxation ladder, including only steps that change the query.
    ladder: list[tuple[dict, str]] = []
    if size is not None:
        # 1. Drop the size filter (keep description + max_price).
        ladder.append((
            {"description": description, "size": None, "max_price": max_price},
            "I relaxed the size filter and searched again.",
        ))
    if max_price is not None:
        # 2. Drop the max_price filter (keep description + size).
        ladder.append((
            {"description": description, "size": size, "max_price": None},
            "I relaxed the price filter and searched again.",
        ))
    if size is not None and max_price is not None:
        # 3. Drop BOTH size and max_price (keep description only).
        ladder.append((
            {"description": description, "size": None, "max_price": None},
            "I relaxed both the size and price filters and searched again.",
        ))

    for relaxed_args, what_relaxed in ladder:
        relaxed_results = search_listings(**relaxed_args)
        if relaxed_results:
            prefix = (
                f"No exact matches for {original} — "
                if original
                else "No exact matches — "
            )
            return relaxed_results, prefix + what_relaxed

    # Every relaxation (if any) was still empty: true no-results.
    return [], None


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        "retry_note": None,          # set only when a relaxed retry found results
        "price_assessment": None,    # string returned by price_comparison
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1 — Initialize the session (single source of truth for this run).
    session = _new_session(query, wardrobe)

    # Step 2 — Parse the query (rule-based; deterministic, free, testable).
    session["parsed"] = _parse_query(query)

    # Step 3 — Search, with a retry ladder (stretch feature). An empty initial
    # search progressively relaxes only the filters that were set (size, then
    # max_price, then both). A successful retry records a human-readable
    # retry_note. Only a true no-results query (every relaxation still empty) sets
    # the hard error and returns before any LLM call.
    results, retry_note = _search_with_fallback(session["parsed"])
    session["search_results"] = results
    if retry_note:
        session["retry_note"] = retry_note
    if not session["search_results"]:
        description = session["parsed"].get("description") or "that"
        session["error"] = (
            f"I couldn't find anything matching \"{description}\" with those "
            "filters. Try loosening them — raise your budget, drop the size, or "
            "use broader words."
        )
        return session

    # Step 4 — Select the top-ranked listing, then assess its price against
    # same-category comparables (Tool 4, pure Python, never raises).
    session["selected_item"] = session["search_results"][0]
    session["price_assessment"] = price_comparison(session["selected_item"])

    # Step 5 — Suggest an outfit. Empty wardrobe is fine (general advice). An
    # exception OR a blank return is a failure: keep the listing, return early.
    try:
        outfit = suggest_outfit(session["selected_item"], wardrobe)
    except Exception as exc:  # noqa: BLE001 — log raw, surface friendly text only
        print(f"[run_agent] suggest_outfit failed: {exc!r}", file=sys.stderr)
        outfit = None

    if outfit is None or not str(outfit).strip():
        session["error"] = (
            "I found a great piece for you, but I couldn't generate outfit "
            "ideas right now — here's the listing. Try again in a moment."
        )
        return session
    session["outfit_suggestion"] = outfit

    # Step 6 — Fit card. Guard the outfit, then call in a try. On failure keep
    # both the listing and the outfit so the user loses nothing.
    if not str(session["outfit_suggestion"]).strip():
        return session
    try:
        session["fit_card"] = create_fit_card(
            session["outfit_suggestion"], session["selected_item"]
        )
    except Exception as exc:  # noqa: BLE001 — log raw, surface friendly text only
        print(f"[run_agent] create_fit_card failed: {exc!r}", file=sys.stderr)
        session["error"] = (
            "Your outfit idea is ready, but I couldn't write the shareable fit "
            "card this time. Here are the listing and styling notes — copy them "
            "straight to your post or hit Find it again."
        )

    # Step 7 — Return the completed session.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
