"""
Serializers for the CBE REST API, v1.
"""
from __future__ import annotations

from rest_framework import serializers

from ...models import CompetencyRuleProfile


class CompetencyRuleProfileSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a CompetencyRuleProfile.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive
    endpoints still to come may change this shape without a deprecation cycle.

    ``rule_payload`` is emitted verbatim as stored, so its shape is the one
    :ref:`openedx-learning-adr-0002` Decision 3 defines per ``rule_type``. For ``Grade``, the only
    rule type supported in this phase, that shape is ``{"op": ..., "value": ..., "scale": ...}``,
    where ``op`` is one of ``gte``, ``lte``, or ``eq``, and ``value`` is a fraction between 0.0 and
    1.0 inclusive, matching the platform's existing fractional grade representation rather than a
    0-100 scale. That shape is declared as ``GradePayload`` and enforced by
    ``validate_rule_payload`` in the applet's ``rule_payloads`` module, so normalizing it here
    would make this serializer a third, competing definition of it. The fractional ``value`` is
    also why the payload's own ``scale`` key matters, since it is what stops a caller reading the
    threshold fraction as a percentage or the reverse.

    ``scope_code`` and the raw ``organization``, ``course``, and ``competency_taxonomy`` columns
    are left out, because the ``scope_type`` below is what a client can act on. ``scope_code`` in
    particular is internal bookkeeping: :ref:`openedx-learning-adr-0002` Decision 3 defines it as a
    derived ``"org:X,course:Y,taxonomy:Z"`` string that exists to carry the one-profile-per-scope
    unique constraint, and that goes null while a profile is archived, so it is not something a
    caller could rely on.
    """

    scope_type = serializers.SerializerMethodField()

    class Meta:
        model = CompetencyRuleProfile
        fields = ["id", "scope_type", "rule_type", "rule_payload", "archived"]
        # scope_type is absent here because DRF refuses a field that is both declared above and
        # named in read_only_fields; a SerializerMethodField is read-only in any case.
        read_only_fields = ["id", "rule_type", "rule_payload", "archived"]

    def get_scope_type(self, profile: CompetencyRuleProfile) -> str:
        """
        Return which kind of scope ``profile`` applies to.

        All four kinds are recognized from the outset, even though only the system default can
        exist today, so enabling a narrower scope needs no edit here. The scope columns are read
        by their ``_id`` attributes so that no row costs a query, and the system default is
        recognized by those columns being null rather than by matching the internal
        ``scope_code`` string.
        """
        if profile.competency_taxonomy_id is not None:
            return "taxonomy"
        if profile.course_id is not None:
            return "course"
        if profile.organization_id is not None:
            return "organization"
        return "system_default"
