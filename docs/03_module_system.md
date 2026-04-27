# 03 — Module System

## Design Goals

The module system has three design goals:

1. **Zero core changes to add a module** — implementing one abstract interface is sufficient
2. **Runtime discovery** — modules can be loaded/unloaded without restarting the server
3. **Type safety** — all module inputs and outputs are Pydantic models, validated at the boundary

---

## Interface Contract

Every module must implement `MCPModule` (defined in `src/modules/base.py`):

```python
class MCPModule(ABC):
    name: str               # Unique identifier, kebab-case
    description: str        # Human-readable purpose
    version: str            # Semver: "1.0.0"
    tags: list[str]         # e.g. ["text", "nlp", "retrieval"]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    # Core execution
    @abstractmethod
    async def execute(self, input: BaseModel, ctx: ExecutionContext) -> BaseModel: ...

    # Lifecycle hooks
    async def on_load(self) -> None: ...             # Startup hook
    async def on_unload(self) -> None: ...           # Shutdown hook
    async def before_execute(self, input, ctx): ...  # Pre-execution hook
    async def after_execute(self, output, ctx): ...  # Post-execution hook
    async def on_error(self, error, ctx): ...        # Error hook

    # Health
    @abstractmethod
    async def health_check(self) -> HealthStatus: ...
```

**Why abstract base class over Protocol?**

`ABC` enforces implementation at class definition time (IDE error on missing method). `Protocol` is structural — easier to implement but silent on missing methods until runtime. For a plugin system where third-party developers implement the interface, `ABC` gives earlier, clearer errors.

---

## Module Registration

### Static Registration (recommended for built-in modules)

```python
# src/api/main.py — at startup
from src.modules.plugins.echo import EchoModule
from src.modules.plugins.summarizer import SummarizerModule

registry = get_registry()
await registry.register(EchoModule())
await registry.register(SummarizerModule())
```

### Dynamic Discovery (for plugin directories)

```python
# Scans MODULES_DIR for Python files containing MCPModule subclasses
await registry.discover(path=settings.modules_dir)
```

Discovery algorithm:
1. Walk `MODULES_DIR` for `*.py` files
2. `importlib.import_module` each file
3. Inspect all classes in the module
4. Register any class that is a concrete (non-abstract) subclass of `MCPModule`
5. Call `on_load()` on each registered module
6. Log warnings for modules that fail to load (don't crash startup)

---

## Lifecycle Hooks

### `on_load()`

Called once when the module is registered. Use for:
- Connecting to external APIs
- Loading model weights
- Warming caches
- Validating configuration (fail fast)

```python
async def on_load(self) -> None:
    self._client = httpx.AsyncClient(base_url=self.api_url)
    await self._client.get("/health")  # Fail fast if external dep unavailable
```

### `before_execute(input, ctx)`

Called before every execution. Use for:
- Input sanitization beyond schema validation
- Context-based auth checks
- Rate limiting per-module
- Logging with rich context

### `after_execute(output, ctx)`

Called after every successful execution. Use for:
- Metrics emission
- Side effect writes (cache, DB)
- Output transformation

### `on_error(error, ctx)`

Called when `execute()` raises an exception. Use for:
- Custom error logging
- Alerting (PagerDuty, Slack)
- Graceful degradation (return fallback)

### `on_unload()`

Called on deregistration or server shutdown. Use for:
- Closing HTTP clients
- Flushing buffers
- Releasing locks

---

## Module Versioning

Modules carry a `version: str` (semver). The registry indexes by `(name, version)`.

```python
# Client request can pin version:
POST /api/v1/modules/summarizer@1.2.0/execute

# Or use latest:
POST /api/v1/modules/summarizer/execute
```

Version resolution: `latest` resolves to the highest semver registered for that name.

---

## ExecutionContext

Passed to every `execute()` call. Provides module access to:

```python
@dataclass
class ExecutionContext:
    request_id: UUID       # Unique per API call
    context_id: UUID       # MCP context ID
    session_id: UUID       # Session ID
    user_id: str           # Authenticated user
    tenant_id: str         # Tenant namespace
    metadata: dict         # Arbitrary client-supplied metadata
    working_memory: dict   # In-process KV store for this request
    logger: BoundLogger    # Pre-bound structlog logger
    metrics: MetricsClient # Prometheus client
    timeout: float         # Remaining time budget (seconds)
```

Modules should use `ctx.logger` (not `logging.getLogger`) to ensure trace IDs are automatically included in all log entries.

---

## Example Module Implementations

### Echo Module (simplest possible)

```python
class EchoInput(BaseModel):
    text: str
    uppercase: bool = False

class EchoOutput(BaseModel):
    text: str
    char_count: int

class EchoModule(MCPModule):
    name = "echo"
    description = "Returns input text, optionally uppercased"
    version = "1.0.0"
    tags = ["utility", "testing"]
    input_schema = EchoInput
    output_schema = EchoOutput

    async def execute(self, input: EchoInput, ctx: ExecutionContext) -> EchoOutput:
        text = input.text.upper() if input.uppercase else input.text
        ctx.logger.info("echo_executed", char_count=len(text))
        return EchoOutput(text=text, char_count=len(text))

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="Echo module always healthy")
```

### Text Summarizer Module (with external API)

```python
class SummarizerInput(BaseModel):
    text: str
    max_length: int = 150
    style: Literal["bullet", "paragraph"] = "paragraph"

class SummarizerOutput(BaseModel):
    summary: str
    original_length: int
    summary_length: int
    compression_ratio: float

class SummarizerModule(MCPModule):
    name = "text-summarizer"
    description = "Summarizes text using an LLM"
    version = "1.0.0"
    tags = ["nlp", "text", "llm"]
    input_schema = SummarizerInput
    output_schema = SummarizerOutput

    def __init__(self, llm_client: LLMInterface) -> None:
        self._llm = llm_client

    async def on_load(self) -> None:
        await self._llm.health_check()

    async def execute(self, input: SummarizerInput, ctx: ExecutionContext) -> SummarizerOutput:
        prompt = self._build_prompt(input)
        summary = await self._llm.complete(prompt, max_tokens=input.max_length)
        return SummarizerOutput(
            summary=summary,
            original_length=len(input.text),
            summary_length=len(summary),
            compression_ratio=len(summary) / max(len(input.text), 1),
        )

    def _build_prompt(self, input: SummarizerInput) -> str:
        style_instruction = (
            "as bullet points" if input.style == "bullet" else "as a concise paragraph"
        )
        return f"Summarize the following text {style_instruction}:\n\n{input.text}"

    async def health_check(self) -> HealthStatus:
        try:
            await self._llm.health_check()
            return HealthStatus(healthy=True, message="LLM reachable")
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
```

---

## Adding a New Module (Step-by-Step)

```
1. Create src/modules/plugins/your_module.py
2. Define Input + Output Pydantic models
3. Implement MCPModule subclass
4. Add to src/modules/plugins/__init__.py
5. Register in src/api/main.py startup (or rely on discovery)
6. Write tests in tests/unit/test_your_module.py
7. Document in docs/examples/your_module.md
```

The module will be immediately available at:
```
GET  /api/v1/modules/your-module        ← schema
POST /api/v1/modules/your-module/execute ← execution
```
