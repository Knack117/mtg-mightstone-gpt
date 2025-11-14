from handlers.edhrec_average_deck import edhrec_average_deck
from handlers.edhrec_budget import edhrec_budget_comparison

TOOL_REGISTRY = {
    "edhrec_average_deck": edhrec_average_deck,
    "edhrec_budget_comparison": edhrec_budget_comparison,
}
