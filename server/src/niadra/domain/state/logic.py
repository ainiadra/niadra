"""The four logical values of a field (L25): not observed is never false."""

from enum import StrEnum


class Logic(StrEnum):
    YES = "yes"
    NO = "no"
    UNOBSERVED = "unobserved"
    KNOWN_DEFECT = "known_defect"

    @property
    def known(self) -> bool:
        return self in (Logic.YES, Logic.NO)


def unknown_of(*logics: Logic) -> Logic | None:
    """The unknown that a combination of values carries, if any: a known defect of a source says more than
    a missing observation (use the other source), so it wins over `unobserved`."""
    if Logic.KNOWN_DEFECT in logics:
        return Logic.KNOWN_DEFECT
    if Logic.UNOBSERVED in logics:
        return Logic.UNOBSERVED
    return None
