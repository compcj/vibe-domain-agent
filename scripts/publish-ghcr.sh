#!/usr/bin/env bash
set -euo pipefail
: "${IMAGE:?}" "${SOURCE_SHA:?}" "${GH_TOKEN:?}" "${GITHUB_REPOSITORY:?}"
tag="$IMAGE:sha-$SOURCE_SHA"
printf '%s' "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin
trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
# Only a confirmed absence permits creation; other registry faults fail the run.
manifest_error="$(mktemp)"
if docker manifest inspect "$tag" > /dev/null 2> "$manifest_error"; then
  echo 'Existing immutable SHA tag found; validating and reusing it.'
  docker pull "$tag"
elif grep -Eqi 'manifest unknown|no such manifest|not found' "$manifest_error"; then
  docker tag vda:candidate "$tag"
  docker push "$tag"
else
  cat "$manifest_error"
  exit 1
fi
rm -f "$manifest_error"
docker pull "$tag"
digest="$(docker image inspect "$tag" --format '{{index .RepoDigests 0}}')"
[[ "$digest" == "$IMAGE@sha256:"* ]]
docker pull "$digest"
bash scripts/image-smoke.sh "$digest" "$SOURCE_SHA"
current="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')"
result='SHA image published and verified; main advanced, latest was not changed. Run manually again.'
if [[ "$current" == "$SOURCE_SHA" ]]; then
  # Retag the already verified manifest by digest without rebuilding it.
  docker buildx imagetools create --prefer-index=false --tag "$IMAGE:latest" "$digest"
  result='SHA image verified; latest now points to this verified digest.'
fi
{
  echo '## GHCR publication'
  echo "- Commit: \`$SOURCE_SHA\`"
  echo "- Immutable tag: \`$tag\`"
  echo "- Digest: \`$digest\`"
  echo "- Pull: \`docker pull $digest\`"
  echo "- Result: $result"
  echo '- First publication: set package visibility to Public, then validate anonymous pull. Repository visibility alone does not make the package public.'
} >> "$GITHUB_STEP_SUMMARY"
