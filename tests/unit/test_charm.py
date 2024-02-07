# Copyright 2024 Tony Meyer
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import ops
import ops.testing
import pytest
from charm import TakahēOperatorCharm


@pytest.fixture()
def harness():
    harness = ops.testing.Harness(TakahēOperatorCharm)
    harness.begin()
    yield harness
    harness.cleanup()


def test_pebble_ready_web(harness):
    # Simulate the container coming up and emission of pebble-ready event
    harness.container_pebble_ready("takahe-web")
    # The status will be Blocked.
    assert harness.model.unit.status.name == "blocked"


def test_pebble_ready_background(harness):
    # Simulate the container coming up and emission of pebble-ready event
    harness.container_pebble_ready("takahe-background")
    # The status will be Blocked.
    assert harness.model.unit.status.name == "blocked"
