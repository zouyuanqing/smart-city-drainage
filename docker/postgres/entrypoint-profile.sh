#!/usr/bin/env bash
set -Eeuo pipefail

image_profile="${HYDRAULIC_IMAGE_SPATIAL_BACKEND:-}"
runtime_profile="${HYDRAULIC_SPATIAL_BACKEND:-${image_profile}}"

if [[ -z "${image_profile}" ]]; then
  printf '%s\n' 'HYDRAULIC_IMAGE_SPATIAL_BACKEND is required' >&2
  exit 64
fi
if [[ "${runtime_profile}" != "${image_profile}" ]]; then
  printf 'database image profile %s cannot run as %s\n' \
    "${image_profile}" "${runtime_profile}" >&2
  exit 64
fi
if [[ "${runtime_profile}" != "postgis" && "${runtime_profile}" != "portable" ]]; then
  printf 'unsupported database profile: %s\n' "${runtime_profile}" >&2
  exit 64
fi

# PostgreSQL accepts namespaced custom GUCs. The init hook reads this value to
# select the extension set, and all later sessions observe the same immutable
# profile without changing PostgreSQL's compiled configuration.
export PGOPTIONS="${PGOPTIONS:-} -c hydraulic.spatial_backend=${runtime_profile}"

exec docker-entrypoint.sh "$@"
