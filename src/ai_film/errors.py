class ProviderError(Exception):
    """Raised when a provider call (submit/poll/get_result) fails."""


class CostGateError(Exception):
    """Raised when generation is attempted without required approval."""
