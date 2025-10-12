from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Union

from adaptix import Omitted, Retort, TypeHint
from adaptix._internal.common import VarTuple
from adaptix._internal.definitions import Direction
from adaptix._internal.morphing.facade.func import DumpedJSONSchema, generate_json_schema
from adaptix._internal.morphing.json_schema.schema_model import JSONSchemaType
from adaptix._internal.morphing.load_error import LoadError
from tests_helpers import raises_exc

try:
    import jsonschema as jsonschema_py
except ImportError:
    jsonschema_py = None


try:
    import jsonschema_rs
except ImportError:
    jsonschema_rs = None


def _validate_json_schema(json_schema: DumpedJSONSchema) -> DumpedJSONSchema:
    if jsonschema_py is not None:
        jsonschema_py.Draft202012Validator.check_schema(json_schema)
    if jsonschema_rs is not None:
        jsonschema_rs.meta.validate(json_schema)
    return json_schema


def _validate_by_json_schema(data, json_schema: DumpedJSONSchema) -> None:
    if jsonschema_py is not None:
        jsonschema_py.Draft202012Validator(json_schema).validate(data)
    if jsonschema_rs is not None:
        jsonschema_rs.validate(json_schema, data)


@dataclass
class JSONSchemaFork:
    input: Any
    output: Any


class JSONSchemaOptItem:
    def __init__(self, input: Any = Omitted(), output: Any = Omitted()): # noqa: A002
        assert isinstance(input, Omitted) ^ isinstance(output, Omitted)
        self.value = input if not isinstance(input, Omitted) else output
        self.direction = Direction.INPUT if not isinstance(input, Omitted) else Direction.OUTPUT


def _keep_item(data, direction: Direction) -> bool:
    if not isinstance(data, JSONSchemaOptItem):
        return True
    return data.direction == direction


def _resolve_json_schema(data, direction: Direction):
    if isinstance(data, JSONSchemaFork):
        if direction == Direction.INPUT:
            return data.input
        if direction == Direction.OUTPUT:
            return data.output
        raise ValueError
    if isinstance(data, JSONSchemaOptItem):
        return data.value
    if isinstance(data, dict):
        return {
            _resolve_json_schema(k, direction): _resolve_json_schema(v, direction)
            for k, v in data.items()
            if _keep_item(k, direction) and _keep_item(v, direction)
        }
    if isinstance(data, list):
        return [
            _resolve_json_schema(element, direction)
            for element in data
            if _keep_item(element, direction)
        ]
    return data


def assert_morphing(
    *,
    retort: Retort,
    tp: TypeHint,
    data: Any,
    loaded: Any,
    dumped: Any = Omitted(),
    json_schema: DumpedJSONSchema,
) -> None:
    produced_loaded = retort.load(data, tp)
    assert produced_loaded == loaded
    produced_dumped = retort.dump(produced_loaded, tp)
    expected_dumped = data if dumped == Omitted() else dumped
    assert produced_dumped == expected_dumped

    input_json_schema = generate_json_schema(retort, tp, direction=Direction.INPUT)
    output_json_schema = generate_json_schema(retort, tp, direction=Direction.OUTPUT)
    _validate_json_schema(input_json_schema)
    _validate_json_schema(output_json_schema)

    expected_input_json_schema = _resolve_json_schema(json_schema, Direction.INPUT)
    expected_output_json_schema = _resolve_json_schema(json_schema, Direction.OUTPUT)
    assert input_json_schema == expected_input_json_schema
    assert output_json_schema == expected_output_json_schema

    _validate_by_json_schema(data, input_json_schema)
    _validate_by_json_schema(produced_dumped, output_json_schema)


JSONSchemaErrorPathItem = Union[str, int]
JSONSchemaErrorAsserter = Callable[[Any], None]


@dataclass
class JSONSchemaErrorTemplate:
    path: VarTuple[JSONSchemaErrorPathItem]
    py_asserter: JSONSchemaErrorAsserter
    rs_asserter: JSONSchemaErrorAsserter


class JSONSchemaErrorAssertionBuilder:
    def __init__(self, path: VarTuple[JSONSchemaErrorPathItem]):
        self._path = path

    def _with_assertion(
        self,
        *,
        py: JSONSchemaErrorAsserter,
        rs: JSONSchemaErrorAsserter,
    ) -> JSONSchemaErrorTemplate:
        return JSONSchemaErrorTemplate(
            path=self._path,
            py_asserter=py,
            rs_asserter=rs,
        )

    def type(self, *, expected: JSONSchemaType) -> JSONSchemaErrorTemplate:
        def py_asserter(error) -> None:
            assert error.validator == "type"
            assert error.validator_value == expected.value

        def rs_asserter(error) -> None:
            # There is no appropriate __eq__
            assert isinstance(error.kind, jsonschema_rs.ValidationErrorKind.Type)
            assert error.kind.types == [expected.value]

        return self._with_assertion(py=py_asserter, rs=rs_asserter)


class JSONSchemaErrorBuilder:
    def at(self, *items: JSONSchemaErrorPathItem) -> JSONSchemaErrorAssertionBuilder:
        return JSONSchemaErrorAssertionBuilder(items)


def _assert_json_schema_errors(
    *,
    errors: Iterable[Any],
    templates: Sequence[JSONSchemaErrorTemplate],
    asserter_getter: Callable[[JSONSchemaErrorTemplate], JSONSchemaErrorAsserter],
    path_getter: Callable[[Any], VarTuple[JSONSchemaErrorPathItem]],
) -> None:
    errors_list = list(errors)
    assert len(errors_list) == len(templates)
    for error, template in zip(errors_list, templates):
        assert path_getter(error) == template.path
        asserter_getter(template)(error)


json_schema_error = JSONSchemaErrorBuilder()


def assert_load_error(
    *,
    retort: Retort,
    tp: TypeHint,
    data: Any,
    exc: LoadError,
    json_schema_errors: Sequence[JSONSchemaErrorTemplate],
) -> None:
    raises_exc(
        exc,
        lambda: retort.load(data, tp),
    )
    input_json_schema = generate_json_schema(retort, tp, direction=Direction.INPUT)

    if jsonschema_py is not None:
        _assert_json_schema_errors(
            errors=jsonschema_py.Draft202012Validator(input_json_schema).iter_errors(data),
            templates=json_schema_errors,
            asserter_getter=lambda t: t.py_asserter,
            path_getter=lambda e: tuple(e.path),
        )
    if jsonschema_rs is not None:
        _assert_json_schema_errors(
            errors=jsonschema_rs.iter_errors(input_json_schema, data),
            templates=json_schema_errors,
            asserter_getter=lambda t: t.rs_asserter,
            path_getter=lambda e: tuple(e.instance_path),
        )
