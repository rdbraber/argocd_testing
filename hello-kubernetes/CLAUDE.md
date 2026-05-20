# hello-kubernetes

A GitOps demo workload deployed via Argo CD to two separate clusters: a development cluster (namespaces `dev1` and `dev2`) and a production cluster (namespace `prd`). Each cluster runs its own Argo CD instance.

The app (`paulbouwer/hello-kubernetes:1.10`) demonstrates weighted canary traffic splitting via Traefik's `TraefikService`.

## Architecture

```
IngressRoute (per namespace)
      ↓
TraefikService hello-kubernetes-weighted
      ↓ 75%                    ↓ 25%
hello-kubernetes-stable   hello-kubernetes-canary
  (Deployment, 2 pods)      (Deployment, 2 pods)
  MESSAGE=<env> - application v1.0  MESSAGE=<env> - application v2.0
```

## Directory Layout

```
hello-kubernetes/
├── CLAUDE.md
├── argocd-hello-kubernetes-dev1.yaml   # Apply to dev cluster Argo CD → dev1 namespace
├── argocd-hello-kubernetes-dev2.yaml   # Apply to dev cluster Argo CD → dev2 namespace
├── argocd-hello-kubernetes-prd.yaml    # Apply to prd cluster Argo CD → prd namespace
└── applications/
    ├── kustomization.yaml              # Unused by Argo CD; legacy top-level aggregator
    ├── base/
    │   ├── kustomization.yaml
    │   └── resources.yaml              # Deployments, Services, TraefikService, IngressRoute
    └── overlays/
        ├── dev1/
        │   ├── kustomization.yaml      # namespace: dev1, patches IngressRoute host
        │   ├── namespace.yaml
        │   └── ingressroute-host.yaml
        ├── dev2/
        │   ├── kustomization.yaml      # namespace: dev2, patches IngressRoute host
        │   ├── namespace.yaml
        │   └── ingressroute-host.yaml
        └── prd/
            ├── kustomization.yaml      # namespace: prd, patches IngressRoute host
            ├── namespace.yaml
            └── ingressroute-host.yaml
```

## Argo CD Application Manifests

Each manifest targets `https://kubernetes.default.svc` because it is applied to the local Argo CD instance of the respective cluster. Do not apply dev manifests to the prd cluster or vice versa.

| Manifest | Cluster | Namespace | Kustomize path |
|---|---|---|---|
| `argocd-hello-kubernetes-dev1.yaml` | dev | dev1 | `applications/overlays/dev1` |
| `argocd-hello-kubernetes-dev2.yaml` | dev | dev2 | `applications/overlays/dev2` |
| `argocd-hello-kubernetes-prd.yaml`  | prd | prd  | `applications/overlays/prd`  |

### Applying

```bash
# Dev cluster (run with kubeconfig pointing at the dev cluster)
kubectl apply -f hello-kubernetes/argocd-hello-kubernetes-dev1.yaml
kubectl apply -f hello-kubernetes/argocd-hello-kubernetes-dev2.yaml

# Prd cluster (run with kubeconfig pointing at the prd cluster)
kubectl apply -f hello-kubernetes/argocd-hello-kubernetes-prd.yaml
```

## Kustomize Overlays

Each overlay sets the target namespace and patches both the `IngressRoute` host and the `MESSAGE` environment variable to be environment-specific. The `MESSAGE` value follows the format `<env> - application <version>` (e.g. `dev1 - application v1.0`). To change the hostname for an environment, edit its `ingressroute-host.yaml`. To change the message, edit the inline patches in the overlay's `kustomization.yaml`.

The base `resources.yaml` defines the shared configuration: replica counts, image tag, canary weight split (75/25), and entrypoint. Changes there apply to all environments after their Argo CD syncs.

## Traffic Splitting

The `TraefikService` in the base applies a 75/25 weight between stable and canary. To adjust the split, edit `applications/base/resources.yaml` and change the `weight` fields under `spec.weighted.services`.
