"""
Tests for the CBE public API surface (openedx_learning.api).
"""
import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.http import Http404
from organizations.models import Organization

from openedx_catalog.models import CatalogCourse, CourseRun
from openedx_learning.api import (
    associate_competency_criterion,
    get_competency_rule_profiles,
    is_competency_taxonomy,
    resolve_or_create_leaf_group,
    resolve_supplied_leaf_group,
    select_competency_taxonomies,
)
from openedx_learning.applets.cbe import api as cbe_api
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    LogicOperator,
    RuleType,
)
from openedx_tagging.models import Tag, Taxonomy

pytestmark = pytest.mark.django_db

GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


def make_course_run(organization: Organization, course_code: str, run_code: str) -> CourseRun:
    """Create a CourseRun distinct from the `course_run` fixture, for the same-tag-different-course tests."""
    catalog_course = CatalogCourse.objects.create(org=organization, course_code=course_code)
    return CourseRun.objects.create(catalog_course=catalog_course, run_code=run_code)


def usage_key(course_run: CourseRun, block_id: str) -> str:
    """Build a gradeable-subsection-shaped usage key string under `course_run`."""
    key = course_run.course_key
    assert key is not None
    return f"block-v1:{key.org}+{key.course}+{key.run}+type@sequential+block@{block_id}"


def test_is_competency_taxonomy() -> None:
    """
    is_competency_taxonomy() is True for a competency taxonomy, False for a plain one.
    """
    competency = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")

    assert is_competency_taxonomy(Taxonomy.objects.get(pk=competency.pk)) is True
    assert is_competency_taxonomy(plain) is False


def test_is_competency_taxonomy_on_child_instance_directly() -> None:
    """
    is_competency_taxonomy() also returns True when handed a CompetencyTaxonomy
    instance directly, not just a parent Taxonomy fetched from the DB.
    """
    competency = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    assert is_competency_taxonomy(competency) is True


def test_is_competency_taxonomy_on_unsaved_instance() -> None:
    """
    is_competency_taxonomy() returns False for an unsaved Taxonomy, rather than raising.
    """
    # pk is None, so the reverse one-to-one descriptor short-circuits and raises
    # RelatedObjectDoesNotExist, which Django defines as an AttributeError subclass
    # precisely so hasattr() catches it here instead of propagating.
    unsaved = Taxonomy(name="Unsaved", export_id="unsaved-v1")
    assert is_competency_taxonomy(unsaved) is False


def test_select_competency_taxonomies_avoids_n_plus_1(django_assert_num_queries) -> None:
    """
    select_competency_taxonomies() joins the CompetencyTaxonomy row in, so checking
    is_competency_taxonomy() on every row in the queryset costs one query, not N+1.
    """
    competency1 = CompetencyTaxonomy.objects.create(name="Nursing", export_id="nursing-v1")
    competency2 = CompetencyTaxonomy.objects.create(name="Welding", export_id="welding-v1")
    plain = Taxonomy.objects.create(name="Plain Tags", export_id="plain-v1")
    # Scoped to just these three: unfiltered Taxonomy.objects.all() would also pick up
    # any taxonomies seeded outside this test, which would make the True/False counts
    # below depend on incidental fixture data.
    taxonomies = Taxonomy.objects.filter(pk__in=[competency1.pk, competency2.pk, plain.pk])

    with django_assert_num_queries(1):
        results = [is_competency_taxonomy(t) for t in select_competency_taxonomies(taxonomies)]

    assert results.count(True) == 2
    assert results.count(False) == 1


def test_get_competency_rule_profiles_returns_the_seeded_default(
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """get_competency_rule_profiles() returns the system default an instance starts with."""
    assert list(get_competency_rule_profiles()) == [default_rule_profile]


def test_get_competency_rule_profiles_excludes_archived(
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """get_competency_rule_profiles() leaves retired profiles out."""
    archived = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE,
        rule_payload=GRADE_PAYLOAD,
        competency_taxonomy=competency_taxonomy,
        archived=True,
    )

    profiles = list(get_competency_rule_profiles())

    assert archived not in profiles
    assert profiles == [default_rule_profile]


def test_get_competency_rule_profiles_is_ordered_by_id(
    default_rule_profile: CompetencyRuleProfile,
    competency_taxonomy: CompetencyTaxonomy,
    organization: Organization,
) -> None:
    """
    get_competency_rule_profiles() returns profiles in ascending id order, every time.

    Without a deterministic order, paginating the collection would repeat and skip rows.
    """
    taxonomy_scoped = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=GRADE_PAYLOAD, competency_taxonomy=competency_taxonomy
    )
    organization_scoped = CompetencyRuleProfile.objects.create(
        rule_type=RuleType.GRADE, rule_payload=GRADE_PAYLOAD, organization=organization
    )

    expected = [default_rule_profile, taxonomy_scoped, organization_scoped]
    assert list(get_competency_rule_profiles()) == expected
    assert list(get_competency_rule_profiles()) == expected


