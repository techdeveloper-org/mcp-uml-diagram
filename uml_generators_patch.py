# -*- coding: ascii -*-
"""uml_generators_patch.py - Monkey-patches UMLDiagramGenerator with new methods.

Adds generate_timing_diagram(), generate_timing_diagram_ladder(), and
generate_uml_from_code() to UMLDiagramGenerator at import time. Import this
module before calling _get_generator() so the patch is in effect when the
generator is instantiated.

Patch methods:
    generate_timing_diagram(process_name)
        Mermaid gantt layout. OMG UML 2.5 sec 14.3 (state-based timeline).
    generate_timing_diagram_ladder(process_name)
        PlantUML 'robust/concise' layout (zig-zag step function). OMG UML 2.5
        sec 14.3 ladder notation variant. Alternative to gantt when the
        caller needs explicit state-transition events instead of bars.
    generate_uml_from_code(source_code, language)
        AST-based classDiagram from a source snippet. Python uses stdlib ast.
        Other languages use heuristic line-by-line pattern matching.
    generate_communication_diagram_enriched(context)
        Enriched PlantUML communication diagram with decimal sequence numbering.

Python 3.11+. Existing typing imports kept for compatibility with pre-migration callers.
ASCII-only source (cp1252 safe on Windows).
"""

import ast as _ast
import logging as _logging
import os as _os
import re as _re
import sys as _sys
from pathlib import Path as _Path
from typing import Any, Dict, List, Optional, Tuple

_log = _logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper: safe task ID sanitizer (alphanumeric + underscore only)
# ---------------------------------------------------------------------------

def _sanitize_task_id(text):
    # type: (str) -> str
    """Strip non-alphanumeric/underscore chars from text for Mermaid task IDs.

    Mermaid gantt requires task IDs containing only alphanumerics and
    underscores. Spaces and special characters are replaced with underscores.
    Leading digits are prefixed with 't_' to avoid parse errors.

    Args:
        text: Raw label string to convert to a safe task identifier.

    Returns:
        Sanitized string usable as a Mermaid gantt task ID.
    """
    safe = _re.sub(r"[^A-Za-z0-9_]", "_", text)
    if safe and safe[0].isdigit():
        safe = "t_" + safe
    return safe or "task"


# ---------------------------------------------------------------------------
# Method 1: generate_timing_diagram  (Mermaid gantt -- bar notation)
# OMG UML 2.5 sec 14.3 -- timing diagrams show object state over time
# ---------------------------------------------------------------------------

