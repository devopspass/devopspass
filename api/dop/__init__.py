from dataclasses import dataclass
from dop import db


@dataclass
class DopError:
    message: str

    def __str__(self) -> str:
        return self.message


def error(message: str) -> DopError:
    return DopError(message=message)
