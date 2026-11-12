# test_m365_json_response_finalization.py
"""
Regression coverage for Microsoft 365 JSON response finalization.
Version: 0.261.065
Implemented in: 0.261.065

Runs the production response hook with real Flask responses without loading
the application's cloud-dependent bootstrap.
"""

import ast
from pathlib import Path
from unittest.mock import Mock

from flask import Flask, Response
import pytest


APP = Path(__file__).resolve().parents[1] / "application" / "single_app"
SCHEMA = APP / "static" / "json" / "schemas" / "plugin.schema.json"


@pytest.fixture
def runtime():
    app = Flask(__name__, static_folder=str(APP / "static"))
    app.config["TESTING"] = True
    complete = Mock()
    path = APP / "app.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "finalize_m365_json_request"
    ]
    assert len(nodes) == 1, "The production M365 response hook was not found."
    namespace = {"app": app, "complete_m365_request": complete}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return app, complete, namespace["finalize_m365_json_request"]


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_static_plugin_schema_is_served_without_json_finalization(runtime, method):
    app, complete, _ = runtime
    with app.test_client() as client:
        response = client.open("/static/json/schemas/plugin.schema.json", method=method)
        assert response.status_code == 200
        assert response.is_json
        assert response.get_data() == (SCHEMA.read_bytes() if method == "GET" else b"")
    complete.assert_not_called()


def test_static_plugin_schema_conditional_request_is_preserved(runtime):
    app, complete, _ = runtime
    with app.test_client() as client:
        response = client.get("/static/json/schemas/plugin.schema.json")
        cached = client.get(
            "/static/json/schemas/plugin.schema.json",
            headers={"If-None-Match": response.headers["ETag"]},
        )
        assert cached.status_code == 304
        assert cached.get_data() == b""
    complete.assert_not_called()


@pytest.mark.parametrize("passthrough,streamed", [(True, False), (False, True), (True, True)])
def test_file_and_streamed_json_responses_are_not_read_or_consumed(runtime, passthrough, streamed):
    _, complete, finalize = runtime
    chunks = [b'{"success":', b"true}"]
    consumed = []

    def generate():
        for chunk in chunks:
            consumed.append(chunk)
            yield chunk

    response = Response(
        generate() if streamed else chunks,
        mimetype="application/json",
        direct_passthrough=passthrough,
    )
    response.get_json = Mock(side_effect=AssertionError("The response body must not be parsed."))
    try:
        result = finalize(response)
        assert result is response
        assert response.direct_passthrough is passthrough
        assert response.is_streamed is streamed
        assert consumed == []
        response.get_json.assert_not_called()
        complete.assert_not_called()
        assert b"".join(response.response) == b'{"success":true}'
    finally:
        response.close()


@pytest.mark.parametrize("payload,status,success", [
    ({}, 200, True),
    ({"success": True}, 200, True),
    ({"error": "", "pending": False}, 200, True),
    ({"success": True}, 201, True),
    ({"error": "failed"}, 200, False),
    ({"pending": True}, 200, False),
    ({"success": False}, 200, False),
    ({"success": True}, 400, False),
    ({"success": True}, 500, False),
    ([], 200, False),
    (None, 200, False),
    ("message", 200, False),
])
def test_buffered_json_preserves_request_completion_status(runtime, payload, status, success):
    app, complete, _ = runtime
    app.add_url_rule("/response", view_func=lambda: (app.json.response(payload), status))
    with app.test_client() as client:
        response = client.get("/response")
        assert response.status_code == status
        assert response.get_json() == payload
    complete.assert_called_once_with(success=success)


@pytest.mark.parametrize("mimetype", ["text/plain", "text/html", "text/event-stream"])
def test_non_json_responses_are_not_finalized(runtime, mimetype):
    _, complete, finalize = runtime
    response = Response("unchanged", mimetype=mimetype)
    response.get_json = Mock(side_effect=AssertionError("Non-JSON responses must not be parsed."))
    assert finalize(response) is response
    assert response.get_data() == b"unchanged"
    response.get_json.assert_not_called()
    complete.assert_not_called()
