# Contributing to MCP System

Thank you for considering a contribution. This guide covers everything you need to get a PR merged.

## Development Setup

```bash
git clone https://github.com/your-org/mcp-system.git
cd mcp-system
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
make setup                      # installs dev deps + pre-commit hooks
cp .env.example .env            # configure local environment
make docker-infra               # start Redis + Qdrant
make api                        # start dev server
```

## Branching Strategy

```
main          ← stable, protected, requires PR + CI pass
dev           ← integration branch for features
feature/*     ← feature branches (branch from dev)
fix/*         ← bug fix branches (branch from main for hotfixes)
docs/*        ← documentation-only changes
```

## Commit Convention (Conventional Commits)

```
<type>(<scope>): <short summary>

Types: feat | fix | docs | style | refactor | test | chore | perf | ci
Scope: core | memory | modules | api | utils | deploy | docs

Examples:
  feat(modules): add web-scraper module with async fetch
  fix(memory): correct Redis TTL calculation on context refresh
  docs(api): add streaming endpoint examples
  test(core): add context fork/merge integration tests
```

## Pull Request Checklist

Before opening a PR, verify:

- [ ] All tests pass: `make test-fast`
- [ ] No lint errors: `make lint`
- [ ] Code is formatted: `make format-check`
- [ ] Types check: `make type-check`
- [ ] New functionality has unit tests
- [ ] New public APIs have docstrings
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`
- [ ] PR description explains **what** and **why**

## Adding a New Module

The fastest way to contribute is adding a new module:

1. Create `src/modules/plugins/your_module.py`
2. Implement `MCPModule` interface (see `src/modules/base.py`)
3. Add unit tests in `tests/unit/test_your_module.py`
4. Add an example in `docs/examples/`
5. Register in `src/modules/plugins/__init__.py`

Minimal module skeleton:

```python
from pydantic import BaseModel
from src.modules.base import MCPModule, ExecutionContext, HealthStatus

class MyModuleInput(BaseModel):
    text: str

class MyModuleOutput(BaseModel):
    result: str

class MyModule(MCPModule):
    name = "my-module"
    description = "Does something useful"
    version = "1.0.0"
    input_schema = MyModuleInput
    output_schema = MyModuleOutput

    async def execute(self, input: MyModuleInput, ctx: ExecutionContext) -> MyModuleOutput:
        return MyModuleOutput(result=input.text.upper())

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="OK")
```

## Code Style

- Line length: 100 characters (Black + Ruff enforce this)
- Type annotations required on all public functions
- Docstrings: Google style on all public classes and methods
- No bare `except:` — always catch specific exceptions
- Prefer `async def` for any I/O-touching code

## Testing Guidelines

- Unit tests must not require external services (mock Redis, Qdrant, etc.)
- Use fixtures from `tests/conftest.py` — don't create parallel fixture sets
- Test names: `test_<unit>_<scenario>_<expected>` e.g. `test_context_manager_fork_creates_child_with_parent_id`
- Mark slow tests with `@pytest.mark.slow`

## Reporting Issues

Use GitHub issue templates:
- **Bug report**: include MCP version, Python version, minimal reproduction, logs
- **Feature request**: describe the problem first, then the proposed solution

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
