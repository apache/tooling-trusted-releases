from collections.abc import Sequence
from datetime import datetime
from typing import Any, Optional, Type, Dict

from cyclonedx.model.component import Component
from cyclonedx.model.contact import OrganizationalEntity
from py_serializable import _JsonSerializable, _XmlSerializable, _T


class BomMetaData:
    component: Optional[Component]
    supplier: Optional[OrganizationalEntity]
    manufacturer: Optional[OrganizationalEntity]
    authors: Any
    timestamp: datetime
    tools: Any


class Bom(_JsonSerializable, _XmlSerializable):
    def __init__(self, **kwargs: Any) -> None: ...

    @property
    def version(self) -> int: ...
    @version.setter
    def version(self, version: int) -> None: ...

    @property
    def metadata(self) -> BomMetaData: ...
    @metadata.setter
    def metadata(self, metadata: BomMetaData) -> None: ...

    @property
    def components(self) -> Sequence[Component]: ...
    @components.setter
    def components(self, components: Any) -> None: ...

    @property
    def properties(self) -> Any: ...
    @properties.setter
    def properties(self, properties: Any) -> None: ...

    @property
    def dependencies(self) -> Any: ...
    @dependencies.setter
    def dependencies(self, dependencies: Any) -> None: ...
