class UmbrellaError(Exception):
    """An expected problem we can explain to the user, with an optional hint on how to fix it."""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint
