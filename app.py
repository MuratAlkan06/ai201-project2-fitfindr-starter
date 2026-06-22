"""
app.py

Gradio interface for FitFindr. The layout and wiring are already set up —
your job is to fill in handle_query() so it calls run_agent() and maps
the session results to the three output panels.

Run with:
    python app.py

Then open the localhost URL shown in your terminal (usually http://localhost:7860,
but check your terminal — the port may differ).
"""

import gradio as gr

from agent import run_agent
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── query handler ─────────────────────────────────────────────────────────────

def _format_listing(item: dict) -> str:
    """Render a selected listing dict into a readable multi-line panel string.

    Includes title, price (e.g. "$24.00"), platform, size, condition, and the
    style tags. Reads only fields documented in utils/data_loader.load_listings.
    """
    title = item.get("title", "Untitled listing")
    price = item.get("price")
    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "Price unavailable"
    platform = item.get("platform", "unknown")
    size = item.get("size", "—")
    condition = item.get("condition", "—")
    style_tags = item.get("style_tags") or []
    tags_text = ", ".join(style_tags) if style_tags else "—"

    return (
        f"{title}\n"
        f"Price: {price_text}\n"
        f"Platform: {platform}\n"
        f"Size: {size}\n"
        f"Condition: {condition}\n"
        f"Style tags: {tags_text}"
    )


def handle_query(user_query: str, wardrobe_choice: str) -> tuple[str, str, str]:
    """
    Called by Gradio when the user submits a query.

    Args:
        user_query:     The text the user typed into the search box.
        wardrobe_choice: Either "Example wardrobe" or "Empty wardrobe (new user)".

    Returns:
        A tuple of three strings:
            (listing_text, outfit_suggestion, fit_card)
        Each string maps to one of the three output panels in the UI.

    Maps the run_agent session to the three panels per the frozen partial-success
    mapping in planning.md ("State Management"), so partial results survive a
    later step's failure:
        (a) search failed   -> (error,        "",     "")
        (b) outfit failed    -> (listing_text, error,  "")
        (c) fit card failed  -> (listing_text, outfit, error)
        (d) success          -> (listing_text, outfit, fit_card)
    """
    # 1. Guard against an empty / whitespace-only query.
    if user_query is None or not user_query.strip():
        return (
            "Please describe what you're looking for "
            "(e.g. 'vintage graphic tee under $30').",
            "",
            "",
        )

    # 2. Select the wardrobe based on the radio choice.
    if wardrobe_choice.startswith("Empty"):
        wardrobe = get_empty_wardrobe()
    else:
        wardrobe = get_example_wardrobe()

    # 3. Run the planning loop.
    session = run_agent(user_query, wardrobe)

    # 4. Build the listing text once when an item was selected. When present,
    #    enrich panel 1 (no new panels): a 🔁 retry note above the listing and a
    #    💰 price-check line below it.
    selected_item = session.get("selected_item")
    if selected_item:
        listing_text = _format_listing(selected_item)
        retry_note = session.get("retry_note")
        if retry_note:
            listing_text = f"🔁 {retry_note}\n\n{listing_text}"
        price_assessment = session.get("price_assessment")
        if price_assessment:
            listing_text = f"{listing_text}\n💰 Price check: {price_assessment}"
    else:
        listing_text = ""

    error = session.get("error")
    outfit = session.get("outfit_suggestion")
    fit_card = session.get("fit_card")

    # 5. Map session -> panels, preserving partial results (cases a–d).
    if error:
        # (a) search failed: nothing was selected.
        if selected_item is None:
            return error, "", ""
        # (b) outfit failed: listing kept, error replaces the outfit panel.
        if not outfit:
            return listing_text, error, ""
        # (c) fit card failed: listing + outfit kept, error replaces panel 3.
        return listing_text, outfit, error

    # (d) success: listing + outfit + fit card.
    return listing_text, outfit, fit_card


# ── interface ─────────────────────────────────────────────────────────────────

EXAMPLE_QUERIES = [
    "vintage graphic tee under $30",
    "90s track jacket in size M",
    "flowy midi skirt under $40",
    "black combat boots size 8",
    "designer ballgown size XXS under $5",   # deliberate no-results test
]

def build_interface():
    with gr.Blocks(title="FitFindr") as demo:
        gr.Markdown("""
# FitFindr 🛍️
Find secondhand pieces and get outfit ideas based on your wardrobe.
Describe what you're looking for — include size and price if you want to filter.
        """)

        with gr.Row():
            query_input = gr.Textbox(
                label="What are you looking for?",
                placeholder="e.g. vintage graphic tee under $30, size M",
                lines=2,
                scale=3,
            )
            wardrobe_choice = gr.Radio(
                choices=["Example wardrobe", "Empty wardrobe (new user)"],
                value="Example wardrobe",
                label="Wardrobe",
                scale=1,
            )

        submit_btn = gr.Button("Find it", variant="primary")

        with gr.Row():
            listing_output = gr.Textbox(
                label="🛍️ Top listing found",
                lines=8,
                interactive=False,
            )
            outfit_output = gr.Textbox(
                label="👗 Outfit idea",
                lines=8,
                interactive=False,
            )
            fitcard_output = gr.Textbox(
                label="✨ Your fit card",
                lines=8,
                interactive=False,
            )

        gr.Examples(
            examples=[[q, "Example wardrobe"] for q in EXAMPLE_QUERIES],
            inputs=[query_input, wardrobe_choice],
            label="Try these queries",
        )

        submit_btn.click(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice],
            outputs=[listing_output, outfit_output, fitcard_output],
        )
        query_input.submit(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice],
            outputs=[listing_output, outfit_output, fitcard_output],
        )

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch()
