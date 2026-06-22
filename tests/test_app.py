"""
Unit tests for the Gradio query handler (app.handle_query) and its listing
formatter (app._format_listing).

The panel-mapping tests PATCH `app.run_agent` to return crafted session dicts,
so the suite runs with NO Groq API key and makes NO real LLM or network calls.
run_agent is patched on the `app` module because app imports it into its own
namespace (`from agent import run_agent`).

One test exercises the REAL handle_query end-to-end on a deliberate no-results
query — search is local and deterministic, so this also needs no API key.

Run from the repo root:  python -m pytest tests/test_app.py -q
"""

from unittest.mock import patch

import app
from app import handle_query, _format_listing


# A representative selected_item, matching listings.json field shapes.
_ITEM = {
    "id": "lst_006",
    "title": "Retro Band Tee — Tour Print",
    "description": "Soft worn-in graphic tee.",
    "category": "tops",
    "style_tags": ["vintage", "graphic tee", "band"],
    "size": "M",
    "condition": "good",
    "price": 24.00,
    "colors": ["black"],
    "brand": None,
    "platform": "depop",
}


def _base_session(**overrides) -> dict:
    """A success-shaped session dict; override fields per test case."""
    session = {
        "query": "vintage graphic tee under $30",
        "parsed": {},
        "search_results": [_ITEM],
        "selected_item": _ITEM,
        "wardrobe": {"items": []},
        "outfit_suggestion": "Pair it with straight-leg jeans and white sneakers.",
        "fit_card": "Thrifted this band tee for $24 on depop — obsessed.",
        "error": None,
        "retry_note": None,
        "price_assessment": None,
    }
    session.update(overrides)
    return session


# ── _format_listing shape ───────────────────────────────────────────────────────

def test_format_listing_shape():
    text = _format_listing(_ITEM)
    # Title leads; price uses two decimals; every required field is present.
    assert text.startswith("Retro Band Tee — Tour Print")
    assert "Price: $24.00" in text
    assert "Platform: depop" in text
    assert "Size: M" in text
    assert "Condition: good" in text
    assert "Style tags: vintage, graphic tee, band" in text


# ── empty-query guard ───────────────────────────────────────────────────────────

def test_empty_query_returns_guard_message():
    listing, outfit, fitcard = handle_query("   ", "Example wardrobe")
    assert listing == (
        "Please describe what you're looking for "
        "(e.g. 'vintage graphic tee under $30')."
    )
    assert outfit == ""
    assert fitcard == ""


# ── panel-mapping cases (run_agent patched) ─────────────────────────────────────

def test_case_a_search_failed():
    """(a) No selected_item + error -> error in panel 1, blanks in 2 and 3."""
    err = "I couldn't find anything matching \"unicorn jacket\"."
    session = _base_session(
        search_results=[],
        selected_item=None,
        outfit_suggestion=None,
        fit_card=None,
        error=err,
    )
    with patch.object(app, "run_agent", return_value=session) as mock_run:
        result = handle_query("unicorn jacket", "Example wardrobe")

    assert result == (err, "", "")
    mock_run.assert_called_once()


def test_case_b_outfit_failed():
    """(b) Listing kept, error in panel 2, blank panel 3."""
    err = (
        "I found a great piece for you, but I couldn't generate outfit "
        "ideas right now — here's the listing. Try again in a moment."
    )
    session = _base_session(outfit_suggestion=None, fit_card=None, error=err)
    with patch.object(app, "run_agent", return_value=session):
        listing, outfit, fitcard = handle_query("graphic tee", "Example wardrobe")

    assert listing == _format_listing(_ITEM)
    assert outfit == err
    assert fitcard == ""


def test_case_c_fit_card_failed():
    """(c) Listing + outfit kept, error in panel 3."""
    err = (
        "Your outfit idea is ready, but I couldn't write the shareable fit "
        "card this time."
    )
    session = _base_session(fit_card=None, error=err)
    with patch.object(app, "run_agent", return_value=session):
        listing, outfit, fitcard = handle_query("graphic tee", "Example wardrobe")

    assert listing == _format_listing(_ITEM)
    assert outfit == "Pair it with straight-leg jeans and white sneakers."
    assert fitcard == err


def test_case_d_success():
    """(d) Full success -> listing, outfit, fit card all populated."""
    session = _base_session()
    with patch.object(app, "run_agent", return_value=session):
        listing, outfit, fitcard = handle_query("graphic tee", "Example wardrobe")

    assert listing == _format_listing(_ITEM)
    assert outfit == "Pair it with straight-leg jeans and white sneakers."
    assert fitcard == "Thrifted this band tee for $24 on depop — obsessed."


def test_panel1_enriched_with_retry_and_price():
    """A success session carrying retry_note + price_assessment surfaces both in
    panel 1: a 🔁 line above the listing and a 💰 price-check line below it."""
    retry_note = (
        "No exact matches for size M under $30 — I relaxed the size filter "
        "and searched again."
    )
    price_assessment = (
        "Fairly priced — at $24.00 it's in line with the 14 comparable tops "
        "(range $15.00–$35.00, median $21.00)."
    )
    session = _base_session(
        retry_note=retry_note, price_assessment=price_assessment
    )
    with patch.object(app, "run_agent", return_value=session):
        listing, outfit, fitcard = handle_query("graphic tee", "Example wardrobe")

    # The 🔁 retry line and 💰 price line both appear in panel 1.
    assert f"🔁 {retry_note}" in listing
    assert f"💰 Price check: {price_assessment}" in listing
    # The original formatted listing is still present, between the two lines.
    assert _format_listing(_ITEM) in listing
    assert listing.index("🔁") < listing.index("Retro Band Tee")
    assert listing.index("💰") > listing.index("Retro Band Tee")
    # Panels 2 and 3 are unaffected.
    assert outfit == "Pair it with straight-leg jeans and white sneakers."
    assert fitcard == "Thrifted this band tee for $24 on depop — obsessed."


def test_empty_wardrobe_choice_routes_to_empty_wardrobe():
    """A choice starting with 'Empty' selects the empty wardrobe."""
    captured = {}

    def fake_run_agent(query, wardrobe):
        captured["wardrobe"] = wardrobe
        return _base_session()

    with patch.object(app, "run_agent", side_effect=fake_run_agent):
        handle_query("graphic tee", "Empty wardrobe (new user)")

    assert captured["wardrobe"]["items"] == []


# ── real (key-free) no-results path ─────────────────────────────────────────────

def test_real_no_results_path_is_key_free():
    """The real handle_query on an impossible query stops before any LLM call.

    Search is local + deterministic, so this needs no Groq key: the error lands
    in panel 1 and panels 2/3 stay empty.
    """
    listing, outfit, fitcard = handle_query(
        "designer ballgown size XXS under $5", "Example wardrobe"
    )
    assert listing.strip() != ""
    assert outfit == ""
    assert fitcard == ""
