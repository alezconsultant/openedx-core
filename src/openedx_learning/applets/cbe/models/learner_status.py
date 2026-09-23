"""
Models tracking a learner's overall mastery status for a competency.

:class:`StudentCompetencyStatus` tracks a learner's current mastery rank at the top
(:class:`~openedx_tagging.models.Tag`) level of a criteria tree; :class:`CompetencyMasteryStatus`
is the lookup table of ranks it points at. There is one :class:`StudentCompetencyStatus` row per
learner per tag, updated in place: finding a learner's current status is a lookup of that single
row, not a query for the most recent of several (ADR-0003 Decision 5). The model accepts any
status value the allow-list check constraint below permits; the rules that decide *which* writes
are allowed, that an automatic write may raise a status but never lower it (ADR-0004 Decision 4),
and that a staff correction may lower one (ADR-0004 Decision 6), are enforced in the API layer,
not here: by the time a write reaches this model there is no caller context left to tell those
cases apart.

``created`` and ``modified`` are caller-supplied UTC datetimes, not automatic. A caller
performing a conditional raise must pass ``modified`` in the same ``update()`` call; there is no
``auto_now`` to do it for them, deliberately, because ``auto_now`` does not fire on
``QuerySet.update()`` and would silently leave the column stale on exactly that path.

``user`` is ``on_delete=models.CASCADE``, not ``PROTECT``: ``PROTECT`` would let this library
veto ``User.delete()`` platform-wide, from openedx-platform code that has no reason to know CBE
rows exist. A learner's status is a derived fact about that learner, so it goes when they do.
``SET_NULL`` is not an option, because a null ``user_id`` would break the ``(user_id, tag_id)``
uniqueness this model's in-place updates rest on.

``tag`` is ``on_delete=models.PROTECT``, and it is load-bearing, not defensive: a future PR's
foreign keys that carry Django's collector down the criteria tree are all ``CASCADE``, so
deleting a ``Tag`` walks into its groups and then their criteria. This ``PROTECT`` is what turns
ADR-0002 Decision 7's guarantee into behavior at this level: the delete succeeds when no learner
holds status beneath the row and raises ``ProtectedError`` when one does. #675 re-implements the
same predicate at the API layer for a clean status code; this is the backstop for paths that
never reach it.

``status`` is also ``on_delete=models.PROTECT``, because the lookup table it points to
(:class:`CompetencyMasteryStatus`) is system-owned immutable data, seeded by migration and never
deleted.
"""
from django.conf import settings
from django.db import models

from openedx_django_lib.fields import manual_date_time_field
from openedx_tagging.models import Tag

__all__ = [
    "MasteryStatus",
    "CompetencyMasteryStatus",
    "StudentCompetencyStatus",
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


class StudentCompetencyStatus(models.Model):
    """
    A learner's current mastery status for one competency (``Tag``).

    One row per learner per tag, updated in place (ADR-0003 Decision 5); see the module
    docstring for the on_delete and timestamp rationale.

    .. no_pii:
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    tag = models.ForeignKey(
        Tag,
        db_column="oel_tagging_tag_id",
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.ForeignKey(
        CompetencyMasteryStatus,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created = manual_date_time_field()
    modified = manual_date_time_field()

    class Meta:
        constraints = [
            # ADR-0002 Decision 5 index 8. This is what makes "one row per learner and
            # competency" true, which is the precondition for the in-place conditional
            # update the module docstring describes: it is load-bearing, not a lookup
            # optimisation.
            models.UniqueConstraint(
                fields=("user", "tag"),
                name="oex_learning_studentcompetencystatus_user_tag_uniq",
            ),
            # Allow list, not a negation of the excluded value: a future fourth
            # status should be rejected here by default rather than silently
            # permitted.
            models.CheckConstraint(
                condition=models.Q(status__in=(MasteryStatus.PARTIALLY_ATTEMPTED, MasteryStatus.DEMONSTRATED)),
                name="oex_learning_studentcompetencystatus_status_allowed",
            ),
        ]
