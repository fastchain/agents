"""Specialist package."""

__all__ = ["SpecialistAgent"]


def __getattr__(name):
    if name == "SpecialistAgent":
        from .agent import SpecialistAgent

        return SpecialistAgent
    raise AttributeError(name)
