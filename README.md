## Namespace Node Affinity Operator

This Charm deploys a modified version of the [Namespace Node Affinity](https://github.com/idgenchev/namespace-node-affinity) Kubernetes MutatingWebhook. 

The Namespace Node Affinity webhook allows a user to add a given set of node affinities and/or tolerations to all pods deployed in a namespace.  This is useful for example in a case where you have a cluster that has some nodes with specific labels (eg: nodes labeled `control-plane`) and you want all workloads in a Kubernetes namespace to be deployed only on those nodes and not any others in the cluster.  More descriptions of the tool are given in the [upstream README.md](https://github.com/idgenchev/namespace-node-affinity).

## Usage

This charm is deployed using the Juju command line tool as follows:

```bash
juju deploy namespace-node-affinity --trust
```

By default, the webhook is not configured to modify pods in any namespace.  To add namespaces to its scope, we must provide the `settings_yaml` config, which is a YAML string as described [upstream](https://github.com/idgenchev/namespace-node-affinity/blob/42674ec6863d38cbc1009e2f83243a5782aa608a/examples/sample_configmap.yaml#L8).  For example, we can configure the tool to apply:

* apply a node affinity for pods in `testing-ns-a` to look for pods with the label `control-plane=true`, but only to pods that do not have the label `ignoreme: ignored`
* apply a node affinity for pods in `testing-ns-b` to look for pods with the label `other-key: other-value`

by setting the charm config:

```bash
cat <<EOF > settings.yaml

testing-ns-a: |
  nodeSelectorTerms:
    - matchExpressions:
      - key: control-plane
        operator: In
        values:
        - true
  excludedLabels:
    ignoreme: ignored
testing-ns-b: |
  nodeSelectorTerms:
    - matchExpressions:
      - key: other-key
        operator: In
        values:
        - other-value
EOF
SETTINGS_YAML=$(cat settings.yaml)
juju config namespace-node-affinity settings_yaml="$SETTINGS_YAML"
```

and by applying the following labels to the namespaces being monitored (this is required by the tool we are charming, but might be something we should remove in the future as it feels like a redundant setting):

```bash
kubectl label ns testing-ns-a namespace-node-affinity=enabled
kubectl label ns testing-ns-b namespace-node-affinity=enabled
```

These configurations can be modified during charm runtime, and the webhook always uses the most up to date value.  

## Development

When debugging this charm, it is sometimes useful to send `AdmissionReview` JSON payloads to the webhook pod in the same format as what the Kubernetes API would send in order to check if the webhook pods are working properly.  To facilitate that, [this tool](https://github.com/ca-scribner/kubernetes-webhook-testers/tree/main/namespace-node-affinity-tester) was used during charm development and might be useful.
