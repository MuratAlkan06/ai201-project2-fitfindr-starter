# FitFindr — Secondhand Stylist Agent

FitFindr is a small agent that takes a natural-language request for a secondhand
clothing item, finds the best matching listing in a mock dataset, suggests an
outfit built around it (using the user's wardrobe when available), and writes a
share-ready "fit card" caption. The three tools are orchestrated by a
deterministic planning loop and surfaced through a three-panel Gradio UI.

This README is kept in parity with [`planning.md`](planning.md) (the frozen
spec) and the real implementation in `tools.py`, `agent.py`, and `app.py`.

## What's Included

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example wardrobe
├── utils/
│   └── data_loader.py         # Helper functions for loading the data
├── tools.py                   # The three tools (search_listings / suggest_outfit / create_fit_card)
├── agent.py                   # run_agent: the rule-based planning loop + session state
├── app.py                     # Gradio UI: handle_query maps the session to 3 panels
├── tests/                     # 35 tests; Groq client mocked (no API key needed)
├── planning.md                # The frozen spec
└── requirements.txt           # Python dependencies
```

## Setup

**macOS / Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (get a free key at [console.groq.com](https://console.groq.com)):
```
GROQ_API_KEY=your_key_here
```

The key is only needed for the **live LLM paths** (`suggest_outfit`,
`create_fit_card`) and for running the full Gradio app. `search_listings` and the
entire test suite run **without a key** (see [Testing](#testing)).

Run the app:
```bash
python app.py
```
Then open the localhost URL shown in your terminal (usually
`http://localhost:7860`).

## The Mock Listings Dataset

`data/listings.json` contains 40 mock secondhand listings across categories (tops, bottoms, outerwear, shoes, accessories) and styles (vintage, y2k, grunge, cottagecore, streetwear, and more).

Each listing has: `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, and `platform`.

Load it with:
```python
from utils.data_loader import load_listings
listings = load_listings()
```

## The Wardrobe Schema

`data/wardrobe_schema.json` defines the format your agent uses to represent a user's existing wardrobe. It includes:

- `schema`: field definitions for a wardrobe item
- `example_wardrobe`: a sample wardrobe with 10 items you can use for testing
- `empty_wardrobe`: a starting template for a new user

Load an example wardrobe with:
```python
from utils.data_loader import get_example_wardrobe
wardrobe = get_example_wardrobe()
```

---

## Tool Inventory

The three tools live in `tools.py`. The documented signatures below match the
real function signatures exactly.

### Tool 1 — `search_listings`

```python
search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]
```

- **Purpose:** Find the best-matching secondhand listings for a request. This is
  a **pure, deterministic** keyword search over `data/listings.json` with **no
  LLM call** — the one tool fully unit-testable without an API key.
- **Inputs:**
  - `description` (`str`): free-text keywords describing the wanted item
    (e.g. `"vintage graphic tee"`).
  - `size` (`str | None`, default `None`): size filter, or `None` to skip size
    filtering. Matching is case-insensitive (`"M"` matches `"S/M"`).
  - `max_price` (`float | None`, default `None`): **inclusive** price ceiling, or
    `None` to skip price filtering.
- **Returns:** `list[dict]` — full listing dicts, **best match first**. Each dict
  carries every dataset field: `id`, `title`, `description`, `category`,
  `style_tags` (list), `size`, `condition`, `price` (float), `colors` (list),
  `brand` (nullable), `platform`. Returns `[]` when nothing matches; **never
  raises**.

### Tool 2 — `suggest_outfit`

```python
suggest_outfit(new_item: dict, wardrobe: dict) -> str
```

- **Purpose:** Suggest 1–2 outfits built around the found item, using the user's
  wardrobe when it has items. Uses Groq (`llama-3.3-70b-versatile`) via the
  shared `_get_groq_client()` helper at temperature `0.7`.
- **Inputs:**
  - `new_item` (`dict`): the listing the user is considering (the search winner).
  - `wardrobe` (`dict`): shape `{"items": [...]}`; the list **may be empty**.
- **Returns:** a non-empty `str`. With a populated wardrobe, outfits that name
  **specific** wardrobe pieces alongside `new_item`. With an empty wardrobe,
  general styling advice for `new_item` (an empty wardrobe is **not** an error).

### Tool 3 — `create_fit_card`

```python
create_fit_card(outfit: str, new_item: dict) -> str
```

- **Purpose:** Turn an outfit suggestion into a casual, share-ready OOTD caption.
  Uses Groq at a **higher temperature** (`0.95`) than `suggest_outfit` so
  captions vary across runs and inputs.
- **Inputs:**
  - `outfit` (`str`): the outfit text produced by `suggest_outfit()`.
  - `new_item` (`dict`): the listing dict.
- **Returns:** a 2–4 sentence casual caption that mentions the item `title`,
  `price`, and `platform` **once each**, naturally. When `outfit` is empty or
  whitespace-only, returns the sentinel `FIT_CARD_NO_OUTFIT_MSG` instead (no LLM
  call, no raise).

### Tool 4 — `price_comparison` *(stretch feature)*

```python
price_comparison(item: dict) -> str
```

- **Purpose:** Tell the shopper whether a found listing's price is good relative
  to comparable listings. Like `search_listings`, this is **pure and
  deterministic** — **no LLM call**, no API key.
- **Inputs:**
  - `item` (`dict`): a listing dict (typically the search winner). Uses
    `category`, `id`, and `price`.
- **Output:** a single readable sentence stating the verdict **and** its
  reasoning, e.g. *"Fairly priced — at $22.00 it's in line with the 14 comparable
  tops (range $15.00–$35.00, median $20.50)."* When the item's category has fewer
  than two comparables, or the price is missing/`None`, it returns
  `"Not enough comparable listings to assess this price."` — it **never raises**.
- **How the verdict is made:** comparables are all listings in the **same
  `category`**, excluding the item itself (matched by `id`). The item's price is
  compared to the comparables' **median**: `<= 0.85 × median` → *"a great deal"*;
  within `0.85×..1.15× median` → *"fairly priced"*; `> 1.15 × median` →
  *"priced above comparable items"*.

---

## Planning Loop

The loop lives in `run_agent(query, wardrobe)` in `agent.py`. It is **not** a
fixed pipeline that always runs all three tools — it makes decisions and can stop
early. The seven steps below describe both the actions and the decisions.

**Step 1 — Initialize.** `session = _new_session(query, wardrobe)`. The session
dict (see [State Management](#state-management)) is the only thing the loop
writes.

**Step 2 — Parse the query (RULE-BASED, deliberate choice).** Rather than ask an
LLM to parse the request, parsing is done with regex/string rules. This is a
deliberate decision: rule-based parsing is **deterministic** (same query always
yields the same parse), **free** (no API call on the hot path), and **testable
without a key** — the same reasons Tool 1 is rule-based. The parser splits the
query into `{"description", "size", "max_price"}`: it takes the text before the
first wardrobe/styling cue (e.g. `"i wear"`, `"how do i"`, `"style it"`), strips
recognized price phrases (`under $NN`, `below $NN`, `less than $NN`, `max $NN`,
bare `$NN`) and size tokens (`size X`, or a standalone whole-word letter size),
and uses what's left as the description. Word boundaries ensure the `s` in
`"jeans"` is never read as size `S`.

**Step 3 — Search, with a retry-with-fallback ladder (stretch feature).** Call
`search_listings(**parsed)`. **Decision:** if the result is **empty**, the loop
**retries by progressively relaxing only the filters that were actually set**,
taking the first relaxation that yields results: (1) **drop the size filter**
(keep description + max_price), (2) **drop the max_price filter** (keep
description + size), (3) **drop both**. When a retry succeeds it records a
human-readable `session["retry_note"]` (e.g. *"No exact matches for size M under
$30 — I relaxed the size filter and searched again."*) so the user is told what
was loosened. Only if **every** relaxation is still empty (a true no-results
query) does the loop set `session["error"]` and **return immediately** — it does
**not** call `suggest_outfit` or `create_fit_card`. The
early-return-on-true-no-results contract is preserved.

**Step 4 — Select, then assess price.** `selected_item = search_results[0]` — the
top-ranked listing, which flows into both downstream LLM tools. The loop then
calls `price_comparison(selected_item)` (Tool 4, pure Python, never raises) and
stores the one-sentence verdict in `session["price_assessment"]`.

**Step 5 — Suggest outfit, with a failure branch.** Call
`suggest_outfit(selected_item, wardrobe)` inside a `try`. An **empty wardrobe is
not a failure** (the tool returns general advice). **Decision:** on an exception
*or* a blank return, set the Step-5 error, **keep** `selected_item`, and return
early — the listing is preserved, but the fit card is skipped. Otherwise store
`outfit_suggestion`.

**Step 6 — Fit card, with a failure branch.** **Guard** that
`outfit_suggestion` is non-empty, then call
`create_fit_card(outfit_suggestion, selected_item)` inside a `try`. **Decision:**
on an exception, set the Step-6 error but **keep** both `outfit_suggestion` and
`selected_item` — the user loses neither the listing nor the outfit just because
the caption step hiccuped. Otherwise store `fit_card`.

**Step 7 — Return.** Return the completed `session`.

**How the loop knows it's done:** there is no open-ended reasoning loop or
re-prompting. The work is a fixed sequence of dependent steps; the loop returns
as soon as it either (a) hits an early-exit/failure branch, or (b) reaches the
end of Step 6. Raw exceptions are logged to **stderr** for debugging; the user
only ever sees the friendly `session["error"]` strings.

---

## State Management

The `session` dict created by `_new_session()` is the **single source of truth**
for one interaction. **`run_agent` is the only writer** — the three tools are
pure functions that read their arguments and return values; they never touch the
session. There is **no re-prompting** of the user mid-run.

Data flows in one direction through the session:

- `selected_item` (`= search_results[0]`) is threaded into **both** LLM tools
  (`suggest_outfit` in Step 5 and `create_fit_card` in Step 6).
- `outfit_suggestion` (from Step 5) flows into `create_fit_card` in Step 6.

### Field read/write table

| Field               | Written by              | Read by                                    |
|---------------------|-------------------------|--------------------------------------------|
| `query`             | `_new_session` (Step 1) | Step 2 (parse)                             |
| `parsed`            | Step 2                  | Step 3 (search args), Step 3 error message |
| `search_results`    | Step 3                  | Step 4 (select)                            |
| `selected_item`     | Step 4                  | Step 5 + Step 6 (into both LLM tools), app |
| `wardrobe`          | `_new_session` (Step 1) | Step 5 (suggest_outfit)                    |
| `outfit_suggestion` | Step 5                  | Step 6 (guard + create_fit_card), app      |
| `fit_card`          | Step 6                  | app (panel 3)                              |
| `error`             | Steps 3 / 5 / 6         | app (decides panel mapping)                |
| `retry_note`        | Step 3 (on retry win)   | app (prepends 🔁 line to panel 1)          |
| `price_assessment`  | Step 4                  | app (appends 💰 line to panel 1)           |

### Partial-success → 3-panel mapping

The Gradio UI has three panels: panel 1 = listing, panel 2 = outfit idea,
panel 3 = fit card. `handle_query` in `app.py` maps the session to panels so that
**partial results are preserved** even when a later step fails — because the loop
keeps `selected_item` and `outfit_suggestion` in the session on later failures,
the user never loses a good listing or outfit just because the *next* step failed.

| Case                | Panel 1 (listing) | Panel 2 (outfit) | Panel 3 (fit card) |
|---------------------|-------------------|------------------|--------------------|
| (a) search failed   | `error`           | `""`             | `""`               |
| (b) outfit failed   | listing text      | `error`          | `""`               |
| (c) fit card failed | listing text      | outfit           | `error`            |
| (d) success         | listing text      | outfit           | fit card           |

On any path where a listing exists (b/c/d), `handle_query` **enriches panel 1**
without adding new panels: when set, `retry_note` is prepended as a `🔁 …` line
above the listing and `price_assessment` is appended as a `💰 Price check: …`
line below it.

---

## Error Handling and Fail Points

Each tool's failure mode is handled deterministically in `run_agent`. All caught
exceptions are logged to **stderr**; users only ever see the friendly strings.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No listing matches the query/filters | Tool returns `[]` (never raises). The loop sets `session["error"]` echoing the parsed description and **returns early** — `suggest_outfit` is never called. |
| `suggest_outfit` | Empty wardrobe | **Not an error.** The empty-wardrobe branch makes a real LLM call for general styling advice and returns it. |
| `suggest_outfit` | LLM exception or blank return | Step 5 sets `session["error"]`, **keeps** the listing, and returns early (no fit card). |
| `create_fit_card` | Empty/whitespace `outfit` input | Tool returns the sentinel `FIT_CARD_NO_OUTFIT_MSG` — **no network call, no raise**. The loop also guards `outfit_suggestion` before calling, so this is primarily a unit-test concern. |
| `create_fit_card` | LLM exception | Step 6 sets `session["error"]` but **keeps** both the listing and the outfit. |

### Concrete examples (verified during testing)

**1. `search_listings` — no results.** A direct call with deliberately
impossible filters returns an empty list:

```python
>>> from tools import search_listings
>>> search_listings('designer ballgown', size='XXS', max_price=5)
[]
```

Run through the agent (query `"designer ballgown size XXS under $5"`), the loop
surfaces:

> I couldn't find anything matching "designer ballgown" with those filters. Try
> loosening them — raise your budget, drop the size, or use broader words.

**2. `suggest_outfit` — empty wardrobe vs. LLM failure.** An empty wardrobe is
**not** an error: that branch still calls the LLM and returns general styling
advice. Only a genuine LLM exception or blank return becomes the Step-5 error:

> I found a great piece for you, but I couldn't generate outfit ideas right now —
> here's the listing. Try again in a moment.

**3. `create_fit_card` — empty outfit (sentinel, verified with no network
call).** A direct call with an empty `outfit` returns the module-level sentinel
without touching the network:

```python
>>> from tools import create_fit_card, FIT_CARD_NO_OUTFIT_MSG
>>> create_fit_card('', {'title': 'X'}) == FIT_CARD_NO_OUTFIT_MSG
True
```

On a true LLM failure (with a real outfit present), Step 6 surfaces:

> Your outfit idea is ready, but I couldn't write the shareable fit card this
> time. Here are the listing and styling notes — copy them straight to your post
> or hit Find it again.

---

## Interaction Walkthrough

A complete, honest trace for the canonical query, verified by replaying the
scoring rules against `data/listings.json`.

**User query:** `vintage graphic tee under $30`

**Step 1 — Parse (rule-based).**
- Tool: `_parse_query` (rule-based, no LLM)
- Input: `"vintage graphic tee under $30"`
- Why this step: extract structured search parameters deterministically and free
  of charge before touching the dataset.
- Output: `{"description": "vintage graphic tee", "size": None, "max_price": 30.0}`
  — `"under $30"` matched the price pattern and was stripped; no size token
  present.

**Step 2 — Search and select.**
- Tool: `search_listings(description="vintage graphic tee", size=None, max_price=30.0)`
- Input: the parsed parameters above.
- Why this tool: find candidate listings ranked by keyword overlap.
- Output: query token set `{vintage, graphic, tee}`. Three listings priced
  `<= $30` score 3 — `lst_002` ($18), `lst_033` ($19), `lst_006` ($24). The
  deterministic tie-break (`price ASC`, then `id ASC`) makes **`lst_002` the
  winner**: *"Y2K Baby Tee — Butterfly Print"*, **$18.00**, on **depop**. This is
  stored as `selected_item`.

**Step 3 — Suggest outfit.**
- Tool: `suggest_outfit(selected_item, wardrobe)` (example wardrobe)
- Input: the `lst_002` dict + the 10-item example wardrobe.
- Why this tool: build a concrete outfit around the found tee using owned pieces.
- Output: a 1–2 outfit suggestion naming specific wardrobe items, e.g. *"Tuck the
  butterfly baby tee into your baggy dark-wash straight-leg jeans, throw the
  vintage black denim jacket over the top, and finish with the chunky white
  sneakers and the black crossbody bag for an easy Y2K-leaning everyday look."*
  Stored as `outfit_suggestion`.

**Step 4 — Create fit card.**
- Tool: `create_fit_card(outfit_suggestion, selected_item)`
- Input: the outfit text above + the `lst_002` dict.
- Why this tool: turn the styling notes into a casual, share-ready caption.
- Output: a 2–4 sentence OOTD caption mentioning the tee title, `$18`, and
  `depop` once each. Stored as `fit_card`.

**Final output to the user (three panels):**
- **Panel 1 — Top listing found:** `Y2K Baby Tee — Butterfly Print` · $18.00 ·
  depop · size S/M · excellent · style tags y2k/vintage/graphic.
- **Panel 2 — Outfit idea:** the `suggest_outfit` text above.
- **Panel 3 — Your fit card:** the casual share-ready caption from
  `create_fit_card`.

---

## Stretch Features

Two stretch features extend the baseline agent. Both are surfaced in the existing
three-panel UI with **no layout change** — they only enrich panel 1.

### 1. Retry Logic with Fallback

When the initial `search_listings(**parsed)` returns nothing, `run_agent` does not
give up immediately. It **progressively relaxes only the filters that were
actually set**, taking the first relaxation that yields results:

1. drop the **size** filter (keep description + max_price),
2. drop the **max_price** filter (keep description + size),
3. drop **both** (keep description only).

When a relaxed search succeeds, the loop records a human-readable
`session["retry_note"]` — e.g. *"No exact matches for size M under $30 — I relaxed
the size filter and searched again."* — so the user knows the results are a
fallback and what was loosened. The note is prepended to panel 1 as a `🔁` line.
A genuinely impossible query (e.g. *"designer ballgown size XXS under $5"*) still
exhausts every relaxation, sets the hard no-results `session["error"]`, and
returns **before any LLM call** — the original early-return contract is intact.

### 2. Price Comparison

After selecting the top listing, `run_agent` calls `price_comparison(selected_item)`
(Tool 4) and stores the result in `session["price_assessment"]`. The comparison is
made against **same-category comparables** (all listings sharing the item's
`category`, excluding the item itself by `id`): it computes those comparables'
**min, max, and median** and classifies the item's price by its ratio to the
median — `≤ 0.85×` is *"a great deal"*, within `0.85×..1.15×` is *"fairly priced"*,
and `> 1.15×` is *"priced above comparable items"*. The one-sentence verdict (with
reasoning) is appended to panel 1 as a `💰 Price check:` line. If a category has
fewer than two comparables, a graceful sentence is returned and the tool never
raises.

---

## Spec Reflection

**One way `planning.md` helped during implementation.** Writing the search
scoring and tie-break rules in the spec *before* any code surfaced a ranking bug
on paper. The original idea let parser "chatter" (stray styling words) inflate
scores, and without an explicit tie-break the "winner" among equally scored
listings was effectively arbitrary. The spec fixed both up front: it pinned a
total, deterministic order (`score DESC`, then `price ASC`, then `id ASC`) and a
distinct-token scoring rule. When `search_listings` was implemented, the
canonical query `"vintage graphic tee under $30"` resolved to **`lst_002`** as
designed, with no guesswork — the test simply confirmed the spec.

**One divergence from the spec, and why.** The spec said the planning loop guards
`outfit_suggestion` before calling `create_fit_card`, so the sentinel is "the
tool's own unit-test concern." During implementation the LLM tools also needed a
**None-content guard** (the Groq response `content` can be `None`, not just empty)
and **`max_tokens` caps** — additions that came out of the perf/security reviews
rather than the original spec. These coerce `None` to `""` so a blank LLM result
is treated as a Step-5/Step-6 failure and routed into `session["error"]`, rather
than crashing or rendering an empty panel. This strengthens, rather than
contradicts, the spec's error-handling intent.

---

## AI Usage

This project was built spec-first: `planning.md` was completed before code, and
specific sections were handed to an AI assistant to generate implementations,
each of which was reviewed, tested, and corrected before being trusted.

**Instance 1 — `search_listings` from the Tool 1 spec.**
- **Given to the AI:** the "Tool 1" section of `planning.md` (the deterministic
  keyword-overlap scoring, the size-matching rules, and the price/size-only edge
  case).
- **Produced:** a pure `search_listings` using `load_listings()` — tokenize,
  filter by price/size, score by distinct query-token overlap, drop zero-score
  listings, sort by the spec's order.
- **Changed / overridden before use:** verified against real queries on the
  dataset, confirming the deterministic tie-break (`"vintage graphic tee under
  $30"` → `lst_002`). Size matching was **tightened** so `size='s'` does not
  match `'US 7'` or `'W28'` (equality against whole normalized size tokens, never
  a substring), which the first pass got subtly wrong.

