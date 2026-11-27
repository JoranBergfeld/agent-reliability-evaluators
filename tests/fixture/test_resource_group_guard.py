"""Teardown and re-attachment only ever touch resource groups the fixture created. No Azure access."""

import pytest

from live_fixture import LiveFixtureError, connect, teardown


@pytest.mark.parametrize("name", ["rg-production", "rg-are-42", "rg-are-42-abcdef12-extra", "RG-ARE-42-ABCDEF12"])
def test_a_resource_group_the_fixture_did_not_name_is_refused(name):
    with pytest.raises(LiveFixtureError, match="not a live-fixture resource group"):
        teardown(name)
    with pytest.raises(LiveFixtureError, match="not a live-fixture resource group"):
        connect(name)