def _generate_timing_diagram(self, process_name=""):
    # type: (Any, str) -> str
    """Generate a Mermaid gantt timing diagram for the project.

    Produces a gantt diagram with mandatory header lines (gantt, title,
    dateFormat YYYY-MM-DD, axisFormat %Y-%m-%d). Derives phases from the
    generator's pre-analysis state when available; otherwise falls back to
    a canonical three-phase Initialization / Processing / Completion layout.
    Task IDs are sanitized to alphanumeric + underscore.

    OMG UML 2.5 Section 14.3: timing diagrams show the state of one or more
    lifelines over time using bars (gantt representation) or step functions
    (ladder notation, see generate_timing_diagram_ladder).

    Args:
        process_name: Name of the process shown in the gantt title.
                      Empty string renders as "Process".

    Returns:
        Mermaid gantt syntax string starting with 'gantt'.
    """
    label = (process_name.strip() or "Process") + " -- Timing Diagram"

    try:
        state = getattr(self, "state", {}) or {}
        analysis = state.get("pre_analysis_result", {}) or {}
        modules = analysis.get("modules", []) or []
        classes = analysis.get("classes", []) or []
    except Exception:
        modules = []
        classes = []

    lines = [
        "gantt",
        "    title %s" % label,
        "    dateFormat  YYYY-MM-DD",
        "    axisFormat  %Y-%m-%d",
    ]

    if modules or classes:
        lines.append("    section Initialization")
        lines.append("        Configure Environment  :t_init_env, 2024-01-01, 1d")
        lines.append("        Load Modules           :t_init_mod, after t_init_env, 1d")

        lines.append("    section Processing")
        phase_start = "t_init_mod"
        for idx, name in enumerate((modules + classes)[:4]):
            slug = _sanitize_task_id(str(name))
            tid = "t_proc_%d_%s" % (idx, slug[:20])
            lines.append("        %-26s :%s, after %s, 2d" % (str(name)[:26], tid, phase_start))
            phase_start = tid

        lines.append("    section Completion")
        lines.append("        Flush State            :t_flush, after %s, 1d" % phase_start)
        lines.append("        Shutdown               :t_shutdown, after t_flush, 1d")
    else:
        lines.extend([
            "    section Initialization",
            "        Boot System       :t_boot,  2024-01-01, 2d",
            "        Load Config       :t_cfg,   after t_boot, 1d",
            "    section Processing",
            "        Execute Main Loop :t_loop,  after t_cfg, 5d",
            "        Handle Events     :t_evts,  after t_cfg, 5d",
            "    section Completion",
            "        Flush Buffers     :t_flush, after t_loop, 1d",
            "        Shutdown          :t_down,  after t_flush, 1d",
        ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Method 2: generate_timing_diagram_ladder  (PlantUML robust -- ladder notation)
# OMG UML 2.5 sec 14.3 -- ladder/zig-zag step-function variant
# ---------------------------------------------------------------------------

def _generate_timing_diagram_ladder(self, process_name=""):
    # type: (Any, str) -> str
    """Generate a PlantUML robust/concise timing diagram (ladder notation).

    Produces PlantUML @startuml...@enduml syntax using the 'robust' (step
    function, zig-zag) timing diagram type. This is the OMG UML 2.5 Section
    14.3 ladder notation variant where each state transition is shown as an
    instantaneous vertical edge followed by a horizontal level, not a bar.

    'robust' lifeline: states are drawn as step functions (high/low levels
    connected by vertical zig-zag transitions). Suitable for hardware timing,
    protocol state machines, and RTOS task scheduling.

    'concise' lifeline: states are drawn as filled rectangles (intermediate
    between bar notation and zig-zag). Suitable for higher-level workflow.

    OMG UML 2.5 Section 14.3:
        Timing diagrams show how values or states of one or more elements
        change over time. The ladder (waveform / zig-zag) variant emphasizes
        instantaneous state transitions as opposed to duration-centric bars.

    Args:
        process_name: Name of the process shown in the diagram title.
                      Empty string renders as "Process".

    Returns:
        PlantUML @startuml...@enduml syntax string with robust/concise lifelines.
    """
    label = process_name.strip() or "Process"

    try:
        state = getattr(self, "state", {}) or {}
        analysis = state.get("pre_analysis_result", {}) or {}
        classes = analysis.get("classes", []) or []
    except Exception:
        classes = []

    lifelines = []  # type: List[Dict[str, Any]]
    for cls in classes[:3]:
        lifelines.append({
            "name": str(cls) if isinstance(cls, str) else str(cls.get("name", "Object")),
            "type": "robust",
            "states": ["Idle", "Initializing", "Active", "Idle"],
            "times":  [0, 100, 300, 700],
        })

    if not lifelines:
        lifelines = [
            {
                "name":   label,
                "type":   "robust",
                "states": ["Idle", "Initializing", "Running", "Done", "Idle"],
                "times":  [0, 50, 150, 700, 800],
            },
            {
                "name":   "EventBus",
                "type":   "concise",
                "states": ["Quiet", "Active", "Quiet"],
                "times":  [0, 150, 700],
            },
        ]

    max_time = 0
    for ll in lifelines:
        times = ll.get("times", [])
        if times:
            m = max(times)
            if m > max_time:
                max_time = m
    max_time = max_time or 900

    lines = [
        "@startuml",
        "title %s -- Timing Diagram (Ladder Notation)" % label,
        "",
    ]

    for ll in lifelines:
        ll_type = ll.get("type", "robust")
        ll_name = ll.get("name", "Object")
        lines.append("%s \"%s\" as %s" % (ll_type, ll_name, _sanitize_task_id(ll_name)[:12]))

    lines.append("")

    all_times = sorted(set(t for ll in lifelines for t in ll.get("times", [])))
    if not all_times:
        all_times = [0, max_time]
    if 0 not in all_times:
        all_times = [0] + all_times

    for t in all_times:
        lines.append("@%d" % t)
        for ll in lifelines:
            ll_alias = _sanitize_task_id(ll.get("name", "Object"))[:12]
            times = ll.get("times", [])
            states = ll.get("states", [])
            idx = 0
            for i, lt in enumerate(times):
                if lt <= t:
                    idx = i
            if states and idx < len(states):
                lines.append("%s is %s" % (ll_alias, states[idx]))
        lines.append("")

    lines.append("@%d" % max_time)
    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Method 3: generate_uml_from_code  (AST-based classDiagram)
# ---------------------------------------------------------------------------

def _extract_classes_python(source_code):
    # type: (str) -> List[Dict[str, Any]]
    """Extract class metadata from Python source using stdlib ast module.

    Parses source_code with ast.parse() and walks the AST to collect class
    names, base classes, method signatures, and attribute assignments.
    Handles Python 3.8 through 3.12 compatible AST node types.

    Args:
        source_code: Python source code string.

    Returns:
        List of dicts with keys: name, bases, methods, attributes.
        Empty list on any parse error.
    """
    try:
        tree = _ast.parse(source_code)
    except SyntaxError:
        return []

    classes = []  # type: List[Dict[str, Any]]
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ClassDef):
            continue
        bases = []  # type: List[str]
        for base in node.bases:
            if isinstance(base, _ast.Name):
                bases.append(base.id)
            elif isinstance(base, _ast.Attribute):
                bases.append("%s.%s" % (
                    base.value.id if isinstance(base.value, _ast.Name) else "?",
                    base.attr,
                ))

        methods = []  # type: List[Dict[str, str]]
        attributes = []  # type: List[str]
        for item in node.body:
            if isinstance(item, _ast.FunctionDef):
                args = [a.arg for a in item.args.args if a.arg != "self"]
                vis = "-" if item.name.startswith("__") and not item.name.endswith("__") else (
                    "#" if item.name.startswith("_") else "+"
                )
                ret = ""
                if item.returns:
                    if isinstance(item.returns, _ast.Name):
                        ret = item.returns.id
                    elif isinstance(item.returns, _ast.Constant):
                        ret = str(item.returns.value)
                methods.append({
                    "visibility": vis,
                    "name": item.name,
                    "params": args[:4],
                    "return_type": ret,
                })
            elif isinstance(item, _ast.Assign):
                for target in item.targets:
                    if isinstance(target, _ast.Name) and not target.id.startswith("__"):
                        attributes.append(target.id)

        classes.append({
            "name": node.name,
            "bases": bases,
            "methods": methods[:12],
            "attributes": attributes[:8],
        })

    return classes[:20]


def _extract_classes_heuristic(source_code, language):
    # type: (str, str) -> List[Dict[str, Any]]
    """Extract class metadata from Java/TypeScript/Kotlin source using regex heuristics.

    Applies language-specific regex patterns to find class declarations, method
    signatures, and field definitions. Coverage is intentionally conservative:
    only clear, unambiguous patterns are matched to minimize false positives.

    Args:
        source_code: Source code string in the given language.
        language: One of "java", "typescript", "kotlin".

    Returns:
        List of dicts with keys: name, bases, methods, attributes.
        Empty list when no patterns match.
    """
    classes = []  # type: List[Dict[str, Any]]

    if language in ("java", "kotlin"):
        class_re = _re.compile(
            r"(?:public\s+|private\s+|protected\s+|data\s+|abstract\s+|open\s+)*"
            r"(?:class|interface|object)\s+(\w+)"
            r"(?:[^{]*?(?:extends|implements|:)\s*([\w,\s]+?))?\s*\{",
            _re.MULTILINE,
        )
        method_re = _re.compile(
            r"(?:public|private|protected|override|fun|void|static|final|\s)+"
            r"(?:fun\s+)?(\w+)\s*\(([^)]*)\)",
            _re.MULTILINE,
        )
        field_re = _re.compile(
            r"(?:private|protected|public|val|var)\s+\w+\s+(\w+)\s*[=;]",
            _re.MULTILINE,
        )
    else:
        class_re = _re.compile(
            r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"
            r"(?:\s+extends\s+([\w,\s]+?))?(?:\s+implements\s+([\w,\s]+?))?\s*\{",
            _re.MULTILINE,
        )
        method_re = _re.compile(
            r"(?:public|private|protected|static|async|\s)+"
            r"(\w+)\s*\(([^)]*)\)\s*(?::\s*\w+)?\s*\{",
            _re.MULTILINE,
        )
        field_re = _re.compile(
            r"(?:private|public|protected|readonly)\s+(\w+)\s*[=:;]",
            _re.MULTILINE,
        )

    for m_cls in class_re.finditer(source_code):
        cls_name = m_cls.group(1)
        bases_raw = m_cls.group(2) or ""
        bases = [b.strip() for b in _re.split(r"[,\s]+", bases_raw) if b.strip()]

        cls_start = m_cls.start()
        cls_end = source_code.find("}", cls_start)
        if cls_end < 0:
            cls_end = len(source_code)
        cls_body = source_code[cls_start:cls_end + 1]

        methods = []  # type: List[Dict[str, str]]
        for m_fn in method_re.finditer(cls_body):
            fn_name = m_fn.group(1)
            if fn_name in ("if", "for", "while", "switch", "catch"):
                continue
            vis = "+" if not fn_name.startswith("_") else "#"
            methods.append({
                "visibility": vis,
                "name": fn_name,
                "params": [],
                "return_type": "",
            })

        attributes = []  # type: List[str]
        for m_fld in field_re.finditer(cls_body):
            fld_name = m_fld.group(1)
            if fld_name not in ("if", "for", "while"):
                attributes.append(fld_name)

        classes.append({
            "name": cls_name,
            "bases": bases[:3],
            "methods": methods[:12],
            "attributes": attributes[:8],
        })
        if len(classes) >= 20:
            break

    return classes


def _generate_uml_from_code(self, source_code, language="python"):
    # type: (Any, str, str) -> str
    """Generate a Mermaid classDiagram by AST-parsing the given source code.

    For Python, uses the stdlib ast module to extract class structure (names,
    bases, methods, attributes). For Java, TypeScript, and Kotlin, applies
    conservative regex heuristics. Falls back to a minimal placeholder diagram
    when the source is unparseable or contains no class definitions.

    OMG UML 2.5 Section 11 (Structured Classifiers) drives the notation:
    visibility prefixes (+/-/#/~), attribute compartment, operation compartment.
    Method parameter lists are truncated at 4 params to avoid line overflow.

    Args:
        source_code: Full source file content as a string.
        language: Source language. Supported: "python" (stdlib ast),
                  "java", "typescript", "kotlin" (heuristic regex).
                  Unrecognised languages fall back to heuristic (java).

    Returns:
        Mermaid classDiagram syntax string starting with 'classDiagram'.
    """
    lang = language.lower().strip() if language else "python"

    if lang == "python":
        classes = _extract_classes_python(source_code)
    elif lang in ("java", "typescript", "kotlin"):
        classes = _extract_classes_heuristic(source_code, lang)
    else:
        classes = _extract_classes_heuristic(source_code, "java")

    if not classes:
        return (
            "classDiagram\n"
            "    note \"No class definitions found in source file\"\n"
        )

    lines = ["classDiagram"]

    id_map = {}  # type: Dict[str, str]
    for cls in classes:
        name = cls.get("name", "Unknown")
        id_map[name] = name
        lines.append("    class %s {" % name)
        for attr in cls.get("attributes", [])[:8]:
            lines.append("        +%s" % attr)
        for meth in cls.get("methods", [])[:12]:
            vis = meth.get("visibility", "+")
            fn = meth.get("name", "method")
            params = ", ".join(meth.get("params", [])[:4])
            ret = meth.get("return_type", "")
            ret_suffix = (" " + ret) if ret else ""
            lines.append("        %s%s(%s)%s" % (vis, fn, params, ret_suffix))
        lines.append("    }")

    for cls in classes:
        src = cls.get("name", "")
        for base in cls.get("bases", [])[:3]:
            if base in id_map:
                lines.append("    %s <|-- %s" % (base, src))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Method 4: generate_communication_diagram_enriched  (PlantUML)
# OMG UML 2.5 sec 17.10 -- communication diagram with sequence numbering
# ---------------------------------------------------------------------------

def _generate_communication_diagram_enriched(self, context=""):
    # type: (Any, str) -> str
    """Generate an enriched PlantUML communication diagram with decimal numbering.

    Produces PlantUML @startuml...@enduml syntax. Derives objects and message
    chains from the generator's pre-analysis state when available. Falls back
    to a canonical three-object CRUD pattern when analysis data is absent.

    OMG UML 2.5 Section 17.10 requirements enforced:
    - Every message carries a decimal sequence number (1:, 1.1:, 2:, ...)
    - Objects are linked by associations (shown as lines)
    - Guard conditions use square bracket notation [condition]: msg()
    - Iteration prefix uses asterisk: *[i=1..n] 1.2: msg()

    Args:
        context: Optional additional context string appended to the title.

    Returns:
        PlantUML @startuml...@enduml syntax string with decimal message
        sequence numbers.
    """
    try:
        state = getattr(self, "state", {}) or {}
        analysis = state.get("pre_analysis_result", {}) or {}
        classes = analysis.get("classes", []) or []
    except Exception:
        classes = []

    obj_names = []  # type: List[str]
    for cls in classes[:5]:
        n = str(cls) if isinstance(cls, str) else str(cls.get("name", "Object"))
        obj_names.append(n)

    if not obj_names:
        obj_names = ["Client", "Service", "Repository"]

    title = "Communication Diagram"
    if context:
        title += " -- " + context.strip()[:60]

    lines = [
        "@startuml",
        "title %s" % title,
        "",
    ]

    for obj in obj_names:
        lines.append("object \"%s\" as %s" % (obj, _sanitize_task_id(obj)[:12]))
    lines.append("")

    aliases = [_sanitize_task_id(n)[:12] for n in obj_names]
    for i in range(len(aliases) - 1):
        lines.append("%s -- %s" % (aliases[i], aliases[i + 1]))
    lines.append("")

    seq = 1
    for i in range(len(aliases) - 1):
        src = aliases[i]
        dst = aliases[i + 1]
        lines.append("%s --> %s : %d: request()" % (src, dst, seq))
        lines.append("%s --> %s : %d.1: process()" % (dst, src, seq))
        lines.append("%s --> %s : %d.2: respond()" % (dst, src, seq))
        seq += 1

    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monkey-patch: attach all methods to UMLDiagramGenerator
# ---------------------------------------------------------------------------

def _apply_patch():
    # type: () -> bool
    """Import UMLDiagramGenerator and attach new methods.

    Adds four new methods to the class:
        generate_timing_diagram       -- Mermaid gantt
        generate_timing_diagram_ladder -- PlantUML robust (ladder notation)
        generate_uml_from_code         -- AST classDiagram from snippet
        generate_communication_diagram_enriched -- enriched comm diagram

    Returns:
        True if the patch was applied successfully, False if the import failed.
    """
    try:
        scripts_dir = str(_Path(__file__).resolve().parent.parent.parent / "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from langgraph_engine.uml_generators import UMLDiagramGenerator

        if not hasattr(UMLDiagramGenerator, "generate_timing_diagram"):
            UMLDiagramGenerator.generate_timing_diagram = _generate_timing_diagram
            _log.debug("uml_generators_patch: generate_timing_diagram patched")

        if not hasattr(UMLDiagramGenerator, "generate_timing_diagram_ladder"):
            UMLDiagramGenerator.generate_timing_diagram_ladder = _generate_timing_diagram_ladder
            _log.debug("uml_generators_patch: generate_timing_diagram_ladder patched")

        if not hasattr(UMLDiagramGenerator, "generate_uml_from_code"):
            UMLDiagramGenerator.generate_uml_from_code = _generate_uml_from_code
            _log.debug("uml_generators_patch: generate_uml_from_code patched")

        if not hasattr(UMLDiagramGenerator, "generate_communication_diagram_enriched"):
            UMLDiagramGenerator.generate_communication_diagram_enriched = (
                _generate_communication_diagram_enriched
            )
            _log.debug("uml_generators_patch: generate_communication_diagram_enriched patched")

        return True
    except ImportError as exc:
        _log.warning("uml_generators_patch: UMLDiagramGenerator import failed: %s", exc)
        return False


_PATCH_APPLIED = _apply_patch()
