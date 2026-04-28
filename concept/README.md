# MCP Concepts — Complete Implementation-Ready Reference

**Model Context Protocol (MCP)** is an open standard by Anthropic (released Nov 2024) that defines
how LLM applications connect to external data sources, tools, and services in a uniform, composable way.

> **Protocol version**: `2024-11-05` (current stable) | Python SDK: `mcp>=1.0.0`

---

## Why MCP?

MCP solves the **N×M integration problem**: before MCP, N hosts × M tools = N×M custom integrations.
With MCP: N+M. Write a server once; every compliant host works with it automatically.

```
USB analogy:
  USB device  ←→  MCP Server     (filesystem, GitHub, DB, …)
  USB computer ←→  MCP Host      (Claude Desktop, VS Code, agents)
  USB protocol ←→  MCP / JSON-RPC 2.0
```

---

## Folder Structure

```
concept/
├── README.md                        ← this file
├── docs/
│   ├── 01_introduction.md           ← What MCP is, N×M problem, ecosystem, MCP vs alternatives
│   ├── 02_architecture.md           ← Host/Client/Server model, deployment topologies, pitfalls
│   ├── 03_protocol.md               ← JSON-RPC 2.0, lifecycle FSM, batching, debugging
│   ├── 04_transports.md             ← stdio, SSE, HTTP+SSE, reconnection, proxy config
│   ├── 05_tools.md                  ← Tool primitive, annotations, chaining, LLM descriptions
│   ├── 06_resources.md              ← Resource primitive, URI templates, chunking, caching
│   ├── 07_prompts.md                ← Prompt primitive, dynamic generation, versioning, testing
│   ├── 08_sampling.md               ← Sampling, token budget, structured output, ReAct pattern
│   ├── 09_roots.md                  ← Roots, symlink safety, multi-workspace, caching
│   ├── 10_server_lifecycle.md       ← Lifecycle, K8s probes, rolling updates, multi-session
│   ├── 11_client_lifecycle.md       ← Client lifecycle, reconnection, LLM integration, context mgmt
│   ├── 12_security.md               ← Auth, SSRF prevention, audit logging, code injection, testing
│   ├── 13_error_handling.md         ← Error hierarchy, aggregation, DLQ, error budget
│   ├── 14_testing.md                ← Unit/integration/E2E, contract, perf, chaos testing
│   └── 15_advanced_patterns.md      ← Federation, event-driven, horizontal scaling, CQRS
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

## Learning Paths

| Goal | Docs | Implementations |
|------|------|-----------------|
| **First time with MCP** | 01 → 02 → 03 → 05 → 06 | `impl/01` → `impl/02` |
| **Building a server** | 02 → 10 → 05 → 06 → 07 | `impl/02` → `impl/03` → `impl/06` |
| **Building a client / host** | 02 → 03 → 04 → 11 | `impl/07` |
| **Security & production hardening** | 09 → 12 → 13 → 10 → 15 | `impl/06` |
| **Testing an MCP server** | 14 → 13 | `impl/08` |
| **Agentic / sampling patterns** | 08 → 11 → 15 | `impl/05` → `impl/07` |
| **Deep protocol understanding** | 03 → 04 → 08 → 09 → 10 → 11 | — |
| **Advanced / production patterns** | 15 → 10 → 12 | `impl/06` |

---

## Key Concepts Reference

### The Four Primitives

| Primitive | Who calls it | Side effects? | Use for |
|-----------|-------------|---------------|---------|
| **Tool** | LLM (via host) | Yes | Actions: write file, send email, execute code |
| **Resource** | Host/LLM | Read-only | Data: files, DB rows, API responses |
| **Prompt** | User/Host | None | Reusable message templates (slash commands) |
| **Sampling** | Server | Yes (LLM call) | Agent loops, structured extraction, reflection |

### The Three Roles

| Role | Location | Responsibility |
|------|----------|---------------|
| **Host** | User's app (Claude Desktop, IDE) | Owns LLM, context window, approval gates |
| **Client** | Inside host | Manages one server connection, speaks wire protocol |
| **Server** | Separate process | Exposes capabilities; stateless preferred |

### Transports at a Glance

| Transport | Best for | Auth | Scalable? |
|-----------|---------|------|-----------|
| **stdio** | Local tools, IDE plugins | OS process | No (1 connection) |
| **SSE** | Remote servers, cloud | HTTP headers, OAuth | Yes (sticky sessions) |
| **Streamable HTTP** | New remote servers | HTTP headers, OAuth | Yes |

### Tool Annotations

| Annotation | Default | Signal to host |
|------------|---------|----------------|
| `readOnlyHint` | `false` | Safe to call without confirmation |
| `destructiveHint` | `true` | Show confirmation dialog |
| `idempotentHint` | `false` | Safe to retry |
| `openWorldHint` | `true` | Accesses internet/external APIs |

---

## Protocol Quick Reference

### Session Lifecycle
```
CLOSED → CONNECTING → INITIALIZING → RUNNING → SHUTTING_DOWN → CLOSED
```

### Error Levels
```
1. Protocol errors  → JSON-RPC error object  { code, message }
2. Soft tool errors → isError: true in result content
3. Validation       → JSON-RPC -32602 (Invalid params)
```

### Key Method Cheatsheet

| Need | Method | Direction |
|------|--------|-----------|
| Start session | `initialize` | C → S |
| List tools | `tools/list` | C → S |
| Call a tool | `tools/call` | C → S |
| Read data | `resources/read` | C → S |
| Watch for changes | `resources/subscribe` | C → S |
| Use a template | `prompts/get` | C → S |
| Ask LLM to reason | `sampling/createMessage` | **S → C** |
| Get file paths | `roots/list` | **S → C** |
| Cancel in-flight | `notifications/cancelled` | C → S |
| Notify tool change | `notifications/tools/list_changed` | S → C |

---

## Document Enhancement Summary

Each of the 15 concept documents has been enhanced with:

| Document | New Sections Added |
|----------|--------------------|
| **01** Introduction | N×M problem diagram, ecosystem table, MCP vs alternatives, misconceptions |
| **02** Architecture | 4 deployment topologies, server-initiated flow diagram, namespace design, pitfalls |
| **03** Protocol | Request ID design, batch note, full message taxonomy, `_meta` field, wire debugging |
| **04** Transports | Reconnection patterns, nginx/Traefik proxy config, session multiplexing, perf table |
| **05** Tools | Tool annotations, tool chaining, multi-content blocks, versioning, LLM description guide |
| **06** Resources | RFC 6570 templates, chunking large resources, caching, embedding, virtual resources |
| **07** Prompts | Dynamic DB-driven prompts, prompt chaining, versioning, extended completion, testing |
| **08** Sampling | Token budget, structured output with retry, multi-modal, cost tracking, ReAct pattern |
| **09** Roots | Symlink safety (`resolve()`), multi-workspace index, scope inheritance, roots caching |
| **10** Server Lifecycle | K8s probes (liveness/readiness/startup), rolling update, multi-session server, checklist |
| **11** Client Lifecycle | Auto-reconnect, context window manager, LLM integration loop, capability caching |
| **12** Security | SSRF prevention, code injection (AST sandbox), structured audit logs, security test suite |
| **13** Error Handling | Typed error hierarchy, batch aggregation, dead letter queue, error budget, structured logs |
| **14** Testing | Contract tests, performance (p50/p99/RPS), chaos/fault injection, coverage strategy |
| **15** Advanced Patterns | Federation gateway, event-driven webhooks, Redis pub/sub scaling, CQRS, lazy loading |

---

## Common Pitfalls (Top 10)

| Pitfall | Fix |
|---------|-----|
| Writing to `stdout` in stdio server | Use `stderr` for all non-protocol output |
| No `isError: true` on tool failures | Always return `CallToolResult(isError=True)` on application errors |
| No SSRF validation in URL-fetching tools | Validate all URLs against private IP blocklist |
| No token budget in agentic sampling loops | Implement `sample_with_budget()` with session-scoped counter |
| Path traversal not blocked | Use `Path.resolve()` before `relative_to()` check against roots |
| No reconnect logic in clients | Implement exponential backoff reconnection |
| Generic tool descriptions | Write LLM-targeted descriptions with when/what/how guidance |
| Injecting full tool results into context | Use `ContextWindowManager` to fit within token budget |
| No `preStop` hook in Kubernetes | Add `lifecycle.preStop: sleep 5` to allow connection draining |
| Missing contract tests | Run protocol contract tests against every server build |

---

## Protocol Version

These docs and implementations target **MCP spec 2024-11-05** (current stable) with Python SDK `mcp>=1.0.0`.

Spec: https://spec.modelcontextprotocol.io  
Python SDK: `pip install mcp`  
TypeScript SDK: `npm install @modelcontextprotocol/sdk`
