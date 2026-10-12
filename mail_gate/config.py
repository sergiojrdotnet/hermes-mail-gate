import json
from dataclasses import dataclass, field

from mail_gate.paths import data_dir


@dataclass
class Config:
    owners: list[str] = field(default_factory=list)


def load_config() -> Config:
    path = data_dir() / "config.json"
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return Config()
    return Config(owners=[str(owner).lower() for owner in raw.get("owners", [])])
