import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_data_home() -> Path:
    override = os.environ.get("BUAATD_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (project_root() / "data").resolve()
