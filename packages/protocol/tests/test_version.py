import pytest
import swarmscribe_protocol
from pydantic import ValidationError
from swarmscribe_protocol.base import WireModel


def test_protocol_version_is_one():
    assert swarmscribe_protocol.PROTOCOL_VERSION == 1


def test_wire_models_are_immutable():
    class Example(WireModel):
        name: str

    example = Example(name="a")
    with pytest.raises(ValidationError):
        example.name = "b"
