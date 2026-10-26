"""Local battle engine boundary shared by play and training."""

from .client import BattleClient, BattleServiceError

__all__ = ["BattleClient", "BattleServiceError"]
