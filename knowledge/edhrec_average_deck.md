# EDHREC Average Deck Retrieval

To fetch an EDHREC Average Deck for a commander:

1. Prefer the Mightstone connector:
   ```json
   file_search.msearch({
     "queries": [
       "tool: edhrec_average_deck name=Eddie Brock bracket=upgraded"
     ],
     "intent": "invoke Mightstone edhrec_average_deck",
     "source_filter": ["mtg_mightstone_gpt_onrender_com__jit_plugin"]
   })
   ```
2. If the connector fails or returns no deck, fall back to the HTTP endpoint:
   ```
   GET https://mtg-mightstone-gpt.onrender.com/edhrec/average-deck?name=Eddie%20Brock&bracket=upgraded
   ```
3. Proceed once the response includes a `cards` list of `{name, qty}` entries with roughly 99 cards and `error: null`.

## Bracket Options

EDHREC categorizes decks by power level (brackets) and budget constraints:

### Power Level Brackets
- **all** (default) - General average deck
- **exhibition** (Bracket 1) - Precon-level power
- **core** (Bracket 2) - Casual/starter builds
- **upgraded** (Bracket 3) - Optimized casual decks
- **optimized** (Bracket 4) - High-power builds
- **cedh** (Bracket 5) - Competitive EDH

### Budget Variants
Add `/budget` or `/expensive` to any bracket:
- **budget** - Budget-friendly version (All budget: `/budget`)
- **expensive** - High-budget version (All expensive: `/expensive`)
- **exhibition/budget** - Budget precon-level (Bracket 1 budget)
- **exhibition/expensive** - Expensive precon-level (Bracket 1 expensive)
- **core/budget** - Budget casual (Bracket 2 budget)
- **core/expensive** - Expensive casual (Bracket 2 expensive)
- **upgraded/budget** - Budget optimized (Bracket 3 budget)
- **upgraded/expensive** - Expensive optimized (Bracket 3 expensive)
- **optimized/budget** - Budget high-power (Bracket 4 budget)
- **optimized/expensive** - Expensive high-power (Bracket 4 expensive)
- **cedh/budget** - Budget cEDH (Bracket 5 budget)
- **cedh/expensive** - Expensive cEDH (Bracket 5 expensive)

### Numeric Shortcuts
You can use numbers 1-5 for brackets:
- **1** = exhibition (Bracket 1)
- **2** = core (Bracket 2)
- **3** = upgraded (Bracket 3)
- **4** = optimized (Bracket 4)
- **5** = cedh (Bracket 5)

## Example Lookups

```
# Default (All)
tool: edhrec_average_deck name=Donatello, the Brains // Michelangelo, the Heart bracket=all

# Bracket 3 (Upgraded)
tool: edhrec_average_deck name=Atraxa, Praetors' Voice bracket=upgraded

# Bracket 3 Budget
tool: edhrec_average_deck name=Atraxa, Praetors' Voice bracket=upgraded/budget

# cEDH (Bracket 5)
tool: edhrec_average_deck name=Thrasios, Triton Hero bracket=cedh

# Budget version (All Budget)
tool: edhrec_average_deck name=Edgar Markov bracket=budget

# Using numeric bracket
tool: edhrec_average_deck name=The Ur-Dragon bracket=3
```

## URLs
- All: `https://edhrec.com/average-decks/commander-name`
- All Budget: `https://edhrec.com/average-decks/commander-name/budget`
- All Expensive: `https://edhrec.com/average-decks/commander-name/expensive`
- Bracket 1: `https://edhrec.com/average-decks/commander-name/exhibition`
- Bracket 1 Budget: `https://edhrec.com/average-decks/commander-name/exhibition/budget`
- Bracket 2: `https://edhrec.com/average-decks/commander-name/core`
- Bracket 3: `https://edhrec.com/average-decks/commander-name/upgraded`
- Bracket 4: `https://edhrec.com/average-decks/commander-name/optimized`
- Bracket 5: `https://edhrec.com/average-decks/commander-name/cedh`

