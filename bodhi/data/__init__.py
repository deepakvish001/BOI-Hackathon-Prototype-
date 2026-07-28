"""Synthetic-but-realistic bank data used to build and evaluate the prototype."""

from bodhi.data.generator import SimulationResult, generate, load, save
from bodhi.data.typologies import TYPOLOGIES, Typology

__all__ = ["SimulationResult", "generate", "save", "load", "TYPOLOGIES", "Typology"]
