import base64
import hashlib
import io
from pathlib import Path


DEFAULT_IMAGE_NAME = "default.jpg"
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".jfif", ".png"}

_JPEG_TEMPLATE = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCABAADADASIAAhEBAxEB/8QAFwABAQEBAAAAAAAAAAAAAAAAAAECB//EABUQAQEAAAAAAAAAAAAAAAAAAAAR/8QAGAEBAQEBAQAAAAAAAAAAAAAAAQACAwT/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwDm8IsI9jgkI1CAswjUIisI1CFlmLFixFmEahEVhGoQssxYsICkI1CIkWLCFlIRqERSEWLEX//Z"
)


def ensure_photos(images_dir: Path, count: int = 1) -> list[str]:
    """Ensure the built-in fallback photo exists and return its filename."""
    images_dir.mkdir(parents=True, exist_ok=True)
    default_path = images_dir / DEFAULT_IMAGE_NAME
    data = _variant_jpeg(0)
    if not default_path.is_file() or not is_jpeg_file(default_path):
        _atomic_write(default_path, data)
    return [DEFAULT_IMAGE_NAME]


def import_photos(images_dir: Path, sources: list[Path]) -> list[str]:
    """Import user photos as content-addressed JPEG files."""
    images_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for source in sources:
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"照片不存在: {source}")
        if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError("照片仅支持 JPG、JPEG、JFIF 或 PNG 格式")
        data = _to_jpeg_bytes(source)
        digest = hashlib.sha256(data).hexdigest()[:20]
        name = f"photo_{digest}.jpg"
        target = images_dir / name
        if not target.is_file() or target.read_bytes() != data:
            _atomic_write(target, data)
        names.append(name)
    return names


def is_jpeg_file(path: Path) -> bool:
    try:
        path = Path(path)
        data = path.read_bytes()
        if not is_jpeg_bytes(data):
            return False
        try:
            from PIL import Image

            with Image.open(path) as image:
                image.verify()
            return True
        except ImportError:
            return b"\xff\xd9" in data[-32:]
        except Exception:
            return False
    except OSError:
        return False


def is_jpeg_bytes(data: bytes) -> bool:
    return len(data) >= 4 and data[:3] == b"\xff\xd8\xff"


def is_default_image(name: str) -> bool:
    return Path(str(name)).name == DEFAULT_IMAGE_NAME


def _to_jpeg_bytes(source: Path) -> bytes:
    raw = source.read_bytes()
    if source.suffix.lower() in {".jpg", ".jpeg", ".jfif"} and is_jpeg_bytes(raw):
        return raw
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("PNG 照片需要 Pillow 转换，请改用 JPG/JPEG 格式") from exc
    try:
        with Image.open(io.BytesIO(raw)) as image:
            converted = image.convert("RGB")
            converted.thumbnail((1600, 1600))
            output = io.BytesIO()
            converted.save(output, format="JPEG", quality=88, optimize=True)
            data = output.getvalue()
    except Exception as exc:
        raise ValueError(f"无法读取照片 {source.name}: {exc}") from exc
    if not is_jpeg_bytes(data):
        raise ValueError(f"照片转换失败: {source.name}")
    return data


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(
        f".{path.name}.{hashlib.md5(data).hexdigest()[:8]}.tmp"
    )
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _variant_jpeg(seed: int) -> bytes:
    del seed
    return _JPEG_TEMPLATE
