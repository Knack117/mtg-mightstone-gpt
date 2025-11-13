import json

import pytest

from services import edhrec


class DummyResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)


class DummySession:
    def __init__(self, responses):
        self._responses = responses
        self.requested = []

    def get(self, url, headers=None, timeout=None):
        self.requested.append(url)
        response = self._responses.get(url)
        if response is None:
            raise AssertionError(f"Unexpected URL {url}")
        return response

    def close(self):
        pass


def _build_commander_html_payload():
    next_data = {
        "props": {
            "pageProps": {
                "commander": {
                    "metadata": {
                        "tagCloud": {
                            "groups": [
                                {
                                    "tags": [
                                        {
                                            "name": "Proliferate",
                                            "deckCount": 1234,
                                            "slug": "/tags/proliferate",
                                        },
                                        {
                                            "tag": {"name": "Angels"},
                                            "deckCount": 987,
                                            "url": "/tags/angels",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    "themes": [
                        {"name": "Proliferate"},
                        {"name": "Angels"},
                        {"name": "Kindred"},
                    ],
                },
                "data": {
                    "container": {
                        "json_dict": {
                            "cardlists": [
                                {
                                    "header": "High Synergy Cards",
                                    "cards": [
                                        {
                                            "name": "Evolution Sage",
                                            "synergy": 0.32,
                                            "num_decks": 120,
                                            "potential_decks": 300,
                                        },
                                        {
                                            "name": "Sol Ring",
                                            "synergy": 0.05,
                                            "num_decks": 280,
                                            "potential_decks": 300,
                                        },
                                    ],
                                },
                                {
                                    "header": "Creatures",
                                    "cards": [
                                        {
                                            "name": "Atraxa, Praetors' Voice",
                                            "num_decks": 150,
                                            "potential_decks": 300,
                                        }
                                    ],
                                },
                            ]
                        }
                    }
                },
            }
        }
    }

    html = f"""
    <html>
      <head>
        <title>Atraxa Summary</title>
        <meta name=\"description\" content=\"Test commander summary\" />
      </head>
      <body>
        <h2>Tags</h2>
        <a href=\"/tags/proliferate\" data-tag-count=\"1234\">Proliferate (1,234)</a>
        <a href=\"/tags/angels\"><span>Angels</span><span>987 decks</span></a>
        <a href=\"/themes/kindred\" data-tag-count=\"555\">Kindred</a>
        <a href=\"/themes/commander-themes\" data-tag-count=\"777\">Themes</a>
        <script id=\"__NEXT_DATA__\" type=\"application/json\">{json.dumps(next_data)}</script>
      </body>
    </html>
    """
    return html


def test_fetch_commander_summary_parses_sections_and_tags():
    name = "Atraxa, Praetors' Voice"
    slug = "atraxa-praetors-voice"
    url = f"https://edhrec.com/commanders/{slug}"
    html = _build_commander_html_payload()
    session = DummySession({url: DummyResponse(html)})

    summary = edhrec.fetch_commander_summary(name, session=session)

    assert summary["slug"] == slug
    assert summary["budget"] is None
    assert summary["source_url"] == url

    categories = summary["categories"]
    assert "High Synergy Cards" in categories
    synergy_cards = categories["High Synergy Cards"]
    assert synergy_cards[0]["name"] == "Evolution Sage"
    assert synergy_cards[0]["synergy_percent"] == 32.0
    assert synergy_cards[0]["inclusion_percent"] == 40.0
    assert synergy_cards[0]["deck_count"] == 120
    assert synergy_cards[0]["potential_deck_count"] == 300

    assert categories["Creatures"][0]["name"] == "Atraxa, Praetors' Voice"
    assert categories["Creatures"][0]["deck_count"] == 150
    assert categories["Creatures"][0]["potential_deck_count"] == 300
    assert isinstance(summary["tags"], list)
    tags_by_name = {tag["name"]: tag for tag in summary["tags"]}
    assert tags_by_name["Proliferate"]["deck_count"] == 1234
    assert tags_by_name["Angels"]["deck_count"] == 987

    assert {tag["name"] for tag in summary["tags"]} >= {"Proliferate", "Angels"}
    assert "Kindred" not in tags_by_name
    assert "Themes" not in tags_by_name

    top_tag = summary["top_tags"][0]
    assert top_tag["name"] == "Proliferate"
    assert top_tag["deck_count"] == 1234


def test_fetch_commander_summary_reads_navigation_panel_tags():
    name = "Heroes in a Half Shell"
    slug = "heroes-in-a-half-shell"
    url = f"https://edhrec.com/commanders/{slug}"

    next_data = {
        "props": {
            "pageProps": {
                "commander": {},
                "data": {"container": {"json_dict": {"cardlists": []}}},
            }
        }
    }

    html = f"""
    <html>
      <body>
        <div class=\"NavigationPanel_tags__abc\">
          <a class=\"LinkHelper_container__tag btn btn-sm btn-secondary\" href=\"/tags/ninjas\">
            <span class=\"NavigationPanel_label__abc\">Ninjas</span>
            <span class=\"badge bg-light\">1.5k</span>
          </a>
          <a class=\"LinkHelper_container__tag btn btn-sm btn-secondary\" href=\"/themes/mutant\">
            <span class=\"NavigationPanel_label__abc\">Mutant</span>
            <span class=\"badge bg-light\">250</span>
          </a>
        </div>
        <script id=\"__NEXT_DATA__\" type=\"application/json\">{json.dumps(next_data)}</script>
      </body>
    </html>
    """

    session = DummySession({url: DummyResponse(html)})

    summary = edhrec.fetch_commander_summary(name, session=session)

    tags_by_name = {tag["name"]: tag for tag in summary["tags"]}
    assert tags_by_name["Ninjas"]["deck_count"] == 1500
    assert tags_by_name["Mutant"]["deck_count"] == 250


def test_fetch_commander_summary_handles_missing_tag_counts():
    name = "Kibo, Uktabi Prince"
    slug = "kibo-uktabi-prince"
    url = f"https://edhrec.com/commanders/{slug}"

    next_data = {
        "props": {
            "pageProps": {
                "commander": {
                    "metadata": {
                        "tagCloud": {
                            "groups": [
                                {"tags": [{"name": "Token Swarm"}, {"name": "Themes"}]}
                            ]
                        }
                    },
                    "themes": [
                        {"name": "Card Draw"},
                        {"name": "Kindred"},
                    ],
                },
                "data": {
                    "container": {
                        "json_dict": {
                            "cardlists": [
                                {
                                    "header": "High Synergy Cards",
                                    "cards": [
                                        {
                                            "name": "Sol Ring",
                                            "synergy": 0.1,
                                            "num_decks": 10,
                                            "potential_decks": 100,
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                },
            }
        }
    }

    html = f"""
    <html>
      <body>
        <section>
          <h2>Tags</h2>
          <a href=\"/themes/kindred\">Kindred</a>
          <a href=\"/themes/commander-themes\">Themes</a>
        </section>
        <script id=\"__NEXT_DATA__\" type=\"application/json\">{json.dumps(next_data)}</script>
      </body>
    </html>
    """

    session = DummySession({url: DummyResponse(html)})

    summary = edhrec.fetch_commander_summary(name, session=session)

    tag_names = [entry["name"] for entry in summary["tags"]]

    assert "Kindred" not in tag_names
    assert "Themes" not in tag_names
    assert tag_names == ["Card Draw", "Token Swarm"]
    assert all(entry["deck_count"] is None for entry in summary["tags"])

def test_fetch_commander_summary_budget_validation():
    with pytest.raises(ValueError):
        edhrec.fetch_commander_summary("Atraxa, Praetors' Voice", budget="invalid", session=DummySession({}))


def test_fetch_commander_tag_theme_returns_cards():
    name = "Atraxa, Praetors' Voice"
    slug = "atraxa-praetors-voice"
    tag_slug = "proliferate"
    url = f"https://edhrec.com/commanders/{slug}/{tag_slug}"
    html = _build_commander_html_payload()
    session = DummySession({url: DummyResponse(html)})

    data = edhrec.fetch_commander_tag_theme(name, tag_slug, session=session)

    assert data["tag"] == tag_slug
    assert data["categories"]["High Synergy Cards"]
    assert data["header"].startswith(name.split(",")[0])


def test_fetch_tag_theme_for_identity():
    tag_slug = "plus-1-plus-1-counters"
    identity = "mono-green"
    url = f"https://edhrec.com/tags/{tag_slug}/{identity}"
    html = _build_commander_html_payload()
    session = DummySession({url: DummyResponse(html)})

    data = edhrec.fetch_tag_theme(tag_slug, identity=identity, session=session)

    assert data["tag"] == tag_slug
    assert data["identity"] == identity
    assert "High Synergy Cards" in data["categories"]


def test_fetch_tag_index_parses_anchor_lists():
    html = """
    <html>
      <body>
        <a href=\"/tags/proliferate\" data-tag-count=\"1234\">Proliferate (1,234)</a>
        <a href=\"/tags/angels/mono-white\">Angels 567 decks</a>
      </body>
    </html>
    """
    url = "https://edhrec.com/tags"
    session = DummySession({url: DummyResponse(html)})

    index = edhrec.fetch_tag_index(session=session)

    assert index["source_url"] == url
    entries = {entry["slug"]: entry for entry in index["tags"]}
    assert "proliferate" in entries
    assert entries["proliferate"]["deck_count"] == 1234
    assert entries["angels"]["identity"] == "mono-white"
