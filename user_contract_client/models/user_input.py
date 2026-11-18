from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UserInput")


@_attrs_define
class UserInput:
    """
    Attributes:
        email (str):
        name (str):
    """

    email: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(
            {
                "email": email,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: dict[str, Any]) -> T:
        d = src_dict.copy()
        email = d.pop("email")

        name = d.pop("name")

        user_input = cls(
            email=email,
            name=name,
        )

        return user_input
