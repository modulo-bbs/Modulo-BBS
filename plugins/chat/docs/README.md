# chat

One global chat channel with event-bus fan-out — Discord-ish feel, terminal
delivery. Messages broadcast to every connected user in real time.

## Screens

Renders inline (prompt line + incoming messages). Reserved screen names for
future overridable versions: `lobby` (the chat entry header).

## Keys

`Q` = leave chat. Everything typed is a message.

## Events

- Chat fan-out subscribes to the bus; other plugins can emit into the
  channel rather than calling chat directly.
- See source for the exact event names before integrating.
