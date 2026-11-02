import logging
import sys
from datetime import date, datetime
from typing import Callable, Optional

from .const import BEIJING_TZ
from .store import Store


class DailyFileHandler(logging.Handler):
    def __init__(self, store: Store, date_provider: Callable[[], date]):
        super().__init__()
        self.store = store
        self.date_provider = date_provider
        self.current_date: Optional[date] = None
        self.file_handler: Optional[logging.FileHandler] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure()
            assert self.file_handler is not None
            self.file_handler.emit(record)
        except Exception:
            self.handleError(record)

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        super().setFormatter(fmt)
        if self.file_handler is not None:
            self.file_handler.setFormatter(fmt)

    def close(self) -> None:
        if self.file_handler is not None:
            self.file_handler.close()
            self.file_handler = None
        super().close()

    def _ensure(self) -> None:
        today = self.date_provider()
        if self.file_handler is not None and self.current_date == today:
            return
        if self.file_handler is not None:
            self.file_handler.close()
        self.current_date = today
        self.store.logs_dir.mkdir(parents=True, exist_ok=True)
        self.file_handler = logging.FileHandler(self.store.logs_dir / f"{today.isoformat()}.log", encoding="utf-8")
        if self.formatter is not None:
            self.file_handler.setFormatter(self.formatter)


def setup_logging(store: Store, stream: bool = True) -> logging.Logger:
    logger = logging.getLogger("buaatd")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if stream:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)
    fh = DailyFileHandler(store, lambda: datetime.now(BEIJING_TZ).date())
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger
