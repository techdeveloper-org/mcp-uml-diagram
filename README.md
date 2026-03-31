# mcp-uml-diagram

A FastMCP server providing **Uml Diagram** capabilities for Claude Code workflows.

---

## Overview

Auto-generate 13 UML diagram types from codebase using AST analysis and LLM synthesis. Tier 1 (AST-only): Class, Package, Component. Tier 2 (AST+LLM): Sequence, Activity, State. Tier 3 (LLM): Use Case, Object, Deployment, Communication, Composite, Interaction, Call Graph. Renders via Kroki.io.

---

## Tools

| Tool | Description |
|------|-------------|
| `generate_class_diagram` | Generate UML class diagram from Python/Java/TS/Kotlin AST |
| `generate_package_diagram` | Generate package/module dependency diagram |
| `generate_component_diagram` | Generate component diagram showing system boundaries |
| `generate_sequence_diagram` | Generate sequence diagram for a flow (AST + LLM) |
| `generate_activity_diagram` | Generate activity diagram for a process flow |
| `generate_state_diagram` | Generate state machine diagram from transition logic |
| `generate_usecase_diagram` | Generate use case diagram (LLM-synthesized) |
| `generate_object_diagram` | Generate object instance diagram |
| `generate_deployment_diagram` | Generate deployment/infrastructure diagram |
| `generate_communication_diagram` | Generate communication/collaboration diagram |
| `generate_composite_structure_diagram` | Generate composite structure diagram |
| `generate_interaction_overview_diagram` | Generate interaction overview |
| `generate_call_graph_diagram` | Generate call graph from AST method calls |
| `generate_all_diagrams` | Generate all 13 diagram types in one call |
| `render_diagram` | Render Mermaid/PlantUML markup to PNG/SVG via Kroki.io |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/techdeveloper-org/mcp-uml-diagram.git
cd mcp-uml-diagram
```

### 2. Install dependencies

```bash
pip install mcp fastmcp
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

---

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Required for Tier 2/3 LLM-assisted diagrams |
| `UML_OUTPUT_DIR` | Directory for generated diagram files (default: docs/uml/) |
| `KROKI_SERVER` | Kroki rendering server URL (default: https://kroki.io) |

---

## Usage in Claude Code

Add to your `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "uml-diagram": {
      "command": "python",
      "args": [
        "/path/to/mcp-uml-diagram/server.py"
      ],
      "env": {}
    }
  }
}
```

## Engine Dependency

> **Note:** This server depends on `claude-workflow-engine` internals.
>
> Depends on scripts/langgraph_engine/uml_generators.py and diagrams/ package from claude-workflow-engine
>
> Ensure `claude-workflow-engine` is cloned alongside this repo and its
> `scripts/` directory is on your `PYTHONPATH`.

```bash
export PYTHONPATH=/path/to/claude-workflow-engine/scripts:$PYTHONPATH
```

---

## Benefits

- 13 diagram types from a single codebase scan — no manual diagram maintenance
- 3-tier approach: fast AST for structural diagrams, LLM only when needed
- Call graph diagram reflects actual method call chains, not guessed architecture
- Kroki.io rendering produces shareable PNG/SVG without local Graphviz install

---

## Requirements

- Python 3.8+
- `mcp fastmcp`
- See `requirements.txt` for pinned versions

---

## Project Context

This MCP server is part of the **Claude Workflow Engine** ecosystem — a LangGraph-based
orchestration pipeline for automating Claude Code development workflows.

Related repos:
- [`claude-workflow-engine`](https://github.com/techdeveloper-org/claude-workflow-engine) — Main pipeline
- [`mcp-base`](https://github.com/techdeveloper-org/mcp-base) — Shared base utilities used by all MCP servers

---

## License

Private — techdeveloper-org
