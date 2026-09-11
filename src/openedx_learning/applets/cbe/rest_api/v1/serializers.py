"""
Serializers for the CBE REST API, v1.
"""
from __future__ import annotations

from rest_framework import serializers

from ...models import CompetencyCriterion, CompetencyRuleProfile, LogicOperator


class CompetencyRuleProfileSerializer(serializers.ModelSerializer):
    """
    Read-only representation of a CompetencyRuleProfile.

    UNSTABLE: the rule profile family is incomplete, so the create, update, and archive
    endpoints still to come may change this shape without a deprecation cycle.

    ``rule_payload`` is emitted verbatim as stored: :ref:`openedx-learning-adr-0002` Decision 3
    owns the payload contract, and normalizing it here would make this a second, competing
    definition of it. That is also why the payload's own ``scale`` key matters, since it is what
    stops a caller reading the threshold fraction as a percentage or the reverse.

    ``scope_code`` and the raw ``organization``, ``course``, and ``competency_taxonomy`` columns
    are internal bookkeeping that ADR-0002 Decision 3 keeps out of anything exported; the
    ``scope_type`` below is what a client reads instead.
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


class CompetencyCriterionSerializer(serializers.ModelSerializer):
    """
    Doubles as the request-body parser and the response representation for a criterion.

    ``object_id``, ``group_id``, and ``logic_operator`` are not CompetencyCriterion fields at
    all (``object_id`` isn't stored anywhere on this model; ``group_id``/``logic_operator``
    belong to CompetencyCriteriaGroup), so they're declared as plain write_only fields the view
    reads out of ``validated_data``, not model-bound fields. ``competency_rule_profile_id``,
    ``competency_criteria_group_id``, and ``oel_tagging_objecttag_id`` are the ticket's
    contracted JSON names, but the model's actual attributes are ``rule_profile_id``,
    ``group_id``, and ``object_tag_id`` -- each needs an explicit ``source=``.
    """

    object_id = serializers.CharField(write_only=True)
    group_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    logic_operator = serializers.ChoiceField(
        choices=LogicOperator.choices, write_only=True, required=False, allow_null=True,
    )
    competency_rule_profile_id = serializers.IntegerField(
        source="rule_profile_id", required=False, allow_null=True,
    )
    competency_criteria_group_id = serializers.IntegerField(source="group_id", read_only=True)
    oel_tagging_objecttag_id = serializers.IntegerField(source="object_tag_id", read_only=True)

    class Meta:
        model = CompetencyCriterion
        fields = [
            "id", "object_id", "group_id", "logic_operator",
            "competency_rule_profile_id", "rule_type_override", "rule_payload_override",
            "competency_criteria_group_id", "oel_tagging_objecttag_id",
        ]
        read_only_fields = ["id"]
