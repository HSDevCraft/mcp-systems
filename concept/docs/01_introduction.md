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

## The N×M → N+M Problem

The most important motivation for MCP is eliminating the **combinatorial explosion** of custom integrations.

```
Without MCP (N×M integrations):
  N = 4 hosts  ×  M = 5 tools  =  20 custom integrations to build & maintain

  Claude Desktop ──custom──► GitHub
  Claude Desktop ──custom──► Postgres
  Claude Desktop ──custom──► Slack
  Claude Desktop ──custom──► Filesystem
  Claude Desktop ──custom──► Jira
  VS Code Copilot ──custom──► GitHub      ← duplicate
  VS Code Copilot ──custom──► Postgres    ← duplicate
  ...

With MCP (N+M integrations):
  N = 4 hosts  +  M = 5 servers  =  9 things to build

  Claude Desktop ──MCP──► GitHub Server    ← one server works for ALL hosts
  VS Code Copilot ──MCP──►    ┘
  Custom Agent ──MCP──►       ┘
  Jupyter ──MCP──►            ┘
```

**One MCP server, written once, works with every MCP-compliant host forever.**

---

## MCP vs. Alternatives

| Approach | What it is | Limitation vs MCP |
|----------|------------|-------------------|
| **OpenAI Function Calling** | JSON schema for functions in a single LLM call | Vendor-locked, no resources/prompts, no server lifecycle |
| **LangChain Tools** | Python tool wrappers for agent frameworks | Framework-locked, no standard wire protocol |
| **OpenAI Plugins (deprecated)** | HTTP-based plugins for ChatGPT | Deprecated, ChatGPT-only |
| **Custom REST APIs** | Bespoke HTTP endpoints | No discovery, no standard auth, N×M problem |
| **LSP (Language Server Protocol)** | IDE ↔ language server protocol | Inspiration for MCP; IDE-scoped only |
| **MCP** | Open, multi-transport, multi-primitive protocol | The standard |

MCP is to AI tools what **LSP is to IDEs**: define the protocol once; every editor (host) supports every language (server).

---

## Ecosystem Overview

Notable real-world MCP servers and clients (as of 2024-11-05):

### Official Servers (Anthropic / community)

| Server | Primitives | What it enables |
|--------|-----------|----------------|
| `mcp-server-filesystem` | Tools + Resources | Read/write local files |
| `mcp-server-github` | Tools + Resources | PRs, issues, code search |
| `mcp-server-postgres` | Tools + Resources | SQL queries, schema inspection |
| `mcp-server-slack` | Tools | Send messages, read channels |
| `mcp-server-brave-search` | Tools | Web search via Brave API |
| `mcp-server-puppeteer` | Tools | Browser automation, screenshots |
| `mcp-server-google-maps` | Tools | Geocoding, directions |
| `mcp-server-sqlite` | Tools + Resources | Local SQLite databases |
| `mcp-server-memory` | Tools + Resources | Persistent agent memory (KV store) |
| `mcp-server-git` | Tools + Resources | Git repository operations |

### MCP Clients / Hosts

| Host | Type | Notes |
|------|------|-------|
| **Claude Desktop** | Desktop app | First-party Anthropic host |
| **Continue.dev** | IDE extension | VS Code + JetBrains |
| **Cursor** | AI code editor | Native MCP support |
| **Zed** | Code editor | Built-in MCP client |
| **LibreChat** | Open-source chat UI | Multi-model, MCP-enabled |
| **Custom agents** | Code | Any Python/TS/Go app using the SDK |

---

## Protocol Philosophy (Deeper)

### What MCP deliberately does NOT do

- **MCP does not define agent orchestration** — how agents plan, loop, or chain calls is the host's business.
- **MCP does not define model selection** — the host picks the LLM; servers only hint via `ModelPreferences`.
- **MCP does not define storage** — servers manage their own state; the protocol is stateless at the message level.
- **MCP does not require authentication** — auth is transport-layer (OAuth 2.1, API keys, OS process ownership).
- **MCP does not define streaming output** — tool results are atomic; streaming is a transport concern.

### What MCP deliberately IS opinionated about

- **JSON-RPC 2.0** as the message format — no reinvention of RPC.
- **Human-in-the-loop** for sampling — servers cannot silently call LLMs.
- **Capability negotiation** — both sides declare what they support; no implicit assumptions.
- **Separation of concerns** — Tools (actions), Resources (data), Prompts (templates), Sampling (LLM calls) are strictly separate primitives.

---

## Quick Mental Model

```
Think of MCP like a USB standard for AI tools:

USB Standard              MCP Standard
─────────────────         ──────────────────────────────
Device (keyboard)         Server (filesystem, GitHub…)
Computer (any brand)      Host (Claude Desktop, IDE…)
USB protocol              MCP / JSON-RPC 2.0
USB port                  Transport (stdio, SSE, HTTP)
Device driver             MCP Client (inside host)
Device capabilities       MCP Primitives (Tools, Resources, Prompts, Sampling)
```

---

## Version History

| Date | Version | Key Changes |
|------|---------|-------------|
| Nov 2024 | 2024-11-05 | Initial public release (current stable) — Tools, Resources, Prompts, Sampling, Roots |

> The spec is maintained at https://spec.modelcontextprotocol.io  
> Python SDK: `pip install mcp`  
> TypeScript SDK: `npm install @modelcontextprotocol/sdk`

---

## Common Misconceptions

| Misconception | Reality |
|---------------|---------|
| "MCP is only for Claude" | Any LLM host can implement MCP; it is fully open |
| "MCP replaces function calling" | MCP *uses* JSON schemas like function calling but adds a full transport, lifecycle, and multi-primitive model |
| "Servers need to be Python" | Servers can be any language that can read/write JSON over a transport |
| "MCP requires internet access" | stdio transport is fully local; no network needed |
| "Resources are the same as Tools" | Resources are read-only data; Tools are callable functions with potential side effects |
| "Sampling lets the server use any LLM" | The *host* controls which LLM is used; the server only hints at preferences |

---

## Key Takeaways

- MCP solves the **N×M integration problem** — write a server once, works everywhere.
- The **three roles** are Host (owns LLM), Client (manages one connection), Server (exposes capabilities).
- The **four primitives** are Tools (actions), Resources (data), Prompts (templates), Sampling (LLM calls from server).
- **Transport is pluggable**: stdio for local, SSE/HTTP for remote.
- **Security lives in the host**: the host decides what servers can do and approves sensitive operations.
