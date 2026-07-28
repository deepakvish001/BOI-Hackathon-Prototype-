"""Layer 9 - what the system actually does about a detection."""

from bodhi.actions.casebook import Casebook
from bodhi.actions.killswitch import KillSwitch, KillSwitchDecision

__all__ = ["KillSwitch", "KillSwitchDecision", "Casebook"]
