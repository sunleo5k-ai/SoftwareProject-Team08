"""NotificationService (FR-25).

Messages go out on the channel the driver chose (push / sms / email).
In the prototype the external notification gateway is simulated: each message
is stored in the notifications table and shown in the driver's inbox.
"""
from sqlalchemy.orm import Session

from .. import clock
from ..models import Notification, User


def send(db: Session, user: User, type_: str, message: str) -> Notification:
    n = Notification(user_id=user.user_id, channel=user.notify_channel, type=type_,
                     message=message, created_at=clock.now())
    db.add(n)
    return n
