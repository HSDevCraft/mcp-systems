# MCP Concepts — Complete Implementation-Ready Reference

**Model Context Protocol (MCP)** is an open standard by Anthropic (released Nov 2024) that defines
how LLM applications connect to external data sources, tools, and services in a uniform, composable way.

---

## Folder Structure

```
concept/
├── README.md                        ← this file
├── docs/
│   ├── 01_introduction.md           ← What MCP is, why it exists, core philosophy
│   ├── 02_architecture.md           ← Host / Client / Server model, capability negotiation
│   ├── 03_protocol.md               ← JSON-RPC 2.0, message types, lifecycle FSM
│   ├── 04_transports.md             ← stdio, SSE, HTTP+SSE transports
│   ├── 05_tools.md                  ← Tool primitive: definition, schemas, execution
│   ├── 06_resources.md              ← Resource primitive: URIs, types, subscriptions
│   ├── 07_prompts.md                ← Prompt primitive: templates, arguments, dynamic
│   ├── 08_sampling.md               ← Sampling primitive: LLM calls from server→client
│   ├── 09_roots.md                  ← Roots: filesystem/workspace exposure
│   ├── 10_server_lifecycle.md       ← Server init, capabilities, shutdown patterns
│   ├── 11_client_lifecycle.md       ← Client connection, session management
│   ├── 12_security.md               ← Auth, OAuth 2.1, API keys, trust levels
│   ├── 13_error_handling.md         ← Error codes, recovery, propagation
│   ├── 14_testing.md                ← Unit + integration testing strategies
│   └── 15_advanced_patterns.md      ← Multi-server, middleware, composition, observability
└── implementations/
    ├── requirements.txt             ← All dependencies pinned
    ├── 01_hello_world_server.py     ← Minimal MCP server (stdio)
    ├── 02_tools_server.py           ← Tools: calculator, web search, code runner
    ├── 03_resources_server.py       ← Resources: files, DB rows, live API data
    ├── 04_prompts_server.py         ← Prompts: templates with dynamic arguments
    ├── 05_sampling_server.py        ← Sampling: server-initiated LLM requests
    ├── 06_multi_capability_server.py← Full server with all 4 primitives
    ├── 07_client_example.py         ← MCP client connecting to a server
    └── 08_testing_example.py        ← Unit + integration tests for MCP servers
```

---

## Quick Start

```bash
cd implementations
pip install -r requirements.txt

# Run the hello-world server (connect via Claude Desktop or mcp CLI)
python 01_hello_world_server.py

# Run the full multi-capability server
python 06_multi_capability_server.py

# Run the client example (starts its own server subprocess)
python 07_client_example.py

# Run all tests
pytest 08_testing_example.py -v
```

---

## Reading Order

| Goal | Path |
|------|------|
| **First time learning MCP** | 01 → 02 → 03 → 05 → 06 → 07 |
| **Building a server** | 02 → 10 → 05 → 06 → 07 → impl/02 → impl/03 |
| **Building a client / host** | 02 → 03 → 04 → 11 → impl/07 |
| **Security & production** | 12 → 13 → 14 → 15 |
| **Deep protocol understanding** | 03 → 04 → 08 → 09 → 10 → 11 |

---

## Key Concepts at a Glance

| Concept | One-liner |
|---------|-----------|
| **Tool** | A function the LLM can call (read/write side effects OK) |
| **Resource** | Data the LLM can read (URI-addressed, like a file or DB row) |
| **Prompt** | A reusable message template with typed arguments |
| **Sampling** | Server asks the *client's* LLM to generate a completion |
| **Root** | A filesystem/workspace path the server is allowed to see |
| **Host** | The application (Claude Desktop, IDE, agent) that owns the LLM |
| **Client** | Protocol layer inside the host managing one server connection |
| **Server** | Lightweight process exposing tools/resources/prompts |

---

## Protocol Version

These docs and implementations target **MCP spec 2024-11-05** (current stable) with Python SDK `mcp>=1.0.0`.