# ==============================================================================================
# resolve_or_create_leaf_group
# ==============================================================================================


def test_resolve_or_create_leaf_group_builds_the_full_hierarchy_on_first_use(
    tag: Tag, course_run: CourseRun
) -> None:
    """A first call with no existing groups creates a root, a course-level group, and a leaf."""
    leaf = resolve_or_create_leaf_group(tag, course_run)

    course_level = leaf.parent
    assert course_level is not None
    root = course_level.parent
    assert root is not None
    assert root.parent is None
    assert root.tag_id == tag.id
    assert root.course_id is None
    assert root.name == f"{tag.value} (root)"
    assert course_level.tag_id == tag.id
    assert course_level.course_id == course_run.id
    assert course_level.name == f"{tag.value} — {course_run.title}"
    assert leaf.tag_id == tag.id
    assert leaf.course_id is None
    assert leaf.logic_operator == LogicOperator.OR


def test_resolve_or_create_leaf_group_passes_through_an_explicit_logic_operator(
    tag: Tag, course_run: CourseRun
) -> None:
    """A supplied logic_operator is stored on the leaf as-is, not defaulted to OR."""
    leaf = resolve_or_create_leaf_group(tag, course_run, logic_operator=LogicOperator.AND)
    assert leaf.logic_operator == LogicOperator.AND


def test_resolve_or_create_leaf_group_reuses_the_root_and_course_level_group_on_a_second_call(
    tag: Tag, course_run: CourseRun
) -> None:
    """A second call for the same tag and course reuses the root and course-level group."""
    first_leaf = resolve_or_create_leaf_group(tag, course_run)
    second_leaf = resolve_or_create_leaf_group(tag, course_run)

    assert first_leaf.id != second_leaf.id
    assert first_leaf.parent_id == second_leaf.parent_id
    assert first_leaf.parent is not None
    assert second_leaf.parent is not None
    assert first_leaf.parent.parent_id == second_leaf.parent.parent_id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent__isnull=True).count() == 1
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course=course_run).count() == 1


def test_resolve_or_create_leaf_group_creates_a_new_course_level_group_for_a_different_course(
    tag: Tag, course_run: CourseRun, organization: Organization
) -> None:
    """A second course under the same tag gets its own course-level group, but shares the root."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")

    first_leaf = resolve_or_create_leaf_group(tag, course_run)
    second_leaf = resolve_or_create_leaf_group(tag, other_course_run)

    assert first_leaf.parent is not None
    assert second_leaf.parent is not None
    assert first_leaf.parent.parent_id == second_leaf.parent.parent_id
    assert first_leaf.parent_id != second_leaf.parent_id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent__isnull=True).count() == 1
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course__isnull=False).count() == 2


def test_resolve_or_create_leaf_group_reuses_a_root_a_concurrent_request_already_committed(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    A root created out-of-band (standing in for a concurrent request's winning commit) is reused.

    This exercises the same fallback ``get_or_create()`` relies on for real concurrent callers:
    the ``oel_cbe_criteria_group_one_root_per_tag`` constraint means a second INSERT attempt for
    the same tag's root fails, and ``get_or_create()`` falls back to fetching the row that is
    already there instead of raising.
    """
    already_committed_root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None, name="pre-existing root")

    leaf = resolve_or_create_leaf_group(tag, course_run)

    assert leaf.parent is not None
    assert leaf.parent.parent_id == already_committed_root.id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, parent__isnull=True).count() == 1


