import pytest

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


@pytest.mark.parametrize("bad", [
    {"type": ["string", "null"]},                 # a type list: unhashable, was a TypeError
    {"type": "integer", "minimum": "1"},          # approved, then validate() raised
    {"type": "integer", "maximum": True},         # bool is not a number here either
    {"type": "string", "maxLength": None},
    {"type": "string", "minLength": 1.5},
    {"type": "array", "maxItems": "5"},
    {"type": "string", "enum": "ab"},
    {"type": "object", "properties": []},
])
def test_keyword_values_must_have_the_right_shape(bad):
    assert not schema.supported({"type": "object", "properties": {"x": bad}})


def test_required_must_be_a_list_of_names():
    # A string would be iterated a character at a time.
    assert not schema.supported({"type": "object", "required": "ab", "properties": {}})
    assert not schema.supported({"type": "object", "required": [1], "properties": {}})
    assert not schema.supported({"type": ["object"], "properties": {}})


# --- the two shapes the official git and GitHub servers need (2026-10-02) ----

#: mcp-server-git 2026.8.18 writes every optional argument this way; `git_log`
#: and `git_branch` were unapprovable until it was understood.
NULLABLE = {"type": "object", "required": ["repo_path"], "properties": {
    "repo_path": {"type": "string"},
    "start_timestamp": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                        "title": "Start Timestamp", "description": "ISO date"}}}


def test_mcp_server_git_optional_arguments_are_approvable():
    assert schema.supported(NULLABLE)


def test_a_nullable_argument_takes_null_or_its_own_type_and_nothing_else():
    assert schema.validate(NULLABLE, {"repo_path": "/r", "start_timestamp": None}) == []
    assert schema.validate(NULLABLE, {"repo_path": "/r", "start_timestamp": "2026-01-01"}) == []
    assert schema.validate(NULLABLE, {"repo_path": "/r", "start_timestamp": 7}) == \
        ["start_timestamp: wrong type"]
    assert schema.validate(NULLABLE, {"repo_path": "/r", "start_timestamp": "a" * 2001}) == \
        ["start_timestamp: too long"]


@pytest.mark.parametrize("bad", [
    {"anyOf": [{"type": "string"}, {"type": "integer"}]},           # a real union
    {"anyOf": [{"type": "string"}, {"type": "null"}, {"type": "integer"}]},
    {"anyOf": [{"type": "null"}]},
    {"anyOf": [{"type": "object", "properties": {"x": {"$ref": "#"}}}, {"type": "null"}]},
    {"anyOf": "string"},
    {"anyOf": [{"type": "string"}, {"type": "null"}], "type": "integer"},  # two answers
])
def test_only_the_optional_null_shape_of_anyof_is_approvable(bad):
    assert not schema.supported({"type": "object", "properties": {"x": bad}})


def test_github_mcp_server_vendor_keys_are_ignored_annotations():
    # github-mcp-server 1.12.2 marks `owner`/`repo` with `x-mcp-header`, which
    # made 22 of its 25 read-only tools unapprovable.
    s = {"type": "object", "properties": {"owner": {"type": "string", "x-mcp-header": "owner"}}}
    assert schema.supported(s)
    assert schema.validate(s, {"owner": "m1"}) == []
    assert schema.validate(s, {"owner": 1}) == ["owner: wrong type"]


def test_a_nullable_wrapper_does_not_smuggle_a_nested_container_into_an_array():
    assert not schema.supported({"type": "object", "properties": {"x": {
        "type": "array", "items": {"anyOf": [{"type": "object"}, {"type": "null"}]}}}})


def test_vercels_dialect_label_does_not_refuse_its_whole_catalogue():
    """Live 2026-10-02: all 133 of Vercel's read-only tools were refused for a
    top-level `$schema` (the zod-to-json-schema dialect URI). It names a draft;
    it constrains nothing, so it may stand at the top of a tool's arguments."""
    from notron import schema
    tool = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
            "properties": {"teamId": {"type": "string"}}, "additionalProperties": False}
    assert schema.supported(tool)
    assert schema.validate(tool, {"teamId": "t1"}) == []


def test_a_dialect_label_is_only_a_label():
    from notron import schema
    nested = {"type": "object", "properties": {
        "q": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "string"}}}
    assert not schema.supported(nested)                       # only at the top
    assert not schema.supported({"$schema": {"evil": 1}, "type": "object"})  # only a string


def test_vercels_example_and_deprecated_notes_do_not_refuse_a_tool():
    """Live 2026-10-02: 102 of Vercel's read-only tools were refused for a
    property-level `example`, one for `deprecated`. Both are notes for people;
    they constrain nothing."""
    from notron import schema
    tool = {"type": "object", "properties": {
        "idOrName": {"type": "string", "example": "prj_12", "description": "project"},
        "old": {"type": "string", "deprecated": True}}}
    assert schema.supported(tool)
    assert schema.supported({"type": "object", "properties": {
        "q": {"anyOf": [{"type": "string"}, {"type": "null"}], "example": "x"}}})


def test_min_items_is_accepted_only_because_it_is_enforced():
    from notron import schema
    tool = {"type": "object", "properties": {
        "level": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}
    assert schema.supported(tool)
    assert schema.validate(tool, {"level": []}) == ["level: too few items"]
    assert schema.validate(tool, {"level": ["info"]}) == []
    assert not schema.supported({"type": "object", "properties": {
        "level": {"type": "array", "minItems": -1}}})


def test_exclusive_bounds_are_accepted_only_because_they_are_enforced():
    from notron import schema
    tool = {"type": "object", "properties": {
        "limit": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 100}}}
    assert schema.supported(tool)
    assert schema.validate(tool, {"limit": 0}) == ["limit: below minimum"]
    assert schema.validate(tool, {"limit": 100}) == ["limit: above maximum"]
    assert schema.validate(tool, {"limit": 50}) == []
    # The draft-04 boolean form means something else; it is refused, not guessed.
    assert not schema.supported({"type": "object", "properties": {
        "limit": {"type": "integer", "minimum": 0, "exclusiveMinimum": True}}})


def test_a_server_supplied_pattern_is_still_refused():
    # A regex from a third party can be made to run for ever on a crafted value.
    from notron import schema
    assert not schema.supported({"type": "object", "properties": {
        "name": {"type": "string", "pattern": "^[a-z]+$"}}})
