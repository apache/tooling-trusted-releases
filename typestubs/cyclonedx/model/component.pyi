from enum import Enum
from typing import Any, Optional

from cyclonedx.model.bom_ref import BomRef
from cyclonedx.model.contact import OrganizationalEntity
from cyclonedx.model.license import LicenseRepository
from packageurl import PackageURL
from py_serializable import _JsonSerializable, _XmlSerializable


class ComponentScope(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    EXCLUDED = "excluded"


class ComponentType(str, Enum):
    APPLICATION = "application"
    CONTAINER = "container"
    CRYPTOGRAPHIC_ASSET = "cryptographic-asset"
    DATA = "data"
    DEVICE = "device"
    DEVICE_DRIVER = "device-driver"
    FILE = "file"
    FIRMWARE = "firmware"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    MACHINE_LEARNING_MODEL = "machine-learning-model"
    OPERATING_SYSTEM = "operating-system"
    PLATFORM = "platform"


class Swid: ...


class Component(_JsonSerializable, _XmlSerializable):
    name: str
    version: Optional[str]
    type: ComponentType
    supplier: Optional[OrganizationalEntity]
    cpe: Optional[str]
    purl: Optional[PackageURL]
    swid: Optional[Swid]
    scope: Optional[ComponentScope]
    licenses: LicenseRepository
    bom_ref: BomRef

    def __init__(self, *, component_type: ComponentType, name: str, **kwargs: Any) -> None: ...
