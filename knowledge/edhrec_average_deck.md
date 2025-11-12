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
3. Proceed once `container.collections[0].items` returns a simple `{count, name}` list with roughly 60 or more cards.

Example lookup:

```
GET https://mtg-mightstone-gpt.onrender.com/edhrec/average-deck
  ?name=Donatello%2C%20the%20Brains%20%2F%2F%20Michelangelo%2C%20the%20Heart

tool: edhrec_average_deck name=Donatello, the Brains // Michelangelo, the Heart bracket=all
```
