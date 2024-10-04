#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""A Juju Charm for Namespace Node Affinity."""

import logging
from base64 import b64encode

import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from lightkube import ApiError
from lightkube.generic_resource import load_in_cluster_generic_resources
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

from certs import gen_certs

K8S_RESOURCE_FILES = ["src/templates/webhook_resources.yaml"]

class NamespaceNodeAffinityOperator(CharmBase):
    """A Juju Charm for Namespace Node Affinity."""

    _stored = StoredState()

    def __init__(self, *args):
        """Initialize charm."""
        super().__init__(*args)

        # convenience variables and base settings
        self.logger = logging.getLogger(__name__)
        self._namespace = self.model.name
        self._lightkube_field_manager = "lightkube"
        self._name = self.model.app.name

        self._gen_certs_if_missing()

        self._k8s_resource_handler = None

        # setup events
        self.framework.observe(self.on.config_changed, self.main)
        self.framework.observe(self.on.install, self.main)
        self.framework.observe(self.on.leader_elected, self.main)
        self.framework.observe(self.on.remove, self.main)
        self.framework.observe(self.on.upgrade_charm, self.main)

    def main(self, _):
        """Entrypoint for most charm events."""
        self.logger.info("Starting main")
        try:
            self._check_leader()
            self._deploy_k8s_resources()
        except ErrorWithStatus as error:
            self.model.unit.status = error.status
            return

        self.model.unit.status = ActiveStatus()

    def _check_leader(self):
        """Check if this unit is a leader."""
        self.logger.info("_check_leader")
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _deploy_k8s_resources(self) -> None:
        """Deploy K8S resources."""
        self.logger.info("_deploy_k8s_resources")
        try:
            self.unit.status = MaintenanceStatus("Creating K8S resources")
            self.k8s_resource_handler.apply()
        except ApiError:
            self.logger.error("K8S resource creation failed with ApiError:")
            self.logger.error(str(ApiError))
            self.logger.error(ApiError.status)
            raise ErrorWithStatus("K8S resources creation failed", BlockedStatus)

        self.model.unit.status = MaintenanceStatus("K8S resources created")

    def _gen_certs_if_missing(self):
        """Generate certificates if they don't already exist in _stored."""
        self.logger.info("_gen_certs_if_missing")
        cert_attributes = ["cert", "ca", "key"]
        # Generate new certs if any cert attribute is missing
        for cert_attribute in cert_attributes:
            try:
                getattr(self._stored, cert_attribute)
            except AttributeError:
                self._gen_certs()
                break

    def _gen_certs(self):
        """Refresh the certificates, overwriting them if they already existed."""
        certs = gen_certs(model=self._namespace, service_name=f"{self._name}-pod-webhook")
        for k, v in certs.items():
            setattr(self._stored, k, v)

    @property
    def k8s_resource_handler(self):
        """Return a KubernetesResourceHandler for managing the k8s resources."""
        if not self._k8s_resource_handler:
            self._k8s_resource_handler = KubernetesResourceHandler(
                field_manager=self._lightkube_field_manager,
                template_files=K8S_RESOURCE_FILES,
                context=self._context,
                logger=self.logger,
            )
        load_in_cluster_generic_resources(self._k8s_resource_handler.lightkube_client)
        return self._k8s_resource_handler

    @k8s_resource_handler.setter
    def k8s_resource_handler(self, handler: KubernetesResourceHandler):
        self._k8s_resource_handler = handler

    @property
    def _context(self):
        return {
            "app_name": self._name,
            "namespace": self._namespace,
            "image": self.config["namespace-node-affinity-image"],
            "ca_bundle": b64encode(self._cert_ca.encode("ascii")).decode("utf-8"),
            "cert": b64encode(self._cert.encode("ascii")).decode("utf-8"),
            "cert_key": b64encode(self._cert_key.encode("ascii")).decode("utf-8"),
            "configmap_settings": self._get_settings_yaml(),
        }

    def _get_settings_yaml(self):
        """Return a string of the settings_yaml if it exists, or an empty string."""
        settings = self.model.config["settings_yaml"]
        if not settings:
            return ""

        settings = yaml.safe_load(self.model.config["settings_yaml"])
        return yaml.dump(settings)

    @property
    def _cert(self):
        return self._stored.cert

    @property
    def _cert_key(self):
        return self._stored.key

    @property
    def _cert_ca(self):
        return self._stored.ca

    # todo: add a remove handler?
    # def _on_remove(self, event):
    #     raise NotImplementedError


if __name__ == "__main__":
    main(NamespaceNodeAffinityOperator)
