from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class FindDiningChargersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(default=10, gt=0, le=100)
    restaurant_radius_m: int = Field(default=500, gt=0, le=2000)
    preferred_radius_m: int = Field(default=800, gt=0, le=2000)
    nacs: bool = True
    ccs: bool = True
    l2: bool = False
    tesla_only: bool = False
    include_fast_food: bool = True
    max_results: int = Field(default=30, gt=0, le=200)

    @model_validator(mode="after")
    def at_least_one_connector(self) -> "FindDiningChargersRequest":
        if not self.nacs and not self.ccs and not self.l2:
            raise PydanticCustomError(
                "semantic_error",
                "At least one of 'nacs', 'ccs', or 'l2' must be true.",
            )
        return self
