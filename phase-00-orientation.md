# Phase 00 — Orientation & Floci setup (detailed plan)

Goal: get Floci running reliably on Apple Silicon, build the account/region/ARN/endpoint mental model, and script a reusable smoke test — before phase 01 touches IAM. Budget: 5–8 hrs.

## 0.1 — Prerequisites (~15 min)

- Docker Desktop running, Apple Silicon (arm64) build, with the Docker socket available at `/var/run/docker.sock`.
- AWS CLI v2 installed (`brew install awscli`) — Floci speaks the real wire protocol, so the real CLI is what you use.
- `jq` installed (`brew install jq`) — useful for reading CLI JSON output in the smoke test script.

Check:

```bash
docker --version
aws --version
jq --version
```

## 0.2 — Run Floci (~20 min)

```bash
docker run -d --name floci \
  -p 4566:4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  floci/floci:latest
```

- `-d` detached so it stays up across your session; `--name floci` so you can `docker logs -f floci` easily.
- The Docker-socket mount is required — Floci uses it to launch the real containers backing Lambda, RDS, ECS, EKS, MSK, ElastiCache, OpenSearch, DocumentDB, and ECR. Without it, only the in-process/emulated services (S3, DynamoDB, SQS, SNS, IAM, Step Functions, CloudFormation, EventBridge, Cognito, and most others) will work.

Verify it's up:

```bash
docker logs floci --tail 30
curl -s http://localhost:4566/_localstack/health 2>/dev/null || curl -s http://localhost:4566/ -o /dev/null -w "%{http_code}\n"
```

**Apple Silicon watch-item:** the first time you exercise a Docker-backed service (RDS, EKS/k3s, MSK), Floci pulls that backing image on demand. On arm64 this can take noticeably longer than on x86 for images without a pre-warmed arm64 layer cache locally. If a container never reports healthy, run `docker ps -a` to find it and `docker logs <container>` to check whether it's still pulling vs. actually failing. Don't debug phase 01+ issues here — confirm image pulls succeed once, up front, for the services you'll use in phases 03/05/06 (ECS, EKS, RDS, MSK), so you're not debugging Floci and your own code at the same time later.

```bash
docker pull --platform linux/arm64 rancher/k3s:latest
docker pull --platform linux/arm64 postgres:16
docker pull --platform linux/arm64 mysql:8
```

If any of these only have an amd64 manifest, Docker Desktop's Rosetta emulation will still run them (slower, but functional) — you'll notice this as a one-time slow startup rather than a hard failure.

## 0.3 — Configure the AWS CLI profile (~15 min)

Create a dedicated profile so you never risk pointing a command at real AWS by mistake.

```bash
aws configure set aws_access_key_id test --profile floci
aws configure set aws_secret_access_key test --profile floci
aws configure set region us-east-1 --profile floci
aws configure set output json --profile floci
```

Then set the endpoint override. The cleanest way is a per-command `--endpoint-url`, but for a whole learning track it's less friction to export it:

```bash
export AWS_PROFILE=floci
export AWS_ENDPOINT_URL=http://localhost:4566
```

Add both lines to a `.envrc` (if you use direnv) or a `floci.env` you `source` at the start of each session — **do not** put them in your global shell profile, so you can never forget you're pointed at Floci vs. real AWS later in the roadmap when you do the real-AWS validation passes (phases 07 and 09).

**Mental model, pinned here:** an AWS "account" is the coarsest boundary (closest to a Docker Hub namespace or an entire separate cluster, not a Kubernetes namespace); "region" + endpoint together are your kubeconfig context; and every resource gets a globally-referenceable ARN (`arn:aws:s3:::my-bucket`) the way a Kubernetes resource has a UID — except an ARN is human-readable and part of the API surface, not an internal implementation detail.

## 0.4 — First calls: confirm the round-trip (~15 min)

```bash
aws sts get-caller-identity
```

Expect an `Account`, `UserId`, and `Arn` back — this is the single most useful "is anything working" check for every later phase, since it fails fast and cheap if your endpoint/credentials are misconfigured.

```bash
aws s3 mb s3://smoke-test-bucket
aws s3 ls
aws s3 rb s3://smoke-test-bucket
```

If all three succeed, the wire protocol round-trip (CLI → Floci → in-process S3 emulation → response) is confirmed working end to end.

## 0.5 — Multi-account isolation, a Floci-specific feature worth knowing now (~20 min)

Floci uses the 12-digit `AWS_ACCESS_KEY_ID` you pass as an account identifier — different 12-digit values give you fully isolated "accounts" inside the same Floci instance. This has no equivalent in a single Kubernetes cluster (namespaces still share cluster-scoped resources) and previews a genuinely job-relevant AWS pattern: cross-account IAM roles, which you'll want later when practicing "assume a role in a different account."

```bash
aws configure set aws_access_key_id 000000000001 --profile floci-a
aws configure set aws_access_key_id 000000000002 --profile floci-b
# both still: secret=test, region=us-east-1, endpoint=http://localhost:4566

AWS_PROFILE=floci-a aws s3 mb s3://account-a-bucket
AWS_PROFILE=floci-b aws s3 ls   # should NOT show account-a-bucket
```

Keep this pattern in your back pocket — you don't need it again until the cross-account exercise later in the roadmap, but it's cheap to confirm it works now while you're already in setup mode.

## 0.6 — Build the reusable smoke-test script (~45–60 min)

This is the phase's actual deliverable: a script you'll re-run at the start of every future phase to confirm Floci is healthy before you start debugging your own code.

```bash
#!/usr/bin/env bash
# smoke-test.sh — run against Floci before starting any phase's work
set -euo pipefail

export AWS_PROFILE=floci
export AWS_ENDPOINT_URL=http://localhost:4566

echo "== identity =="
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
```

Save it as `smoke-test.sh`, `chmod +x smoke-test.sh`, and commit it to the repo — it's infrastructure for the rest of the roadmap, not throwaway scratch work.

```bash
chmod +x smoke-test.sh
./smoke-test.sh
```

## 0.7 — Exit criteria for phase 00

You're ready for phase 01 (IAM basics) when:

- [ ] `docker ps` shows the Floci container healthy and staying up without restarts
- [ ] `smoke-test.sh` passes clean, end to end, with no manual intervention
- [ ] You've pre-pulled the arm64 images for k3s/Postgres/MySQL so phase 03/05 don't stall on a first-pull
- [ ] You have a `floci` CLI profile you source deliberately (not a global default), and you understand the account/region/ARN mapping well enough to explain it back without looking it up
- [ ] You've confirmed the 12-digit multi-account trick works, even though you won't use it again immediately

## Notes for the rest of the roadmap

- Re-run `smoke-test.sh` at the start of every future phase. If it fails, you're debugging Floci, not your code that day — don't conflate the two.
- Keep `docker logs -f floci` in a side terminal during phases 03 (Compute), 05 (Databases), and 06 (Messaging) the first time you touch each new Docker-backed service — that's where you'll actually see image-pull or container-boot problems as they happen, rather than as an opaque CLI timeout.
