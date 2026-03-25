from collections.abc import Callable
from enum import Enum
from io import TextIOBase
from typing import Any, Iterable, Self, TypeVar, overload
from xml.etree.ElementTree import Element

_T = TypeVar("_T")
_E = TypeVar("_E")
_F = TypeVar("_F", bound=Callable[..., Any])


class ViewType: ...


class SerializationType(str, Enum):
    JSON = "JSON"
    XML = "XML"


class XmlArraySerializationType(Enum): ...


class XmlStringSerializationType(Enum): ...


class _JsonSerializable:
    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self | None: ...
    def as_json(self, view_: type[ViewType] | None = ...) -> str: ...


class _XmlSerializable:
    @classmethod
    def from_xml(
        cls,
        data: TextIOBase | Element,
        default_namespace: str | None = ...,
    ) -> Self | None: ...
    def as_xml(
        self,
        view_: type[ViewType] | None = ...,
        as_string: bool = ...,
        element_name: str | None = ...,
        xmlns: str | None = ...,
    ) -> Element | str: ...


class ObjectMetadataLibrary: ...


# Typed as a no-op to avoid the Union[Type[_T], Type[_JsonSerializable], Type[_XmlSerializable]]
# that py_serializable produces using Union as a stand-in for the unsupported Intersection type.
# The serialization methods are added to individual classes via their own stubs.
@overload
def serializable_class(
    cls: None = ...,
    *,
    name: str | None = ...,
    serialization_types: Iterable[SerializationType] | None = ...,
    ignore_during_deserialization: Iterable[str] | None = ...,
    ignore_unknown_during_deserialization: bool = ...,
) -> Callable[[type[_T]], type[_T]]: ...
@overload
def serializable_class(
    cls: type[_T],
    *,
    name: str | None = ...,
    serialization_types: Iterable[SerializationType] | None = ...,
    ignore_during_deserialization: Iterable[str] | None = ...,
    ignore_unknown_during_deserialization: bool = ...,
) -> type[_T]: ...


@overload
def serializable_enum(cls: None = ...) -> Callable[[type[_E]], type[_E]]: ...
@overload
def serializable_enum(cls: type[_E]) -> type[_E]: ...


def type_mapping(type_: type) -> Callable[[_F], _F]: ...
def include_none(
    view_: type[ViewType] | None = ..., none_value: Any | None = ...
) -> Callable[[_F], _F]: ...
def json_name(name: str) -> Callable[[_F], _F]: ...
def string_format(format_: str) -> Callable[[_F], _F]: ...
def view(view_: type[ViewType]) -> Callable[[_F], _F]: ...
def xml_attribute() -> Callable[[_F], _F]: ...
def xml_array(
    array_type: XmlArraySerializationType, child_name: str
) -> Callable[[_F], _F]: ...
def xml_string(string_type: XmlStringSerializationType) -> Callable[[_F], _F]: ...
def xml_name(name: str) -> Callable[[_F], _F]: ...
def xml_sequence(sequence: int) -> Callable[[_F], _F]: ...
def allow_none_value_for_dict(key: str) -> Callable[[_F], _F]: ...
