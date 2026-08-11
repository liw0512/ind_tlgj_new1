from pathlib import Path
from urllib.parse import quote

from system.base.config.SysConfig import config


PROJECT_ROOT = Path(config["base_path"]).resolve()
IMAGE_DIR = PROJECT_ROOT / "resource" / "images"


def image_path(name: str) -> str:
    return str((IMAGE_DIR / name).resolve())


def image_url(name: str) -> str:
    path = (IMAGE_DIR / name).resolve().as_posix()
    return '"' + quote(path, safe="/:") + '"'


def image_src(name: str) -> str:
    return quote((IMAGE_DIR / name).resolve().as_posix(), safe="/:")


def background_image_style(name: str) -> str:
    return f"background-image:url({image_url(name)});"
