import attr
import hikari
from models import Timer


@attr.define()
class TimerCompleteEvent(hikari.Event):
    """
    Dispatched when a scheduled timer has expired.
    """

    app: hikari.RESTAware = attr.field()
    timer: Timer = attr.field()