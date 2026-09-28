"""Small helpers that turn numbers into words people read easily."""

import time


def human_size(n):
    units = ("B", "KB", "MB", "GB")
    size, index = float(n), 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    unit = units[index]
    if unit == "B":
        return "{} B".format(int(size))
    return "{:.1f} {}".format(size, unit).replace(".0 ", " ")


def human_age(timestamp, now=None):
    if not timestamp:
        return "never"
    seconds = max(0, (time.time() if now is None else now) - timestamp)
    for limit, div, unit in ((60, 1, None), (3600, 60, "minute"), (86400, 3600, "hour"),
                             (86400 * 45, 86400, "day"), (86400 * 365 * 2, 86400 * 30, "month")):
        if seconds < limit:
            break
    else:
        div, unit = 86400 * 365, "year"
    if unit is None:
        return "just now"
    return "{} ago".format(plural(int(seconds // div), unit))


def plural(count, word, plural_word=None):
    if count == 1:
        return "1 {}".format(word)
    return "{} {}".format(count, plural_word or word + "s")
