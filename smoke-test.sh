#!/usr/bin/env bash
set -euo pipefail

export AWS_PROFILE=floci
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_PAGER=""

echo "== Identity =="
aws sts get-caller-identity

echo "== s3 round-trip =="
BUCKET="smoke-$(date +%s)"
aws s3 mb "s3://$BUCKET"
echo "hello floci" > /tmp/smoke.txt
aws s3 cp /tmp/smoke.txt "s3://$BUCKET/smoke.txt"
aws s3 cp "s3://$BUCKET/smoke.txt" -
aws s3 rb "s3://$BUCKET" --force

echo "== dynamodb round-trip =="
TABLE="smoke-$(date +%s)"
aws dynamodb create-table \
  --table-name "$TABLE" \
  --attribute-definitions AttributeName=id,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST >/dev/null
aws dynamodb wait table-exists --table-name "$TABLE"
aws dynamodb put-item --table-name "$TABLE" --item '{"id":{"S":"1"}}'
aws dynamodb get-item --table-name "$TABLE" --key '{"id":{"S":"1"}}'
aws dynamodb delete-table --table-name "$TABLE" >/dev/null

echo "ALL OK"