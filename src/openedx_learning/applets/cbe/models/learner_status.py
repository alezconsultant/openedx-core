"""
The lookup table of competency mastery ranks.

:class:`CompetencyMasteryStatus` is system-owned lookup data, seeded by migration, that the
learner-status models added in the next PR in this stack point at.
"""
from django.db import models

__all__ = [
    "MasteryStatus",
    "CompetencyMasteryStatus",
]


class MasteryStatus(models.IntegerChoices):
    """
    Ranks of competency mastery.

    Each member's value is the pinned primary key of its ``CompetencyMasteryStatus``
    row, seeded by the ``seed_competency_mastery_statuses`` data migration.

    The integer value of each member is also its rank, lowest to highest mastery.
    Pinning the rank order into the stored id is what lets raising a learner's
    status be written as one conditional ``UPDATE``
    (``... WHERE status_id < new_status_id``) instead of a read, a comparison in
    Python, and a write: ADR-0004 Decision 4 requires that, because two concurrent
    writers doing read-compare-write can each read the same old value, and the
    later of the two writes then lowers what the earlier one had already raised.

    Because the ids are pinned, a new status can be added above or below the
    existing three, but never between them.

    This subclasses ``IntegerChoices`` rather than ``enum.IntEnum`` so that
    Django's migration serializer writes a member as a bare integer instead of an
    import of this module. That keeps an already-applied migration's meaning
    independent of later edits to this enum.
    """

    ATTEMPTED_NOT_DEMONSTRATED = 1, "AttemptedNotDemonstrated"
    PARTIALLY_ATTEMPTED = 2, "PartiallyAttempted"
    DEMONSTRATED = 3, "Demonstrated"


class CompetencyMasteryStatus(models.Model):
    """
    Lookup table of the mastery statuses a competency can be assigned.

    System-owned lookup data, seeded by the ``seed_competency_mastery_statuses`` data
    migration and treated as immutable configuration, not user-authored rows (ADR-0002
    Decision 6.1). See :class:`MasteryStatus` for the pinned ids and names of its rows.

    .. no_pii:
    """

    # ADR-0002 Decision 5 index 10.
    status = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        """User-facing string representation of a CompetencyMasteryStatus."""
        return self.status
