"""Контракт заказа: сумма числом с точностью до копеек."""

from datetime import datetime
from decimal import Decimal

from pydantic import Field, StrictInt, field_validator

from app.common_models import StrictModel


class OrderInput(StrictModel):
    user_id: StrictInt = Field(gt=0)
    total: Decimal = Field(gt=0, le=1000000000, max_digits=12, decimal_places=2)

    @field_validator("total", mode="before")
    @classmethod
    def numeric_total(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("total должен быть числом")
        return value


class Order(StrictModel):
    id: int
    user_id: int
    total: float
    status: str
    created_at: datetime


class UserSnapshot(StrictModel):
    id: StrictInt = Field(gt=0)
    email: str
    name: str
    created_at: datetime
