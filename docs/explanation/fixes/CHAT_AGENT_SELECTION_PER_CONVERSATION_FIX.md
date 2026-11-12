# Chat Agent Selection Per Conversation Fix - v0.261.067

Fixed in version: **0.261.067**

Related version update: `application/single_app/config.py` advances to `0.261.067`.

## Issue and root cause

The Chat agent dropdown showed one user-level saved agent, regardless of the
conversation open. Selecting an agent in one chat and then opening another chat
carried that agent over. Returning to the first chat required reselecting its
agent by hand, or the next message used the wrong agent. The model dropdown
already restored per conversation; the agent dropdown did not.

## Changes and impact

- `static/js/chat/chat-messages.js` reads the agent from the most recent
  message in the loaded conversation that recorded one (`metadata.agent_selection`).
  No backend or stored-data change is needed.
- `static/js/chat/chat-agents.js` adds `restoreAgentSelectionFromConversationMetadata`.
  It matches by stable agent ID first, then name, with the same global, group,
  and personal scope rules as the dropdown, so renamed agents still match.
- The restored agent is kept across dropdown rebuilds, such as scope changes,
  and is applied even when the dropdown finishes loading after the messages.
- A manual dropdown change takes over and stops the conversation agent from
  overriding the user.
- Agents that are unavailable (deleted or out of scope) and conversations with
  no recorded agent leave the dropdown unchanged.
- Restoring also saves the agent as the user's selected agent, so other chat
  flows that read the saved agent stay aligned. A new chat starts with the agent
  most recently shown.

Chats are restored from agents recorded in their messages, so a conversation
that has not yet received an agent reply has nothing to restore.

## Validation

`ui_tests/test_chat_agent_conversation_restore.py` loads the real `chat-agents.js`
in Chromium with mocked settings routes and verifies the following:

- opening a conversation selects its agent and it survives a dropdown rebuild
- a restore before the dropdown loads is applied once it loads
- unavailable or missing agents leave the dropdown alone
- a manual change overrides the conversation agent
