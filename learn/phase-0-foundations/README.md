# Phase 0: Foundations & First Deploy

Weeks 1–2 · ~20–30 hrs total · companion to [`roadmap.html`](../../roadmap.html)

**Goal:** a sane AWS account, refreshed Go/Python project hygiene, and two scaffolded
projects — `gateway` in Go, `assistant` in Python, both as directories in one
monorepo — each deployed to AWS as a trivial hello-world, via a pipeline, not console
clicks. Nothing clever ships this phase. You're building the plumbing the real system
runs on for the next 24 weeks.

One structural note that matters once you get to CI: GitHub Actions only ever reads
workflows from `.github/workflows/` at the **repository root** — never from a nested
`.github/` inside a subdirectory. In a monorepo, both projects' workflows live
together at the root, distinguished by `paths:` filters rather than by location.
Sections 3 and 4 write the workflow files with that in mind; section 7 covers the
one shared OIDC role both of them deploy through.

This is written as a straight walkthrough: sections run in order, each one assumes
the last is done, and the commands are meant to be run as you read them rather than
skimmed and revisited later. Toolchain versions used throughout: **Go 1.27** and
**Python 3.14** — whatever's actually current when you read this may differ, but
the two need to agree with each other (go.mod, Dockerfile, CI) and Python needs to
agree with the Lambda runtime you deploy to. Where that matters, it's called out.

## Time budget

| Block | Hours |
|---|---|
| AWS account, IAM, budgets, CLI | 2–3 |
| Go repo scaffold + lint + CI | 3–4 |
| Python repo scaffold + lint + CI | 3–4 |
| `assistant` hello-world → Lambda via CDK | 5–6 |
| `gateway` hello-world → one Fargate run | 4–5 |
| Wire CD (auto-deploy on push to main) | 3–4 |
| Reading | 3–4 |
| **Total** | **23–30** |

## Before you start

Install these locally. You'll use every one of them in the next two weeks:

