import json
from threading import Lock

from telegram import Update

from .config import ADMIN_IDS, ADMINS_PATH

_LOCK = Lock()


def _stored_admin_ids() -> set[int]:
    if not ADMINS_PATH.exists():
        return set()
    try:
        values = json.loads(ADMINS_PATH.read_text(encoding="utf-8"))
        return {int(value) for value in values}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return set()


def admin_ids() -> set[int]:
    return set(ADMIN_IDS) | _stored_admin_ids()


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in admin_ids())


def add_admin(user_id: int) -> None:
    with _LOCK:
        values = _stored_admin_ids()
        values.add(int(user_id))
        ADMINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = ADMINS_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(sorted(values), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(ADMINS_PATH)
