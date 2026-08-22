# Phase 01 — IAM basics (detailed plan)

Goal: learn identity and policy structure hands-on, against real deny/allow behavior — every later phase hands you a "which role does this need" decision, so this is worth doing carefully rather than skimming. Budget: 8–10 hrs.

Prerequisite: phase 00 complete — Floci running, `smoke-test.sh` passing clean.

```bash
source .env
./smoke-test.sh
```

---

## 1.1 — Mental model, before touching the CLI (~30 min)

| You know | → | AWS equivalent |
|---|---|---|
| ServiceAccount | → | IAM Role |
| Role / RoleBinding (namespace-scoped) | → | IAM Policy (account-wide unless scoped by `Resource`) |
| ServiceAccount token exchange | → | STS `AssumeRole` |

A policy document has four moving parts: `Effect` (`Allow`/`Deny`), `Action` (the API calls it covers, e.g. `s3:GetObject`), `Resource` (which ARNs it covers), and optionally `Condition`. Two rules that don't have a Kubernetes analogue and will trip you up if you don't internalize them now:

- **Default is deny.** If no statement matches a request, it's denied — there's no implicit allow the way an unrestricted RBAC setup might feel.
- **Explicit `Deny` always wins**, regardless of any `Allow` elsewhere, even a broader one. You'll prove this in 1.4.

