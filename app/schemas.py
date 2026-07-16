from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class FindDiningChargersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(default=10, gt=0, le=100)
    restaurant_radius_m: int = Field(default=500, gt=0, le=2000)
    nacs: bool = True
    ccs: bool = True
    tesla_only: bool = False

    @model_validator(mode="after")
    def at_least_one_connector(self) -> "FindDiningChargersRequest":
        if not self.nacs and not self.ccs:
            raise PydanticCustomError(
                "semantic_error",
                "At least one of 'nacs' or 'ccs' must be true.",
            )
        return self
