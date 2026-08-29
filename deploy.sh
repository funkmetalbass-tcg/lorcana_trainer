#!/usr/bin/env bash
set -euo pipefail

# --- set these ---
ACCOUNT=123456789012
REGION=us-east-1
REPO=lorcana-trainer
TAG=latest
URI=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG

# --- build for the instance arch (c6i = Intel x86) ---
docker buildx build --platform linux/amd64 -t $REPO:$TAG --load .

# --- create repo once (ignore error if it exists) ---
aws ecr create-repository --repository-name $REPO --region $REGION 2>/dev/null || true

# --- auth, tag, push ---
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker tag $REPO:$TAG $URI
docker push $URI

echo "Pushed: $URI"
