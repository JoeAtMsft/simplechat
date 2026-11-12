# test_global_chat_agent_runtime.py
"""
Functional tests for request-scoped global chat agents.
Version: 0.261.066
Implemented in: 0.261.066

Real application modules, Semantic Kernel agents, and SQL plugins run offline
against two temporary SQLite databases. Only cloud storage and model I/O are
replaced. Fresh processes isolate application bootstrap and singleton state.
"""

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "application" / "single_app"
TESTS = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def test_both_chat_routes_use_request_scoped_initialization():
    tree = ast.parse((APP / "route_backend_chats.py").read_text(encoding="utf-8"))
    for name in ("chat_api", "chat_stream_api"):
        route = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        calls = [
            node for node in ast.walk(route)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        initializations = [
            node for node in calls if node.func.id == "_initialize_chat_semantic_kernel"
        ]
        require(len(initializations) == 1, f"{name} must initialize the selected chat agent.")
        authorizations = [
            node.lineno for node in calls
            if node.func.id == "_set_authorized_chat_request_context"
        ]
        require(
            authorizations and max(authorizations) < initializations[0].lineno,
            f"{name} must bind authorized action context before initialization.",
        )


def run_offline_scenarios():
    # Real application imports must happen inside the network-blocked bootstrap seam.
    from copy import deepcopy
    import json
    import sqlite3
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    from test_support.offline_bootstrap import offline_app_imports

    with offline_app_imports() as offline, TemporaryDirectory() as database_dir:
        import app
        import builtins
        import config
        from flask import Flask, g, session
        from pydantic import Field
        from semantic_kernel import Kernel
        from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
        from semantic_kernel.contents import (
            AuthorRole, ChatMessageContent, FunctionCallContent, StreamingChatMessageContent,
        )
        import route_backend_chats as routes
        import semantic_kernel_loader as loader
        from semantic_kernel_plugins.sql_query_plugin import SQLQueryPlugin
        from semantic_kernel_plugins.sql_schema_plugin import SQLSchemaPlugin

        class OfflineModel(OpenAIChatCompletion):
            plugin_name: str = ""
            tool_results: list = Field(default_factory=list)

            async def _inner_get_chat_message_contents(self, chat_history, settings):
                results = [
                    str(item.result)
                    for message in chat_history.messages if message.role == AuthorRole.TOOL
                    for item in message.items if hasattr(item, "result")
                ]
                if results:
                    self.tool_results.extend(results)
                    return [ChatMessageContent(role=AuthorRole.ASSISTANT, content=results[-1])]
                return [ChatMessageContent(role=AuthorRole.ASSISTANT, items=[
                    FunctionCallContent(
                        id="sql-count",
                        name=f"{self.plugin_name}-execute_scalar",
                        arguments=json.dumps({"query": "SELECT COUNT(*) FROM records"}),
                    ),
                ])]

            async def _inner_get_streaming_chat_message_contents(
                self, chat_history, settings, function_invoke_attempt=0,
            ):
                for message in await self._inner_get_chat_message_contents(chat_history, settings):
                    yield [StreamingChatMessageContent(
                        role=message.role, content=message.content, items=message.items, choice_index=0,
                    )]

        agents = []
        actions = []
        for name, count in (("synthetic", 2), ("operational", 5)):
            path = Path(database_dir) / f"{name}.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE records (id INTEGER)")
                connection.executemany("INSERT INTO records VALUES (?)", [(i,) for i in range(count)])
                connection.commit()
            finally:
                connection.close()
            actions.append({
                "id": f"{name}-action", "name": f"{name}_sql", "type": "sql_query",
                "is_enabled": True, "is_global": True,
                "additionalFields": {
                    "database_type": "sqlite", "connection_string": str(path),
                    "read_only": True, "timeout": 30, "max_rows": 1000,
                },
                "auth": {"type": "connection_string"},
            })
            agents.append({
                "id": f"{name}-agent", "name": f"{name}_agent",
                "display_name": f"{name.title()} Agent", "is_global": True,
                "is_enabled": True, "agent_type": "local",
                "instructions": "Use your assigned SQL action to count the records.",
                "actions_to_load": [f"{name}-action"],
                "model_endpoint_id": "endpoint", "model_id": "row",
            })

        settings = {
            "enable_semantic_kernel": True, "per_user_semantic_kernel": False,
            "global_selected_agent": {"name": "synthetic_agent", "is_global": True},
            "enable_multi_agent_orchestration": False,
            "enable_multi_model_endpoints": True,
            "model_endpoints": [{
                "id": "endpoint", "provider": "aoai", "enabled": True,
                "contextWindow": 200000,
                "connection": {"endpoint": "https://model.invalid", "api_version": "2025-01-01-preview"},
                "auth": {"type": "api_key", "api_key": "offline"},
                "models": [{
                    "id": "row", "deploymentName": "offline",
                    "catalogModelId": "gpt-5.6-terra", "modelVersion": "2026-07-09",
                    "responseLength": 2048, "outputTokenLimit": 16000,
                }],
            }],
            "default_model_selection": {"endpoint_id": "endpoint", "model_id": "row"},
            **{f"enable_{name}_plugin": False for name in (
                "time", "http", "wait", "math", "text", "fact_memory", "default_embedding_model",
            )},
        }
        app.configure_application_cache(
            settings, None, redis_client_factory=app.functions_redis_client.create_redis_client,
        )
        web = Flask("global-chat-runtime")
        web.secret_key = "offline"
        models = []

        def model_service(resolved, service_id, app_settings=None):
            agent = next(candidate for candidate in agents if service_id == f"aoai-chat-{candidate['name']}")
            action = next(candidate for candidate in actions if candidate["id"] in agent["actions_to_load"])
            model = OfflineModel(
                ai_model_id=resolved["deployment"], service_id=service_id, api_key="offline",
            )
            model.plugin_name = action["name"]
            models.append(model)
            return model

        def close_sql_connections(kernel):
            instances = {}
            for plugin in kernel.plugins.values():
                for function in plugin.functions.values():
                    instance = getattr(getattr(function, "method", None), "__self__", None)
                    if isinstance(instance, (SQLQueryPlugin, SQLSchemaPlugin)):
                        instances[id(instance)] = instance
            for instance in instances.values():
                if instance._connection is not None:
                    instance._connection.close()
                    instance._connection = None

        async def invoke(agent, streaming):
            if not streaming:
                response = await agent.invoke(messages="Count the records.")
                return str(response)
            return "".join([
                str(message.content) async for message in agent.invoke_stream(messages="Count the records.")
            ])

        stale_kernel = Kernel()
        stale_agents = {"synthetic_agent": SimpleNamespace(name="synthetic_agent")}
        with (
            patch.object(builtins, "kernel", stale_kernel, create=True),
            patch.object(builtins, "kernel_agents", stale_agents, create=True),
            patch.object(config.cosmos_global_agents_container, "query_items",
                         side_effect=lambda **kwargs: deepcopy([a for a in agents if a["is_enabled"]])),
            patch.object(config.cosmos_global_actions_container, "query_items",
                         side_effect=lambda **kwargs: deepcopy([a for a in actions if a["is_enabled"]])),
            patch.object(loader, "create_model_endpoint_chat_completion_service", model_service),
        ):
            for streaming in (False, True):
                for index in (1, 0, 1):
                    stored = agents[index]
                    selection = {key: stored[key] for key in ("id", "name", "is_global")}
                    with web.test_request_context():
                        session["user"] = {"oid": "owner"}
                        routes._initialize_chat_semantic_kernel(settings, "owner", selection)
                        kernel = routes.get_kernel()
                        try:
                            loaded = routes.get_kernel_agents()
                            require(list(loaded) == [stored["name"]], "Chat reused the startup-selected agent.")
                            require(kernel is not stale_kernel, "Chat mutated the shared startup kernel.")
                            expected_plugin = actions[index]["name"]
                            other_plugin = actions[1 - index]["name"]
                            require(expected_plugin in kernel.plugins, "The assigned SQL action was not loaded.")
                            require(other_plugin not in kernel.plugins, "Another agent's SQL action leaked into chat.")
                            result = offline.loop.run_until_complete(invoke(loaded[stored["name"]], streaming))
                            expected_count = 5 if index else 2
                            require(f"'value': {expected_count}" in result, f"Wrong SQL database result: {result}")
                            require(bool(models[-1].tool_results), "The model did not invoke the SQL action.")
                        finally:
                            close_sql_connections(kernel)
                    require(builtins.kernel is stale_kernel, "Request replaced the shared global kernel.")
                    require(builtins.kernel_agents is stale_agents, "Request replaced shared global agents.")

            # Existing browser selections must survive a rename through their stable IDs.
            selection = {"id": agents[1]["id"], "name": agents[1]["name"], "is_global": True}
            agents[1]["name"] = "renamed_operational_agent"
            agents[1]["instructions"] = "Updated SQL instructions."
            actions[1]["name"] = "renamed_operational_sql"
            with web.test_request_context():
                session["user"] = {"oid": "owner"}
                routes._initialize_chat_semantic_kernel(settings, "owner", selection)
                try:
                    loaded = routes.get_kernel_agents()
                    agent = loaded["renamed_operational_agent"]
                    require("Updated SQL instructions." in agent.instructions, "Agent edits were not refreshed.")
                    require("renamed_operational_sql" in routes.get_kernel().plugins, "Action rename was not refreshed.")
                    require("operational_sql" not in routes.get_kernel().plugins, "Stale action survived a rename.")
                finally:
                    close_sql_connections(routes.get_kernel())

            with web.test_request_context(), patch.object(routes, "initialize_semantic_kernel") as initialize:
                routes._initialize_chat_semantic_kernel(
                    {**settings, "per_user_semantic_kernel": True}, "owner", selection, redis_client="redis",
                )
                initialize.assert_called_once_with(user_id="owner", redis_client="redis")

            with web.test_request_context():
                routes._initialize_chat_semantic_kernel(
                    {**settings, "enable_multi_agent_orchestration": True}, "owner", selection,
                )
                require(routes.get_kernel() is stale_kernel, "Multi-agent orchestration behavior changed.")
                require(routes.get_kernel_agents() is stale_agents, "Orchestrator agents were discarded.")

            with web.test_request_context():
                routes._initialize_chat_semantic_kernel(settings, "owner", None)
                require(routes.get_kernel() is stale_kernel, "Model-only kernel behavior changed.")

            with web.test_request_context():
                g.kernel = None
                g.kernel_agents = {}
                require(routes.get_kernel() is None, "Failed request inherited the startup kernel.")
                require(routes.get_kernel_agents() == {}, "Failed request inherited startup agents.")

            with web.test_request_context(), patch.object(routes, "ensure_governance_access", side_effect=PermissionError):
                try:
                    routes._initialize_chat_semantic_kernel(settings, "owner", selection)
                except routes.ChatAgentInitializationError as exc:
                    require(exc.status_code == 403, "Denied global agent did not produce a forbidden error.")
                else:
                    raise AssertionError("Denied global agent was loaded.")

            with web.test_request_context(), patch.object(
                routes, "load_single_agent_for_kernel", Mock(return_value=(None, None)),
            ):
                session["user"] = {"oid": "owner"}
                try:
                    routes._initialize_chat_semantic_kernel(settings, "owner", selection)
                except routes.ChatAgentInitializationError as exc:
                    require(exc.status_code == 503, "Failed initialization did not produce an unavailable error.")
                    require(routes.get_kernel() is None, "Failed initialization inherited the startup kernel.")
                    require(routes.get_kernel_agents() == {}, "Failed initialization inherited startup agents.")
                else:
                    raise AssertionError("Failed initialization was silently accepted.")

            agents[1]["is_enabled"] = False
            with web.test_request_context():
                try:
                    routes._initialize_chat_semantic_kernel(settings, "owner", selection)
                except routes.ChatAgentInitializationError as exc:
                    require(exc.status_code == 400, "Unavailable agent did not produce a selection error.")
                    require(routes.get_kernel_agents() == {}, "Unavailable agent fell back to the startup agent.")
                else:
                    raise AssertionError("Disabled global agent was loaded.")

        require(not offline.network_attempts, "Regression tests attempted network access.")
        print("PASS: global agent switching, real SQL invocation, renames, isolation, and failure paths.")


@pytest.mark.parametrize("optimized", [False, True])
def test_global_agent_sql_runtime_without_restart(optimized):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(TESTS), str(APP), str(ROOT)))
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable]
    if optimized:
        command.append("-O")
    command.extend([str(Path(__file__).resolve()), "--offline-scenarios"])
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    require(result.returncode == 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if "--offline-scenarios" in sys.argv:
        run_offline_scenarios()
    else:
        raise SystemExit(pytest.main([str(Path(__file__).resolve())]))
