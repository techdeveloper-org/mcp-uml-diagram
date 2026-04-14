# mcp-uml-diagram

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![MCP](https://img.shields.io/badge/protocol-MCP%20stdio-lightgrey)
![Part of claude-workflow-engine](https://img.shields.io/badge/part%20of-claude--workflow--engine-orange)

An MCP server that auto-generates 13 UML diagram types directly from your codebase using a three-tier approach: pure AST analysis for structural diagrams (no LLM required), AST-plus-LLM hybrid for behavioral diagrams, and LLM-powered synthesis for high-level architectural diagrams. Output is written as Mermaid or PlantUML source files, with optional rendering to SVG or PNG via the free Kroki.io service. The server integrates into the Claude Workflow Engine pipeline as the Step 13 documentation node and can be used standalone inside any Claude Code session.

---

## Features

- 13 diagram types from a single codebase scan — class, package, component, sequence, activity, state, use case, object, deployment, communication, composite structure, interaction overview, and call graph
- Three-tier architecture: Tier 1 (AST-only, fastest), Tier 2 (AST + LLM), Tier 3 (LLM-synthesized from docs and infra files)
- Call graph diagram traces actual AST method call chains, with cyclomatic complexity highlighting
- Kroki.io rendering produces shareable PNG/SVG with no local Graphviz or Java installation required
- Single `generate_all_diagrams` call to produce the full diagram suite in one step
- Works standalone inside any Claude Code session or as part of the Claude Workflow Engine pipeline

---

## Tools

14 tools total (12 individual diagram types + 1 bulk generator + 1 renderer).

### Tier 1: AST-Based Diagrams (no LLM required)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `generate_class_diagram` | UML class diagram from Python AST — classes, attributes, methods, inheritance. Output: Mermaid `classDiagram`. | `project_path` (required), `scope` (default: `"all"`), `output_dir` (default: `"docs/uml"`) |
| `generate_package_diagram` | Module dependency diagram from import analysis. Output: Mermaid flowchart. | `project_path` (required), `output_dir` |
| `generate_component_diagram` | Component diagram showing system boundaries and subsystem groupings. Output: Mermaid flowchart with subgraphs. | `project_path` (required), `output_dir` |
| `generate_call_graph_diagram` | Actual method-level call graph from AST. Entry points highlighted, high-complexity methods flagged in red. Capped at 40 methods and 60 edges. Output: Mermaid flowchart. | `project_path` (required), `output_dir` |

### Tier 2: AST + LLM Hybrid Diagrams

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `generate_sequence_diagram` | Sequence diagram from call chain analysis. AST extracts chains, LLM enriches labels. Output: Mermaid `sequenceDiagram`. | `project_path` (required), `entry_function` (optional — trace from specific function), `output_dir` |
| `generate_activity_diagram` | Activity diagram from function control flow analysis. Output: Mermaid flowchart TD. | `project_path` (required), `function_path` (optional — `"file.py:function_name"`), `output_dir` |
| `generate_state_diagram` | State machine diagram from state pattern detection. Output: Mermaid `stateDiagram-v2`. | `project_path` (required), `context` (optional — additional hints about states), `output_dir` |

### Tier 3: LLM-Powered Diagrams

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `generate_usecase_diagram` | Use case diagram synthesized from `SRS.md` and `README.md`. Output: PlantUML. | `project_path` (required), `output_dir` |
| `generate_object_diagram` | Object instance diagram with realistic field values synthesized from class definitions. Output: PlantUML. | `project_path` (required), `output_dir` |
| `generate_deployment_diagram` | Deployment diagram from `Dockerfile`, `docker-compose.yml`, and Kubernetes manifests. Output: PlantUML. | `project_path` (required), `output_dir` |
| `generate_communication_diagram` | Communication diagram showing numbered message flows between modules. Output: PlantUML. | `project_path` (required), `output_dir` |
| `generate_composite_structure_diagram` | Composite structure diagram showing ports, parts, and connectors inside classes. Output: PlantUML. | `project_path` (required), `output_dir` |
| `generate_interaction_overview_diagram` | Interaction overview combining activity and sequence diagram elements. Output: PlantUML. | `project_path` (required), `output_dir` |

### Utility Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `generate_all_diagrams` | Generate all 13 diagram types in one call. Tier 1 always runs; Tier 2/3 run best-effort. | `project_path` (required), `output_dir` |
| `render_diagram` | Render any Mermaid or PlantUML source to SVG/PNG via Kroki.io (no local Java required). | `diagram_text` (required), `diagram_type` (default: `"plantuml"`), `output_format` (`"svg"` or `"png"`), `output_path` (optional) |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/techdeveloper-org/mcp-uml-diagram.git
cd mcp-uml-diagram
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Or install directly:

```bash
pip install mcp fastmcp
```

### 3. Set up the engine dependency

This server depends on the `uml_generators` module from the Claude Workflow Engine. Clone it alongside this repo and set `PYTHONPATH`:

```bash
git clone https://github.com/techdeveloper-org/claude-workflow-engine.git
export PYTHONPATH=/path/to/claude-workflow-engine/scripts:$PYTHONPATH
```

### 4. Configure Claude Code

Add the server to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "uml-diagram": {
      "command": "python",
      "args": [
        "/path/to/mcp-uml-diagram/server.py"
      ],
      "env": {
        "PYTHONPATH": "/path/to/claude-workflow-engine/scripts",
        "ANTHROPIC_API_KEY": "your_key_here"
      }
    }
  }
}
```

---

## Configuration

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes (Tier 2/3) | — | Claude API key. Required for sequence, activity, state, use case, object, deployment, communication, composite, and interaction overview diagrams. Tier 1 diagrams (class, package, component, call graph) work without it. |
| `UML_OUTPUT_DIR` | No | `docs/uml` | Output directory for all generated `.md` diagram files. Set to `uml/` to write to the project root `uml/` directory (the Claude Workflow Engine default). |
| `KROKI_SERVER` | No | `https://kroki.io` | Kroki rendering server URL. Override with a self-hosted Kroki instance for air-gapped environments. |

