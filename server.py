# -*- coding: ascii -*-
"""UML Diagram MCP Server - Auto-generate 14 UML diagram types from codebase.

Tier 1 (AST-based): Class, Package, Component, Call Graph
Tier 2 (AST + LLM): Sequence, Activity, State
Tier 3 (LLM-powered): Use Case, Object, Deployment, Communication,
                       Composite Structure, Interaction Overview
NEW Tier 1: generate_uml_from_code (AST -> classDiagram)
NEW Tier 2: generate_timing_diagram (LLM gantt)
NEW Utility: select_optimal_diagram_type (KG router)

17 tools total (14 diagram types + generate_all + render + select_optimal_diagram_type).
Python 3.8+ only. ASCII-only source (cp1252 safe on Windows).
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP
from base.decorators import mcp_tool_handler

mcp = FastMCP(
    "uml-diagram",
    instructions=(
        "UML diagram generation from codebase analysis. "
        "Generates 14 UML diagram types using AST analysis, "
        "LLM enrichment, and Kroki.io rendering. "
        "Domain 46 skill context enriches LLM prompts when GLOBAL_LIBRARY_PATH is set."
    ),
)

try:
    from skill_context import get_skill_context, get_domain_context
    _SKILL_CONTEXT_AVAILABLE = True
except ImportError:
    _SKILL_CONTEXT_AVAILABLE = False

try:
    from kg_router import select_diagram_type as _kg_select_diagram_type
    _KG_ROUTER_AVAILABLE = True
except ImportError:
    _KG_ROUTER_AVAILABLE = False

if not _SKILL_CONTEXT_AVAILABLE:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "skill_context not importable. Domain 46 enrichment unavailable. "
        "Set GLOBAL_LIBRARY_PATH env var to enable."
    )

_DIAGRAM_TYPE_TO_SKILL = {
    "class":         "uml-class-diagram-core",
    "package":       "uml-package-diagram-core",
    "component":     "uml-component-diagram-core",
    "deployment":    "uml-deployment-diagram-core",
    "object":        "uml-object-diagram-core",
    "composite":     "uml-composite-structure-core",
    "usecase":       "uml-use-case-diagram-core",
    "activity":      "uml-activity-diagram-core",
    "state":         "uml-state-machine-core",
    "interaction":   "uml-interaction-overview-core",
    "sequence":      "uml-sequence-diagram-core",
    "communication": "uml-communication-diagram-core",
    "timing":        "uml-timing-diagram-core",
    "call_graph":    "diagram-from-code-core",
    "call_graph_rich": "diagram-layout-algorithms-core",
}

_ALL_DIAGRAM_TYPE_SLUGS = [
    "class", "sequence", "activity", "state", "component", "package",
    "deployment", "usecase", "object", "communication", "composite",
    "interaction", "timing", "call_graph",
]


def _get_generator(project_path, output_dir="docs/uml"):
    """Lazy import and create UMLDiagramGenerator.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory for generated diagram files.

    Returns:
        Initialized UMLDiagramGenerator instance.
    """
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from langgraph_engine.uml_generators import UMLDiagramGenerator
    return UMLDiagramGenerator(project_path, output_dir)


def _get_renderer():
    """Lazy import and create KrokiRenderer.

    Returns:
        Initialized KrokiRenderer instance.
    """
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from langgraph_engine.uml_generators import KrokiRenderer
    return KrokiRenderer()


def _resolve_project_file(project_path, source_file):
    """Resolve source_file relative to project_path with path traversal check.

    Uses abspath prefix comparison to prevent escaping project_path via
    '..' components or absolute paths embedded in source_file.

    Args:
        project_path: Absolute root path of the project.
        source_file: Path to source file, relative to project_path.

    Returns:
        Tuple of (resolved_str_path, error_str_or_None). On traversal attempt
        or missing file, resolved path is None and error contains description.
    """
    base = os.path.abspath(project_path)
    candidate = os.path.abspath(os.path.join(project_path, source_file))
    if not candidate.startswith(base + os.sep) and candidate != base:
        return None, "Path traversal attempt detected in source_file"
    if not os.path.isfile(candidate):
        return None, "File not found: %s" % candidate
    return candidate, None


# ==================================================================
# Tier 1: AST-based diagrams (no LLM required) -- UNCHANGED
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def generate_class_diagram(
    project_path: str,
    scope: str = "all",
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML class diagram from Python AST analysis.

    Produces Mermaid classDiagram syntax showing classes, attributes,
    methods, and inheritance. Saved to docs/uml/class-diagram.md.
    System prompt enriched with Domain 46 uml-class-diagram-core when
    GLOBAL_LIBRARY_PATH is configured.

    Args:
        project_path: Root path of the project to analyze.
        scope: "all" for full project, or a specific directory/file path.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_class_diagram(scope=scope)
    path = gen.save_diagram("class-diagram", syntax)
    return {
        "diagram_type": "class",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_package_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML package diagram from module import analysis.

    Produces Mermaid flowchart showing module dependencies.
    Saved to docs/uml/package-diagram.md.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_package_diagram()
    path = gen.save_diagram("package-diagram", syntax)
    return {
        "diagram_type": "package",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_component_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML component diagram from project structure.

    Produces Mermaid flowchart with subgraphs representing components.
    Saved to docs/uml/component-diagram.md.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_component_diagram()
    path = gen.save_diagram("component-diagram", syntax)
    return {
        "diagram_type": "component",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


# ==================================================================
# Tier 2: AST + LLM hybrid diagrams -- UNCHANGED
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def generate_sequence_diagram(
    project_path: str,
    entry_function: str = "",
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML sequence diagram from call chain analysis.

    Uses AST to extract call chains and optionally LLM to enrich labels.
    Produces Mermaid sequenceDiagram syntax.

    Args:
        project_path: Root path of the project to analyze.
        entry_function: Optional entry function to trace from.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_sequence_diagram(context=entry_function)
    path = gen.save_diagram("sequence-diagram", syntax)
    return {
        "diagram_type": "sequence",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_activity_diagram(
    project_path: str,
    function_path: str = "",
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML activity diagram from function logic.

    Uses LLM to analyze control flow and produce Mermaid flowchart TD.

    Args:
        project_path: Root path of the project to analyze.
        function_path: Optional file:function to analyze (e.g., "src/main.py:run").
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)

    func_code = ""
    if function_path and ":" in function_path:
        file_part, func_name = function_path.rsplit(":", 1)
        file_full = Path(project_path) / file_part
        if file_full.is_file():
            func_code = file_full.read_text(encoding="utf-8", errors="replace")[:3000]

    syntax = gen.generate_activity_diagram(func_code)
    path = gen.save_diagram("activity-diagram", syntax)
    return {
        "diagram_type": "activity",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_state_diagram(
    project_path: str,
    context: str = "",
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML state diagram from state pattern detection.

    Uses LLM to identify states and transitions, produces
    Mermaid stateDiagram-v2 syntax.

    Args:
        project_path: Root path of the project to analyze.
        context: Additional context about states in the system.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_state_diagram(context=context)
    path = gen.save_diagram("state-diagram", syntax)
    return {
        "diagram_type": "state",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


# ==================================================================
# Tier 3: LLM-powered diagrams -- UNCHANGED
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def generate_usecase_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML use case diagram from requirements docs.

    Reads SRS.md and README.md, uses LLM to produce PlantUML
    use case diagram syntax.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_usecase_diagram()
    path = gen.save_diagram("usecase-diagram", syntax)
    return {
        "diagram_type": "usecase",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_object_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML object diagram showing class instances.

    Uses AST class extraction + LLM to produce PlantUML object diagram
    with realistic field values.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_object_diagram()
    path = gen.save_diagram("object-diagram", syntax)
    return {
        "diagram_type": "object",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_deployment_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML deployment diagram from infrastructure files.

    Reads Dockerfile, docker-compose, K8s manifests, and uses LLM
    to produce PlantUML deployment diagram.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_deployment_diagram()
    path = gen.save_diagram("deployment-diagram", syntax)
    return {
        "diagram_type": "deployment",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_communication_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML communication diagram from module interactions.

    Uses dependency graph + LLM to show numbered message flows
    between modules in PlantUML.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_communication_diagram()
    path = gen.save_diagram("communication-diagram", syntax)
    return {
        "diagram_type": "communication",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_composite_structure_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML composite structure diagram.

    Shows internal structure of classes (ports, parts, connectors)
    using PlantUML.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_composite_structure_diagram()
    path = gen.save_diagram("composite-structure-diagram", syntax)
    return {
        "diagram_type": "composite_structure",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_interaction_overview_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML interaction overview diagram.

    Combines activity and sequence diagram elements showing
    combined interaction flows in PlantUML.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_interaction_overview()
    path = gen.save_diagram("interaction-overview-diagram", syntax)
    return {
        "diagram_type": "interaction_overview",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


@mcp.tool()
@mcp_tool_handler
def generate_call_graph_diagram(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a Mermaid flowchart showing the project call graph.

    Tier 1 diagram: AST-based, no LLM required. Classes are rendered
    as subgraphs with methods as nodes. Call edges connect methods
    across classes. Entry points (public methods not called by others)
    are highlighted with bold borders. High-complexity methods
    (cyclomatic complexity >= 5) are highlighted in red.

    Limits output to 40 methods and 60 edges for readability.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_call_graph_diagram()
    path = gen.save_diagram("call-graph-diagram", syntax)
    return {
        "diagram_type": "call_graph",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
    }


# ==================================================================
# Utility tools -- UNCHANGED
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def generate_all_diagrams(
    project_path: str,
    output_dir: str = "docs/uml",
) -> dict:
    """Generate all applicable UML diagrams for a project.

    Generates Tier 1 (AST-based) diagrams always, and attempts
    Tier 2/3 (LLM-powered) diagrams on best-effort basis.
    Includes the 3 new diagram generators added in Domain 46 integration:
    generate_uml_from_code (requires source_file), generate_timing_diagram,
    and generates all 14 types via gen.generate_all().

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
    """
    gen = _get_generator(project_path, output_dir)
    results = gen.generate_all()

    timing_syntax = ""
    try:
        timing_syntax = gen.generate_timing_diagram()
        results["timing-diagram"] = timing_syntax
    except Exception:
        pass

    saved = []
    for name, syntax in results.items():
        path = gen.save_diagram(name, syntax)
        saved.append({"name": name, "file": path})

    return {
        "diagrams_generated": len(saved),
        "diagrams": saved,
        "output_dir": str(gen.output_dir),
    }


@mcp.tool()
@mcp_tool_handler
def render_diagram(
    diagram_text: str,
    diagram_type: str = "plantuml",
    output_format: str = "svg",
    output_path: str = "",
) -> dict:
    """Render any diagram via Kroki.io free API.

    Converts PlantUML/Mermaid source to SVG/PNG using the free
    Kroki.io rendering service (no Java required).

    Args:
        diagram_text: PlantUML or Mermaid source text.
        diagram_type: "plantuml", "mermaid", "graphviz", etc.
        output_format: "svg" or "png".
        output_path: Optional file path to save rendered output.
    """
    renderer = _get_renderer()

    if output_path:
        result_path = renderer.render_to_file(
            diagram_text, output_path, diagram_type, output_format
        )
        if result_path:
            return {
                "rendered": True,
                "output_path": result_path,
                "format": output_format,
            }
        return {
            "rendered": False,
            "error": "Kroki rendering failed",
        }
    else:
        data = renderer.render(diagram_text, diagram_type, output_format)
        if data:
            return {
                "rendered": True,
                "format": output_format,
                "size_bytes": len(data),
                "note": "Use output_path parameter to save to file",
            }
        return {
            "rendered": False,
            "error": "Kroki rendering failed",
        }


# ==================================================================
# NEW Tool #15: generate_uml_from_code
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def generate_uml_from_code(
    project_path: str,
    source_file: str,
    language: str = "python",
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML class diagram by AST-parsing a source file.

    Reads source_file from project_path, parses it with AST analysis,
    and produces Mermaid classDiagram syntax. For Python, uses the stdlib
    ast module directly. Falls back to LLM-based parsing for other languages
    or on AST parse failure. System prompt enriched with Domain 46
    diagram-from-code-core skill context (M1-M6) when GLOBAL_LIBRARY_PATH
    is configured.

    Supported languages: python (AST), java, typescript, kotlin (regex/LLM).
    Path traversal: source_file is validated to remain within project_path.

    Args:
        project_path: Root path of the project.
        source_file: Path to source file, relative to project_path.
                     E.g. "src/models.py" or "lib/service.java".
        language: Source language. Supports "python", "java", "typescript",
                  "kotlin". Defaults to "python".
        output_dir: Output directory relative to project root.
                    Defaults to "docs/uml".

    Returns:
        dict with diagram_type, format, output_file, language, lines_parsed.
    """
    resolved, err = _resolve_project_file(project_path, source_file)
    if err:
        return {
            "diagram_type": "class",
            "format": "mermaid",
            "output_file": "",
            "error": err,
            "language": language,
            "lines_parsed": 0,
        }

    try:
        source_code = Path(resolved).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {
            "diagram_type": "class",
            "format": "mermaid",
            "output_file": "",
            "error": "Could not read source file: %s" % str(exc),
            "language": language,
            "lines_parsed": 0,
        }

    lines_parsed = len(source_code.splitlines())
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_uml_from_code(source_code, language)
    path = gen.save_diagram("uml-from-code-diagram", syntax)

    return {
        "diagram_type": "class",
        "format": "mermaid",
        "output_file": path,
        "language": language,
        "lines_parsed": lines_parsed,
    }


