# Bot Mode

Bot mode turns ouro into a persistent IM assistant. Each chat conversation maps to an independent agent session with its own memory, so multiple users (or group chats) can interact concurrently without interference.

## Quick Start

```bash
pip install ouro-ai[bot]
```

Add credentials to `~/.ouro/config` (see [LARK.md](LARK.md) / [SLACK.md](SLACK.md)):

```
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=your_secret
```

Then start:

```bash
ouro --bot
```

## Architecture

```
IM Platform (Lark / Slack)
    │  long connection (WebSocket / Socket Mode)
    ▼
 Channel ──► BotServer ──► SessionRouter ──► LoopAgent (per conversation)
                │
            /health (HTTP)
```

- **Channel** — long-lived connection to an IM platform. Currently supported: Lark (`LARK.md`) and Slack (`SLACK.md`).
- **BotServer** — receives messages from all channels, dispatches slash commands, and routes regular messages to agents.
- **SessionRouter** — maps each `channel:conversation_id` to its own `LoopAgent` instance with a per-conversation lock and idle cleanup.

## Slash Commands

Users can send these commands directly in the chat to manage their session:

| Command | Description |
|---------|-------------|
| `/new` | Reset the current session. The next message starts a fresh conversation with a new agent. |
| `/reset` | Alias for `/new`. |
| `/compact` | Compress conversation memory to save tokens. Reports how many messages were compressed and tokens saved. |
| `/status` | Show session statistics: age, message count, token usage, and compression history. |
| `/help` | List all available commands. |

Any unrecognized `/command` is forwarded to the agent as a normal message.

### Examples

```
User:  /status
Bot:   Session age: 12m 30s
       Messages: 15
       Context tokens: 5000
       Total input tokens: 12000
       Total output tokens: 3000
       Compressions: 0

User:  /compact
Bot:   Compressed 15 messages — saved 3200 tokens (42%)

User:  /new
Bot:   Session reset. Send a message to start a new conversation.
```

## Health Endpoint

The bot server exposes a lightweight HTTP endpoint for monitoring:

```
GET /health
```

```json
{"status": "ok", "active_sessions": 3}
```

Default: `0.0.0.0:8080`. Configure with `BOT_HOST` / `BOT_PORT` in `~/.ouro/config`.

## Session Lifecycle

1. **Creation** — A session is created on the first message from a conversation.
2. **Reuse** — Subsequent messages in the same conversation reuse the same agent (with full memory).
3. **Reset** — `/new` or `/reset` destroys the session; the next message creates a fresh one.
4. **Idle cleanup** — Sessions inactive for over 1 hour are automatically cleaned up.

## Channel Setup

- **Lark (Feishu)**: See [LARK.md](LARK.md)
- **Slack**: See [SLACK.md](SLACK.md)

Both channels can run simultaneously — add all four keys to `~/.ouro/config` and both will be active.
