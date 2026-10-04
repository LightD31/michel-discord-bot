"""When the daily keep/remove poll gets extended for lack of votes, decide when to nag.

The poll closes at 20:00 every day once it has :data:`MIN_VOTES` votes;
below that it is extended by another day. Extending only edits the poll
message, which notifies nobody, so a guild can opt into a channel reminder
every *N* consecutive extensions (``voteReminderEveryDays``).
"""

MIN_VOTES = 3


def should_send_low_vote_reminder(extensions: int, every_days: int) -> bool:
    """Return whether the *extensions*-th consecutive extension warrants a reminder.

    ``every_days <= 0`` disables the reminder. Otherwise one is sent on
    extension ``every_days``, ``2 * every_days``, and so on.
    """
    if every_days <= 0 or extensions <= 0:
        return False
    return extensions % every_days == 0
