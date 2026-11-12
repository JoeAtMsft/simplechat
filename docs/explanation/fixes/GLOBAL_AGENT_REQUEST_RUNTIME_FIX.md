# Global Chat Agent Runtime Fix - v0.261.066

Fixed in version: **0.261.066**

Related version update: `application/single_app/config.py` advances to `0.261.066`.

## Issue and root cause

With Workspace Mode disabled, global single-agent chat reused the agent loaded
at server startup. Saving a different selection or renaming an agent or SQL
action did not rebuild that runtime. An action could pass its connection test
while Chat still used the previous agent and its tools. The runtime reload hook
cleared its pending flag without reloading the kernel.

## Changes and impact

- `application/single_app/route_backend_chats.py` now initializes the explicitly
  selected global agent for each streaming or non-streaming chat request.
- Selection resolves against current stored configuration, using stable IDs
  where available. Saved instructions and assigned actions are loaded together.
- Request-local kernels load the selected agent's assigned custom actions
  without importing other agents' SQL actions or modifying the shared runtime.
- Existing core plugins, action governance, and authorization-before-loading
  ordering are preserved. Unavailable, denied, or failed agent initialization
  produces an explicit error instead of inheriting the startup agent.
- Workspace Mode, multi-agent orchestration, and model-only chat retain their
  existing behavior.

Deploy the updated application and restart the backend once to load the code.
Subsequent global single-agent requests pick up saved agent changes without
another restart. Name-only references to renamed records may require reselection;
stable-ID references survive renames.

See [Agents & Actions settings](../../admin/agents-actions.md) for configuration
guidance.

## Validation

`functional_tests/test_global_chat_agent_runtime.py` passes with normal and
optimized Python using real Semantic Kernel agents and SQL plugins against two
temporary SQLite databases. Tests switch between agents without restarting,
verify actual SQL counts of two and five in both invocation modes, and cover
rename refresh, action isolation, governance denial, and initialization failure.
Model responses and cloud storage are replaced; live Azure SQL is not tested.

`functional_tests/test_m365_chat_preflight_order.py` verifies authorization still
precedes initialization. The route-policy tests also pass, preserving route
registration and access requirements.
