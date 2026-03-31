# mcp-uml-diagram — Claude Project Context

**Type:** FastMCP Server
**Transport:** stdio
**Python:** 3.8+

---

## What This Server Does

Auto-generate 13 UML diagram types from codebase using AST analysis and LLM synthesis. Tier 1 (AST-only): Class, Package, Component. Tier 2 (AST+LLM): Sequence, Activity, State. Tier 3 (LLM): Use Case, Object, Deployment, Communication, Composite, Interaction, Call Graph. Renders via Kroki.io.

---

## Entry Point

```
server.py
```

Run via `python server.py` — communicates over stdio using the MCP protocol.

---

## Available Tools

- `generate_class_diagram` — Generate UML class diagram from Python/Java/TS/Kotlin AST
- `generate_package_diagram` — Generate package/module dependency diagram
- `generate_component_diagram` — Generate component diagram showing system boundaries
- `generate_sequence_diagram` — Generate sequence diagram for a flow (AST + LLM)
- `generate_activity_diagram` — Generate activity diagram for a process flow
- `generate_state_diagram` — Generate state machine diagram from transition logic
- `generate_usecase_diagram` — Generate use case diagram (LLM-synthesized)
- `generate_object_diagram` — Generate object instance diagram
- `generate_deployment_diagram` — Generate deployment/infrastructure diagram
- `generate_communication_diagram` — Generate communication/collaboration diagram
- `generate_composite_structure_diagram` — Generate composite structure diagram
- `generate_interaction_overview_diagram` — Generate interaction overview
- `generate_call_graph_diagram` — Generate call graph from AST method calls
- `generate_all_diagrams` — Generate all 13 diagram types in one call
- `render_diagram` — Render Mermaid/PlantUML markup to PNG/SVG via Kroki.io

---

## Shared Utilities (in this repo)

- `base/` — Shared MCP infrastructure package (response builder, decorators, persistence, clients)
- `mcp_errors.py` — Structured error response helpers
- `input_validator.py` — Null-byte strip, length limits, prompt injection detection
- `rate_limiter.py` — Token bucket rate limiter (enable via ENABLE_RATE_LIMITING=1)

## Engine Dependency

Depends on scripts/langgraph_engine/uml_generators.py and diagrams/ package from claude-workflow-engine

Set PYTHONPATH to include the workflow engine scripts directory before running:

```bash
export PYTHONPATH=/path/to/claude-workflow-engine/scripts:$PYTHONPATH
```

---

## Environment Variables

- `ANTHROPIC_API_KEY` — Required for Tier 2/3 LLM-assisted diagrams
- `UML_OUTPUT_DIR` — Directory for generated diagram files (default: docs/uml/)
- `KROKI_SERVER` — Kroki rendering server URL (default: https://kroki.io)

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

### File structure

```
mcp-uml-diagram/
+-- server.py          # Main FastMCP server (entry point)
+-- base/              # Shared base package (response, decorators, persistence, clients)
+-- mcp_errors.py      # Error helpers
+-- input_validator.py # Input validation
+-- rate_limiter.py    # Rate limiting
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

---

**Last Updated:** 2026-03-31
