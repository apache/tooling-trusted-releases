from atr.models import distribution, sql
from atr.shared import distribution as shared_distribution
from atr.storage.writers import distributions


def test_distribution_forms_explain_maven_group_id():
    for form_cls in (
        shared_distribution.DistributionAutomateForm,
        shared_distribution.DistributionRecordForm,
    ):
        documentation = form_cls.model_fields["owner_namespace"].json_schema_extra["documentation"]

        assert "Maven Central" in documentation
        assert "groupId" in documentation
        assert "search.maven.org" in documentation


def test_maven_api_error_mentions_group_id():
    data = distribution.Data(
        platform=sql.DistributionPlatform.MAVEN,
        owner_namespace="org.apache.maven",
        package="maven",
        version="1.0.0",
        details=False,
    )

    error = distributions._distribution_api_error(data, RuntimeError("not found"))

    assert error.status == 502
    assert "Maven groupId" in str(error)
    assert "search.maven.org" in str(error)
