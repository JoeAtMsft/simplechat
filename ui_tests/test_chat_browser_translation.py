# test_chat_browser_translation.py
"""
Template tests for suppressing browser translation prompts on the chats page.
Version: 0.261.064
Implemented in: 0.261.064

These tests verify the rendered chat head opts out of browser translation
without changing translation behavior on other pages.
"""

from pathlib import Path

from flask import Flask
import pytest


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "application" / "single_app" / "templates"


@pytest.mark.parametrize("speech_enabled", [False, True])
def test_chat_head_disables_browser_translation(speech_enabled):
    """Include the browser translation opt-out regardless of speech settings."""
    app = Flask(__name__, template_folder=str(TEMPLATE_ROOT))

    with app.test_request_context("/chats"):
        template = app.jinja_env.get_template("chats.html")
        context = template.new_context({
            "app_settings": {"enable_speech_to_text_input": speech_enabled},
        })
        head = "".join(template.blocks["head"](context))

    assert '<meta name="google" content="notranslate">' in head
    assert 'href="/static/css/chats.css"' in head
    assert ("chat-speech-input.css" in head) == speech_enabled


def test_shared_base_keeps_english_language_and_translation_available():
    """Keep the translation opt-out scoped to chats rather than every page."""
    base_source = (TEMPLATE_ROOT / "base.html").read_text(encoding="utf-8")

    assert 'lang="en"' in base_source
    assert "notranslate" not in base_source
    assert 'translate="no"' not in base_source