# ==================================================================
# NEW Tool #16: select_optimal_diagram_type
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def select_optimal_diagram_type(
    project_description: str,
    constraint: str = "",
) -> dict:
    """Select the most appropriate UML diagram type for a given description.

    Routes project_description through kg_router.select_diagram_type() for
    keyword-based diagram type selection. When kg_router is unavailable,
    falls back to "class" with an availability flag in the response.
    Validates input for null bytes and enforces a 2000-character limit.

    Args:
        project_description: Natural language description of what to model.
                             Max 2000 characters. Null bytes rejected.
        constraint: Optional hint for diagram family ("structural",
                    "behavioral", "interaction", "tooling", "").
                    Empty string means no constraint. Max 200 chars.

    Returns:
        dict with diagram_type, reason, confidence (keyword match count),
        available_types (list of all 14 slugs), available (bool).
    """
    if "\x00" in project_description or "\x00" in constraint:
        return {
            "diagram_type": "class",
            "reason": "Invalid input: null bytes not allowed",
            "confidence": 0,
            "available_types": _ALL_DIAGRAM_TYPE_SLUGS,
            "available": False,
        }

    if len(project_description) > 2000:
        project_description = project_description[:2000]
    if len(constraint) > 200:
        constraint = constraint[:200]

    if not _KG_ROUTER_AVAILABLE:
        return {
            "diagram_type": "class",
            "reason": "kg_router not available; defaulting to class diagram",
            "confidence": 0,
            "available_types": _ALL_DIAGRAM_TYPE_SLUGS,
            "available": False,
        }

    combined = project_description
    if constraint:
        combined = combined + " " + constraint

    diagram_type, reason = _kg_select_diagram_type(combined)

    from kg_router import DIAGRAM_TYPE_KEYWORDS
    matched_count = 0
    desc_lower = combined.lower()
    if diagram_type in DIAGRAM_TYPE_KEYWORDS:
        for kw in DIAGRAM_TYPE_KEYWORDS[diagram_type]:
            if kw in desc_lower:
                matched_count += 1

    return {
        "diagram_type": diagram_type,
        "reason": reason,
        "confidence": matched_count,
        "available_types": _ALL_DIAGRAM_TYPE_SLUGS,
        "available": True,
    }


