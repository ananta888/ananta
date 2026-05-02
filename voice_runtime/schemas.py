from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    retriable: bool = False
    status: int = 400

    def to_response(self) -> tuple[dict, int]:
        return (
            {
                "error": {
                    "code": self.code,
                    "message": self.message,
                    "retriable": self.retriable,
                }
            },
            self.status,
        )
