# 01 — Introduction to MCP

## What is the Model Context Protocol?

**Model Context Protocol (MCP)** is an open, vendor-neutral protocol published by Anthropic in
November 2024. It standardises the *interface* between LLM-powered applications and the external
world (tools, databases, files, APIs, services).

Before MCP every integration was bespoke:
- Each IDE plugin had its own tool-calling format
- Each agent framework reinvented file-access APIs
- Context injection was copy-pasted across projects

MCP provides one protocol so that **any host** (Claude Desktop, VS Code Copilot, a custom agent)
can talk to **any server** (filesystem, GitHub, Postgres, Slack, a custom microservice) without
glue code.

---

## The Core Problem MCP Solves

```
Without MCP                        With MCP
──────────────────────────────     ───────────────────────────────
LLM App A ──► custom API ──► Tool  LLM App A ──►┐
LLM App B ──► bespoke RPC ──► DB   LLM App B ──►┤ MCP Protocol ──► Server
LLM App C ──► hard-coded ──► Files LLM App C ──►┘
```

The analogy most used: **MCP is to AI tools what HTTP is to web services**, or what
**LSP (Language Server Protocol) is to IDEs**. Write a server once; every compliant host works.

---

## Design Principles

| Principle | What it means in practice |
|-----------|---------------------------|
| **Simplicity** | Servers expose capabilities; clients discover and invoke them. No magic. |
| **Safety** | Humans approve sensitive operations. Servers cannot silently exfiltrate data. |
| **Composability** | Hosts connect to many servers simultaneously; servers stay small and focused. |
| **Openness** | JSON-RPC 2.0 over standard transports. No proprietary binary formats. |
| **Progressive disclosure** | A server can expose only one tool and still be fully spec-compliant. |

---

## Core Vocabulary

| Term | Definition |
|------|------------|
| **Host** | The user-facing application that contains an LLM (e.g. Claude Desktop, a custom chatbot). The host owns the LLM context window. |
| **Client** | A protocol layer *inside* the host that manages exactly one connection to one MCP server. A host may contain many clients. |
| **Server** | A lightweight, focused process (or in-process object) that exposes capabilities via MCP. Servers are intentionally scoped (one server = one concern). |
| **Transport** | The byte-level channel between client and server (stdio, SSE, HTTP). |
| **Primitive** | The four building blocks a server can expose: **Tools**, **Resources**, **Prompts**, **Sampling**. |
| **Root** | A filesystem path the server is permitted to access, declared at init time. |
| **Capability** | A flag in the initialization handshake advertising which primitives a party supports. |
| **Session** | The stateful conversation between a connected client and server from `initialize` to `shutdown`. |

---

## The Four Primitives

```
┌──────────────────────────────────────────────────────────────┐
│                        MCP SERVER                            │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  TOOLS   │  │RESOURCES │  │ PROMPTS  │  │ SAMPLING │   │
│  │          │  │          │  │          │  │          │   │
│  │ Actions  │  │  Data    │  │Templates │  │LLM calls │   │
│  │ (write   │  │ (read    │  │ (reuse-  │  │(server → │   │
│  │  ok)     │  │  only)   │  │  able)   │  │ client)  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└──────────────────────────────────────────────────────────────┘
```

- **Tools** — functions the LLM *calls* (like function calling in OpenAI). Can have side effects.
- **Resources** — addressable data the LLM *reads* (URI-based, like REST GET endpoints).
- **Prompts** — reusable message templates with typed arguments; invoked by the user/host.
- **Sampling** — the server asks the *client's* LLM to generate a completion (inverse direction).

---

## Where MCP Sits in the Stack

```
┌─────────────────────────────────────────────────────────────┐
│                         HOST                                │
│                                                             │
│   ┌──────────────┐    ┌──────────────┐                     │
│   │  LLM Engine  │    │   UI Layer   │                     │
│   └──────┬───────┘    └──────────────┘                     │
│          │ tool results / context                           │
│   ┌──────▼───────────────────────────────────┐             │
│   │             MCP CLIENT MANAGER           │             │
│   │  ┌──────────┐  ┌──────────┐  ┌────────┐ │             │
│   │  │ Client 1 │  │ Client 2 │  │Client 3│ │             │
│   │  └────┬─────┘  └────┬─────┘  └───┬────┘ │             │
│   └────────────────────────────────────────────┘            │
└──────────┬────────────────┬────────────────┬────────────────┘
           │ stdio          │ SSE            │ HTTP+SSE
    ┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼──────┐
    │  Server A   │  │  Server B   │  │  Server C  │
    │ (filesystem)│  │  (GitHub)   │  │(custom API)│
    └─────────────┘  └─────────────┘  └────────────┘
```

---

## Real-World Use Cases

| Server | Primitives Used | Benefit |
|--------|-----------------|---------|
| **Filesystem** | Resources (files), Tools (write/delete) | LLM reads and edits files |
| **GitHub** | Resources (repos, PRs), Tools (create PR) | LLM manages code repos |
| **Postgres** | Resources (tables/rows), Tools (execute SQL) | LLM queries and writes DB |
| **Slack** | Tools (send message), Resources (channels) | LLM sends notifications |
| **Web browser** | Tools (navigate, screenshot), Resources (page HTML) | LLM browses the web |
| **Code execution** | Tools (run Python/JS) | LLM executes generated code |
| **Memory** | Resources (recall), Tools (store) | Persistent agent memory |
| **Email** | Resources (inbox), Tools (send, reply) | LLM manages email |

---

## Version History

| Date | Version | Key Changes |
|------|---------|-------------|
| Nov 2024 | 2024-11-05 | Initial public release (current stable) |

The spec is maintained at https://spec.modelcontextprotocol.io
Python SDK: `pip install mcp`
TypeScript SDK: `npm install @modelcontextprotocol/sdk`
