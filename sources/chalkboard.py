"""
Chalkboard Fantasy adapter — currently a stub.

Chalkboard does post CS2 (kills / assists / headshots on Map 1 and Maps 1-2)
at major events, but unlike PrizePicks and Underdog there is no public board
API to pull those lines from. This keeps the same fetch(...) -> list[PropLine]
interface as the other sources so it can be dropped in once one exists.
"""
import config
from models import PropLine


def fetch(session=None) -> list[PropLine]:
    if not config.CHALKBOARD_ENABLED:
        return []
    raise NotImplementedError(
        "Chalkboard fetch is enabled in config but not implemented yet. "
        "See the module docstring in sources/chalkboard.py."
    )
