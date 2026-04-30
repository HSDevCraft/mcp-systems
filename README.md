# MCP System — Model Context Protocol Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A **production-grade Model Context Protocol (MCP) platform** that provides a standardized, extensible infrastructure for AI systems to manage context, memory, tools, and modular interactions at enterprise scale.

---

## What is MCP?

The **Model Context Protocol** is an open standard (pioneered by Anthropic) that defines how AI models connect to external data sources, tools, and services through a unified, provider-agnostic interface. Think of it as "USB-C for AI" — one protocol, infinite integrations.

This repository implements MCP *as a platform*: not just the protocol layer, but the full production stack around it — context lifecycle management, tiered memory, plugin orchestration, observability, and secure API exposure.

---

## Key Features

- **Standardized MCP protocol** — tools, resources, and prompt primitives fully implemented
- **Tiered memory system** — short-term (Redis), long-term (vector DB), working memory (in-process)
- **Plugin/module architecture** — hot-loadable modules with lifecycle hooks and interface contracts
- **Context lifecycle management** — create, fork, merge, expire, and serialize context windows
- **FastAPI gateway** — REST + SSE transport, OpenAPI docs, JWT/API-key auth
- **Full observability** — structured logging, Prometheus metrics, OpenTelemetry traces
- **Production hardening** — rate limiting, input validation, secrets management, RBAC
- **Docker + Kubernetes** — compose for dev, Helm-ready manifests for production

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Redis (or use Docker Compose)

### Local Development

```bash
# Clone
git clone https://github.com/your-org/mcp-system.git
cd mcp-system

# Install dependencies
make setup

# Copy and configure environment
cp .env.example .env
# Edit .env with your secrets

# Start infrastructure (Redis, optional vector DB)
make docker-infra

# Run API server
make api

# Visit docs
open http://localhost:8000/docs
```

### Docker Compose (Full Stack)

```bash
docker-compose up --build
```

Services started:
| Service | Port | Description |
|---------|------|-------------|
| `mcp-api` | 8000 | MCP API gateway |
| `redis` | 6379 | Short-term memory + cache |
| `qdrant` | 6333 | Long-term vector memory |
| `prometheus` | 9090 | Metrics collection |
| `grafana` | 3000 | Dashboards |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        API Gateway                           │
│              (FastAPI + JWT Auth + Rate Limit)               │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                      MCP Engine (Core)                       │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Context Manager │  │ Orchestrator │  │Module Registry│  │
│  └────────┬────────┘  └──────┬───────┘  └───────┬───────┘  │
└───────────┼──────────────────┼──────────────────┼──────────┘
            │                  │                  │
┌───────────▼──────┐  ┌────────▼────────┐  ┌─────▼─────────┐
│  Memory Manager  │  │  Tool Executor  │  │ Module Loader │
│ ┌──────────────┐ │  │ ┌─────────────┐ │  │ ┌───────────┐ │
│ │ Short-term   │ │  │ │   Tools     │ │  │ │  Plugins  │ │
│ │   (Redis)    │ │  │ │  Resources  │ │  │ │  Modules  │ │
│ ├──────────────┤ │  │ │   Prompts   │ │  │ └───────────┘ │
│ │  Long-term   │ │  │ └─────────────┘ │  └───────────────┘
│ │  (Qdrant)    │ │  └─────────────────┘
│ ├──────────────┤ │
│ │   Working    │ │
│ │  (In-proc)   │ │
│ └──────────────┘ │
└──────────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full design documentation.

---

## Repository Structure

```
mcp-system/
├── src/
│   ├── core/           # MCP engine, context manager, orchestrator, registry
│   ├── memory/         # Tiered memory system (working / short / long)
│   ├── modules/        # Plugin interface, lifecycle, loader, examples
│   ├── api/            # FastAPI app, routers, middleware, schemas
│   └── utils/          # Config, logging, metrics, security, exceptions
├── tests/
│   ├── unit/           # Unit tests per module
│   └── integration/    # End-to-end tests
├── docs/               # Deep-dive documentation (9 guides)
├── configs/            # YAML configs (dev / prod / test)
├── deploy/             # Dockerfile, k8s manifests, monitoring
│   ├── kubernetes/
│   └── monitoring/
├── .github/
│   ├── workflows/      # CI/CD (lint, test, build, publish)
│   └── ISSUE_TEMPLATE/
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── ARCHITECTURE.md
├── CONTRIBUTING.md
└── CHANGELOG.md
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system design, data flow, tradeoffs |
| [docs/01_conceptual_foundation.md](docs/01_conceptual_foundation.md) | What MCP is, philosophy, problems solved |
| [docs/02_system_architecture.md](docs/02_system_architecture.md) | Component breakdown, interactions |
| [docs/03_module_system.md](docs/03_module_system.md) | Plugin design, interface contracts |
| [docs/04_memory_system.md](docs/04_memory_system.md) | Memory tiers, retrieval, stitching |
| [docs/05_api_reference.md](docs/05_api_reference.md) | All endpoints, schemas, auth |
| [docs/06_deployment.md](docs/06_deployment.md) | Docker, Kubernetes, CI/CD |
| [docs/07_observability.md](docs/07_observability.md) | Logging, metrics, tracing |
| [docs/08_security.md](docs/08_security.md) | Auth, rate limiting, hardening |
| [docs/09_scalability.md](docs/09_scalability.md) | Scaling patterns, multi-tenancy |

---

## Zero-Infrastructure Demo

Run the full pipeline in-process — no Redis, Qdrant, or Docker required:

```bash
python scripts/quickstart.py
```

This bootstraps all components with in-memory fallbacks, registers modules,
creates a context, executes echo and summarizer modules, and stores/retrieves
from memory — a complete end-to-end tour in seconds.

---

## Development Commands

```bash
make help              # Show all targets
make setup             # Install all dependencies (dev + prod)
make lint              # Run ruff + mypy
make format            # Run black + isort
make test-fast         # Unit tests only (no external deps)
make test              # Full test suite + coverage
make test-cov          # Coverage report (HTML)
make api               # Start FastAPI dev server
make docker-up         # Full docker-compose stack
make docker-down       # Tear down containers
make clean             # Remove build artifacts

# Utility scripts
make quickstart        # Run in-process demo
make generate-keys     # Generate MCP_SECRET_KEY, JWT_SECRET_KEY, STORAGE_ENCRYPTION_KEY
make health-check      # Check health of running API (localhost:8000)
make validate-module FILE=path/to/module.py  # Validate a module implementation
```

## Utility Scripts

| Script | Description |
|--------|-------------|
| `scripts/quickstart.py` | Full pipeline demo with in-memory fallbacks |
| `scripts/generate_keys.py` | Generate cryptographic keys for `.env` |
| `scripts/health_check.py` | CLI health check against any MCP API instance |
| `scripts/register_module.py` | Validate + test a module file before deploying |

---

## Configuration

All configuration is driven by environment variables + YAML files.

```bash
# Core
MCP_ENV=development          # development | production | test
MCP_HOST=0.0.0.0
MCP_PORT=8000
MCP_SECRET_KEY=changeme

# Redis (short-term memory)
REDIS_URL=redis://localhost:6379/0

# Qdrant (long-term vector memory)
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=mcp_memory

# Auth
JWT_SECRET_KEY=your-jwt-secret
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# Observability
LOG_LEVEL=INFO
ENABLE_METRICS=true
OTEL_ENDPOINT=http://localhost:4317
```

Full reference: [.env.example](.env.example)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributions welcome — bugs, features, docs, modules.

---

## License

MIT — see [LICENSE](LICENSE).