- AWS CLI v2 — check with `aws --version`
- Docker
- Go 1.27 (or whatever's current — just make sure `go.mod`, the CI workflow, and your Dockerfile all agree with each other, not with a number written here)
- [`uv`](https://docs.astral.sh/uv/) for Python env/deps
- Node.js, then `npm install -g aws-cdk` — the CDK *CLI* is Node-based even though your stacks are Python
- `golangci-lint` — `go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest`
- A GitHub account with a repo for this project — one monorepo holding both `gateway` and `assistant` as subdirectories, not two separate repos

With that done, work through the sections below in order.

---

## 1. AWS account & IAM

### Step 1 — Decide your account and region

Use an AWS account dedicated to this project rather than a personal or work account you might already have. It makes the cost analysis in Phase 4 unambiguous — every dollar in Cost Explorer is this project, nothing else. Sign up at [aws.amazon.com](https://aws.amazon.com) with an email you're fine being the account's permanent root identity; that can't be changed casually later.

While you're there, pick a default region and commit to it: **`us-east-1`** (N. Virginia) is the safe default — it gets new services and new Bedrock models first, and almost every tutorial you'll read assumes it. `us-west-2` is the other broadly-supported option. Region matters more here than in most AWS work, because Bedrock model availability varies by region — you can always deploy an individual resource elsewhere later, but pick one now so you're not chasing "why isn't this here" bugs in Phase 1.

### Step 2 — Lock down the root user

The root user is the account's superuser. It can do a few things nothing else can — close the account, change the support plan, view certain billing/tax settings — and for everything else it's a liability: a single compromised credential with unlimited blast radius. You use it exactly twice: right now, and never again for daily work.

1. Sign in at the AWS Console with the root email and password you just created.
2. Top-right account menu → **Security credentials** → **Multi-factor authentication (MFA)** → **Assign MFA device** → **Authenticator app**. Scan the QR code with an authenticator (1Password's built-in TOTP works fine) and enter two consecutive codes to confirm.
3. On the same Security Credentials page, check **Access keys**. If root has any, delete them — root should never hold long-lived API keys, since everything else from here on authenticates through Identity Center.
4. Set an account alias: IAM console → Dashboard → **Create account alias**. This turns your sign-in URL into something like `https://route-and-ship.signin.aws.amazon.com/console` instead of a bare 12-digit account ID — small thing, but you'll type it often.

### Step 3 — Turn on IAM Identity Center

This is the modern replacement for "just create an IAM user for yourself." Identity Center is AWS's built-in SSO: a real login portal, MFA, and centrally-managed **permission sets** — critically, no long-lived access keys ever sit on your laptop, because everything you assume through it is a short-lived, auto-expiring credential.

1. Console search bar → **IAM Identity Center** → **Enable**, in the same region you picked in Step 1. It offers to create an AWS Organization if one doesn't exist yet — accept that; for a single account it's just a required container, not something you manage.
2. Confirm **Identity source** is left on its default, **Identity Center directory**. Don't switch to AWS Managed AD or an external IdP (Okta, Azure AD, Google Workspace) — those exist to federate an *existing* corporate directory, which is irrelevant here.
3. Settings → Identity source → **AWS access portal** → customize the subdomain if you want something memorable, e.g. `route-and-ship` → `https://route-and-ship.awsapps.com/start` instead of the auto-generated `d-xxxxxxxxxx` one. You can only change this a limited number of times, so pick something you'll keep — this is the URL you log in at from now on, not the root sign-in page.

### Step 4 — Configure authentication settings

Before creating any users, go to Settings → **Authentication** and set these:

1. **Multi-factor authentication** → MFA types allowed: enable **Authenticator apps**.
2. "Prompt users for MFA" → **Every time they sign in (Always-on)**. The "context-aware" alternative only prompts when sign-in risk looks elevated, which adds unpredictability that isn't worth it for a single-user account.
3. "If a user doesn't have a registered MFA device" → **Require registration at sign-in**, so there's never a window where the account is protected by password alone.
4. **Session** → raise the portal session duration toward **8 hours**. This is separate from the permission-set session duration you'll set in Step 5 — this one governs your browser session to the `awsapps.com` portal itself.

### Step 5 — Create your user, group, and permission set

1. **Users** → **Add user** → fill in username, email, first/last name. You'll get a one-time password by email.
2. **Groups** → create one, e.g. `Admins`, and add your user to it. Even solo, a group is one less thing to rewire later if you add a second identity — a CI service user, a future collaborator.
3. **Permission sets** → **Create permission set** → **Predefined permission set** → `AdministratorAccess`. Raise the session duration from the 1-hour default to **8 hours** (12 hours is the max selectable) — this is the setting that actually matters day to day, since it governs how long your CLI credentials from `aws sso login` stay valid. Leave relay state blank.
4. **AWS accounts** tab → select your account → **Assign users or groups** → your `Admins` group → attach the `AdministratorAccess` permission set.
5. Log in once at the Identity Center portal URL with the emailed temporary password, set a real password, and register MFA on this identity too — same authenticator app, separate entry, a distinct credential from root's.

Yes, `AdministratorAccess` is broad. That's deliberate: Phase 4 runs a real least-privilege IAM audit once you know what the system actually touches. Guessing narrow permissions today just means fighting opaque `AccessDenied` errors for two weeks over nothing — premature restriction, not security.

One thing not to conflate this with: in section 7, GitHub Actions gets its own separate IAM OIDC provider and role. That has nothing to do with Identity Center, which is strictly for your own browser-based human login.

### Step 6 — Point the CLI at it

Run the configuration wizard:

```bash
aws configure sso
```

Answer the prompts like this:

```
SSO session name (Recommended): route-and-ship
SSO start URL [None]: https://route-and-ship.awsapps.com/start   # from Step 3
SSO region [None]: us-east-1                                     # wherever you enabled Identity Center
SSO registration scopes [sso:account:access]:                    # just hit enter, keep the default
```

Naming the session, rather than leaving it blank, means future profiles can share this one login instead of each forcing a separate browser round-trip. A browser opens for you to approve the login — pick your account and the `AdministratorAccess` permission set, then choose a **CLI profile name**: use `route-and-ship` again so it's unambiguous once you have other AWS profiles around.

Note this profile's token lifetime is governed by the *permission set's* session duration (Step 5), not the portal session duration (Step 4) — the two 8-hour settings you configured control different things.

Now confirm it works:

```bash
export AWS_PROFILE=route-and-ship
aws sts get-caller-identity
```

You should get back a JSON blob with `Account`, `UserId`, and `Arn`. Read the `Arn` — it'll look like `arn:aws:sts::<account-id>:assumed-role/AWSReservedSSO_AdministratorAccess_.../<you>`. The words `assumed-role` are the tell: you aren't "logged in as a user," you're **assuming a role** that Identity Center manages, which is exactly the mechanism explained next.

Sessions expire per the duration you set in Step 5; when a command starts failing with an expired-token error, `aws sso login --profile route-and-ship` refreshes it.

### Step 7 — Tie the concepts to what you just did

This phase's checkpoint asks you to explain **user**, **role**, and **policy** without looking it up. Here's the version tied to what you just built, which sticks better than the abstract definition:

- **User** — an identity with credentials directly attached to it (a password, or long-lived access keys). You didn't create one of these for yourself, on purpose — Identity Center's whole pitch is that humans shouldn't need them.
- **Role** — an identity with *no* attached credentials at all. Instead, something **assumes** it temporarily and receives short-lived, auto-expiring credentials in return. When you ran `aws sso login`, you weren't logging into a user — you were assuming a role, which is why the ARN above says `assumed-role`. Roles are also what EC2 instances, Lambda functions, and (in section 7) GitHub Actions use to get AWS access without ever holding a static secret.
- **Policy** — a JSON document listing allowed (or explicitly denied) actions on specific resources, e.g. `{"Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::my-bucket/*"}`. A policy does nothing by itself; it only matters once attached to a user, group, or role. `AdministratorAccess` is just AWS's name for one particular, very permissive, policy.

Put together: your permission set from Step 5 *is* a policy, bundled up and attached to a role that Identity Center creates in your account on your behalf. Every `aws sso login` is you assuming that role and getting temporary credentials scoped by that policy. Once you can trace that whole chain — policy → role → temporary credentials — this checkpoint is genuinely done, not just checked off.

## 2. Billing guardrails

### Step 1 — Turn on billing alerts

Billing console → **Billing Preferences** (root user only — this setting is root-gated) → find the **Alert preferences** section → **Edit** → check **Receive CloudWatch Billing Alerts** → **Save preferences**. AWS has renamed and relocated this a few times; if a flat "Receive Billing Alerts" checkbox doesn't exist on the page you land on, it's under this Edit action. Skip it and CloudWatch's billing metrics never populate — the single most common reason someone's "billing alarm" never fires. This only matters if you set up a CloudWatch billing alarm, which section 2.4 has you skip in favor of Budgets — Budgets itself doesn't need this setting at all.

While you're on that page, also check **Receive Free Tier usage alerts** and confirm your email. This is a separate, built-in alert that fires around 85% of a free-tier limit — genuinely useful early on, when you're most likely to accidentally spin up a non-free-tier instance size without noticing.

### Step 2 — Create the budget

1. Billing console → **Budgets** → **Create budget** (button at the top of the page) → **Customize (advanced)** → **Cost budget** → **Next**.
2. Name it, e.g. `route-and-ship-monthly`.
3. **Period: Monthly**, **Budget renewal type: Recurring budget**, **Budgeting method: Fixed**. Amount: **$150** — your stated ceiling for "spend more when it's justified."
4. Under **Advanced options**, leave cost aggregation on the default (**unblended costs**) — the blended/amortized alternatives only matter once you're sharing Reserved Instance or Savings Plan commitments across multiple accounts, which doesn't apply here.
5. **Next**, then add each threshold as its own separate entry via **Add an alert threshold** — this is a repeatable action, not one form with multiple rows:
   - Threshold 1: **% of budgeted amount** = `66`, **Actual** → under that same alert's **Notification preferences**, enter your email. (66% of $150 ≈ $100, the "something's ramping up" warning.)
   - Threshold 2: **% of budgeted amount** = `100`, **Actual** → your email again — notification preferences are per-alert, so this needs its own entry, not a shared one.
   - Optional third: **% of budgeted amount** = `100`, **Forecasted** instead of Actual — fires *before* you actually overspend, based on trend, which becomes the more useful signal once you have a few weeks of data.
6. **Next** brings you to **Attach actions** — a separate, optional screen for auto-applying a deny-all policy or stopping resources at a threshold. Skip it; that's a hard stop, and you want to *know*, then decide, not have AWS decide for you.
7. Review and **Create budget**.

### Step 3 — Turn on Cost Explorer and cost allocation tags

Billing console → **Cost Explorer**. If you already created the budget in Step 2, this may already be live with real report data waiting for you — Budgets auto-enables Cost Explorer the first time you use it, so there's often no separate "Launch Cost Explorer" screen left to click by the time you get here. If you land on a **Welcome to Cost Explorer** page instead, choose **Launch Cost Explorer**. Either way: current-month data is viewable in about 24 hours, and the full 14-month history plus 12-month forecast takes a few days longer to fully populate.

This same action automatically turns on **Cost Anomaly Detection** — a free, ML-based monitor AWS sets up on your behalf, with a daily summary alert on any spend anomaly exceeding both $100 and 40% of your expected spend. That's a second, smarter tripwire layered on top of the static Budgets thresholds from Step 2, at no extra setup cost.

Now adopt a tagging convention — `Project=route-and-ship` — and actually apply it to every resource you create from this point forward: the CDK stack via `Tags.of(this).add(...)`, the `--tags` flag on `aws ecr create-repository`, and so on. Get the sequencing right here, because it's easy to get backwards: you can't pre-create or activate a cost allocation tag key in Billing before anything is tagged with it. The key only becomes available to activate once at least one real resource carries it, which itself can take up to 24 hours to surface. Once it does: Billing console → **Cost allocation tags** → select the `Project` key → **Activate**, then wait up to another 24 hours before it starts appearing in cost reports. Doing this now, when you have almost nothing deployed, costs nothing; doing it in Phase 4 when you actually need "what did the homelab save me" numbers means backfilling tags across a whole system first, then waiting out both delays before you can measure anything.

### What you're skipping on purpose

- **A separate CloudWatch billing alarm** — the older, more manual mechanism, alarming on the `EstimatedCharges` metric in `us-east-1`. Budgets does the same job with a better UI and native email alerts; only reach for the CloudWatch version if you later want a billing threshold to trigger a Lambda or SNS action beyond email.
- **A paid support plan** — Basic (free) support is fine for a learning account. Developer/Business tiers exist for when you need AWS to answer a ticket quickly, not for this.

## 3. `gateway` — Go repo scaffold

### Step 1 — Module and layout

Use your real GitHub username in the module path now — it's baked into every import statement from here on, and renaming it later means rewriting every file that imports the module.

```bash
mkdir gateway && cd gateway
go mod init github.com/<you>/gateway
mkdir -p cmd/gateway internal/hello
```

Pull the actual logic out of `main()` into a package under `internal/`. `main()` should just wire things together and stay untestable-by-design, while the logic underneath is plain functions you can unit test. It's overkill for one `println`, but the habit is what matters, not this particular function:

```bash
cat > internal/hello/hello.go <<'EOF'
package hello

func Message() string {
	return "gateway: hello"
}
EOF

cat > cmd/gateway/main.go <<'EOF'
package main

import (
	"fmt"

	"github.com/<you>/gateway/internal/hello"
)

func main() {
	fmt.Println(hello.Message())
}
EOF

go build ./... && go run ./cmd/gateway
```

You should see `gateway: hello` printed.

### Step 2 — Lint

golangci-lint shipped a v2 config schema with a breaking, incompatible format. If you're installing today, you have v2, and a v1-style flat `linters: enable: [...]` file either gets rejected or silently misparsed. Check first:

```bash
golangci-lint version
```

If it reports 2.x — true for any fresh install now — `gosimple` and `stylecheck` no longer exist as separate linters (they were folded into `staticcheck`), and v2 already ships errcheck/govet/staticcheck/unused enabled by default under its standard tier. The only thing worth adding on top is `revive`:

```bash
cat > .golangci.yml <<'EOF'
version: "2"
linters:
  enable:
    - revive
EOF

golangci-lint run
```

If you ever land on a machine still running v1, the equivalent config is the flat form: `linters: { enable: [errcheck, govet, staticcheck, unused, gosimple, revive] }`, `run: { timeout: 3m }`. And if you write a v1-style file by mistake and golangci-lint complains, `golangci-lint migrate` converts it to v2 automatically — and backs up the original — rather than you hand-editing it.

Either way, this set catches what actually bites in practice: unchecked errors, dead code, and the subtler `go vet`/`staticcheck` class of bugs, without noisier style-only linters that mostly generate busywork this early.

### Step 3 — Write a trivial table-driven test

The pattern matters more than the content here — this is the shape every Go test in this project follows from now on:

```bash
cat > internal/hello/hello_test.go <<'EOF'
package hello

import "testing"

func TestMessage(t *testing.T) {
	tests := []struct {
		name string
		want string
	}{
		{name: "returns the gateway greeting", want: "gateway: hello"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := Message(); got != tt.want {
				t.Errorf("Message() = %q, want %q", got, tt.want)
			}
		})
	}
}
EOF

go test ./... -race
```

### Step 4 — Wire CI

This is a monorepo — `gateway/` and `assistant/` are directories inside one repository, not separate repos — and GitHub Actions only ever reads workflows from `.github/workflows/` at the **repository root**. A workflow file placed at `gateway/.github/workflows/ci.yml` is invisible to GitHub; it doesn't error, it just never triggers. So step out of `gateway/` first, and write this at the top level, scoped to only fire when something under `gateway/` actually changes:

```bash
cd ..   # back to the repo root
mkdir -p .github/workflows
cat > .github/workflows/gateway-ci.yml <<'EOF'
name: gateway-ci
on:
  push:
    branches: [main]
    paths: ['gateway/**']
  pull_request:
    paths: ['gateway/**']

defaults:
  run:
    working-directory: gateway

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: gateway/go.mod
          cache: true
          cache-dependency-path: gateway/go.sum
      - run: go vet ./...
      - uses: golangci/golangci-lint-action@v6
        with:
          working-directory: gateway
          version: latest
      - run: go test ./... -race
EOF
cd gateway   # back in, for the rest of this section
```

That `version: latest` isn't decoration either. Left unset, `golangci-lint-action` falls back to whatever version was current when the action itself was last released — a binary built with an older Go toolchain than the one you're targeting. golangci-lint refuses to run against a Go version newer than the one it was built with, so on a fast-moving Go version like 1.27 you can hit `can't load config: the Go language version (go1.24) used to build golangci-lint is lower than the targeted Go version (1.27.0)` — a failure that has nothing to do with your code, and would otherwise only show up the first time CI actually runs.

`paths: ['gateway/**']` is what keeps a Python-only change from wastefully triggering a Go test run and vice versa (`assistant-ci.yml` gets the matching `assistant/**` filter in section 4). `defaults.run.working-directory: gateway` scopes the plain `run:` steps to the right subdirectory — but it doesn't affect action *inputs*, which is why `go-version-file` and `cache-dependency-path` still need the `gateway/` prefix spelled out explicitly.

`go-version-file: go.mod` reads the version straight from your module file instead of a number hardcoded in the workflow — bump Go later and this stays correct on its own, rather than silently testing against whatever you originally typed here.

### Step 5 — Write the Dockerfile

Build with `CGO_ENABLED=0` so the binary is statically linked. Without it, a `scratch` or `distroless` runtime image has no libc to satisfy the dynamic linker, and the container fails at startup with a cryptic "exec format" or "no such file" error — not an obviously-Go error. Match the `golang:` build-stage tag to your actual `go.mod` version (`go version` / `head go.mod`), and the `distroless/static-debian*` tag to whatever's current; both drift over time, so treat the versions below as illustrative for a `go 1.27` module, not something to copy verbatim forever:

```dockerfile
FROM golang:1.27 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian13
COPY --from=build /gateway /gateway
ENTRYPOINT ["/gateway"]
```

You'll build and push this image to ECR in section 6.

## 4. `assistant` — Python repo scaffold

### Step 1 — Initialize the project

Pin the Python version to match the Lambda runtime you'll target in section 5 (`PYTHON_3_14`). A mismatch here is the kind of bug that only shows up as a cryptic failure on deploy, not locally — `aws-lambda-python-alpha`'s bundler installs your dependencies inside a container matching the Lambda runtime, and a package resolved against a newer Python can fail to install there at all. Lambda has supported Python 3.14 since November 2025, including via CDK, so pinning to it is safe rather than aspirational.

Use `uv python pin`, not a hand-written `.python-version` file — writing the file yourself doesn't stop `uv init` from overwriting it with whatever interpreter it finds first, which is exactly the failure mode to avoid:

```bash
mkdir assistant && cd assistant
uv python pin 3.14
uv init --package .
uv add fastapi mangum boto3
uv add --dev ruff mypy pytest
```

Now verify the pin actually held — `uv init` and `uv add` have both been known to quietly re-pin to whatever Python they find, which defeats the whole point:

```bash
cat .python-version                    # must read 3.14
grep requires-python pyproject.toml    # must be >=3.14, not something else
```

If either doesn't match, fix it directly rather than re-running init:

```bash
uv python pin 3.14
# then edit pyproject.toml's requires-python to ">=3.14" by hand if uv didn't update it
uv sync
```

`uv sync` after fixing the pin re-resolves `uv.lock` against 3.14 — skip this and stale lock entries resolved under the wrong interpreter won't surface as an error until you try to install them somewhere that only has 3.14.

Finally, confirm the src-layout landed: `src/assistant/__init__.py` should exist, with `pyproject.toml` at the root.

### Step 2 — Configure lint and type-checking

```bash
cat >> pyproject.toml <<'EOF'

[tool.ruff]
line-length = 100
target-version = "py314"
extend-exclude = ["infra"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mypy]
python_version = "3.14"
strict = true
EOF
```

`strict = true` on mypy feels aggressive for a hello-world, but it's much cheaper to start strict than retrofit strictness onto a codebase that's already grown a hundred untyped functions by Phase 3. It does mean the handler in section 5 needs real type annotations, not bare `def handler(event, context):` — `strict` rejects an unannotated function outright.

The other two settings exist specifically because of where `infra/` sits. `extend-exclude = ["infra"]` keeps `ruff check .` from also scanning the CDK app — without it, a bare `.` picks up `infra/`'s own imports, its CDK-generated boilerplate test, and whatever pip-managed style choices don't match this project's config, none of which have anything to do with the `assistant` package. `testpaths = ["tests"]` does the same job for pytest: without it, a bare `pytest` invocation from `assistant/` also tries to collect `infra/tests/`, and if that boilerplate test still imports a stack class you've since renamed (section 5 has you do exactly that), you get a collection error that has nothing to do with your actual code.

### Step 3 — Write a trivial test

```bash
mkdir -p tests
cat > tests/test_placeholder.py <<'EOF'
def test_placeholder() -> None:
    assert 1 + 1 == 2
EOF

uv run pytest
```

### Step 4 — Wire CI

Same monorepo rule as section 3: this has to land in `.github/workflows/` at the repo root, not inside `assistant/`, or GitHub never sees it.

```bash
cd ..   # back to the repo root
mkdir -p .github/workflows
cat > .github/workflows/assistant-ci.yml <<'EOF'
name: assistant-ci
on:
  push:
    branches: [main]
    paths: ['assistant/**']
  pull_request:
    paths: ['assistant/**']

defaults:
  run:
    working-directory: assistant

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run mypy src
      - run: uv run pytest
EOF
cd assistant   # back in, for the rest of this section
```

Notice this workflow never states a Python version anywhere — unlike Go's CI in section 3, it doesn't need to. `uv sync` reads your project's own pin (`.python-version` / `requires-python`) and provisions the matching interpreter itself, so this file stays correct automatically as you bump versions, the same way `go-version-file` does for Go. The `paths: ['assistant/**']` filter is what stops a Go-only commit from triggering a pointless Python test run.

## 5. `assistant` hello-world → Lambda + API Gateway (CDK)

Introducing CDK here, rather than waiting for Phase 3, is deliberate: Phase 3 upgrades this same tool to manage the *whole* stack, so it's worth already being comfortable with it on something trivial.

### Step 1 — Write the handler

This is the file the CDK stack in Step 2 points at — it doesn't exist yet, since section 4 only scaffolded the package skeleton.

```bash
cat > src/assistant/app.py <<'EOF'
from typing import Any


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return {"statusCode": 200, "body": "assistant: hello"}
EOF
```

No FastAPI/Mangum wiring yet — a bare Lambda handler function is all this phase needs. That comes in Phase 1, once there's an actual API surface worth routing. The type annotations aren't decoration: section 4's mypy config runs in `strict` mode, which rejects an unannotated `def handler(event, context):` outright — `Any` is the honest type here anyway, since a Lambda event's shape depends entirely on what triggered it.

### Step 2 — Set up the CDK app

`cdk init` creates its own Python project with its **own** virtual environment, managed by `pip`, separate from the `uv`-managed one for the `assistant` package itself. That's expected, not a mistake — the infra code (CDK, which generates CloudFormation) and the application code (the Lambda handler) are two different Python projects that happen to live in the same repo.

```bash
mkdir infra && cd infra
cdk init app --language python
source .venv/bin/activate
pip install aws-cdk-lib constructs
```

**This is the part worth reading slowly, because it answers exactly where your stack code goes.** `cdk init` names the generated stack file after the current directory. Since that directory is `infra`, you end up with a nested `infra/infra/` structure — the outer `infra/` is the CDK project root you just `cd`'ed into, and the inner `infra/` is a Python package of the same name:

```
infra/                       ← you are here (CDK project root)
├── app.py                   ← CDK entry point — what cdk.json actually invokes
├── cdk.json
├── requirements.txt
├── infra/                   ← generated package, same name as the folder above
│   ├── __init__.py
│   └── infra_stack.py       ← THE FILE — contains class InfraStack
└── tests/
```

So: **the stack code goes in `infra/infra_stack.py`**, relative to the CDK project root you're currently in. Before pasting the stack in, rename both the file and the class so it doesn't stay called `InfraStack` for a stack that's actually named `assistant`:

```bash
mv infra/infra_stack.py infra/assistant_stack.py
sed -i '' 's/InfraStack/AssistantStack/g' infra/assistant_stack.py   # macOS sed; drop the '' on Linux
```

Now open `infra/assistant_stack.py` and replace its contents with a minimal stack — one Lambda behind an HTTP API:

```python
from aws_cdk import Stack, CfnOutput, aws_lambda as _lambda, aws_apigatewayv2 as apigwv2
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct

class AssistantStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        fn = _lambda.Function(
            self, "AssistantFn",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="app.handler",
            code=_lambda.Code.from_asset("../dist"),
        )

        api = apigwv2.HttpApi(
            self, "AssistantApi",
            default_integration=HttpLambdaIntegration("AssistantIntegration", fn),
        )

        CfnOutput(self, "ApiUrl", value=api.url)
```

`from_asset` only zips whatever's in the given directory — it won't install `fastapi`/`mangum` from PyPI into that zip. Once you go past hello-world, switch to `aws-cdk.aws-lambda-python-alpha`'s `PythonFunction` construct instead; it bundles dependencies via a Docker build for you. For this phase's dependency-free handler, plain `from_asset` is fine — you just have to actually produce the `../dist` directory it points at, which CDK won't do for you (that's Step 3).

Last piece for this step: `cdk init` also generated a root `app.py` that instantiates whatever stack class it scaffolded. Since you just renamed that class, open `infra/app.py` (the outer one, the CDK entry point) and replace it to match:

```python
#!/usr/bin/env python3
import aws_cdk as cdk

from infra.assistant_stack import AssistantStack

app = cdk.App()
AssistantStack(app, "AssistantStack")
app.synth()
```

### Step 3 — Package and deploy

If it's been a while since section 1's `aws sso login`, refresh it first — `cdk deploy` fails before it ever reaches CloudFormation if your session token has expired, which reads as a confusing local error rather than anything AWS-side:

```bash
aws sso login --profile route-and-ship
```

Then, still inside `infra/`:

```bash
mkdir -p ../dist
cp ../src/assistant/app.py ../dist/

cdk bootstrap   # once per account/region
cdk deploy
```

`cdk bootstrap` provisions the handful of resources every CDK deployment needs regardless of what you're actually deploying: an S3 bucket for staging assets (like the zip `from_asset` just created) and a few IAM roles CDK uses to perform the deployment itself. It's idempotent — safe to run again — and a one-time cost per account/region combination; the S3 bucket sits at a few cents a month for this little data, well inside your budget.

When `cdk deploy` finishes, it prints an `ApiUrl`. Hit it and confirm you get back `assistant: hello`.

## 6. `gateway` hello-world → one Fargate run

The goal here is just proving the container runs on Fargate — a real service with a load balancer is Phase 2's job.

### Step 1 — Build and push

```bash
aws ecr create-repository --repository-name gateway
aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com

docker build -t gateway .
docker tag gateway:latest <account>.dkr.ecr.<region>.amazonaws.com/gateway:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/gateway:latest
```

### Step 2 — Create the execution role

ECS draws a distinction Kubernetes doesn't: a **task execution role** (what ECS itself uses to pull your image from ECR and write logs to CloudWatch, on the task's behalf, before your code even starts) versus a **task role** (what your *running code* uses to call AWS APIs — the equivalent of a K8s service account). Skip the execution role and the task fails immediately with `CannotPullContainerError`, a genuinely confusing error for a hello-world, since it has nothing to do with your image.

```bash
aws iam create-role --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

### Step 3 — Register and run the task

Create the cluster:

```bash
aws ecs create-cluster --cluster-name route-and-ship
```

Pre-create the log group by hand rather than relying on the task to create it for you:

```bash
aws logs create-log-group --log-group-name /ecs/gateway --region <region>
```

This matters more than it looks like it should. `AmazonECSTaskExecutionRolePolicy` — the managed policy you attached in Step 2 — only grants `logs:CreateLogStream` and `logs:PutLogEvents`. It does **not** grant `logs:CreateLogGroup`. If the task definition's log config sets `"awslogs-create-group": "true"` and the group doesn't already exist, the task fails before your container ever runs, with `ResourceInitializationError: ... AccessDeniedException: ... not authorized to perform: logs:CreateLogGroup`. Creating the group up front sidesteps the gap entirely — the permissions you already have are enough once the group exists.

If you're on Apple Silicon (or any arm64 machine), there's a second, unrelated trap waiting right after: `docker build` produces an image matching your host architecture by default, but Fargate defaults to x86_64 when a task definition doesn't say otherwise. Deploy an arm64 image onto an x86_64 task and the container starts, then immediately dies with `exec /gateway: exec format error` — a kernel-level "wrong architecture" error, easy to mistake for something wrong with the binary itself. Tell the task definition explicitly what it's running via `runtimePlatform` — this also means no cross-compilation, and ARM64 Fargate is typically cheaper per second than x86_64:

```bash
cat > task-def.json <<EOF
{
  "family": "gateway",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "runtimePlatform": {
    "cpuArchitecture": "ARM64",
    "operatingSystemFamily": "LINUX"
  },
  "executionRoleArn": "arn:aws:iam::<account>:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "gateway",
      "image": "<account>.dkr.ecr.<region>.amazonaws.com/gateway:latest",
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/gateway",
          "awslogs-region": "<region>",
          "awslogs-stream-prefix": "gateway"
        }
      }
    }
  ]
}
EOF

aws ecs register-task-definition --cli-input-json file://task-def.json
```

If you're on an x86_64/Intel machine instead, drop the `runtimePlatform` block entirely — the Fargate default already matches what `docker build` produces for you.

`networkMode: awsvpc` is mandatory for Fargate — it's the only mode Fargate supports. Every task gets its own elastic network interface, which is why `run-task` below needs an explicit subnet and security group, the same way a K8s pod on a CNI network does. `cpu`/`memory` are strings, and Fargate only accepts specific paired combinations; 256 CPU units (0.25 vCPU) with 512 MB memory is the smallest valid pairing, comfortably enough for `println`.

Grab a subnet and security group from your account's default VPC (`aws ec2 describe-subnets`, `aws ec2 describe-security-groups`), then run the task:

```bash
aws ecs run-task \
  --cluster route-and-ship \
  --launch-type FARGATE \
  --task-definition gateway \
  --network-configuration "awsvpcConfiguration={subnets=[<subnet-id>],securityGroups=[<sg-id>],assignPublicIp=ENABLED}"
```

`assignPublicIp=ENABLED` matters specifically because default-VPC subnets are public, but a task still needs a public IP of its own to reach the internet — and therefore ECR — unless you've set up a NAT gateway or VPC endpoints, neither of which exists yet.

Check CloudWatch Logs (log group `/ecs/gateway`) for the task and confirm `gateway: hello` showed up. Then stop the task — `aws ecs stop-task --cluster route-and-ship --task <task-arn>` — since Fargate bills per vCPU/memory-second while a task is running and there's no reason to leave this one up.

## 7. Wire continuous deployment

### Step 1 — Create the OIDC provider and role

The goal: GitHub Actions gets temporary AWS credentials scoped to this specific repo and branch, minted fresh on every run — never a long-lived access key sitting in repo secrets waiting to leak.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

That thumbprint is GitHub's OIDC root CA thumbprint — a fixed, well-known value for this specific provider, not something to compute yourself.

Now the trust policy — the part that actually enforces "only this repo, only this branch." The `sub` condition below rejects an assume-role attempt from anywhere else, including a PR from a fork or a different branch. Since `gateway` and `assistant` are directories in one monorepo rather than separate repos, `<you>/<repo>` here is your one actual GitHub repo (e.g. `<you>/learn-aws`) — **not** `<you>/gateway` — and this whole step runs once, not once per project:

```bash
cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:<you>/<repo>:ref:refs/heads/main"
        }
      }
    }
  ]
}
EOF

aws iam create-role --role-name github-actions-deploy \
  --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy --role-name github-actions-deploy \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

Double-check the `sub` value actually landed correctly — a literal, un-substituted `<you>/<repo>` in a pasted command is an easy mistake to make and won't error at creation time, only later, opaquely, when a real deploy tries to assume the role and gets `AccessDenied`.

`AdministratorAccess` on the CI role is the same "start broad, narrow in Phase 4" call as section 1's permission set — the real fix isn't guessing a narrower policy today, it's remembering to actually do the audit later. One role is enough for both `gateway-ci.yml` and `assistant-ci.yml` — they're workflows in the same repo, so they share the same `sub` claim; there's nothing to repeat here.

### Step 2 — Add the deploy job to CI

Both files need a `deploy` job appended, but the two projects deploy in genuinely different ways, so each gets its own — not one shared snippet copy-pasted twice. `permissions: id-token: write` is required in both: without it, the workflow can't request the OIDC token in the first place, and `configure-aws-credentials` fails with an opaque permissions error that has nothing to do with AWS.

**`assistant-ci.yml`** — this is the CDK deploy from section 5, run non-interactively. Two things worth noticing: the job needs its own Python *and* Node.js (CDK's CLI is Node-based, same as when you ran it by hand), and the `cdk deploy` step overrides `working-directory` to `assistant/infra` explicitly — a step-level `working-directory` **replaces** the job's `defaults.run.working-directory: assistant` rather than nesting inside it, so it needs the full path from the repo root, not just `infra`:

```yaml
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::<account>:role/github-actions-deploy
          aws-region: us-east-1
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - uses: actions/setup-node@v4
        with:
          node-version: '24'
      - run: npm install -g aws-cdk
      - name: Package handler into dist/
        run: |
          mkdir -p dist
          cp src/assistant/app.py dist/
      - name: cdk deploy
        working-directory: assistant/infra
        run: |
          pip install -r requirements.txt
          cdk deploy --require-approval never
```

**`gateway-ci.yml`** — this is where the earlier architecture fix actually matters. GitHub's hosted `ubuntu-latest` runners are **x86_64**, but the gateway's task definition is pinned to `ARM64` to match Apple Silicon builds. A plain `docker build` here would produce an x86_64 image and reproduce the exact `exec format error` from section 6 — just built in CI instead of on your laptop. `docker/setup-qemu-action` plus `platforms: linux/arm64` cross-compiles the image on the x86_64 runner so it matches the task definition regardless of who's building it:

```yaml
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::<account>:role/github-actions-deploy
          aws-region: us-east-1
      - id: ecr-login
        uses: aws-actions/amazon-ecr-login@v2
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: gateway
          platforms: linux/arm64
          push: true
          tags: ${{ steps.ecr-login.outputs.registry }}/gateway:latest
      - name: Run task on Fargate
        run: |
          aws ecs run-task \
            --cluster route-and-ship \
            --launch-type FARGATE \
            --task-definition gateway \
            --network-configuration "awsvpcConfiguration={subnets=[<subnet-id>],securityGroups=[<sg-id>],assignPublicIp=ENABLED}"
```

Note this still runs the same one-off task from section 6, not a persistent service — that upgrade is Phase 2's job. For now, "redeploy" honestly means "run the hello-world task again," which is exactly what this phase's checkpoint asks for: a push to `main` that reaches AWS with no console clicks, nothing more.

Finish by making a trivial change, pushing to `main`, and watching both actually redeploy with zero manual steps.

---

## Checkpoints — clear all three before moving to Phase 1

- [ ] I can explain IAM user vs. role vs. policy without looking it up.
- [ ] A `git push` to `main` redeploys something to AWS — no console clicks.
- [ ] Both repos run tests in CI, even trivial ones.

If any of these feel shaky, stay here. Phase 1 assumes this plumbing is boring and reliable.

## Resources

- [AWS Skill Builder](https://skillbuilder.aws/) — free "Getting Started as a Builder" track
- [IAM best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS CDK v2 developer guide](https://docs.aws.amazon.com/cdk/v2/guide/home.html)
- [Amazon ECS task execution IAM role](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-execution-IAM-role.html) — the execution-role-vs-task-role distinction from section 6
- [Configuring OpenID Connect in Amazon Web Services](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services) — GitHub's own guide to the section 7 trust policy pattern
- [Effective Go](https://go.dev/doc/effective_go)
- *100 Go Mistakes and How to Avoid Them* (Teiva Harsanyi, Manning) — chapters 1–2

## Next

Once every checkpoint above is checked → Phase 1: core AWS dev services + SRE Copilot v0.
