# Volunteer Scheduler — Sample App

A GitOps demo app deployed via Argo CD on a homelab Kubernetes cluster. It is a volunteer scheduling tool built with PostgreSQL, FastAPI, and a vanilla-JS nginx frontend.

## Architecture

```
browser → MetalLB LoadBalancer (frontend:80)
                ↓
          nginx (ConfigMap-served SPA)
                ↓ /api/*
          FastAPI backend (:8000)
                ↓
          PostgreSQL 16 (StatefulSet, 2 Gi PVC)
```

**Namespace:** `volunteer-scheduler`

| Component | Image | Source |
|-----------|-------|--------|
| Frontend  | `nginx:stable-alpine` | `frontend/index.html` via ConfigMap |
| Backend   | `192.168.123.248:5000/volunteer-backend:<tag>` | `backend/main.py` |
| Database  | `postgres:16` | StatefulSet with PVC |

## Directory Layout

```
sample_app/
├── CLAUDE.md                        # this file
├── argocd-application.yaml          # Argo CD Application manifest
├── backend/
│   ├── Dockerfile
│   ├── main.py                      # FastAPI app (single file)
│   └── requirements.txt
├── frontend/
│   └── index.html                   # Single-page app (vanilla JS)
└── k8s/
    ├── namespace.yaml
    ├── backend/
    │   ├── deployment.yaml          # image tag drives Argo CD updates
    │   ├── secret.yaml              # DATABASE_URL, JWT_SECRET
    │   └── service.yaml             # ClusterIP :8000
    ├── frontend/
    │   ├── configmap-app.yaml       # generated — do not edit by hand
    │   ├── configmap-nginx.yaml     # nginx reverse-proxy config
    │   ├── deployment.yaml          # checksum/config annotation triggers restarts
    │   └── service.yaml             # LoadBalancer :80 (MetalLB)
    └── postgres/
        ├── secret.yaml              # POSTGRES_* env vars
        ├── service.yaml             # ClusterIP :5432
        └── statefulset.yaml         # postgres:16, 2 Gi PVC
```

## GitOps Flow

Argo CD watches `sample_app/k8s` (recursive) on the `main` branch of `github.com/rdbraber/argocd_testing`. Auto-sync is enabled with prune and selfHeal.

A PostToolUse hook (`.claude/hooks/commit-argocd.sh`) fires after every Write/Edit inside this repo and:
1. If the edited file is `frontend/index.html`:
   - Regenerates `k8s/frontend/configmap-app.yaml` from the HTML
   - Updates the `checksum/config` annotation in `k8s/frontend/deployment.yaml`
   - Commits and pushes all three files — Kubernetes rolls out new pods automatically
2. For any other file inside `argocd_testing/`: commits and pushes that file

**Do not run `git add/commit/push` manually** for files in this directory — the hook handles it.

## Making Frontend Changes

Edit `frontend/index.html` only. The ConfigMap and deployment annotation are regenerated automatically by the hook. Never edit `k8s/frontend/configmap-app.yaml` directly.

## Making Backend Changes

1. Edit `backend/main.py`
2. Build and push a new versioned image to the local registry:
   ```bash
   docker build -t 192.168.123.248:5000/volunteer-backend:vX.Y.Z backend/
   docker push 192.168.123.248:5000/volunteer-backend:vX.Y.Z
   ```
3. Update the image tag in `k8s/backend/deployment.yaml` — the hook commits and pushes it, and Argo CD rolls out the new image.

**Always use a versioned tag** (e.g. `v1.4.0`). Never use `:latest` — Argo CD won't detect the change.

## Backend: Key Design Decisions

- **Single-file FastAPI app** (`main.py`). All models, schemas, routes, and startup logic live there.
- **Schema migrations** are handled inline in the `startup` event with `ALTER TABLE … ADD COLUMN IF NOT EXISTS` — idempotent across restarts, no migration tool.
- **Admin seeding**: on startup, `seed_admin()` creates `admin@scheduler.local` (password: `admin`, `is_admin=True`) if it does not exist.
- **JWT auth**: `PyJWT` + `bcrypt`. Token payload includes `user_id` and `is_admin`.
- **Admin routes** are protected by the `get_admin_user` dependency (returns 403 for non-admins):
  - `GET/POST /api/admin/users`
  - `PUT/DELETE /api/admin/users/{user_id}`
  - `POST/DELETE /api/admin/slots/{slot_id}/users/{user_id}`

## Frontend: Key Design Decisions

- **Single HTML file** with inline CSS and JS. No build step, no npm.
- Branding: Voedselbanken Nederland federation — color `#EE7402`, inline SVG logo in white pill header.
- `isAdmin` is stored in `localStorage` (set on login from `data.is_admin`). Admin UI (Schedule admin controls + Admin Panel tab) is shown only when `isAdmin === true`.
- `esc(s)` is used for all user-provided strings inserted into `innerHTML` to prevent XSS.
- `userMap = {}` (id → user object) is populated in `loadAdminPanel()` for O(1) user lookup in onclick handlers.

## Infrastructure Notes

- **Local registry**: `192.168.123.248:5000` (insecure HTTP). containerd is configured via `certs.d/hosts.toml` on each node to allow it.
- **MetalLB**: L2 mode, IP pool `192.168.123.245–249`.
- **Traefik** is the cluster ingress controller (not currently used by this app — the frontend Service is a direct LoadBalancer).
