"""Active run duration, excluding overlapping user-wait intervals."""
from datetime import datetime


def active_seconds(start, end, waits):
    cursor = start
    paused = 0.0
    for began, finished in sorted(waits, key=lambda wait: wait[0]):
        began = max(start, datetime.fromisoformat(began), cursor)
        finished = min(end, datetime.fromisoformat(finished) if finished else end)
        if finished > began:
            paused += (finished - began).total_seconds()
            cursor = finished
    return max(0, int((end - start).total_seconds() - paused))
