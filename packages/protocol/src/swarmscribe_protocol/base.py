from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    """Base for everything that crosses the leader-follower wire."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())