def test_resolve_or_create_leaf_group_reuses_a_course_level_group_a_concurrent_request_already_committed(
    tag: Tag, course_run: CourseRun
) -> None:
    """The course-level group half of the same race-safety guarantee, isolated from the root."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    already_committed_course_level = CompetencyCriteriaGroup.objects.create(
        tag=tag, course=course_run, parent=root, name="pre-existing course-level group",
    )

    leaf = resolve_or_create_leaf_group(tag, course_run)

    assert leaf.parent_id == already_committed_course_level.id
    assert CompetencyCriteriaGroup.objects.filter(tag=tag, course=course_run).count() == 1


def test_root_group_unique_constraint_rejects_a_second_root_for_the_same_tag(tag: Tag) -> None:
    """
    The DB constraint resolve_or_create_leaf_group's get_or_create() relies on actually exists.

    Proven directly (bypassing get_or_create) so the race-safety tests above aren't the only
    thing standing between this suite and a silently-dropped migration.
    """
    CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    with pytest.raises(IntegrityError):
        CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)


def test_course_level_group_unique_constraint_rejects_a_second_group_for_the_same_tag_and_course(
    tag: Tag, course_run: CourseRun
) -> None:
    """The course-level half of the same constraint-existence proof."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    with pytest.raises(IntegrityError):
        CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)


# ==============================================================================================
# resolve_supplied_leaf_group
# ==============================================================================================


def test_resolve_supplied_leaf_group_returns_the_leaf_when_it_is_valid(tag: Tag, course_run: CourseRun) -> None:
    """A group_id that names a genuine, matching leaf is returned unchanged."""
    leaf = resolve_or_create_leaf_group(tag, course_run)
    assert resolve_supplied_leaf_group(leaf.id, tag, course_run) == leaf


