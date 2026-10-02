"""The small slice of JSON Schema a connector tool may use, checked in plain code.

Nemotron proposes a tool's arguments; nothing it proposes leaves the Mac until
this module agrees. A tool whose schema needs more than this slice is refused at
approval time rather than half-checked at call time. Errors name the field and
the rule, never the value: a value can be a secret the model copied from a note.
"""

from __future__ import annotations

TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
         "array": list, "object": dict}
KEYWORDS = {"type", "description", "title", "properties", "required", "additionalProperties",
            "enum", "items", "minimum", "maximum", "minLength", "maxLength", "maxItems",
            "default", "examples"}
# Caps used when the server's schema sets none: an uncapped string or list is
# where a model smuggles a whole note out.
MAX_STRING = 2000
MAX_ITEMS = 50


BOUNDS = ("minimum", "maximum")
LENGTHS = ("minLength", "maxLength", "maxItems")  # counts: whole and not negative


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _well_formed(s: dict) -> bool:
    """Keyword values of the shape `validate` relies on.

    Checking names alone let `{"minimum": "1"}` or `{"required": "ab"}` through
    approval and then crash, or worse quietly mis-check, at call time.
    """
    if not isinstance(s.get("type"), str):
        return False
    if any(k in s and not _number(s[k]) for k in BOUNDS):
        return False
    if any(k in s and not (_number(s[k]) and isinstance(s[k], int) and s[k] >= 0)
           for k in LENGTHS):
        return False
    if "required" in s and not (isinstance(s["required"], list)
                                and all(isinstance(n, str) for n in s["required"])):
        return False
    if "enum" in s and not isinstance(s["enum"], list):
        return False
    if "properties" in s and not isinstance(s["properties"], dict):
        return False
    return True


def supported(s: dict, *, top: bool = True) -> bool:
    """True when every keyword in `s` is one this module actually enforces."""
    if not isinstance(s, dict) or set(s) - KEYWORDS or not _well_formed(s):
        return False
    if top and s.get("type") != "object":
        return False
    t = s.get("type")
    if t not in TYPES:
        return False
    if t == "object":
        return all(supported(v, top=False) for v in s.get("properties", {}).values())
    if t == "array":
        # Arrays hold scalars only in v1: nested containers are refused, not half-checked.
        item = s.get("items", {"type": "string"})
        return supported(item, top=False) and item.get("type") not in ("array", "object")
    return True


def _is(value, t: str) -> bool:
    # bool is a subclass of int in Python; JSON Schema says `true` is not an integer.
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[t])


def validate(s: dict, value, path: str = "") -> list[str]:
    """Every rule `value` breaks, as `field: rule`. An empty list means it may go."""
    here = path or "arguments"
    t = s.get("type")
    if t not in TYPES or not _is(value, t):
        return [f"{here}: wrong type"]
    if "enum" in s and value not in s["enum"]:
        return [f"{here}: not allowed"]
    errs: list[str] = []
    if t == "string":
        if len(value) > s.get("maxLength", MAX_STRING):
            errs.append(f"{here}: too long")
        # `minLength` is in KEYWORDS, so it is enforced: accepting a keyword at
        # approval and ignoring it at call time is the half-check this module refuses.
        if len(value) < s.get("minLength", 0):
            errs.append(f"{here}: too short")
    elif t in ("integer", "number"):
        if "minimum" in s and value < s["minimum"]:
            errs.append(f"{here}: below minimum")
        if "maximum" in s and value > s["maximum"]:
            errs.append(f"{here}: above maximum")
    elif t == "array":
        if len(value) > s.get("maxItems", MAX_ITEMS):
            errs.append(f"{here}: too many items")
        item = s.get("items", {"type": "string"})
        for i, v in enumerate(value):
            errs += validate(item, v, f"{path}[{i}]")
    elif t == "object":
        props = s.get("properties", {})
        prefix = f"{path}." if path else ""
        for name in s.get("required", []):
            if name not in value:
                errs.append(f"{prefix}{name}: required")
        for name, v in value.items():
            if name not in props:
                # Always closed, whatever the schema says: unknown arguments are
                # where smuggled data goes. The name is the model's, so it is
                # shown; the value never is.
                errs.append(f"{prefix}{name}: unexpected")
            else:
                errs += validate(props[name], v, f"{prefix}{name}")
    return errs
