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
Python 3.11+. ASCII-only source (cp1252 safe on Windows).
"""

import datetime as _datetime
import inspect as _inspect
import json as _json
import logging as _logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Tuple

from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parent))

# mcp 2.0 renamed FastMCP to MCPServer and moved it to mcp.server.mcpserver.
# Both names are probed so this server runs under either major version; the
# API used below (tool decorator, run(transport=...)) is identical in both.
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp < 2.0
    from mcp.server.fastmcp import FastMCP as MCPServer

try:
    from mcp.types import ToolAnnotations
except ImportError:  # very old SDK without the annotations model
    ToolAnnotations = None

from base.decorators import mcp_tool_handler

mcp = MCPServer(
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
    _logging.getLogger(__name__).warning(
        "skill_context not importable. Domain 46 enrichment unavailable. "
        "Set GLOBAL_LIBRARY_PATH env var to enable."
    )

try:
    from kg_router import select_diagram_type as _kg_select_diagram_type
    _KG_ROUTER_AVAILABLE = True
except ImportError:
    _KG_ROUTER_AVAILABLE = False

_AUDIT_LOG_ENABLED = os.environ.get("ENABLE_AUDIT_LOG", "0") == "1"
_AUDIT_LOGGER = _logging.getLogger("uml_diagram.audit")
_LOG = _logging.getLogger("uml_diagram")


def _audit(tool_name, params):
    # type: (str, dict) -> None
    """Log a structured audit entry when ENABLE_AUDIT_LOG=1.

    Emits a single-line JSON record to the uml_diagram.audit logger at INFO
    level. A serialization failure is downgraded to a warning rather than
    propagating, so audit logging never interrupts tool execution, but it is
    never silently discarded either. Set ENABLE_AUDIT_LOG=1 to activate.

    Args:
        tool_name: MCP tool name being invoked.
        params: Dict of sanitized parameter names and scalar values.
                Must not contain file contents or secrets.
    """
    if not _AUDIT_LOG_ENABLED:
        return
    try:
        _AUDIT_LOGGER.info(_json.dumps({
            "ts": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            "tool": tool_name,
            "params": params,
        }))
    except (TypeError, ValueError) as exc:
        _AUDIT_LOGGER.warning(
            "audit record for %s could not be serialized: %s", tool_name, exc
        )


_TOOL_KWARGS = set(_inspect.signature(mcp.tool).parameters)


def _tool(**kwargs):
    """Register an MCP tool, dropping kwargs the installed SDK does not accept.

    ``annotations`` and ``structured_output`` were added to FastMCP at
    different points, so unsupported keywords are filtered rather than raising
    at import time on an older SDK.

    Args:
        **kwargs: Keyword arguments for the underlying ``mcp.tool`` decorator.

    Returns:
        The decorator returned by ``mcp.tool``.
    """
    supported = {key: value for key, value in kwargs.items() if key in _TOOL_KWARGS}
    return mcp.tool(**supported)


def _annotations(title, read_only, destructive, idempotent, open_world=False):
    """Build a ``ToolAnnotations`` object, or None on an SDK without the model.

    An omitted annotation set is read by the specification as the least-safe
    possible declaration, so every tool here declares all four hints.

    Args:
        title: Human-readable tool title.
        read_only: True when the tool performs no writes.
        destructive: True when the tool's effect is irreversible.
        idempotent: True when repeat calls with identical arguments have the
            same cumulative effect as a single call.
        open_world: True when the tool reaches an external or open-ended system.

    Returns:
        A ``ToolAnnotations`` instance, or None when unavailable.
    """
    if ToolAnnotations is None:
        return None
    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


ProjectPath = Annotated[str, Field(
    description="Absolute root path of the project to analyze."
)]
OutputDir = Annotated[str, Field(
    description=(
        "Output directory for the generated Mermaid/PlantUML .md file, relative "
        "to the project root or absolute. Leave empty to use the UML_OUTPUT_DIR "
        "environment variable, falling back to '{project_root}/uml'."
    )
)]


def _model_data_field(primary_key):
    """Build a ModelData Field description naming this diagram type's primary key.

    Args:
        primary_key: The UDM key this diagram type requires (e.g. "classes",
            "nodes", "steps") -- see UDM_PRIMARY_KEY.

    Returns:
        An Annotated[str, Field(...)] type for use as a tool parameter.
    """
    return Annotated[str, Field(
        description=(
            "Optional JSON object of pre-built structural data to render from, "
            "instead of scanning project_path for source code. Use this when the "
            "authoritative structure lives somewhere other than code -- an "
            "architecture corpus, a spec, or a schema. When supplied, the diagram "
            "is rendered deterministically from this payload and no AST scan or "
            "LLM call is performed; when omitted, behaviour is unchanged. The "
            "object must contain a non-empty \"%s\" list; a payload missing it is "
            "rejected rather than silently rendered from placeholder data. "
            "Max 512 KB (see UML_MAX_MODEL_DATA_KB)." % primary_key
        )
    )]


# Generic fallback for tools (generate_all_diagrams) that are not specific to
# one diagram type's primary key.
ModelData = Annotated[str, Field(
    description=(
        "Optional JSON object of pre-built structural data to render from, "
        "instead of scanning project_path for source code. Use this when the "
        "authoritative structure lives somewhere other than code -- an "
        "architecture corpus, a spec, or a schema. When supplied, the diagram "
        "is rendered deterministically from this payload and no AST scan or "
        "LLM call is performed; when omitted, behaviour is unchanged. The "
        "object must contain the primary key for this diagram type (for "
        "example \"classes\" for class, \"nodes\" for deployment, \"steps\" "
        "for activity); a payload missing it is rejected rather than "
        "silently rendered from placeholder data. Max 512 KB."
    )
)]

_MAX_MODEL_DATA_BYTES = int(os.environ.get("UML_MAX_MODEL_DATA_KB", "512")) * 1024


def _parse_model_data(raw, diagram_type):
    # type: (str, str) -> Tuple[Optional[dict], Optional[str]]
    """Parse and validate a caller-supplied model_data JSON string.

    Args:
        raw: JSON object string, or "" when the parameter was not supplied.
        diagram_type: Diagram type slug, used to check the UDM primary key.

    Returns:
        (data, error): (None, None) when raw is empty ("not supplied" -- the
        caller follows the existing derived path); (dict, None) on success;
        (None, str) with a caller-actionable message on validation failure.
    """
    if raw is None or raw.strip() == "":
        return None, None

    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) > _MAX_MODEL_DATA_BYTES:
        return None, (
            "model_data exceeds %d KB limit; set UML_MAX_MODEL_DATA_KB to raise it"
            % (_MAX_MODEL_DATA_BYTES // 1024)
        )
    if "\x00" in raw:
        return None, "model_data contains null bytes"

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        return None, "model_data is not valid JSON: %s at line %d column %d" % (
            exc.msg, exc.lineno, exc.colno,
        )

    if not isinstance(data, dict):
        return None, "model_data must be a JSON object, got %s" % type(data).__name__

    try:
        from langgraph_engine.uml_generators import UDM_PRIMARY_KEY
    except ImportError:
        return None, "langgraph_engine not available; cannot validate model_data"

    if diagram_type not in UDM_PRIMARY_KEY:
        return None, "unknown diagram_type for model_data validation: %s" % diagram_type

    primary_key = UDM_PRIMARY_KEY[diagram_type]
    value = data.get(primary_key)
    if not value:
        return None, (
            "model_data for '%s' must contain a non-empty '%s' list"
            % (diagram_type, primary_key)
        )
    if not isinstance(value, list):
        return None, "model_data['%s'] must be a list, got %s" % (
            primary_key, type(value).__name__,
        )

    return data, None

# Canonical output file stems mandated for the standard diagram set. The engine
# names its results with hyphens and two longer stems; every write goes through
# _canonical_stem() so the files on disk carry these exact names.
_CANONICAL_STEMS = {
    "class": "class_diagram",
    "package": "package_diagram",
    "component": "component_diagram",
    "sequence": "sequence_diagram",
    "state": "state_diagram",
    "activity": "activity_diagram",
    "deployment": "deployment_diagram",
    "usecase": "usecase_diagram",
    "object": "object_diagram",
    "composite": "composite_diagram",
    "interaction": "interaction_diagram",
    "communication": "communication_diagram",
    "call_graph": "call_graph_diagram",
}

_ENGINE_STEM_ALIASES = {
    "composite_structure_diagram": "composite_diagram",
    "interaction_overview_diagram": "interaction_diagram",
    "use_case_diagram": "usecase_diagram",
}

# Diagram-type slug (UDM_PRIMARY_KEY / generate_from_data key) -> the hyphenated
# key UMLDiagramGenerator.generate_all() uses in its results dict. Needed only
# by generate_all_diagrams's model_data map to route a per-type payload to the
# right slot in that dict.
_SLUG_TO_ENGINE_KEY = {
    "class": "class-diagram",
    "package": "package-diagram",
    "component": "component-diagram",
    "call_graph": "call-graph-diagram",
    "sequence": "sequence-diagram",
    "activity": "activity-diagram",
    "state": "state-diagram",
    "usecase": "usecase-diagram",
    "object": "object-diagram",
    "deployment": "deployment-diagram",
    "communication": "communication-diagram",
    "composite": "composite-structure-diagram",
    "interaction": "interaction-overview-diagram",
}

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


def _engine_root_candidates():
    """Return the candidate directories that may contain ``langgraph_engine``.

    Ordered most-specific first: explicit environment overrides, then the
    sibling claude-workflow-engine checkout (where the package lives at the
    repository root), then its legacy ``scripts/`` location.

    Returns:
        List of Path objects, in probe order.
    """
    here = Path(__file__).resolve().parent
    workspace = here.parent
    candidates = []
    for var in ("CLAUDE_WORKFLOW_ENGINE_PATH", "WORKFLOW_ENGINE_PATH"):
        raw = os.environ.get(var, "").strip()
        if raw:
            candidates.append(Path(raw))
            candidates.append(Path(raw) / "scripts")
    candidates.append(workspace / "claude-workflow-engine")
    candidates.append(workspace / "claude-workflow-engine" / "scripts")
    candidates.append(workspace.parent / "scripts")
    return candidates


def _ensure_engine_path():
    """Put the claude-workflow-engine root on sys.path.

    Only a directory that actually contains ``langgraph_engine/__init__.py`` is
    added, so a stale or renamed checkout produces an explicit failure instead
    of a silently ineffective sys.path entry.

    Returns:
        The Path that was added or already present, or None when no candidate
        contains the package.
    """
    for candidate in _engine_root_candidates():
        if (candidate / "langgraph_engine" / "__init__.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    _LOG.warning(
        "langgraph_engine not found in any known location: %s. "
        "Set CLAUDE_WORKFLOW_ENGINE_PATH to the claude-workflow-engine checkout.",
        ", ".join(str(c) for c in _engine_root_candidates()),
    )
    return None


_ENGINE_ROOT = _ensure_engine_path()

try:
    import uml_generators_patch as _uml_patch  # noqa: F401
    _UML_PATCH_AVAILABLE = True
except ImportError:
    _UML_PATCH_AVAILABLE = False
    _LOG.warning(
        "uml_generators_patch not importable. generate_timing_diagram and "
        "generate_uml_from_code rely on this patch module."
    )


def _resolve_output_dir(output_dir):
    """Resolve the diagram output directory per the UML lifecycle rule.

    Precedence is UML_OUTPUT_DIR, then the caller-supplied directory, then the
    mandated default of ``uml`` beneath the project root. An empty string means
    "not supplied", which is why the tool defaults are empty rather than a
    hard-coded path.

    Args:
        output_dir: Caller-supplied directory, or an empty string.

    Returns:
        The directory string to hand to the generator.
    """
    env_dir = os.environ.get("UML_OUTPUT_DIR", "").strip()
    return env_dir or (output_dir or "").strip() or "uml"


def _canonical_stem(name):
    """Map an engine diagram name onto its mandated output file stem.

    Args:
        name: Engine-supplied name such as ``class-diagram`` or a slug such as
            ``composite``.

    Returns:
        The canonical snake_case stem, e.g. ``composite_diagram``.
    """
    slug = str(name).strip().lower().replace("-", "_")
    if slug in _CANONICAL_STEMS:
        return _CANONICAL_STEMS[slug]
    if slug in _ENGINE_STEM_ALIASES:
        return _ENGINE_STEM_ALIASES[slug]
    if not slug.endswith("_diagram"):
        slug = "%s_diagram" % slug
    return _ENGINE_STEM_ALIASES.get(slug, slug)


def _get_generator(project_path, output_dir=""):
    """Lazy import and create UMLDiagramGenerator.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory for generated diagram files, or an empty
            string to resolve it from UML_OUTPUT_DIR / the mandated default.

    Returns:
        Initialized UMLDiagramGenerator instance.
    """
    _ensure_engine_path()

    from langgraph_engine.uml_generators import UMLDiagramGenerator
    return UMLDiagramGenerator(project_path, _resolve_output_dir(output_dir))


def _get_renderer():
    """Lazy import and create KrokiRenderer.

    Returns:
        Initialized KrokiRenderer instance.
    """
    _ensure_engine_path()

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

@_tool(
    annotations=_annotations("Generate class diagram", False, False, True, False),
    structured_output=False,
)
@mcp_tool_handler
def generate_class_diagram(
    project_path: ProjectPath,
    scope: Annotated[str, Field(description="\"all\" for the whole project, or a directory/file path relative to project_path to restrict the analysis.")] = "all",
    output_dir: OutputDir = "",
    model_data: _model_data_field("classes") = "",
) -> dict:
    """Generate a UML class diagram from Python AST analysis.

    Produces Mermaid classDiagram syntax showing classes, attributes,
    methods, and inheritance. Written to the resolved output directory as class_diagram.md (default: uml/ at the project root)..
    System prompt enriched with Domain 46 uml-class-diagram-core when
    GLOBAL_LIBRARY_PATH is configured.

    Args:
        project_path: Root path of the project to analyze.
        scope: "all" for full project, or a specific directory/file path.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"classes": [...]}) to render
            deterministically instead of scanning project_path.
    """
    _audit("generate_class_diagram", {
        "project_path": project_path, "scope": scope, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "class")
    if err:
        return {"diagram_type": "class", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("class", data)
        source = "model_data"
    else:
        syntax = gen.generate_class_diagram(scope=scope)
        source = "derived"
    path = gen.save_diagram(_canonical_stem("class"), syntax)
    return {
        "diagram_type": "class",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate package diagram", False, False, True, False),
    structured_output=False,
)
@mcp_tool_handler
def generate_package_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("packages") = "",
) -> dict:
    """Generate a UML package diagram from module import analysis.

    Produces Mermaid flowchart showing module dependencies.
    Written to the resolved output directory as package_diagram.md (default: uml/ at the project root)..

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"packages": [...], "imports":
            [...]}) to render deterministically instead of scanning
            project_path.
    """
    _audit("generate_package_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "package")
    if err:
        return {"diagram_type": "package", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("package", data)
        source = "model_data"
    else:
        syntax = gen.generate_package_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("package"), syntax)
    return {
        "diagram_type": "package",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate component diagram", False, False, True, False),
    structured_output=False,
)
@mcp_tool_handler
def generate_component_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("components") = "",
) -> dict:
    """Generate a UML component diagram from project structure.

    Produces Mermaid flowchart with subgraphs representing components.
    Written to the resolved output directory as component_diagram.md (default: uml/ at the project root)..

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"components": [{"name",
            "provides", "requires"}], "dependencies": [...]}) to render
            deterministically instead of scanning project_path.
    """
    _audit("generate_component_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "component")
    if err:
        return {"diagram_type": "component", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("component", data)
        source = "model_data"
    else:
        syntax = gen.generate_component_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("component"), syntax)
    return {
        "diagram_type": "component",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


# ==================================================================
# Tier 2: AST + LLM hybrid diagrams -- UNCHANGED
# ==================================================================

@_tool(
    annotations=_annotations("Generate sequence diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_sequence_diagram(
    project_path: ProjectPath,
    entry_function: Annotated[str, Field(description="Optional function name to trace the call chain from. Empty analyses the whole project.")] = "",
    output_dir: OutputDir = "",
    model_data: _model_data_field("call_chains") = "",
) -> dict:
    """Generate a UML sequence diagram from call chain analysis.

    Purely AST-derived: entry_function, when given, is a real filter
    (BFS on caller->callee reachability from that function), not an LLM
    enrichment hint - issue #312 found this parameter used to be passed
    into an `_llm_enrich()` call under the name `context`, silently
    replacing the deterministic rendering with LLM output whenever it
    was non-empty, despite this tool's Tier 2 AST labelling. Produces
    Mermaid sequenceDiagram syntax.

    Args:
        project_path: Root path of the project to analyze.
        entry_function: Optional entry function to trace from.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"participants": [...],
            "call_chains": [...]}) to render deterministically instead of
            scanning project_path. entry_function is ignored when supplied.
    """
    _audit("generate_sequence_diagram", {
        "project_path": project_path, "entry_function": entry_function,
        "output_dir": output_dir, "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "sequence")
    if err:
        return {"diagram_type": "sequence", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("sequence", data)
        source = "model_data"
    else:
        syntax = gen.generate_sequence_diagram(entry_function=entry_function)
        source = "derived"
    path = gen.save_diagram(_canonical_stem("sequence"), syntax)
    return {
        "diagram_type": "sequence",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate activity diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_activity_diagram(
    project_path: ProjectPath,
    function_path: Annotated[str, Field(description="Optional \"file:function\" selector, e.g. \"src/main.py:run\". The file part is resolved inside project_path and rejected if it escapes it. Empty analyses the whole project.")] = "",
    output_dir: OutputDir = "",
    model_data: _model_data_field("steps") = "",
) -> dict:
    """Generate a UML activity diagram from function logic.

    Uses LLM to analyze control flow and produce Mermaid flowchart TD.

    Args:
        project_path: Root path of the project to analyze.
        function_path: Optional file:function to analyze (e.g., "src/main.py:run").
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"steps": [{"name", "type"}]})
            to render deterministically instead of using the LLM.
    """
    _audit("generate_activity_diagram", {
        "project_path": project_path, "function_path": function_path,
        "output_dir": output_dir, "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "activity")
    if err:
        return {"diagram_type": "activity", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)

    if data is not None:
        syntax = gen.generate_from_data("activity", data)
        source = "model_data"
    else:
        func_code = ""
        if function_path and ":" in function_path:
            file_part, _func_name = function_path.rsplit(":", 1)
            resolved_file, _err = _resolve_project_file(project_path, file_part)
            if resolved_file:
                func_code = Path(resolved_file).read_text(encoding="utf-8", errors="replace")[:3000]
        syntax = gen.generate_activity_diagram(func_code)
        source = "derived"

    path = gen.save_diagram(_canonical_stem("activity"), syntax)
    return {
        "diagram_type": "activity",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate state diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_state_diagram(
    project_path: ProjectPath,
    context: Annotated[str, Field(description="Free-text hint about the states and transitions in the system, passed to the LLM prompt.")] = "",
    output_dir: OutputDir = "",
    model_data: _model_data_field("states") = "",
) -> dict:
    """Generate a UML state diagram from state pattern detection.

    Uses LLM to identify states and transitions, produces
    Mermaid stateDiagram-v2 syntax.

    Args:
        project_path: Root path of the project to analyze.
        context: Additional context about states in the system.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"states": [...],
            "transitions": [...]}) to render deterministically instead of
            using the LLM.
    """
    _audit("generate_state_diagram", {
        "project_path": project_path, "context": context, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "state")
    if err:
        return {"diagram_type": "state", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("state", data)
        source = "model_data"
    else:
        syntax = gen.generate_state_diagram(context=context)
        source = "derived"
    path = gen.save_diagram(_canonical_stem("state"), syntax)
    return {
        "diagram_type": "state",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


# ==================================================================
# Tier 3: LLM-powered diagrams -- UNCHANGED
# ==================================================================

@_tool(
    annotations=_annotations("Generate use case diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_usecase_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("use_cases") = "",
) -> dict:
    """Generate a UML use case diagram from requirements docs.

    Reads SRS.md and README.md, uses LLM to produce PlantUML
    use case diagram syntax.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"use_cases": [...], "actors":
            [...]}) to render deterministically instead of using the LLM.
    """
    _audit("generate_usecase_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "usecase")
    if err:
        return {"diagram_type": "usecase", "format": "plantuml", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("usecase", data)
        source = "model_data"
    else:
        syntax = gen.generate_usecase_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("usecase"), syntax)
    return {
        "diagram_type": "usecase",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate object diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_object_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("objects") = "",
) -> dict:
    """Generate a UML object diagram showing class instances.

    Uses AST class extraction + LLM to produce PlantUML object diagram
    with realistic field values.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"objects": [{"name", "class",
            "values"}]}) to render deterministically instead of using the LLM.
    """
    _audit("generate_object_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "object")
    if err:
        return {"diagram_type": "object", "format": "plantuml", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("object", data)
        source = "model_data"
    else:
        syntax = gen.generate_object_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("object"), syntax)
    return {
        "diagram_type": "object",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate deployment diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_deployment_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("nodes") = "",
) -> dict:
    """Generate a UML deployment diagram from infrastructure files.

    Reads Dockerfile, docker-compose, K8s manifests, and uses LLM
    to produce PlantUML deployment diagram.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"nodes": [{"name", "type",
            "artifacts"}]}) to render deterministically instead of using the LLM.
    """
    _audit("generate_deployment_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "deployment")
    if err:
        return {"diagram_type": "deployment", "format": "plantuml", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("deployment", data)
        source = "model_data"
    else:
        syntax = gen.generate_deployment_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("deployment"), syntax)
    return {
        "diagram_type": "deployment",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate communication diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_communication_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("participants") = "",
) -> dict:
    """Generate a UML communication diagram from module interactions.

    Uses dependency graph + LLM to show numbered message flows
    between modules in PlantUML.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"participants": [...],
            "messages": [...]}) to render deterministically instead of
            using the LLM.
    """
    _audit("generate_communication_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "communication")
    if err:
        return {"diagram_type": "communication", "format": "plantuml", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("communication", data)
        source = "model_data"
    else:
        syntax = gen.generate_communication_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("communication"), syntax)
    return {
        "diagram_type": "communication",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate composite structure diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_composite_structure_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("components") = "",
) -> dict:
    """Generate a UML composite structure diagram.

    Shows internal structure of classes (ports, parts, connectors)
    using PlantUML.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"components": [{"name",
            "parts", "ports"}]}) to render deterministically instead of
            using the LLM.
    """
    _audit("generate_composite_structure_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "composite")
    if err:
        return {"diagram_type": "composite_structure", "format": "plantuml", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("composite", data)
        source = "model_data"
    else:
        syntax = gen.generate_composite_structure_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("composite"), syntax)
    return {
        "diagram_type": "composite_structure",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate interaction overview diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_interaction_overview_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("steps") = "",
) -> dict:
    """Generate a UML interaction overview diagram.

    Combines activity and sequence diagram elements showing
    combined interaction flows in PlantUML.

    Args:
        project_path: Root path of the project to analyze.
        output_dir: Output directory relative to project root.
        model_data: Optional UDM JSON payload ({"steps": [{"type", "name"}]})
            to render deterministically instead of using the LLM.
    """
    _audit("generate_interaction_overview_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "interaction")
    if err:
        return {"diagram_type": "interaction_overview", "format": "plantuml", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("interaction", data)
        source = "model_data"
    else:
        syntax = gen.generate_interaction_overview()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("interaction"), syntax)
    return {
        "diagram_type": "interaction_overview",
        "format": "plantuml",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


@_tool(
    annotations=_annotations("Generate call graph diagram", False, False, True, False),
    structured_output=False,
)
@mcp_tool_handler
def generate_call_graph_diagram(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: _model_data_field("methods") = "",
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
        model_data: Optional UDM JSON payload ({"methods": [{"fqn", "name",
            "params", "cyclomatic", "parent_class"}], "edges": [...]}) to
            render deterministically instead of scanning project_path.
    """
    _audit("generate_call_graph_diagram", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })
    data, err = _parse_model_data(model_data, "call_graph")
    if err:
        return {"diagram_type": "call_graph", "format": "mermaid", "output_file": "",
                "error": err, "lines": 0, "source": "error"}

    gen = _get_generator(project_path, output_dir)
    if data is not None:
        syntax = gen.generate_from_data("call_graph", data)
        source = "model_data"
    else:
        syntax = gen.generate_call_graph_diagram()
        source = "derived"
    path = gen.save_diagram(_canonical_stem("call_graph"), syntax)
    return {
        "diagram_type": "call_graph",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "source": source,
    }


