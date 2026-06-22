"""
Unit tests for Tool 1: search_listings (tools.py).

These tests are fully deterministic and require no Groq API key — they exercise
the pure keyword-search behaviour against the real data/listings.json dataset.

Run from the repo root:  python -m pytest tests/test_tools.py -q
"""

from unittest.mock import MagicMock, patch

import tools
from tools import (
    FIT_CARD_NO_OUTFIT_MSG,
    PRICE_NO_COMPARABLES_MSG,
    create_fit_card,
    price_comparison,
    search_listings,
    suggest_outfit,
)
from utils.data_loader import (
    get_empty_wardrobe,
    get_example_wardrobe,
    load_listings,
)


def _normalize_size_tokens(size):
    """Mirror the tool's size normalization for assertions."""
    import re

    if not size:
        return set()
    return {t for t in re.split(r"[/\s]+", str(size).strip().upper()) if t}


def _listings_by_id():
    return {lst["id"]: lst for lst in load_listings()}


def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0
    assert all(isinstance(r, dict) for r in results)


def test_search_empty_results():
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=40)
    assert len(results) > 0  # sanity: there is something to filter
    assert all(r["price"] <= 40 for r in results)


def test_search_size_token_match():
    # "M" should only return listings whose normalized size tokens include "M".
    results = search_listings("vintage", size="M", max_price=None)
    assert len(results) > 0
    for r in results:
        assert "M" in _normalize_size_tokens(r["size"])

    # Guard: letter size "s" must NOT match "US 7" (lst_009) or "W28" (lst_005/lst_037).
    by_id = _listings_by_id()
    assert by_id["lst_009"]["size"] == "US 7"
    assert by_id["lst_005"]["size"] == "W28"

    s_results = search_listings("vintage", size="s", max_price=None)
    s_ids = {r["id"] for r in s_results}
    assert "lst_009" not in s_ids  # "US 7" must not match size "s"
    assert "lst_005" not in s_ids  # "W28" must not match size "s"
    # And every returned listing genuinely has an "S" size token.
    for r in s_results:
        assert "S" in _normalize_size_tokens(r["size"])


def test_search_ranking_tie_break():
    results = search_listings("vintage graphic tee", max_price=30)
    assert len(results) > 0
    assert results[0]["id"] == "lst_002"


# ── Tool 2 + Tool 3: LLM tools (mocked Groq client, no API key needed) ──────────

NEW_ITEM = {
    "id": "lst_002",
    "title": "Y2K Baby Tee — Butterfly Print",
    "category": "tops",
    "colors": ["pink", "white"],
    "style_tags": ["y2k", "graphic", "vintage"],
    "condition": "excellent",
    "price": 18.0,
    "platform": "depop",
}


def _make_fake_client(reply_text):
    """Build a fake Groq client whose chat completion returns `reply_text`.

    The returned MagicMock mirrors the real response shape:
    client.chat.completions.create(...).choices[0].message.content
    """
    fake_client = MagicMock()
    fake_message = MagicMock()
    fake_message.content = reply_text
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_client.chat.completions.create.return_value = fake_response
    return fake_client


def _prompt_sent(fake_client):
    """Return the prompt text passed to the mocked create() call."""
    _, kwargs = fake_client.chat.completions.create.call_args
    return kwargs["messages"][0]["content"]


def test_suggest_outfit_empty_wardrobe():
    fake_client = _make_fake_client("Here is some general styling advice.")
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        result = suggest_outfit(NEW_ITEM, get_empty_wardrobe())

    # Empty wardrobe is not an error — the LLM is still called and text returned.
    assert isinstance(result, str) and result.strip()
    fake_client.chat.completions.create.assert_called_once()

    # The prompt reflects the general-advice branch, not the wardrobe branch.
    prompt = _prompt_sent(fake_client)
    assert "general styling advice" in prompt.lower()

    # Sanity: it differs from the populated-wardrobe prompt for the same item.
    other_client = _make_fake_client("outfit text")
    with patch.object(tools, "_get_groq_client", return_value=other_client):
        suggest_outfit(NEW_ITEM, get_example_wardrobe())
    assert prompt != _prompt_sent(other_client)


