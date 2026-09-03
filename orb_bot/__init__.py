"""ORB (Opening Range Breakout) trading bot package."""

import warnings

# Alpaca's screener models emit a harmless "Pydantic serializer warnings"
# UserWarning on some responses; it's noise, so silence it package-wide.
warnings.filterwarnings("ignore", message=r".*Pydantic serializer warning.*")

__version__ = "1.1.0"