**Instance 2 — `run_agent` from the loop/state/error sections.**
- **Given to the AI:** the "Planning Loop", "State Management", and "Error
  Handling" sections of `planning.md`.
- **Produced:** the seven-step `run_agent` with the early-exit on empty search
  and the session as single source of truth.
- **Changed / overridden before use:** added `try/except` routing of LLM failures
  into `session["error"]` (from the security review) and confirmed the no-results
  branch skips `suggest_outfit` entirely. The same review-driven **None-content
  guard** and **`max_tokens` caps** (see [Spec Reflection](#spec-reflection))
  were applied to both LLM tools so blank/None responses become friendly errors
  rather than crashes or empty panels.

---

## Testing

```bash
python -m pytest tests/
```

This runs **35 tests** with the Groq client **mocked**, so **no API key is
needed** — `search_listings`, the rule-based parser, `price_comparison`, the
planning-loop branches (no-results, outfit failure, fit-card failure,
partial-success preservation, retry-with-fallback), and the app's panel mapping
(including the enriched 🔁/💰 panel-1 lines) are all covered offline. The mocked
tests also assert the empty-wardrobe branch still calls the client and that the
None-content guard coerces a `None` LLM response correctly.

The **live LLM paths** (`suggest_outfit` and `create_fit_card` end to end, and
the Gradio app) require `GROQ_API_KEY` in `.env`.

---

## Where to Start

1. Read `planning.md` — it is the frozen spec this README mirrors.
2. Verify the data loads: `python utils/data_loader.py`.
3. Run the tests offline: `python -m pytest tests/`.
4. Try the agent CLI: `python agent.py` (happy path + no-results path).
5. Launch the UI: `python app.py`.
