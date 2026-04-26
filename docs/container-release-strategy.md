# Container Release Strategy

Ananta currently treats container images as release evidence and operator build outputs, not as automatically published official packages.

## Current Policy

Release tags can trigger container image builds through `.github/workflows/container-release.yml`.

The workflow:

- builds the backend image from `Dockerfile`
- builds the frontend image from `frontend-angular/Dockerfile`
- builds the single-image quickstart artifact from `Dockerfile.quickstart-no-ollama`
- tags all images with the release tag
- writes image metadata to `container-release-metadata/`
- uploads the metadata as `ananta-container-release-metadata`

The workflow does not push images by default. This avoids publishing unsigned or unapproved images before the release environment and package ownership model are active.

## Future Publishing Gate

Before enabling push to GHCR or another registry:

1. Configure the protected `release` GitHub Environment.
2. Decide package namespace and image names.
3. Use least-privilege package publishing credentials.
4. Add image digest output to release evidence.
5. Add signing or attestation if official images become primary artifacts.
6. Document consumer pull and verification commands.

## Image Naming

Reserved official names:

- `ghcr.io/<owner>/ananta-backend`
- `ghcr.io/<owner>/ananta-frontend`
- `ghcr.io/<owner>/ananta-quickstart-no-ollama`

Local-only and CI-only tags such as `ananta-backend:release-gate` or `ananta-frontend:<tag>` are not official distribution artifacts.
