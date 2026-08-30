import logging
import re
import uuid
from contextvars import ContextVar


SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

ANONYMOUS = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=ANONYMOUS)
_caller: ContextVar[str] = ContextVar("caller", default=ANONYMOUS)


def new_request_id(supplied: str | None = None) -> str:
    if supplied and SAFE_ID.match(supplied):
        return supplied
    return uuid.uuid4().hex[:12]


def bind(request_id: str, caller: str) -> None:
    _request_id.set(request_id)
    _caller.set(caller)


def current_request_id() -> str:
    return _request_id.get()


def current_caller() -> str:
    return _caller.get()


class ContextFilter(logging.Filter):
 
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.caller = _caller.get()
        return True
