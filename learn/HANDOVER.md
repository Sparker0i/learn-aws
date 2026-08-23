# Handover: Route & Ship, end of Phase 0

Written for whichever agent (AI or human) picks this up next. Phase 0 is complete,
verified live against real AWS resources, and pushed. This document is the
"read this first" briefing before starting Phase 1 — it exists so you don't have
to rediscover the same bugs, and don't waste time treating stale local files as
ground truth when they aren't.

## What this project is

**Route & Ship** — a six-phase, ~26-week roadmap combining AWS (developer angle),
AI engineering, and advanced Go/Python, built around one compounding system rather
than scattered tutorials. Full roadmap, phase-by-phase, with checkboxes:
[`roadmap.html`](../roadmap.html) (open it in a browser — it's a real document,
not just markdown).

The flagship system: **`gateway`** (Go) is a routing/streaming layer in front of
two LLM backends — AWS Bedrock and a homelab GPU (RTX 5080) reachable over
Tailscale, not yet wired up — plus **`assistant`** (Python), which will become a
retrieval-augmented **SRE Copilot**: a Q&A system over public incident post-mortems
(Cloudflare, GitHub, AWS, Google) and the user's own Kafka/Kubernetes/MySQL/MongoDB
domain knowledge, later gaining a second retrieval source over the system's own
CloudWatch/X-Ray telemetry (Phase 4). Both live in one monorepo, not separate repos.

**Corpus decision, already made — don't relitigate it**: the corpus is
post-mortems + the user's own SRE expertise, specifically *not* AWS docs and *not*
a personal blog. Both were considered and rejected as too generic/tutorial-shaped.
See roadmap.html Phase 1's AI block for the reasoning if you need it.

## Repo layout

```
aws-learn/                          (GitHub: Sparker0i/learn-aws, branch: main)
├── roadmap.html                    the full 6-phase roadmap, with progress checkboxes
├── .github/workflows/
│   ├── gateway-ci.yml              path-filtered to gateway/**, test+deploy
│   └── assistant-ci.yml            path-filtered to assistant/**, test+deploy
├── gateway/                        Go service
│   ├── cmd/gateway/main.go
│   ├── internal/hello/             logic lives here, main() just wires it up
│   ├── task-def.json, trust-policy.json   scratch files from Phase 0 tutorial —
│   │                                       already fixed to match live AWS state,
│   │                                       but treat live resources as truth, not these
│   └── .golangci.yml               v2-schema config
├── assistant/                      Python service
│   ├── src/assistant/app.py        current Lambda handler (bare hello-world)
│   ├── tests/                      uv-managed test suite
│   ├── pyproject.toml              ruff/mypy/pytest config — note extend-exclude=["infra"]
│   └── infra/                      separate CDK app, its OWN pip-managed .venv
│       └── infra/assistant_stack.py   the actual CDK stack (renamed from CDK's
│                                       default InfraStack)
└── learn/                          project-management docs, not application code
    ├── HANDOVER.md                 this file
    └── phase-0-foundations/
        └── README.md               the full Phase 0 tutorial — READ THIS if you
                                     need to understand *why* anything is set up
                                     the way it is; it's a step-by-step walkthrough
                                     with every gotcha explained inline
```

## Current live state (verified, not assumed)

**`assistant`** — genuinely live. `curl https://rrvvc7bh2g.execute-api.us-east-1.amazonaws.com/`
returns `assistant: hello`, HTTP 200. This URL is a CDK stack output
(`AssistantStack.ApiUrl`) — if the stack is ever destroyed and redeployed, the URL
changes; get the current one with:
```bash
aws cloudformation describe-stacks --stack-name AssistantStack --region us-east-1 \
  --query "Stacks[0].Outputs" --output json
```

