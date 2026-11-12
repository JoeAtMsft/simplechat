# test_chat_agent_conversation_restore.py
"""
UI test for restoring the agent dropdown from a conversation.
Version: 0.261.067
Implemented in: 0.261.067

This test ensures reopening a conversation selects the agent its messages
recorded, even when the user-level saved agent differs, and that a manual
dropdown change stops the conversation selection from overriding the user.
"""

import json
from pathlib import Path

import pytest


ORIGIN = "http://agent-restore.test"
STATIC_ROOT = Path(__file__).resolve().parents[1] / "application" / "single_app" / "static"

HARNESS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Agent restore</title></head>
<body>
<button id="enable-agents-btn" class="active" type="button">Agents</button>
<div id="model-select-container"></div>
<div id="agent-select-container">
  <select id="agent-select"></select>
  <div id="agent-dropdown">
    <button id="agent-dropdown-button" type="button"><span class="chat-searchable-select-text"></span></button>
    <div id="agent-dropdown-menu">
      <input id="agent-search-input" type="text" aria-label="Search agents">
      <div id="agent-dropdown-items"></div>
    </div>
  </div>
</div>
</body></html>"""

AGENTS = [
    {"id": "synthetic-id", "name": "synthetic_agent", "display_name": "Synthetic Data Agent", "is_global": True, "is_group": False},
    {"id": "operational-id", "name": "operational_agent", "display_name": "Operational Efficiency Agent", "is_global": True, "is_group": False},
]

OPERATIONAL_METADATA = {
    "agent_id": "operational-id",
    "selected_agent": "operational_agent",
    "is_global": True,
    "is_group": False,
    "group_id": None,
}


def _json(route, payload):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _open_harness(page, enable_agents):
    saved = {"selected_agent": {"id": "synthetic-id", "name": "synthetic_agent", "is_global": True, "is_group": False}}

    def handle_settings(route):
        _json(route, {**saved, "settings": {"enable_agents": enable_agents}})

    def handle_selected_agent(route):
        saved["selected_agent"] = json.loads(route.request.post_data or "{}").get("selected_agent")
        _json(route, {"success": True})

    def handle_origin(route):
        path = route.request.url[len(ORIGIN):].split("?", 1)[0]
        if path.startswith("/static/"):
            asset = STATIC_ROOT / path[len("/static/"):]
            if asset.is_file():
                route.fulfill(status=200, content_type="text/javascript", body=asset.read_text(encoding="utf-8"))
                return
        if path == "/chats":
            route.fulfill(status=200, content_type="text/html", body=HARNESS_HTML)
            return
        _json(route, {})

    page.route(f"{ORIGIN}/**", handle_origin)
    page.route(f"{ORIGIN}/api/user/settings", handle_settings)
    page.route(f"{ORIGIN}/api/user/settings/selected_agent", handle_selected_agent)
    page.add_init_script(f"window.chatAgentOptions = {json.dumps(AGENTS)};")
    page.goto(f"{ORIGIN}/chats")
    page.evaluate("async () => { window.agentsModule = await import('/static/js/chat/chat-agents.js'); }")
    return saved


def _selected_agent_name(page):
    return page.evaluate(
        "() => document.getElementById('agent-select').selectedOptions[0]?.dataset.name || ''"
    )


@pytest.fixture
def page(playwright):
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    yield context.new_page()
    context.close()
    browser.close()


@pytest.mark.ui
def test_open_conversation_selects_its_agent_and_keeps_it_after_rebuild(page):
    saved = _open_harness(page, enable_agents=True)
    page.wait_for_function("() => document.querySelectorAll('#agent-select option').length === 2")
    assert _selected_agent_name(page) == "synthetic_agent"

    restored = page.evaluate(
        "metadata => window.agentsModule.restoreAgentSelectionFromConversationMetadata(metadata)",
        OPERATIONAL_METADATA,
    )

    assert restored is True
    assert _selected_agent_name(page) == "operational_agent"
    assert page.locator("#agent-dropdown-button").inner_text().strip() == "Operational Efficiency Agent"
    assert saved["selected_agent"]["id"] == "operational-id"

    page.evaluate("() => window.agentsModule.populateAgentDropdown()")
    assert _selected_agent_name(page) == "operational_agent"


@pytest.mark.ui
def test_conversation_agent_applies_when_dropdown_loads_after_messages(page):
    saved = _open_harness(page, enable_agents=False)
    assert page.evaluate("() => document.querySelectorAll('#agent-select option').length") == 0

    restored = page.evaluate(
        "metadata => window.agentsModule.restoreAgentSelectionFromConversationMetadata(metadata)",
        OPERATIONAL_METADATA,
    )
    assert restored is False
    assert saved["selected_agent"]["name"] == "synthetic_agent"

    page.evaluate("() => window.agentsModule.populateAgentDropdown()")
    assert _selected_agent_name(page) == "operational_agent"


@pytest.mark.ui
def test_unavailable_or_missing_conversation_agent_leaves_dropdown_alone(page):
    _open_harness(page, enable_agents=True)
    page.wait_for_function("() => document.querySelectorAll('#agent-select option').length === 2")

    removed = page.evaluate(
        "metadata => window.agentsModule.restoreAgentSelectionFromConversationMetadata(metadata)",
        {**OPERATIONAL_METADATA, "agent_id": "deleted-id", "selected_agent": "deleted_agent"},
    )
    cleared = page.evaluate(
        "() => window.agentsModule.restoreAgentSelectionFromConversationMetadata(null)"
    )

    assert removed is False
    assert cleared is False
    assert _selected_agent_name(page) == "synthetic_agent"


@pytest.mark.ui
def test_manual_agent_change_overrides_the_conversation_agent(page):
    _open_harness(page, enable_agents=True)
    page.wait_for_function("() => document.querySelectorAll('#agent-select option').length === 2")
    page.evaluate(
        "metadata => window.agentsModule.restoreAgentSelectionFromConversationMetadata(metadata)",
        OPERATIONAL_METADATA,
    )

    page.evaluate(
        """
        () => {
            const select = document.getElementById('agent-select');
            select.value = Array.from(select.options).find(option => option.dataset.name === 'synthetic_agent').value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """
    )
    page.wait_for_function(
        "() => document.getElementById('agent-select').selectedOptions[0]?.dataset.name === 'synthetic_agent'"
    )
    page.evaluate("() => window.agentsModule.populateAgentDropdown()")

    assert _selected_agent_name(page) == "synthetic_agent"