def test_resolve_supplied_leaf_group_404s_when_the_group_does_not_exist(tag: Tag, course_run: CourseRun) -> None:
    """An unknown group_id 404s rather than raising an unhandled 500."""
    with pytest.raises(Http404):
        resolve_supplied_leaf_group(999999, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_root_group_as_not_a_leaf(tag: Tag, course_run: CourseRun) -> None:
    """A root group (no parent) is not a usable leaf."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(root.id, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_course_level_group_as_not_a_leaf(
    tag: Tag, course_run: CourseRun
) -> None:
    """A course-level group (has its own course) is not a usable leaf either."""
    root = CompetencyCriteriaGroup.objects.create(tag=tag, parent=None)
    course_level = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run, parent=root)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(course_level.id, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_group_for_a_different_tag(
    tag: Tag, course_run: CourseRun, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """A leaf that belongs to a different competency tag is rejected."""
    other_tag = Tag.objects.create(taxonomy=competency_taxonomy, value="Other Competency")
    leaf = resolve_or_create_leaf_group(other_tag, course_run)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(leaf.id, tag, course_run)


def test_resolve_supplied_leaf_group_rejects_a_group_for_a_different_course(
    tag: Tag, course_run: CourseRun, organization: Organization
) -> None:
    """A leaf whose course-level parent belongs to a different course is rejected."""
    other_course_run = make_course_run(organization, "Python200", "Fall2026")
    leaf = resolve_or_create_leaf_group(tag, other_course_run)
    with pytest.raises(ValidationError, match="group_id"):
        resolve_supplied_leaf_group(leaf.id, tag, course_run)


# ==============================================================================================
# associate_competency_criterion
# ==============================================================================================


def test_associate_competency_criterion_creates_the_hierarchy_and_criterion_when_nothing_exists(
    tag: Tag, course_run: CourseRun, default_rule_profile: CompetencyRuleProfile
) -> None:
    """The happy path: no group_id, no existing groups, no rule fields supplied."""
    object_id = usage_key(course_run, "p1")

    criterion = associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    assert criterion.group.tag_id == tag.id
    assert criterion.group.parent is not None
    assert criterion.group.parent.course_id == course_run.id
    assert criterion.object_tag.object_id == object_id
    assert criterion.object_tag.tag_id == tag.id
    assert criterion.rule_profile_id == default_rule_profile.id
    assert criterion.rule_type_override is None
    assert criterion.rule_payload_override is None


def test_associate_competency_criterion_uses_a_supplied_group_id(tag: Tag, course_run: CourseRun) -> None:
    """A caller-supplied group_id is used as-is, rather than deriving or creating a new leaf."""
    leaf = resolve_or_create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")

    criterion = associate_competency_criterion(tag_id=tag.id, object_id=object_id, group_id=leaf.id)

    assert criterion.group_id == leaf.id


def test_associate_competency_criterion_rejects_group_id_and_logic_operator_together(
    tag: Tag, course_run: CourseRun
) -> None:
    """Supplying both group_id and logic_operator is rejected before anything is created."""
    leaf = resolve_or_create_leaf_group(tag, course_run)
    object_id = usage_key(course_run, "p1")

    with pytest.raises(ValidationError, match="logic_operator"):
        associate_competency_criterion(
            tag_id=tag.id, object_id=object_id, group_id=leaf.id, logic_operator=LogicOperator.AND,
        )


def test_associate_competency_criterion_rejects_a_duplicate_tag_object_association(
    tag: Tag, course_run: CourseRun
) -> None:
    """Via the derive-or-create path (no group_id), a second criterion for the same pair is rejected."""
    object_id = usage_key(course_run, "p1")
    associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    with pytest.raises(ValidationError, match="object_id"):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id)


def test_associate_competency_criterion_rejects_re_targeting_the_same_group(
    tag: Tag, course_run: CourseRun
) -> None:
    """Re-supplying the exact group a (tag_id, object_id) pair is already associated with is rejected."""
    object_id = usage_key(course_run, "p1")
    first = associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    with pytest.raises(ValidationError, match="group_id"):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id, group_id=first.group_id)


def test_associate_competency_criterion_allows_a_different_explicit_group_for_the_same_pair(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    A different, explicitly supplied existing group creates a second, deliberate association --
    not a duplicate. ADR-0002's own worked example requires the same tag/object association to
    be able to participate in more than one CompetencyCriteriaGroup.
    """
    object_id = usage_key(course_run, "p1")
    first = associate_competency_criterion(tag_id=tag.id, object_id=object_id)
    other_leaf = resolve_or_create_leaf_group(tag, course_run)

    second = associate_competency_criterion(tag_id=tag.id, object_id=object_id, group_id=other_leaf.id)

    assert second.object_tag_id == first.object_tag_id
    assert second.group_id == other_leaf.id
    assert CompetencyCriterion.objects.filter(object_tag_id=first.object_tag_id).count() == 2


def test_associate_competency_criterion_404s_for_an_unknown_tag_id(course_run: CourseRun) -> None:
    """An unresolvable tag_id 404s before anything else is validated."""
    object_id = usage_key(course_run, "p1")
    with pytest.raises(Http404):
        associate_competency_criterion(tag_id=999999, object_id=object_id)


def test_associate_competency_criterion_rejects_a_malformed_object_id(tag: Tag) -> None:
    """An object_id that isn't a parseable usage key is a 400, keyed by object_id."""
    with pytest.raises(ValidationError, match="object_id"):
        associate_competency_criterion(tag_id=tag.id, object_id="not-a-usage-key")


def test_associate_competency_criterion_rejects_an_unresolvable_course(tag: Tag) -> None:
    """A well-formed usage key whose course has no matching CourseRun is a 400, keyed by object_id."""
    object_id = "block-v1:NoOrg+NoCourse+NoRun+type@sequential+block@p1"
    with pytest.raises(ValidationError, match="object_id"):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id)


def test_associate_competency_criterion_rolls_back_newly_created_groups_when_criterion_creation_fails(
    monkeypatch: pytest.MonkeyPatch, tag: Tag, course_run: CourseRun
) -> None:
    """
    A downstream failure during criterion creation rolls back any group this call created.

    ADR-0002 forbids persisting empty groups, so a failed attempt via the derive-or-create path
    must not leave a root, course-level, or leaf group behind. Simulated here by making the final
    CompetencyCriterion.objects.create() call itself fail -- standing in for any failure at that
    point, including the containment check #666 will add right before it.
    """
    def _reject(**_kwargs) -> None:
        raise ValidationError({"object_id": "rejected for this test"})

    monkeypatch.setattr(cbe_api.CompetencyCriterion.objects, "create", _reject)
    object_id = usage_key(course_run, "p1")

    with pytest.raises(ValidationError):
        associate_competency_criterion(tag_id=tag.id, object_id=object_id)

    assert not CompetencyCriteriaGroup.objects.filter(tag=tag).exists()
