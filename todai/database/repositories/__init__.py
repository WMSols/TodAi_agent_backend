"""
Domain repositories — profile, chat, calendar.

`stores/` composes these into a per-user unit (with locking for JSON).
"""

from todai.database.repositories.composite import CompositeUserRepository
from todai.database.repositories.protocols import (
    CalendarRepository,
    ChatRepository,
    ProfileRepository,
    UserStoreRepository,
)

__all__ = [
    "CompositeUserRepository",
    "ProfileRepository",
    "ChatRepository",
    "CalendarRepository",
    "UserStoreRepository",
]
