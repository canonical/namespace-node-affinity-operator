# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Unit tests for Namespace Node Affinity/Charm."""
from base64 import b64encode

import pytest
import yaml
from charmed_kubeflow_chisme.kubernetes import create_charm_default_labels
from lightkube.resources.admissionregistration_v1 import MutatingWebhookConfiguration
from lightkube.resources.apps_v1 import Deployment
from lightkube.resources.core_v1 import ConfigMap, Secret, Service, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import Role, RoleBinding
from ops.model import WaitingStatus
from ops.testing import Harness

from charm import K8S_RESOURCE_FILES, TAGGED_IMAGE, NamespaceNodeAffinityOperator

# Used for test_get_settings_yaml
SETTINGS_YAML = """
kubeflow: |
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


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""
    harness = Harness(NamespaceNodeAffinityOperator)

    return harness


class TestCharm:
    """Test class for NamespaceNodeAffinityOperator."""

    def test_not_leader(self, harness: Harness):
        """Test not a leader scenario."""
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    def test_context(self, harness: Harness):
        """Test context property."""
        model_name = "test-model"
        image = TAGGED_IMAGE
        ca_bundle = "bundle123"
        cert = "cert123"
        cert_key = "cert_key123"
        settings_yaml = "abc: 123"
        harness.update_config({"settings_yaml": settings_yaml})

        harness.set_model_name(model_name)

        harness.begin()
        harness.charm._stored.ca = ca_bundle
        harness.charm._stored.cert = cert
        harness.charm._stored.key = cert_key

        expected_context = {
            "app_name": "namespace-node-affinity",  # Can we set this somehow?
            "namespace": model_name,
            "image": image,
            "ca_bundle": b64encode(ca_bundle.encode("ascii")).decode("utf-8"),
            "cert": b64encode(cert.encode("ascii")).decode("utf-8"),
            "cert_key": b64encode(cert_key.encode("ascii")).decode("utf-8"),
            "configmap_settings": f"{settings_yaml}\n",
        }

        assert harness.charm._context == expected_context

    def test_k8s_resource_handler(self, harness: Harness, mocker):
        """Tests whether the k8s_resource_handler is instantiated and cached properly."""
        # Set up the test
        harness.begin()
        logger = "logger"
        harness.charm.logger = logger
        context = harness.charm._context
        field_manager = "field_manager"
        harness.charm._lightkube_field_manager = field_manager
        k8s_resource_files = K8S_RESOURCE_FILES

        # Patch charm.KubernetesResourceHandler so that it does not create a real lightkube client
        # Patch charm.load_in_cluster_generic_resources so we can check it was called without
        # actually using a client
        krh_mocker = mocker.patch("charm.KubernetesResourceHandler")
        krh_mocker.return_value = MockedKRH()
        mocked_load_in_cluster_generic_resources = mocker.patch(
            "charm.load_in_cluster_generic_resources"
        )

        # Use the resource handler a first time and confirm it was created successfully
        krh = harness.charm.k8s_resource_handler
        krh_mocker.assert_called_once_with(
            field_manager=field_manager,
            template_files=k8s_resource_files,
            context=context,
            logger=logger,
            labels=create_charm_default_labels(
                harness.model.app.name,
                harness.model.name,
                scope="auths-deploy-configmaps-sa-secrets-svc-webhooks",
            ),
            resource_types={
                MutatingWebhookConfiguration,
                Deployment,
                Role,
                RoleBinding,
                Service,
                ServiceAccount,
                Secret,
                ConfigMap,
            },
        )
        mocked_load_in_cluster_generic_resources.assert_called_once_with("lightkube_client")

        # Confirm when we use it again, we get the cached version
        krh2 = harness.charm.k8s_resource_handler
        assert krh_mocker.call_count == 1
        assert krh is krh2

    @pytest.mark.parametrize(
        "cert_data_dict, should_certs_refresh",
        [
            # Cases where we should generate a new cert
            # No cert data, we should refresh certs
            ({}, True),
            # We are missing one of the required cert data fields, we should refresh certs
            ({"ca": "x", "key": "x"}, True),
            ({"cert": "x", "key": "x"}, True),
            ({"cert": "x", "ca": "x"}, True),
            # Cases where we should not generate a new cert
            # Cert data already exists, we should not refresh certs
            (
                {
                    "cert": "x",
                    "ca": "x",
                    "key": "x",
                },
                False,
            ),
        ],
    )
    def test_gen_certs_if_missing(
        self, cert_data_dict, should_certs_refresh, harness: Harness, mocker
    ):
        """Test _gen_certs_if_missing.

        This tests whether _gen_certs_if_missing:
        * generates a new cert if there is no existing one
        * does not generate a new cert if there is an existing one
        """
        # Arrange
        # Mock away gen_certs so the class does not generate any certs unless we want it to
        mocked_gen_certs = mocker.patch(
            "charm.NamespaceNodeAffinityOperator._gen_certs", autospec=True
        )
        harness.begin()
        mocked_gen_certs.reset_mock()

        # Set any provided cert data to _stored
        for k, v in cert_data_dict.items():
            setattr(harness.charm._stored, k, v)

        # Act
        harness.charm._gen_certs_if_missing()

        # Assert that we have/have not called refresh_certs, as expected
        assert mocked_gen_certs.called == should_certs_refresh

    def test_get_settings_yaml(self, harness: Harness):
        """Test _get_settings_yaml."""
        harness.begin()

        # Assert that we return an empty settings if no config is set
        returned_settings = harness.charm._get_settings_yaml()
        assert returned_settings == ""

        # Assert that we return a formatted yaml string if settings_yaml config is set
        settings_yaml = """
        key:
          subkey: value
        """
        expected_settings = yaml.dump(yaml.safe_load(settings_yaml))
        harness.update_config({"settings_yaml": settings_yaml})
        returned_settings = harness.charm._get_settings_yaml()
        assert returned_settings == expected_settings

        settings_yaml = SETTINGS_YAML
        expected_settings = yaml.dump(yaml.safe_load(settings_yaml))
        harness.update_config({"settings_yaml": settings_yaml})
        returned_settings = harness.charm._get_settings_yaml()
        assert returned_settings == expected_settings


class MockedKRH:
    """Mocked KubernetesResourceHandler."""

    def __init__(self, *args, **kwargs):
        """Mock KubernetesResourceHandler."""
        self.lightkube_client = "lightkube_client"