---

## Usage Examples

### Generate a class diagram

```
Generate a class diagram for /path/to/my-project
```

Claude Code invokes `generate_class_diagram` with `project_path=/path/to/my-project`. The diagram is written to `docs/uml/class-diagram.md` as Mermaid syntax.

---

### Generate a sequence diagram from a specific entry function

```
Generate a sequence diagram for /path/to/my-project starting from the run_pipeline function
```

Claude Code invokes `generate_sequence_diagram` with `entry_function="run_pipeline"`. The call chain is traced from that function and enriched by LLM.

---

### Analyze the call graph to identify hot nodes

```
Generate the call graph for /path/to/my-project and show me which methods have high complexity
```

Claude Code invokes `generate_call_graph_diagram`. Methods with cyclomatic complexity >= 5 are highlighted in red. Entry points (public methods not called by other methods in the graph) are given bold borders.

---

### Generate all 13 diagram types at once

```
Generate all UML diagrams for /path/to/my-project and save them to docs/uml
```

Claude Code invokes `generate_all_diagrams`. Tier 1 diagrams always complete; Tier 2/3 diagrams attempt best-effort with LLM. All output files are listed in the response.

---

### Render an existing diagram to PNG

```
Render this PlantUML source to a PNG file at /tmp/my-diagram.png:

@startuml
Alice -> Bob: Hello
@enduml
```

Claude Code invokes `render_diagram` with `diagram_type="plantuml"`, `output_format="png"`, and `output_path="/tmp/my-diagram.png"`.

---

## Diagram Types Reference

| # | Diagram | Tier | Output Format | Saved As |
|---|---------|------|---------------|----------|
| 1 | Class | 1 (AST) | Mermaid `classDiagram` | `class-diagram.md` |
| 2 | Package | 1 (AST) | Mermaid flowchart | `package-diagram.md` |
| 3 | Component | 1 (AST) | Mermaid flowchart | `component-diagram.md` |
| 4 | Call Graph | 1 (AST) | Mermaid flowchart | `call-graph-diagram.md` |
| 5 | Sequence | 2 (AST+LLM) | Mermaid `sequenceDiagram` | `sequence-diagram.md` |
| 6 | Activity | 2 (AST+LLM) | Mermaid flowchart TD | `activity-diagram.md` |
| 7 | State | 2 (AST+LLM) | Mermaid `stateDiagram-v2` | `state-diagram.md` |
| 8 | Use Case | 3 (LLM) | PlantUML | `usecase-diagram.md` |
| 9 | Object | 3 (LLM) | PlantUML | `object-diagram.md` |
| 10 | Deployment | 3 (LLM) | PlantUML | `deployment-diagram.md` |
| 11 | Communication | 3 (LLM) | PlantUML | `communication-diagram.md` |
| 12 | Composite Structure | 3 (LLM) | PlantUML | `composite-structure-diagram.md` |
| 13 | Interaction Overview | 3 (LLM) | PlantUML | `interaction-overview-diagram.md` |

**Tier 1** — Pure AST parsing. No API key needed. Fastest and most accurate for structural information.

**Tier 2** — AST provides structure; LLM enriches labels, infers intent, and narrows scope to relevant flows.

**Tier 3** — LLM reads project documentation, infrastructure files, and class definitions to synthesize diagrams that cannot be derived from AST alone.

---

## Pipeline Integration

This server is used by the Claude Workflow Engine as the Step 13 documentation node (Documentation Update + UML Diagram Generation). In that context:

