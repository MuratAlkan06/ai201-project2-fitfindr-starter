"""
Unit tests for the FitFindr planning loop (agent.run_agent) and the rule-based
query parser (agent._parse_query).

These tests MOCK the two LLM tools (and, where helpful, the search tool) so the
whole suite runs with NO Groq API key and makes NO real LLM calls. The LLM tools
are patched on the `agent` module because run_agent imports them into agent's
namespace (`from tools import ... suggest_outfit, create_fit_card`).

Run from the repo root:  python -m pytest tests/test_agent.py -q
"""

from unittest.mock import patch

import agent
from agent import _parse_query, run_agent
from utils.data_loader import get_empty_wardrobe, get_example_wardrobe


# ── full-loop tests (LLM tools mocked) ──────────────────────────────────────────

def test_happy_path():
    """A real query flows through all 7 steps with both LLM tools mocked."""
    with patch.object(
        agent, "suggest_outfit", return_value="Tuck the tee into your jeans."
    ) as mock_suggest, patch.object(
        agent, "create_fit_card", return_value="Thrifted gold for $18 on depop."
    ) as mock_card:
        session = run_agent(
            query="vintage graphic tee under $30",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] is None
    # Search is real and deterministic: lst_002 is the documented winner.
    assert session["selected_item"] is not None
    assert session["selected_item"]["id"] == "lst_002"
    assert session["outfit_suggestion"] == "Tuck the tee into your jeans."
    assert session["fit_card"] == "Thrifted gold for $18 on depop."
    mock_suggest.assert_called_once()
    mock_card.assert_called_once()


def test_no_results_early_return():
    """An impossible query sets an error and never reaches suggest_outfit."""
    with patch.object(agent, "suggest_outfit") as mock_suggest, patch.object(
        agent, "create_fit_card"
    ) as mock_card:
        session = run_agent(
            query="designer ballgown size XXS under $5",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] is not None
    assert session["search_results"] == []
    assert session["selected_item"] is None
    assert session["fit_card"] is None
    # No LLM tool may be touched once search comes back empty.
    mock_suggest.assert_not_called()
    mock_card.assert_not_called()


def test_branch_suggest_outfit_failure():
    """suggest_outfit raising yields the outfit-failure message, listing kept."""
    with patch.object(
        agent, "suggest_outfit", side_effect=RuntimeError("groq down")
    ), patch.object(agent, "create_fit_card") as mock_card:
        session = run_agent(
            query="vintage graphic tee under $30",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] == (
        "I found a great piece for you, but I couldn't generate outfit "
        "ideas right now — here's the listing. Try again in a moment."
    )
    # The good listing is preserved; the fit card is never attempted.
    assert session["selected_item"] is not None
    assert session["selected_item"]["id"] == "lst_002"
    assert session["outfit_suggestion"] is None
    assert session["fit_card"] is None
    mock_card.assert_not_called()


def test_branch_create_fit_card_failure():
    """create_fit_card raising yields the fit-card-failure message, partials kept."""
    with patch.object(
        agent, "suggest_outfit", return_value="Tuck the tee into your jeans."
    ), patch.object(
        agent, "create_fit_card", side_effect=RuntimeError("groq down")
    ):
        session = run_agent(
            query="vintage graphic tee under $30",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] == (
        "Your outfit idea is ready, but I couldn't write the shareable fit "
        "card this time. Here are the listing and styling notes — copy them "
        "straight to your post or hit Find it again."
    )
    # Both earlier partials survive the fit-card failure.
    assert session["selected_item"] is not None
    assert session["selected_item"]["id"] == "lst_002"
    assert session["outfit_suggestion"] == "Tuck the tee into your jeans."
    assert session["fit_card"] is None


def test_branch_suggest_outfit_blank_return():
    """A blank/whitespace suggest_outfit return is treated as a failure."""
    with patch.object(
        agent, "suggest_outfit", return_value="   \n\t "
    ), patch.object(agent, "create_fit_card") as mock_card:
        session = run_agent(
            query="vintage graphic tee under $30",
            wardrobe=get_empty_wardrobe(),
        )

    assert session["error"] == (
        "I found a great piece for you, but I couldn't generate outfit "
        "ideas right now — here's the listing. Try again in a moment."
    )
    assert session["selected_item"] is not None
    assert session["outfit_suggestion"] is None
    mock_card.assert_not_called()