**`gateway`** — works, but by design has no persistent endpoint yet (that's Phase
2). It runs as a one-off Fargate task, currently on task definition `gateway:2`
(revision 2 — revision 1 predates the ARM64 fix, don't use it). Verify it by
re-running CI (`gh workflow run gateway-ci.yml`) or the task directly, then
checking `/ecs/gateway` in CloudWatch Logs.

**CI/CD** — both `gateway-ci` and `assistant-ci` are green, end to end, test
through real AWS deploy. Confirmed by manually triggering both via
`workflow_dispatch` and watching them pass.

## AWS account facts

| Fact | Value |
|---|---|
| Account ID | `186599549641` |
| Region | `us-east-1` (all resources) |
| CLI profile | `route-and-ship` (SSO) — `export AWS_PROFILE=route-and-ship` |
| SSO portal | `https://route-and-ship.awsapps.com/start` |
| Budget | $150/mo, alerts at 66%/100% actual |
| CDK bootstrap | done (`CDKToolkit` stack, `CREATE_COMPLETE`) |
| Bedrock | enabled; `anthropic.*` and `amazon.titan-embed-*` models are visible via `list-foundation-models`. **Model access approval status not verified** — check the Bedrock console's "Model access" page before Phase 1 assumes you can actually invoke one. |

Resource inventory:
- ECS cluster: `route-and-ship`
- ECS task definition family: `gateway` (use revision **2**, has `runtimePlatform: ARM64`)
- ECR repo: `186599549641.dkr.ecr.us-east-1.amazonaws.com/gateway`
- Lambda function: name is CDK-generated with a random suffix (currently
  `AssistantStack-AssistantFn15314C76-z7bWZDnEaPbg`) — look it up, don't hardcode it:
  `aws lambda list-functions --query "Functions[?contains(FunctionName,'AssistantFn')].FunctionName" --output text`
- IAM roles: `ecsTaskExecutionRole` (Fargate execution role), `github-actions-deploy`
  (CI's OIDC-assumed role, `AdministratorAccess` — narrow this in Phase 4, not before)
- OIDC provider: `arn:aws:iam::186599549641:oidc-provider/token.actions.githubusercontent.com`
- CloudWatch log groups: `/ecs/gateway`, `/aws/lambda/AssistantStack-AssistantFn...`

## Local environment quirks (if you're operating a sandboxed/non-interactive shell)

- `node`/`npm`/`cdk` are **not** on the default PATH in a non-interactive bash
  shell — they're nvm-managed. Fix per-command with:
  `export PATH="$HOME/.nvm/versions/node/v24.19.0/bin:$PATH"` (check
  `ls ~/.nvm/versions/node` if that exact version is gone).
- `gh` (GitHub CLI) is at `/opt/homebrew/bin/gh`, also not on default PATH:
  `export PATH="/opt/homebrew/bin:$PATH"`.
- `~/.aws/config` has a `[default]` profile pointing at a **different, unrelated**
  AWS account (`ap-south-1`, root-based). Never use it for this project — always
  explicit `AWS_PROFILE=route-and-ship`.
- `assistant/` has **two separate Python projects** with two separate venvs: the
  `uv`-managed one at `assistant/.venv` (Python 3.14, the actual app), and a
  `pip`-managed one at `assistant/infra/.venv` (the CDK app). Don't conflate them
  — `uv run` only ever touches the first.
- Toolchain versions are pinned to whatever was current in August 2026: **Go
  1.27**, **Python 3.14**. If you're picking this up much later, don't assume
  these are still "latest" — check `go.mod` and `.python-version` for the actual
  current pins rather than trusting this document's numbers.

## Hard-won bugs already fixed — do not "fix" these again

Every one of these was found by actually running things against the real
account, not inferred. Full detail and reasoning for each is in
[`phase-0-foundations/README.md`](phase-0-foundations/README.md), inline at the
step where it bites. Short version, so you recognize them if you see symptoms
resembling these:

1. **GitHub Actions workflows must live at the repo root** (`.github/workflows/`),
   never nested inside a subdirectory — a monorepo gotcha, not optional.
2. **golangci-lint v1 vs v2 config schema** — the repo uses v2
   (`version: "2"` in `.golangci.yml`), and `golangci-lint-action` must be `@v7`,
   not `@v6` (`@v6` hard-rejects v2 configs/binaries).
3. **ECS task execution role needs the log group pre-created** —
   `AmazonECSTaskExecutionRolePolicy` grants `logs:CreateLogStream`/`PutLogEvents`
   but never `logs:CreateLogGroup`. The log group `/ecs/gateway` is pre-created
   by hand; the task definition does **not** set `awslogs-create-group`.
4. **Fargate task architecture must be ARM64**, explicitly, via
   `runtimePlatform` in the task definition — Apple Silicon builds arm64 images
   by default, Fargate defaults to x86_64, mismatch = `exec format error`.
   CI cross-compiles to arm64 via `docker/setup-qemu-action` for the same reason.
5. **`ruff check .` / bare `pytest` from `assistant/` also scan `infra/`** — a
   separate pip-managed project with its own style. Fixed via
   `extend-exclude = ["infra"]` (ruff) and `testpaths = ["tests"]` (pytest) in
   `pyproject.toml`. If you add more Python subprojects, extend these.
6. **mypy runs in `strict` mode** — every new function needs full type
   annotations, no exceptions, from day one.
7. **GitHub's OIDC `sub` claim is not `repo:owner/repo:ref:...`** — it's
   `repo:OWNER@OWNER_ID/REPO@REPO_ID:ref:refs/heads/main`, numeric IDs included.
   Confirmed by decoding a real token, not by trusting docs. The trust policy on
   `github-actions-deploy` already matches this
   (`repo:Sparker0i@*/learn-aws@*:ref:refs/heads/main`). If you ever recreate
   this role from `gateway/trust-policy.json` or the README's example, make sure
   whatever you paste still has the `@*` wildcards — the plain-format version
   will silently fail with an opaque `AccessDenied`.
8. **CDK's generated stack file is nested and misleadingly named** — running
   `cdk init` inside a folder called `infra` produces `infra/infra/infra_stack.py`
   with a class called `InfraStack`; this project renamed both to
   `assistant_stack.py` / `AssistantStack`. If you scaffold a second CDK app
   later, expect the same nesting and rename it the same way.

## Known loose ends (not bugs, just unfinished)

- `gateway/.golangci.bck.yml` is a harmless leftover backup from a
  `golangci-lint migrate` run early on. Safe to delete, never actually used.
- The homelab Ollama routing (RTX 5080, Tailscale) is Phase 2 work — nothing
  about it exists yet, including the tunnel itself.
- Neither `gateway` nor `assistant` do anything beyond printing/returning a
  static hello-world string. All real logic starts in Phase 1.

## What Phase 1 actually needs to ship

Straight from `roadmap.html`'s Phase 1 card (weeks 3–6) — **SRE Copilot v0**:

- **AWS**: S3 (source docs for the corpus), pgvector on RDS for the vector store
  (chosen over a managed vector DB — SQL is already familiar territory), CloudWatch
  Logs for the new pieces.
- **AI**: enable Bedrock model access for Claude + Titan embeddings if not already
  approved (see the "Bedrock" row above), source the post-mortem corpus, chunk it,
  embed it, retrieve top-k, generate an answer — the first real RAG pipeline, naive
  is fine, evals come in Phase 3.
- **Go**: extend `gateway` from a static hello-world into an actual reverse proxy
  (`net/http`) forwarding to the new RAG endpoint — typed config struct instead of
  scattered `os.Getenv`, `log/slog` for structured logging.
- **Ships**: a live "ask an incident-response question" endpoint on Lambda + API
  Gateway, answering from real post-mortems, reachable through the Go proxy —
  deployed on AWS, not running locally.
- **Checkpoints** (roadmap.html's own self-assessment gate before Phase 2):
  RAG v0 answers a real question correctly from a live AWS endpoint; you can
  explain how chunk size/overlap affects retrieval quality; the Go proxy
  successfully forwards a request through and returns the answer.

Follow the same pattern as Phase 0: write `learn/phase-1-.../README.md` as a
step-by-step tutorial (not a checklist) as you go, verify every AWS-specific claim
against the real account before writing it down, and update this handover doc's
"Current live state" section when Phase 1 is done so whoever reads this next
doesn't have to re-verify what you already proved.
