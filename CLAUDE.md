# mcp-uml-diagram — Claude Project Context

**Type:** FastMCP Server
**Transport:** stdio
**Python:** 3.11+

---

## What This Server Does

Auto-generate 17 UML diagram tools from codebase using AST analysis and LLM synthesis. Tier 1 (AST-only): Class, Package, Component, Call Graph. Tier 2 (AST+LLM): Sequence, Activity, State. Tier 3 (LLM): Use Case, Object, Deployment, Communication, Composite, Interaction Overview, Timing. Plus: KG-based type selector, UML-from-code generator, bulk runner, and Kroki.io renderer.

Domain 46 (UML & Diagram Engineering) knowledge is injected at runtime via `skill_context.py` (LRU-cached SKILL.md reader) and routed via `kg_router.py` (keyword-scored KG router for 14 diagram types). This gives every tool access to OMG UML 2.5-grounded notation rules, Mermaid 10.x syntax, and PlantUML 1.2024.x style guides without embedding them in the server binary.

---

## Entry Point

```
server.py
```

Run via `python server.py` — communicates over stdio using the MCP protocol.

---

## Available Tools (17 total)

### Diagram Generation (14 types)

- `generate_class_diagram` — UML class diagram from Python/Java/TS/Kotlin AST
- `generate_package_diagram` — Package/module dependency diagram
- `generate_component_diagram` — Component diagram showing system boundaries
- `generate_sequence_diagram` — Sequence diagram for a flow (AST + LLM)
- `generate_activity_diagram` — Activity diagram for a process flow
- `generate_state_diagram` — State machine diagram from transition logic
- `generate_usecase_diagram` — Use case diagram (LLM-synthesized from SRS.md/README.md)
- `generate_object_diagram` — Object instance diagram
- `generate_deployment_diagram` — Deployment/infrastructure diagram
- `generate_communication_diagram` — Communication/collaboration diagram
- `generate_composite_structure_diagram` — Composite structure diagram
- `generate_interaction_overview_diagram` — Interaction overview
- `generate_call_graph_diagram` — Call graph from AST method calls
- `generate_timing_diagram` — UML timing diagram (lifeline + state/value transitions, OMG UML 2.5 §14.3)

### Intelligence Tools

- `select_optimal_diagram_type` — KG-based diagram type selector: scores 14 types against input description keywords using kg_router.py, returns recommended_type + alternative_types + rationale
- `generate_uml_from_code` — Language-agnostic UML from code snippet: AST-parses Python/Java/TS/Kotlin source text, returns Mermaid markup + diagram_type + lines count

### Bulk + Render

- `generate_all_diagrams` — Generate all 13 standard diagram types in one call
- `render_diagram` — Render Mermaid/PlantUML markup to PNG/SVG via Kroki.io

---

## New Support Modules (Domain 46 Integration)

- `skill_context.py` — LRU-cached (TTL=300 s) reader of Domain 46 SKILL.md files from claude-global-library. Provides OMG UML 2.5 notation context to every generator. Uses manual dict+timestamp cache (Python 3.8 compatible, stdlib-only). Validates paths via `_safe_join()` to prevent directory traversal.
- `kg_router.py` — KG-based diagram type router. Scores 14 diagram types using keyword frequency against domain knowledge graph `math_routing.json`. Used by `select_optimal_diagram_type`. Returns ranked list with rationale.
- `UML_SYSTEM_PROMPTS.py` — LLM system prompt constants for all 14 diagram types. Loaded by Tier 2/3 generators to produce OMG UML 2.5-compliant, Mermaid 10.x-valid output.
- `uml_generators_patch.py` — Patch module that extends `UMLDiagramGenerator` with `generate_timing_diagram()` and enriches `generate_communication_diagram()` with numbered message flows.

---

## Shared Utilities (in this repo)

- `base/` — Shared MCP infrastructure package (response builder, decorators, persistence, clients)
- `mcp_errors.py` — Structured error response helpers
- `input_validator.py` — Null-byte strip, length limits, prompt injection detection
- `rate_limiter.py` — Token bucket rate limiter (enable via ENABLE_RATE_LIMITING=1)

## Engine Dependency

Depends on scripts/langgraph_engine/uml_generators.py and diagrams/ package from claude-workflow-engine.

Set PYTHONPATH to include the workflow engine scripts directory before running:

```bash
export PYTHONPATH=/path/to/claude-workflow-engine/scripts:$PYTHONPATH
```

---

## Environment Variables