# ── stretch: retry-with-fallback + price assessment ─────────────────────────────

def test_retry_relaxes_size():
    """An exact-size miss relaxes the size filter, returns results, notes it, and
    still flows through suggest_outfit + create_fit_card (both mocked)."""
    with patch.object(
        agent, "suggest_outfit", return_value="Tuck the tee into your jeans."
    ) as mock_suggest, patch.object(
        agent, "create_fit_card", return_value="Thrifted gold on depop."
    ) as mock_card:
        # No graphic tee exists in size XS under $30, but dropping size yields hits.
        session = run_agent(
            query="vintage graphic tee under $30 size XS",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] is None
    assert session["search_results"]  # results came back after relaxation
    assert session["selected_item"] is not None
    # The retry note is set and explicitly mentions relaxing the size filter.
    assert session["retry_note"]
    assert "size" in session["retry_note"].lower()
    assert "relaxed" in session["retry_note"].lower()
    # The downstream LLM tools still flow.
    assert session["outfit_suggestion"] == "Tuck the tee into your jeans."
    assert session["fit_card"] == "Thrifted gold on depop."
    mock_suggest.assert_called_once()
    mock_card.assert_called_once()


def test_retry_exhausted_hard_error():
    """A truly impossible query exhausts all retries, sets the hard error, and
    never calls suggest_outfit."""
    with patch.object(agent, "suggest_outfit") as mock_suggest, patch.object(
        agent, "create_fit_card"
    ) as mock_card:
        session = run_agent(
            query="designer ballgown size XXS under $5",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] is not None
    assert session["search_results"] == []
    assert session["selected_item"] is None
    # No retry succeeded, so no retry_note was recorded.
    assert session["retry_note"] is None
    # The LLM tools must not be touched on a true no-results query.
    mock_suggest.assert_not_called()
    mock_card.assert_not_called()


def test_price_assessment_in_session():
    """The happy path stores a non-empty price_assessment string."""
    with patch.object(
        agent, "suggest_outfit", return_value="Tuck the tee into your jeans."
    ), patch.object(
        agent, "create_fit_card", return_value="Thrifted gold for $18 on depop."
    ):
        session = run_agent(
            query="vintage graphic tee under $30",
            wardrobe=get_example_wardrobe(),
        )

    assert session["error"] is None
    assert isinstance(session["price_assessment"], str)
    assert session["price_assessment"].strip()


# ── parser tests (no tools involved) ────────────────────────────────────────────

def test_parse_query():
    # Price stripped from the description; no size present.
    parsed = _parse_query("vintage graphic tee under $30")
    assert parsed["description"] == "vintage graphic tee"
    assert parsed["size"] is None
    assert parsed["max_price"] == 30.0

    # A styling cue strips the trailing chatter from the description.
    parsed_cue = _parse_query(
        "vintage graphic tee under $30, how would i style it"
    )
    assert parsed_cue["description"] == "vintage graphic tee"
    assert parsed_cue["max_price"] == 30.0

    # "size M" parses the size and keeps it out of the description tokens.
    parsed_size = _parse_query("denim jacket size M")
    assert parsed_size["size"] == "M"
    assert "size" not in parsed_size["description"].lower()
    assert parsed_size["description"] == "denim jacket"


def test_parse_query_size_not_inside_word():
    # The "s" inside "jeans" must not be read as size S.
    parsed = _parse_query("baggy jeans")
    assert parsed["size"] is None

    # A standalone whole-word "S" is a real size.
    parsed_bare = _parse_query("cropped tee S")
    assert parsed_bare["size"] == "S"


def test_parse_query_bare_dollar_is_ceiling():
    parsed = _parse_query("leather boots $45")
    assert parsed["max_price"] == 45.0
    assert parsed["description"] == "leather boots"