def test_suggest_outfit_with_wardrobe():
    fake_client = _make_fake_client("Outfit 1: tee + jeans + jacket.")
    wardrobe = get_example_wardrobe()
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        result = suggest_outfit(NEW_ITEM, wardrobe)

    assert isinstance(result, str) and result.strip()
    fake_client.chat.completions.create.assert_called_once()

    # Every wardrobe item name must appear in the prompt sent to the LLM.
    prompt = _prompt_sent(fake_client)
    for item in wardrobe["items"]:
        assert item["name"] in prompt


def test_create_fit_card_empty_outfit():
    fake_client = _make_fake_client("should never be returned")
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        result = create_fit_card("", NEW_ITEM)

    assert result == FIT_CARD_NO_OUTFIT_MSG
    # The LLM must NOT be called when there is no outfit.
    fake_client.chat.completions.create.assert_not_called()

    # Whitespace-only outfit is also treated as missing.
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        assert create_fit_card("   \n\t ", NEW_ITEM) == FIT_CARD_NO_OUTFIT_MSG
    fake_client.chat.completions.create.assert_not_called()


def test_create_fit_card_happy():
    caption = "Thrifted gold with this butterfly baby tee. OOTD energy unmatched."
    fake_client = _make_fake_client(caption)
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        result = create_fit_card("tee tucked into jeans with a denim jacket", NEW_ITEM)

    assert result == caption
    assert isinstance(result, str) and result.strip()
    fake_client.chat.completions.create.assert_called_once()


def test_suggest_outfit_none_content():
    # The Groq response content can be None; suggest_outfit must not crash and
    # must never return None.
    fake_client = _make_fake_client(None)
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        result = suggest_outfit(NEW_ITEM, get_example_wardrobe())

    assert result is not None
    assert isinstance(result, str)


def test_create_fit_card_none_content():
    # The Groq response content can be None; create_fit_card must not crash and
    # must return a non-empty fallback string rather than None or "".
    fake_client = _make_fake_client(None)
    with patch.object(tools, "_get_groq_client", return_value=fake_client):
        result = create_fit_card("tee tucked into jeans with a denim jacket", NEW_ITEM)

    assert result is not None
    assert isinstance(result, str) and result.strip()


# ── Tool 4: price_comparison (pure Python, no key, real dataset) ────────────────

# Crafted tops whose `id` is NOT in the dataset, so all real tops are comparables
# (median $21.00, range $15.00–$35.00). Prices chosen to land cleanly in each band.
_FAIR_TOP = {"id": "crafted_fair", "category": "tops", "price": 21.0}
_DEAL_TOP = {"id": "crafted_deal", "category": "tops", "price": 10.0}
_ABOVE_TOP = {"id": "crafted_above", "category": "tops", "price": 50.0}


def test_price_comparison_fair():
    result = price_comparison(_FAIR_TOP)
    assert isinstance(result, str) and result.strip()
    assert "fairly priced" in result.lower()
    assert "$21.00" in result  # the item price is mentioned


def test_price_comparison_deal():
    result = price_comparison(_DEAL_TOP)
    assert isinstance(result, str) and result.strip()
    assert "great deal" in result.lower()
    assert "$10.00" in result


def test_price_comparison_above():
    result = price_comparison(_ABOVE_TOP)
    assert isinstance(result, str) and result.strip()
    assert "above" in result.lower()
    assert "$50.00" in result


def test_price_comparison_no_comparables():
    # A category with fewer than 2 comparables yields a graceful string, no raise.
    lonely_item = {"id": "crafted_x", "category": "spacesuit", "price": 99.0}
    result = price_comparison(lonely_item)
    assert result == PRICE_NO_COMPARABLES_MSG

    # A missing/None price is also handled gracefully (never raises).
    no_price = {"id": "crafted_y", "category": "tops"}
    assert price_comparison(no_price) == PRICE_NO_COMPARABLES_MSG
    assert price_comparison(None) == PRICE_NO_COMPARABLES_MSG
