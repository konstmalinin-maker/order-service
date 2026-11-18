import datetime
from typing import Any, TypeVar

from attrs import define as _attrs_define
from dateutil.parser import isoparse

T = TypeVar("T", bound="User")


@_attrs_define
class User:
    """
    Attributes:
        id (int):
        email (str):
        name (str):
        created_at (datetime.datetime):
    """

    id: int
    email: str
    name: str
    created_at: datetime.datetime

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        email = self.email

        name = self.name

        created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(
            {
                "id": id,
                "email": email,
                "name": name,
                "created_at": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: dict[str, Any]) -> T:
        d = src_dict.copy()
        id = d.pop("id")

        email = d.pop("email")

        name = d.pop("name")

        created_at = isoparse(d.pop("created_at"))

        user = cls(
            id=id,
            email=email,
            name=name,
            created_at=created_at,
        )

        return user