- `UML_OUTPUT_DIR` is set via the `UML_OUTPUT_DIR` environment variable (defaults to the `uml/` directory at the project root when running inside the pipeline).
- The pipeline calls `generate_all_diagrams` at the end of each implementation cycle to keep documentation current.
- The call graph data produced during Step 10 (Implementation) feeds into the diagram generator, so diagrams reflect post-implementation state rather than the pre-change snapshot.

To replicate this behavior outside the pipeline:

```bash
UML_OUTPUT_DIR=uml python server.py
```

---

## Architecture

```
server.py
  |
  +-- generate_class_diagram()         Tier 1 (AST)
  +-- generate_package_diagram()       Tier 1 (AST)
  +-- generate_component_diagram()     Tier 1 (AST)
  +-- generate_call_graph_diagram()    Tier 1 (AST + complexity analysis)
  |
  +-- generate_sequence_diagram()      Tier 2 (AST + LLM)
  +-- generate_activity_diagram()      Tier 2 (AST + LLM)
  +-- generate_state_diagram()         Tier 2 (AST + LLM)
  |
  +-- generate_usecase_diagram()       Tier 3 (LLM from docs)
  +-- generate_object_diagram()        Tier 3 (LLM from classes)
  +-- generate_deployment_diagram()    Tier 3 (LLM from infra files)
  +-- generate_communication_diagram() Tier 3 (LLM from deps)
  +-- generate_composite_structure_diagram()      Tier 3 (LLM)
  +-- generate_interaction_overview_diagram()     Tier 3 (LLM)
  |
  +-- generate_all_diagrams()          Runs all tiers
  +-- render_diagram()                 Kroki.io HTTP render
  |
  +-- base/                            Shared utilities (mcp-base copy)
  |     decorators.py                  @mcp_tool_handler error wrapper
  |     response.py                    MCPResponse builder
  |     persistence.py                 AtomicJsonStore
  |     clients.py                     LazyClient pattern
  |
  +-- [engine dependency]
        claude-workflow-engine/scripts/langgraph_engine/
          uml_generators.py            UMLDiagramGenerator + KrokiRenderer
          diagrams/                    Strategy pattern: 13 diagram generators
          parsers/                     AST parsers (Python, Java, TS, Kotlin)
          call_graph_builder.py        AST call graph construction
```

---

## Project Context

This MCP server is one of 13 servers in the **Claude Workflow Engine** ecosystem, a LangGraph-based orchestration pipeline for automating Claude Code development workflows.

| Repository | Purpose |
|------------|---------|
| [`claude-workflow-engine`](https://github.com/techdeveloper-org/claude-workflow-engine) | Main pipeline — LangGraph orchestration, 8 active steps, 578-class call graph |
| [`mcp-base`](https://github.com/techdeveloper-org/mcp-base) | Shared base package — MCPResponse, @mcp_tool_handler, AtomicJsonStore, LazyClient |
| [`mcp-drawio-diagram`](https://github.com/techdeveloper-org/mcp-drawio-diagram) | Companion server — editable draw.io diagrams (12 types, no API needed) |

All 13 servers in the ecosystem:

| Server | Tools | Purpose |
|--------|-------|---------|
| mcp-session-mgr | 14 | Session lifecycle |
| mcp-git-ops | 14 | Git operations |
| mcp-github-api | 12 | GitHub PR and issue management |
| mcp-policy-enforcement | 11 | Policy compliance checks |
| mcp-token-optimizer | 10 | Token reduction (60-85% savings) |
| mcp-pre-tool-gate | 13 | Pre-tool validation |
| mcp-post-tool-tracker | 6 | Post-tool tracking |
| mcp-standards-loader | 7 | Standards detection and loading |
| **mcp-uml-diagram** | **14** | **UML diagram generation (this server)** |
| mcp-drawio-diagram | 5 | Draw.io editable diagrams |
| mcp-jira-api | 10 | Jira issue tracking |
| mcp-jenkins-ci | 10 | Jenkins CI/CD |
| mcp-figma | 10 | Figma design integration |

---

## Requirements

- Python 3.8+
- `mcp >= 1.0.0`
- `fastmcp >= 0.1.0`
- `claude-workflow-engine` cloned and on `PYTHONPATH` (for `uml_generators` module)
- `ANTHROPIC_API_KEY` set in environment (required for Tier 2 and Tier 3 diagrams only)

---

## Contributing

Contributions are welcome. Please follow these guidelines:

1. Fork the repository and create a feature branch from `main`.
2. Keep changes focused — one diagram type or one utility improvement per pull request.
3. If adding a new diagram type, add the tool in `server.py` following the existing Tier pattern, and add the corresponding generator in the engine's `diagrams/` package.
4. Ensure all existing Tier 1 tools continue to work without an API key.
5. Open a pull request against `main` with a clear description of what changed and why.

---

## License

MIT License. Copyright (c) 2024 techdeveloper-org.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
