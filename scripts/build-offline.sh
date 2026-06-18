#!/usr/bin/env bash
#
# Build the file-browser images for linux/amd64 and bundle them into a single
# tarball you can copy to an offline RHEL host. Run this on the (online) build
# machine — e.g. an Apple-silicon Mac with Docker Desktop.
#
#   ./scripts/build-offline.sh                # -> file-browser-offline.tar
#   OUT=/tmp/fb.tar ./scripts/build-offline.sh
#
# On the RHEL host (offline):
#   docker load -i file-browser-offline.tar
#   docker compose up -d            # uses the loaded images, never pulls/builds
#
set -euo pipefail
cd "$(dirname "$0")/.."

PLATFORM="linux/amd64"
PG_IMAGE="postgres:16"
WEB_IMAGE="file-browser-web:latest"
OUT="${OUT:-file-browser-offline.tar}"

echo ">> Pulling ${PG_IMAGE} (${PLATFORM})"
docker pull --platform "${PLATFORM}" "${PG_IMAGE}"

echo ">> Building ${WEB_IMAGE} (${PLATFORM})"
# Plain docker build keeps the result in the local image store (buildx with
# --load) so it can be saved. Requires Docker Desktop / buildx + QEMU.
docker buildx build --platform "${PLATFORM}" --load -t "${WEB_IMAGE}" .

echo ">> Saving images to ${OUT}"
docker save -o "${OUT}" "${WEB_IMAGE}" "${PG_IMAGE}"

echo ">> Done. Copy these to the offline host:"
echo "     ${OUT}"
echo "     docker-compose.yml   (and an optional .env)"
echo "   Then: docker load -i $(basename "${OUT}") && docker compose up -d"