**The structural gotcha vs RBAC:** IAM splits *who can assume this identity* (the role's **trust policy** — a resource-based policy attached to the role itself) from *what the identity can do* (**permission policies** attached to the role). A Kubernetes RoleBinding has no equivalent split — binding a ServiceAccount to a Role just grants it, full stop. In IAM, creating a role and attaching permissions to it is only half the job; something also has to be allowed to assume it.

## 1.2 — Set up two buckets and a role, scoped to only one of them (~2 hrs)

```bash
source .env
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account: $ACCOUNT_ID"

BUCKET_A="iam-basics-a-$(date +%s)"
BUCKET_B="iam-basics-b-$(date +%s)"
aws s3 mb "s3://$BUCKET_A"
aws s3 mb "s3://$BUCKET_B"
echo "hello a" | aws s3 cp - "s3://$BUCKET_A/hello.txt"
echo "hello b" | aws s3 cp - "s3://$BUCKET_B/hello.txt"
```

Trust policy — who can assume this role. For a self-contained local exercise, trust your own account root:

```bash
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::${ACCOUNT_ID}:root" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role \
  --role-name iam-basics-role \
  --assume-role-policy-document file:///tmp/trust-policy.json
```

Permission policy — scoped to `BUCKET_A` only:

```bash
cat > /tmp/bucket-a-only.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::${BUCKET_A}",
      "arn:aws:s3:::${BUCKET_A}/*"
    ]
  }]
}
EOF

aws iam put-role-policy \
  --role-name iam-basics-role \
  --policy-name bucket-a-only \
  --policy-document file:///tmp/bucket-a-only.json
```

Assume the role and capture temporary credentials:

```bash
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/iam-basics-role"
CREDS=$(aws sts assume-role --role-arn "$ROLE_ARN" --role-session-name iam-basics-test)
AK=$(echo "$CREDS" | jq -r '.Credentials.AccessKeyId')
SK=$(echo "$CREDS" | jq -r '.Credentials.SecretAccessKey')
ST=$(echo "$CREDS" | jq -r '.Credentials.SessionToken')
```

Note the pattern below: prefixing a single command with `AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=...` overrides credentials for *just that command*, so your shell's `AWS_PROFILE=floci` from `.env` is untouched and you don't have to remember to switch back.

```bash
echo "-- assumed role -> bucket A (expect: succeeds) --"
AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="$ST" \
  aws s3 ls "s3://$BUCKET_A"

echo "-- assumed role -> bucket B (expect: AccessDenied) --"
AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="$ST" \
  aws s3 ls "s3://$BUCKET_B"
```

If the second call succeeds instead of failing, don't assume you misconfigured the policy — jump to the Floci gap note below before you start debugging JSON syntax.

## 1.3 — Broaden the policy and confirm the difference (~30–45 min)

```bash
cat > /tmp/bucket-wildcard.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
    "Resource": "*"
  }]
}
EOF

aws iam put-role-policy \
  --role-name iam-basics-role \
  --policy-name bucket-a-only \
  --policy-document file:///tmp/bucket-wildcard.json
```

Re-run the same `aws s3 ls "s3://$BUCKET_B"` call from 1.2 with the same temporary credentials (they're still valid — assumed-role credentials are short-lived but this session won't expire mid-exercise). It should now succeed. This is the concrete version of "IAM policy is account-wide unless you scope `Resource`" — nothing about the *role* changed, only what its attached policy's `Resource` field covers.

## 1.4 — Explicit Deny beats Allow (~45 min)

Add a second statement denying deletes, on top of the wildcard allow from 1.3:

```bash
cat > /tmp/bucket-wildcard-with-deny.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": "*"
    },
    {
      "Effect": "Deny",
      "Action": "s3:DeleteObject",
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name iam-basics-role \
  --policy-name bucket-a-only \
  --policy-document file:///tmp/bucket-wildcard-with-deny.json
```

```bash
echo "-- assumed role -> put object in bucket B (expect: succeeds, Allow covers it) --"
AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="$ST" \
  aws s3 cp /tmp/trust-policy.json "s3://$BUCKET_B/scratch.json"

echo "-- assumed role -> delete that object (on real AWS: AccessDenied, explicit Deny wins) --"
AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="$ST" \
  aws s3 rm "s3://$BUCKET_B/scratch.json"
```

**Confirmed Floci gap, not a hypothetical:** on real AWS, the delete above fails — `Deny` isn't "lower priority than Allow," it's absolute, and there's no k8s RBAC equivalent to reach for; this has to be rebuilt as its own mental model. On Floci as tested for this roadmap, the delete **succeeds anyway** — the explicit `Deny` statement is accepted into the policy document but not enforced against `s3:DeleteObject`. This is the concrete instance of the enforcement-gap warning below, not just a caveat to keep in mind: don't treat a successful delete here as "my policy JSON is wrong." The policy is correct; Floci just isn't the thing to trust for this specific case. Re-verify explicit-Deny behavior against a real AWS account before relying on it in a production policy.

## 1.5 — Users vs roles, briefly (~15 min, no hands-on required)

Roles are the default for anything a service assumes on your behalf — a Lambda execution role, an ECS task role, the role you just built above. Long-lived IAM **users** with static access keys are mostly a legacy/CI-credential pattern now (or a break-glass human login), not how workloads authenticate to each other. You won't create an IAM user anywhere else in this roadmap — every later phase's compute (ECS, EKS, Lambda) gets its permissions via a role, not a user, so it's worth not building the habit here even though `aws iam create-user` would work fine against Floci.

## 1.6 — Cleanup (~10 min)

```bash
aws iam delete-role-policy --role-name iam-basics-role --policy-name bucket-a-only
aws iam delete-role --role-name iam-basics-role
aws s3 rm "s3://$BUCKET_A" --recursive && aws s3 rb "s3://$BUCKET_A"
aws s3 rm "s3://$BUCKET_B" --recursive && aws s3 rb "s3://$BUCKET_B"
```

Leaving Floci's IAM/S3 state clean matters more here than it will later — phase 02 rewrites this exact role+policy as Terraform, and a leftover role with the same name will make `terraform plan` show a confusing diff against something you created by hand.

---

## Stretch goal (optional): cross-account AssumeRole

Phase 00 set up two isolated "accounts" in Floci via 12-digit `AWS_ACCESS_KEY_ID` values (`floci-a` / `floci-b` profiles) and promised you'd use them for a cross-account IAM exercise — this is that exercise. Skip it on a first pass if you're already at the 10-hour mark; it's valuable but not load-bearing for phase 02.

```bash
ACCOUNT_A=$(AWS_PROFILE=floci-a aws sts get-caller-identity --query Account --output text)
ACCOUNT_B=$(AWS_PROFILE=floci-b aws sts get-caller-identity --query Account --output text)
```

In account B, create a role whose trust policy names account A as principal (instead of its own account root):

```bash
cat > /tmp/cross-account-trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::${ACCOUNT_A}:root" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

AWS_PROFILE=floci-b aws iam create-role \
  --role-name cross-account-role \
  --assume-role-policy-document file:///tmp/cross-account-trust.json
```

Then, as account A, assume it — same `sts assume-role` call as 1.2, just with a role ARN in account B and the `floci-a` profile's credentials. This is structurally the same pattern real cross-account access uses (e.g. a CI pipeline in one account deploying into another, or a central logging account reading from workload accounts) — the only thing that changed from 1.2 is whose account root the trust policy names.

---

## Floci gap: don't trust silence as proof

Floci's IAM is implemented in-process across most services, but the project's own documentation notes coverage isn't uniform — some services accept a policy but treat it as inert rather than enforcing it. **Confirmed while building this exercise:** explicit `Deny` on `s3:DeleteObject` (1.4) is accepted into the policy document without error but not actually enforced — the delete succeeds regardless. If 1.2's "expect: AccessDenied" call for the scoped-down role also unexpectedly succeeded, treat that as the same class of gap, not a mistake in your JSON.

What's actually safe to trust from Floci, updated for what we now know:

- **Allow-based scoping** (1.3: broadening `Resource` and watching previously-denied access start working) is a trustworthy positive signal — it's a pure Allow test with no Deny statement involved.
- **Any test whose expected outcome is a denial** — a scoped-down Allow blocking an out-of-scope resource (1.2), or an explicit Deny blocking an otherwise-allowed action (1.4) — is not reliable on Floci as tested here. Write and reason about deny-shaped policies with confidence; just don't use Floci's response as proof they're enforced. Re-verify against a real AWS account before trusting a production policy that depends on either pattern — the same caution the roadmap flags again at phases 07 and 09.

## Exit criteria for phase 01

- [ ] Built a role scoped to one bucket and (either confirmed the second bucket was denied, or confirmed Floci let it through and understood why per the gap note above)
- [ ] Broadened the policy and watched previously-denied access start working
- [ ] Ran the explicit-Deny-vs-Allow test and can explain the result either way — enforced (real-AWS parity) or not (confirmed Floci gap, see note above) — without treating either outcome as a policy-writing mistake
- [ ] Can explain the trust-policy vs permission-policy split out loud, without looking it up
- [ ] Cleaned up the role, policies, and buckets
- [ ] (Optional) completed the cross-account stretch goal

## Notes for phase 02

Phase 02 (IaC bootstrap) rewrites this exact role + policy as a Terraform module — keep `/tmp/bucket-wildcard-with-deny.json`'s statements in mind, since translating them into `aws_iam_role`/`aws_iam_role_policy` resources is the first real Terraform exercise in the roadmap.
