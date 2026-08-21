# Phase 00 — Orientation & Floci setup (detailed plan)

Goal: get Floci running reliably on Apple Silicon, build the account/region/ARN/endpoint mental model, and script a reusable smoke test — before phase 01 touches IAM. Budget: 5–8 hrs.

## 0.1 — Prerequisites (~20 min)

- Podman Desktop installed, with a rootless Podman machine initialized and running on Apple Silicon (arm64).
- AWS CLI v2 installed (`brew install awscli`) — Floci speaks the real wire protocol, so the real CLI is what you use.
- `jq` installed (`brew install jq`) — useful for reading CLI JSON output in the smoke test script.

Check:

```bash
podman --version
aws --version
jq --version
podman machine list
```

If no machine shows as `Currently running`, initialize and start one (rootless is the default — no `--rootful` flag):

```bash
podman machine init
podman machine start
```

**Rootless, and why it matters here:** Floci needs to launch sibling containers (Lambda, RDS, ECS, EKS, MSK, ElastiCache, OpenSearch, DocumentDB, ECR) by talking to a container-engine socket mounted inside its own container. Docker Desktop exposes `/var/run/docker.sock` for this directly. Rootless Podman does the equivalent through a per-user socket inside the Podman machine's Linux VM, not at that fixed path — so the mount step below is different from what Floci's own docs assume, and worth doing deliberately rather than skimming.

## 0.2 — Run Floci (~25 min)

Rootless Podman's socket lives at a machine-specific path, not `/var/run/docker.sock`. Resolve it, then mount it into the container at the path Floci's internal Docker SDK client expects:

```bash
SOCK=$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')
echo "$SOCK"   # sanity-check it's non-empty before using it below

podman run -d --name floci \
  -p 4566:4566 \
  -v "$SOCK":/var/run/docker.sock \
  floci/floci:latest
```

- `-d` detached so it stays up across your session; `--name floci` so you can `podman logs -f floci` easily.
- The socket mount is required — without it, only the in-process/emulated services (S3, DynamoDB, SQS, SNS, IAM, Step Functions, CloudFormation, EventBridge, Cognito, and most others) will work; anything Docker-backed (Lambda, RDS, ECS, EKS, MSK, ElastiCache) will fail to start.
- Alternative: Podman Desktop's **Settings → Preferences → Docker Compatibility** toggle aims to make Podman answer at the conventional Docker socket location, which would let you skip the `SOCK=` resolution step. It wasn't verified for this roadmap — try the explicit mount above first, and treat the toggle as a shortcut to test once the explicit version is confirmed working, not a replacement for understanding it.

Verify it's up:

```bash
podman logs floci --tail 30
curl -s http://localhost:4566/_localstack/health 2>/dev/null || curl -s http://localhost:4566/ -o /dev/null -w "%{http_code}\n"
```

**Floci-on-rootless-Podman gotcha:** Floci is written and tested against Docker Desktop's socket and networking model. Rootless Podman's user-namespace remapping and rootless networking (slirp4netns/pasta) differ from Docker's default bridge networking in ways that mostly don't matter for a single container, but can surface as sibling-container problems here specifically — a Docker-backed service's container starts but can't be reached, or fails to start with a permissions error tied to UID remapping. If a Docker-backed service (03/05/06) misbehaves and the AWS-level command looks correct, check `podman ps -a` and `podman logs <container>` on the *sibling* container before assuming your own code is wrong — this combination is less common than Docker Desktop, so expect occasional troubleshooting here rather than clean parity.

**Apple Silicon watch-item:** the first time you exercise a Docker-backed service (RDS, EKS/k3s, MSK), Floci pulls that backing image on demand, inside the Podman machine's Linux VM. On arm64 this can take noticeably longer than on x86 for images without a pre-warmed arm64 layer cache locally. If a container never reports healthy, run `podman ps -a` to find it and `podman logs <container>` to check whether it's still pulling vs. actually failing. Don't debug phase 01+ issues here — confirm image pulls succeed once, up front, for the services you'll use in phases 03/05/06 (ECS, EKS, RDS, MSK), so you're not debugging Floci and your own code at the same time later.

```bash
podman machine ssh -- podman pull --platform linux/arm64 rancher/k3s:latest
podman machine ssh -- podman pull --platform linux/arm64 postgres:16
podman machine ssh -- podman pull --platform linux/arm64 mysql:8
```

Run these inside the machine (`podman machine ssh --`) rather than from the host, since that's the same container runtime Floci itself will use to launch these images. If any of these only have an amd64 manifest, the Podman machine's Linux VM will still run them via emulation (slower, but functional) — you'll notice this as a one-time slow startup rather than a hard failure.

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

- [ ] `podman ps` shows the Floci container healthy and staying up without restarts
- [ ] `smoke-test.sh` passes clean, end to end, with no manual intervention
- [ ] You've pre-pulled the arm64 images for k3s/Postgres/MySQL so phase 03/05 don't stall on a first-pull
- [ ] You have a `floci` CLI profile you source deliberately (not a global default), and you understand the account/region/ARN mapping well enough to explain it back without looking it up
- [ ] You've confirmed the 12-digit multi-account trick works, even though you won't use it again immediately

## Notes for the rest of the roadmap

- Re-run `smoke-test.sh` at the start of every future phase. If it fails, you're debugging Floci, not your code that day — don't conflate the two.
- Keep `podman logs -f floci` in a side terminal during phases 03 (Compute), 05 (Databases), and 06 (Messaging) the first time you touch each new Docker-backed service — that's where you'll actually see image-pull or container-boot problems as they happen, rather than as an opaque CLI timeout. If Floci's own log looks fine but the service still misbehaves, check the sibling container directly (`podman ps -a`, `podman logs <container>`) — with rootless Podman that's a more likely failure point than it would be under Docker Desktop.
