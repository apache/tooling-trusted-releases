from abc import abstractmethod
from typing import Any, Optional, Union

from cyclonedx.model.bom import Bom
from cyclonedx.schema import OutputFormat, SchemaVersion


class BaseOutput:
    @abstractmethod
    def output_as_string(
        self,
        *,
        indent: Optional[Union[int, str]] = ...,
        **kwargs: Any,
    ) -> str: ...


def make_outputter(
    bom: Bom,
    output_format: OutputFormat,
    schema_version: SchemaVersion,
) -> BaseOutput: ...
