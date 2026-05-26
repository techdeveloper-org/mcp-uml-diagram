# kg_router.py
# Keyword-based UML diagram type selector for Domain 46 MCP integration.
# Compatible: Python 3.8+, stdlib only, ASCII encoding safe (cp1252 compliant).
# Place at: mcp-uml-diagram/kg_router.py  OR  mcp-drawio-diagram/kg_router.py
import os
from typing import Dict, List, Tuple

DIAGRAM_TYPE_KEYWORDS = {
    "class": [
        "class", "interface", "inheritance", "attribute", "method",
        "object-oriented", "oop", "type", "hierarchy", "entity",
        "struct", "uml class", "domain model",
    ],
    "sequence": [
        "sequence", "request", "response", "message", "call", "api",
        "flow", "protocol", "interaction", "handshake", "async",
        "lifeline", "api call",
    ],
    "activity": [
        "activity", "process", "workflow", "business logic", "decision",
        "steps", "procedure", "flowchart", "swimlane", "parallel",
        "fork", "join", "control flow",
    ],
    "state": [
        "state", "transition", "event", "machine", "status", "lifecycle",
        "phase", "mode", "statechart", "hsm", "guard", "action",
        "protocol",
    ],
    "component": [
        "component", "module", "service", "microservice", "boundary",
        "layer", "subsystem", "port", "connector", "provided", "required",
    ],
    "package": [
        "package", "dependency", "import", "module structure", "namespace",
        "coupling", "library", "architecture",
    ],
    "deployment": [
        "deployment", "infrastructure", "server", "container", "cloud",
        "docker", "kubernetes", "node", "artifact", "host",
    ],
    "usecase": [
        "use case", "actor", "user story", "feature", "requirement",
        "stakeholder", "scenario", "user goal", "system boundary",
        "functional",
    ],
    "object": [
        "object", "instance", "runtime", "concrete", "values", "snapshot",
        "example", "link", "slot", "prototype",
    ],
    "communication": [
        "communication", "collaboration", "message flow", "event", "bus",
        "interaction pattern", "numbered message", "topology",
        "object network", "routing",
    ],
    "composite": [
        "composite", "internal structure", "port", "connector", "part",
        "encapsulation", "collaboration use", "ports",
    ],
    "interaction": [
        "interaction overview", "combined", "overview", "multiple scenarios",
        "high level", "alt", "opt", "loop", "fragment overview",
        "scenario flow",
    ],
    "timing": [
        "timing", "time", "duration", "deadline", "schedule", "gantt",
        "timeline", "when", "real-time", "wcet", "tctl", "embedded timing",
        "period",
    ],
    "call_graph": [
        "call graph", "function calls", "method calls", "call chain",
        "callee", "caller", "cyclomatic", "call stack", "entry point",
        "control flow graph",
    ],
}  # type: Dict[str, List[str]]


def select_diagram_type(user_description):
    # type: (str) -> Tuple[str, str]
    """Select the best UML diagram type from a natural language description.

    Lowercases user_description, then counts substring keyword matches for each
    of the 14 diagram types defined in DIAGRAM_TYPE_KEYWORDS. The type with
    the highest score is returned. On a tie, the type that appears earlier in
    DIAGRAM_TYPE_KEYWORDS dictionary order wins (class wins all ties because
    it is the first key). On zero matches, returns ("class", "No keywords
    matched -- defaulting to class diagram").

    The caller is responsible for sanitizing user_description via
    input validation BEFORE calling this function. This function performs
    no additional input sanitization.

    Args:
        user_description: Free-text description of what the user wants to model.
                          Expected to be pre-sanitized by the caller. Passed
                          as-is after lowercasing; no truncation applied here.

    Returns:
        Tuple of (diagram_type_slug, reason) where diagram_type_slug is one of
        the 14 keys in DIAGRAM_TYPE_KEYWORDS and reason is a human-readable
        explanation of the selection decision, including matched keywords.
    """
    desc = user_description.lower()

    best_type = "class"
    best_score = 0
    best_matched = []  # type: List[str]

    for diagram_type, keywords in DIAGRAM_TYPE_KEYWORDS.items():
        matched = []  # type: List[str]
        for kw in keywords:
            if kw in desc:
                matched.append(kw)
        score = len(matched)
        if score > best_score:
            best_score = score
            best_type = diagram_type
            best_matched = matched

    if best_score == 0:
        return ("class", "No keywords matched -- defaulting to class diagram")

    reason = "Matched " + str(best_score) + " keyword"
    if best_score != 1:
        reason += "s"
    reason += ": " + ", ".join(best_matched)
    return (best_type, reason)