# ==================================================================
# Utility tools -- UNCHANGED
# ==================================================================

@_tool(
    annotations=_annotations("Generate all diagrams", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_all_diagrams(
    project_path: ProjectPath,
    output_dir: OutputDir = "",
    model_data: Annotated[str, Field(
        description=(
            "Optional JSON object mapping diagram type slugs to UDM payloads, "
            "e.g. {\"class\": {\"classes\": [...]}, \"deployment\": {\"nodes\": "
            "[...]}}. Slugs present in the map are rendered deterministically "
            "from their payload -- no AST scan or LLM call for that type; "
            "slugs absent fall back to the existing derived/LLM path, "
            "unchanged. A payload missing its type's primary key is rejected "
            "for that type only (reported in model_data_errors) -- other "
            "types in the map are unaffected. Valid slugs: class, package, "
            "component, sequence, state, activity, deployment, usecase, "
            "object, communication, composite, interaction, call_graph. "
            "Max 512 KB total."
        )
    )] = "",
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
        model_data: Optional per-type UDM payload map. See parameter
            description for shape.
    """
    _audit("generate_all_diagrams", {
        "project_path": project_path, "output_dir": output_dir,
        "model_data_bytes": len(model_data or ""),
    })

    model_data_map = {}
    model_data_errors = {}
    if model_data and model_data.strip():
        if len(model_data.encode("utf-8")) > _MAX_MODEL_DATA_BYTES:
            return {
                "diagrams_generated": 0, "diagrams": [], "diagrams_failed": 0,
                "failed": [], "model_data_errors": {},
                "error": "model_data exceeds %d KB limit; set UML_MAX_MODEL_DATA_KB to raise it" % (_MAX_MODEL_DATA_BYTES // 1024),
            }
        try:
            parsed = _json.loads(model_data)
        except _json.JSONDecodeError as exc:
            return {
                "diagrams_generated": 0, "diagrams": [], "diagrams_failed": 0,
                "failed": [], "model_data_errors": {},
                "error": "model_data is not valid JSON: %s at line %d column %d" % (exc.msg, exc.lineno, exc.colno),
            }
        if not isinstance(parsed, dict):
            return {
                "diagrams_generated": 0, "diagrams": [], "diagrams_failed": 0,
                "failed": [], "model_data_errors": {},
                "error": "model_data must be a JSON object mapping diagram type slugs to payloads",
            }

        try:
            from langgraph_engine.uml_generators import UDM_PRIMARY_KEY
        except ImportError:
            UDM_PRIMARY_KEY = {}

        for slug, payload in parsed.items():
            if slug not in UDM_PRIMARY_KEY:
                model_data_errors[slug] = "unknown diagram type slug: %s" % slug
                continue
            if not isinstance(payload, dict):
                model_data_errors[slug] = "payload must be a JSON object, got %s" % type(payload).__name__
                continue
            primary_key = UDM_PRIMARY_KEY[slug]
            if not payload.get(primary_key):
                model_data_errors[slug] = "must contain a non-empty '%s' list" % primary_key
                continue
            model_data_map[slug] = payload

    gen = _get_generator(project_path, output_dir)
    gen.output_dir.mkdir(parents=True, exist_ok=True)

    results = gen.generate_all()
    sources = {name: "derived" for name in results}

    for slug, payload in model_data_map.items():
        engine_key = _SLUG_TO_ENGINE_KEY.get(slug)
        if not engine_key:
            continue
        try:
            results[engine_key] = gen.generate_from_data(slug, payload)
            sources[engine_key] = "model_data"
        except Exception as exc:
            model_data_errors[slug] = str(exc)

    try:
        results["timing-diagram"] = gen.generate_timing_diagram()
        sources["timing-diagram"] = "derived"
    except Exception as exc:
        _LOG.warning("timing-diagram generation failed: %s: %s", type(exc).__name__, exc)

    saved = []
    failed = []
    for name, syntax in results.items():
        stem = _canonical_stem(name)
        try:
            path = gen.save_diagram(stem, syntax)
        except Exception as exc:
            _LOG.error(
                "saving %s failed: %s: %s", stem, type(exc).__name__, exc
            )
            failed.append(
                {"name": stem, "error": str(exc), "error_type": type(exc).__name__}
            )
            continue
        saved.append({"name": stem, "file": path, "source": sources.get(name, "derived")})

    return {
        "diagrams_generated": len(saved),
        "diagrams": saved,
        "diagrams_failed": len(failed),
        "failed": failed,
        "model_data_errors": model_data_errors,
        "output_dir": str(gen.output_dir),
    }


@_tool(
    annotations=_annotations("Render diagram via Kroki", True, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def render_diagram(
    diagram_text: Annotated[str, Field(description="PlantUML or Mermaid source text to render.")],
    diagram_type: Annotated[str, Field(description="Source dialect passed to Kroki: \"plantuml\", \"mermaid\", \"graphviz\", and other Kroki-supported names.")] = "plantuml",
    output_format: Annotated[str, Field(description="Rendered image format: \"svg\" or \"png\".")] = "svg",
    output_path: Annotated[str, Field(description="File path to write the rendered image to. Empty returns only the byte size without writing.")] = "",
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
    _audit("render_diagram", {"diagram_type": diagram_type, "output_format": output_format, "output_path": output_path})
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

@_tool(
    annotations=_annotations("Generate UML from one source file", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_uml_from_code(
    project_path: ProjectPath,
    source_file: Annotated[str, Field(description="Path to the source file, relative to project_path, e.g. \"src/models.py\". Validated to stay inside project_path.")],
    language: Annotated[str, Field(description="Source language: \"python\" (stdlib AST), or \"java\", \"typescript\", \"kotlin\" (regex/LLM).")] = "python",
    output_dir: OutputDir = "",
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
        dict with diagram_type, format, output_file, language, lines.
    """
    _audit("generate_uml_from_code", {"project_path": project_path, "source_file": source_file, "language": language, "output_dir": output_dir})
    resolved, err = _resolve_project_file(project_path, source_file)
    if err:
        return {
            "diagram_type": "class",
            "format": "mermaid",
            "output_file": "",
            "error": err,
            "language": language,
            "lines": 0,
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
            "lines": 0,
        }

    lines_parsed = len(source_code.splitlines())
    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_uml_from_code(source_code, language)
    path = gen.save_diagram(_canonical_stem("uml_from_code"), syntax)

    return {
        "diagram_type": "class",
        "format": "mermaid",
        "output_file": path,
        "language": language,
        "lines": lines_parsed,
    }


# ==================================================================
# NEW Tool #16: select_optimal_diagram_type
# ==================================================================

@_tool(
    annotations=_annotations("Select optimal diagram type", True, False, True, False),
    structured_output=False,
)
@mcp_tool_handler
def select_optimal_diagram_type(
    project_description: Annotated[str, Field(description="Natural language description of what needs to be modelled. Truncated at 2000 characters; null bytes are rejected.")],
    constraint: Annotated[str, Field(description="Optional diagram family hint: \"structural\", \"behavioral\", \"interaction\", or \"tooling\". Truncated at 200 characters.")] = "",
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
    _audit("select_optimal_diagram_type", {"project_description": project_description[:100], "constraint": constraint})
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

@_tool(
    annotations=_annotations("Generate timing diagram", False, False, True, True),
    structured_output=False,
)
@mcp_tool_handler
def generate_timing_diagram(
    project_path: ProjectPath,
    process_name: Annotated[str, Field(description="Process name used as the gantt title. Truncated at 200 characters; empty renders as \"Process\".")] = "",
    output_dir: OutputDir = "",
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
                      "Process").
        output_dir: Output directory relative to project root.
                    Defaults to "docs/uml".

    Returns:
        dict with diagram_type, format, output_file, lines, process_name.
    """
    _audit("generate_timing_diagram", {"project_path": project_path, "process_name": process_name, "output_dir": output_dir})
    if len(process_name) > 200:
        process_name = process_name[:200]

    gen = _get_generator(project_path, output_dir)
    syntax = gen.generate_timing_diagram(process_name)
    path = gen.save_diagram(_canonical_stem("timing"), syntax)

    return {
        "diagram_type": "timing",
        "format": "mermaid",
        "output_file": path,
        "lines": len(syntax.split("\n")),
        "process_name": process_name if process_name else "Process",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