- `ANTHROPIC_API_KEY` — Required for Tier 2/3 LLM-assisted diagrams
- `UML_OUTPUT_DIR` — Directory for generated diagram files (default: docs/uml/)
- `KROKI_SERVER` — Kroki rendering server URL (default: https://kroki.io)
- `GLOBAL_LIBRARY_PATH` — Path to claude-global-library repo root (default: ~/Documents/workspace-spring-tool-suite-4-4.27.0-new/claude-global-library). Used by skill_context.py to load Domain 46 SKILL.md files at runtime.

---

## Development

### Running locally

```bash
# Install deps
pip install -r requirements.txt

# Run the MCP server (stdio mode)
python server.py
```

### Testing a tool call manually

```python
import subprocess, json

proc = subprocess.Popen(
    ["python", "server.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
# Send MCP initialize + tool call via stdin
```

### Running tests

```bash
# Unit tests (no API key needed)
pytest tests/test_unit.py -v --cov=. --cov-fail-under=100

# Integration tests (no API key needed - uses stubs)
pytest tests/test_integration.py -v --cov=. --cov-fail-under=100

# E2E tests (no live server - tests tool schemas and response structure)
pytest tests/test_e2e_mcp.py -v --cov=. --cov-fail-under=100

# Full suite
pytest tests/ -v --cov=. --cov-fail-under=100
```

### File structure

```
mcp-uml-diagram/
+-- server.py                  # Main FastMCP server (entry point)
+-- skill_context.py           # Domain 46 KG skill reader (LRU cache, TTL=300s) [v29.7.0]
+-- kg_router.py               # KG-based diagram type router (14 types) [v29.7.0]
+-- UML_SYSTEM_PROMPTS.py      # LLM system prompt constants for 14 diagram types [v29.7.0]
+-- uml_generators_patch.py    # Extends UMLDiagramGenerator with timing + enriched comms [v29.7.0]
+-- base/                      # Shared base package (response, decorators, persistence, clients)
+-- mcp_errors.py              # Error helpers
+-- input_validator.py         # Input validation
+-- rate_limiter.py            # Rate limiting
+-- tests/
|   +-- conftest.py            # pytest fixtures (mock_global_library, cache-clear autouse)
|   +-- test_unit.py           # 36 unit tests (skill_context, kg_router, DrawioConverter, UMLGen)
|   +-- test_integration.py    # 12 integration tests + security traversal tests
|   +-- test_e2e_mcp.py        # 23 E2E tests, 22/22 tools, DRE=1.0 certified
+-- requirements.txt
+-- .gitignore
+-- README.md
+-- CLAUDE.md
```

---

## Key Rules

1. Do NOT edit `base/` directly — it is a copy from `mcp-base` repo
2. To update shared utilities, edit in `mcp-base` and re-copy
3. Keep `server.py` as the single entry point
4. All tool handlers must use `@mcp_tool_handler` decorator for consistent error handling
5. All responses must use `success()` / `error()` / `MCPResponse` builder from `base.response`
6. Python 3.11+ minimum target. New files may use `list[X]`, `dict[K,V]`, `X|Y` unions. Existing files retain `from typing import ...` imports until a full annotation migration is done (tracked separately). All source files remain ASCII-only (cp1252 safe).
7. All source files must be ASCII-only (cp1252 safe) — no non-ASCII literals in .py files
8. `skill_context.py` reads from `GLOBAL_LIBRARY_PATH` — always validate paths via `_safe_join()` before any file I/O
9. Domain 46 context loaded once per cache TTL (300 s) — do NOT reload on every tool call

---

## Domain 46 Integration Notes

**Reliability Score:** RS = 1.0 (NLI=1.0, FactScore=1.0, DRE=1.0, Coverage=1.0) — APPROVED_FOR_PRODUCTION

**Security audit:** CRITICAL=0, HIGH=0, MEDIUM=0 (unresolved). CVSS max 3.3 (LOW). 0 secrets. Secrets scan 0/0 findings.

**Test coverage:** 108 tests (36 unit + 12 integration + 23 E2E + 37 regression). All 22 tools covered. DRE matrix 8/8 items verified.

**New diagram type:** Timing diagram (OMG UML 2.5 §14.3) — renders lifelines with compact or ladder notation using Mermaid gantt block as substrate. Empty `process_name` defaults to "Process".

**KG router scoring:** `kg_router.py` uses term-frequency scoring against Domain 46 `math_routing.json`. Produces `recommended_type` + `alternative_types` + `skill_context_available` flag.

**Advisory Sprint 2 (2026-05-27) — COMPLETE:**
- SAST-001 fixed: `generate_activity_diagram` now uses `_resolve_project_file()` path guard
- 512 KB SKILL.md read cap: `SKILL_MAX_BYTES` env var added to `skill_context.py`
- Structured audit logging: `_audit()` + `ENABLE_AUDIT_LOG=1` flag; all 18 tools instrumented
- Python target upgraded: 3.8+ EOL → 3.11+ (annotation migration deferred, tracked in backlog)
- `uml_generators_patch.py` import guard: `_UML_PATCH_AVAILABLE` flag + fallback warning
- `generate_timing_diagram_ladder()`: PlantUML robust/concise ladder notation variant added
- `_esc()` coverage: attr type_hint, method name, params, return_type now escaped in drawio_converter_enriched

**Remaining deferred (next sprint):**
- Full annotation migration: replace `from typing import ...` with builtin generics (Python 3.11+)
- CVE lxml/requests upgrades (lxml>=4.9.4, requests>=2.32.4 when pinned in consumers)
- `_apply_uml_styles` post-processing implementation (currently a documented stub)

---

**Last Updated:** 2026-05-28 (Advisory Sprint 3 — Python 3.11+, ladder notation, _esc() fixes)
