# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for Namespace Node Affinity Operator/Charm."""

import logging
from pathlib import Path
from time import sleep

import pytest
import yaml
from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.core_v1 import Container, ContainerPort, PodSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Namespace, Pod
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = "namespace-node-affinity"


SETTINGS_YAML_TEMPLATE = """
{namespace}: |
    nodeSelectorTerms:
      - matchExpressions:
        - key: the-testing-key
          operator: In
          values:
          - the-testing-val1
      - matchExpressions:
        - key: the-testing-key2
          operator: In
          values:
          - the-testing-val2
        """


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy the charm, asserting on the unit status."""
    charm_under_test = await ops_test.build_charm(".")

    await ops_test.model.deploy(charm_under_test, application_name=APP_NAME, trust=True)

    # NOTE: idle_period is used to ensure all resources are deployed
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


LABEL_KEY = "app"
LABEL_VALUE = "tests-for-namespace-node-affinity"
TEST_LABEL = {LABEL_KEY: LABEL_VALUE}


def create_test_pod_resource(name: str) -> Pod:
    """Return a sample Pod resource with the given name."""
    pod = Pod(
        metadata=ObjectMeta(
            name=name,
            labels=TEST_LABEL,
            # namespace=namespace
        ),
        spec=PodSpec(
            containers=[
                Container(
                    name="nginx", image="nginx:1.14.2", ports=[ContainerPort(containerPort=80)]
                )
            ]
        ),
    )
    return pod


@pytest.fixture()
def temp_pod_deleter():
    """Fixture to delete all pods with the test label after a test has run.

    Use this fixture to to ensure no leftover pods remain after a test
    """
    # Do nothing on call
    yield
    # Clean up on return to fixture
    lightkube_client = Client()
    pods = lightkube_client.list(Pod, labels=TEST_LABEL)

    for pod in pods:
        try:
            lightkube_client.delete(Pod, pod.metadata.name)
        except ApiError:
            # Failed to delete, but nothing more we can do
            pass


async def set_application_config(ops_test: OpsTest, app_name: str, config: dict, waittime=10):
    """Update the config of an application and wait for the settings to take effect.

    It would be nice if there was a cleaner way to ensure these settings are propagated, instead of
    just waiting N seconds before checking for idle, but without the wait we usually pass through
    too quickly.  Using just `wait_for_idle` doesn't work here because the charm is idle already,
    so usually we pass `wait_for_idle` before the charm has a chance to wake up and do the config
    change.
    """
    application = ops_test.model.applications[APP_NAME]
    await application.set_config(config)
    sleep(waittime)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60
    )


async def test_webhook_workload(ops_test: OpsTest, temp_pod_deleter):
    """Test whether the webhook properly adds a node affinity to new pods."""
    lightkube_client = Client()

    # Enable the namespace node affinity tool in this model's namespace
    patch = {"metadata": {"labels": {"namespace-node-affinity": "enabled"}}}
    lightkube_client.patch(Namespace, ops_test.model_name, patch)

    # Create a pod in this namespace and ensure it comes up without any node affinity
    # Configure webhook to not apply to any namespaces
    await set_application_config(
        ops_test=ops_test,
        app_name=APP_NAME,
        config={"settings_yaml": ""},
    )
    test_pod_name = "test-pod-no-affinity"
    test_pod = create_test_pod_resource(name=test_pod_name)

    test_pod_created = lightkube_client.create(
        test_pod, name=test_pod.metadata.name, namespace=ops_test.model.name
    )
    assert test_pod_created.spec.affinity is None

    # Update config to add a node affinity to pods in this namespace
    settings_yaml = SETTINGS_YAML_TEMPLATE.format(namespace=ops_test.model.name)
    await set_application_config(
        ops_test=ops_test,
        app_name=APP_NAME,
        config={"settings_yaml": settings_yaml},
    )

    # Create a pod in this namespace and ensure it comes up with the node affinity
    test_pod_name = "test-pod-with-affinity"
    test_pod = create_test_pod_resource(name=test_pod_name)
    test_pod_created = lightkube_client.create(
        test_pod, name=test_pod.metadata.name, namespace=ops_test.model.name
    )
    assert test_pod_created.spec.affinity is not None


async def test_charm_removal(ops_test: OpsTest):
    """Test that the  charm can be removed without errors and leaves no leftovers."""
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)
    deleted = True
    lightkube_client = Client()
    pods = lightkube_client.list(
        Pod, namespace=ops_test.model.name, labels={"app": "namespace-node-affinity-pod-webhook"}
    )
    for pod in pods:
        # if there is a pod in the list, then the pod hasn't been deleted
        deleted = False
    assert deleted, (
        "Workload pod with label 'app': 'namespace-node-affinity-pod-webhook'"
        "still present on the cluster."
    )
