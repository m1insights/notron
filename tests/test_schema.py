from notron import schema

S = {"type": "object", "additionalProperties": False, "required": ["q"],
     "properties": {"q": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "state": {"type": "string", "enum": ["open", "closed"]},
                    "labels": {"type": "array", "items": {"type": "string"}}}}


def test_supported_subset_is_accepted():
    assert schema.supported(S)


def test_unsupported_keywords_make_a_tool_unapprovable():
    assert not schema.supported({"type": "object", "properties": {"x": {"$ref": "#/defs/x"}}})
    assert not schema.supported({"type": "object", "properties": {"x": {"oneOf": []}}})
    assert not schema.supported({"type": "string"})  # top level must be an object


def test_valid_arguments_pass():
    assert schema.validate(S, {"q": "bug", "limit": 5, "state": "open", "labels": ["p1"]}) == []


def test_every_violation_is_reported_without_echoing_values():
    errs = schema.validate(S, {"limit": 999, "state": "nope", "extra": "sk-secret", "labels": [1]})
    assert set(errs) == {"q: required", "limit: above maximum", "state: not allowed",
                         "extra: unexpected", "labels[0]: wrong type"}
    assert not any("sk-secret" in e or "nope" in e for e in errs)


def test_booleans_are_not_integers():
    assert schema.validate(S, {"q": "x", "limit": True}) == ["limit: wrong type"]


def test_strings_and_lists_are_capped_when_the_schema_sets_no_limit():
    s = {"type": "object", "properties": {"t": {"type": "string"},
                                          "xs": {"type": "array", "items": {"type": "integer"}}}}
    assert schema.validate(s, {"t": "a" * 2001, "xs": list(range(51))}) == \
        ["t: too long", "xs: too many items"]


def test_every_accepted_keyword_is_enforced():
    s = {"type": "object", "properties": {"t": {"type": "string", "minLength": 3}}}
    assert schema.validate(s, {"t": "ab"}) == ["t: too short"]


def test_nested_containers_are_not_approvable():
    assert not schema.supported({"type": "object", "properties": {
        "x": {"type": "array", "items": {"type": "object"}}}})


def test_top_level_must_be_an_object_of_arguments():
    assert schema.validate(S, ["q"]) == ["arguments: wrong type"]