# ==================================================================
# NEW Tool #17: generate_timing_diagram
# ==================================================================

@mcp.tool()
@mcp_tool_handler
def generate_timing_diagram(
    project_path: str,
    process_name: str = "",
    output_dir: str = "docs/uml",
) -> dict:
    """Generate a UML timing diagram as Mermaid gantt syntax.

    Produces a gantt diagram with mandatory header lines (gantt, title,
    dateFormat YYYY-MM-DD, axisFormat %Y-%m-%d). Task IDs are sanitized
    to alphanumeric + underscore. System prompt enriched with Domain 46
    uml-timing-diagram-core skill context when GLOBAL_LIBRARY_PATH is set.

    Output Mermaid gantt structure:
        gantt
            title {process_name} -- Timing Diagram
            dateFormat  YYYY-MM-DD
            axisFormat  %Y-%m-%d
            section Initialization
                ...
            section Processing
                ...
            section Completion
                ...

    Args:
        project_path: Root path of the project to analyze.
        process_name: Name of the process for the gantt title.
                      Max 200 characters. Defaults to "" (rendered as
                      "System Process Timeline").
        output_dir: Output directory relative to project root.
                    Defaults to "docs/uml".

    Returns:
        dict with diagram_type, format, output_file, lines, process_name.
    """
    if len(process_name) > 200:
        process_name = process_name[:200]

    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_timing_diagram(process_name)
    path = gen.save_diagram("timing-diagram", syntax)

    return {
        "diagram_type": "timing",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "process_name": process_name if process_name else "System Process Timeline",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
