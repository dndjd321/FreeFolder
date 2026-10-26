"""Training and evaluation on the same authoritative engine used by play."""

__all__ = ["ShowdownBattleEnv", "PolicyAgent"]


def __getattr__(name):
    # Inference needs NumPy/PyTorch but not the optional Gym training runtime.
    if name == "ShowdownBattleEnv":
        from .environment import ShowdownBattleEnv
        return ShowdownBattleEnv
    if name == "PolicyAgent":
        from .policy import PolicyAgent
        return PolicyAgent
    raise AttributeError(name)
