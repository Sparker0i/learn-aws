# AWS via Floci — a developer-first roadmap

Practiced entirely locally against [Floci](https://floci.io) (`floci/floci:latest`, port 4566) — no AWS account, no bill. Sequenced for a developer with hands-on Docker, Kubernetes, MySQL, MongoDB, and Kafka experience, aiming for AWS job/role readiness rather than exam prep.

**Total effort:** ~115–145 hours, roughly 8–13 weeks at 10–15 hrs/week.

## Why this order (not ops-first, not exam-first)

- **IAM (01) comes first** because every service call needs it, but **IaC (02)** is pulled up right after IAM instead of tacked on at the end — every phase from 3 onward is built as Terraform/CDK, not retrofitted later.
- **Compute (03)** comes before storage/databases/messaging because ECS and EKS are the shortest path from what you already know (containers, Kubernetes) to a working deployment — that early win anchors everything else.
- **Networking/VPC (07)** is deliberately placed *after* compute and data, once you have real resources that need to talk to each other, so subnets and security groups have something concrete to attach to. It's also the phase with the weakest Floci fidelity — see its gap note below.

Day 0 setup:

```bash
docker run --rm -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock floci/floci:latest
```

Endpoint: `http://localhost:4566` · credentials: any non-empty values (e.g. `test`/`test`) · region: any, defaults to `us-east-1`.

---

## 00 · Orientation & Floci setup — 5–8 hrs

Get the emulator running and build the account/region/ARN mental model before touching any service.

| You know | → | AWS equivalent |
|---|---|---|
| Docker Hub namespace / registry | → | AWS account (12-digit ID) |
| kubeconfig cluster/context | → | Region + endpoint + credentials profile |
| Resource UID | → | ARN (`arn:aws:s3:::bucket-name`) |

- Install Floci via Docker, mounting the Docker socket — Floci needs it to spin up real containers for Lambda, RDS, ECS, EKS, MSK, ElastiCache.
- Configure an AWS CLI profile: any non-empty `aws_access_key_id`/`aws_secret_access_key` and `endpoint_url=http://localhost:4566`.
- Run `aws sts get-caller-identity` and `aws s3 ls` to confirm the wire protocol round-trip works before building anything.

**Floci-specific tip:** a 12-digit `AWS_ACCESS_KEY_ID` gives you isolated "accounts" inside one Floci instance — useful later for practicing cross-account IAM roles, a real job-relevant pattern most tutorials skip.

**Apple Silicon note:** Floci's own image is multi-arch, but the containers it launches on your behalf (Postgres/MySQL for RDS, k3s for EKS, Kafka for MSK) need arm64 pulls. Most official images have them — expect a slower first pull per service and check `docker logs` if a container never reports healthy.

**Hands-on:** stand up Floci, wire the CLI profile, and script a 5-line smoke test (`sts get-caller-identity` → create a bucket → list it → delete it) you'll reuse to sanity-check every future phase.

---

## 01 · IAM basics — 8–10 hrs

Learn identity and policy structure now — every later phase hands you a "which role does this need" decision.

| You know | → | AWS equivalent |
|---|---|---|
| ServiceAccount | → | IAM Role |
| Role / RoleBinding (namespace-scoped) | → | IAM Policy (account-wide unless scoped by Resource) |
| ServiceAccount token exchange | → | STS `AssumeRole` |

- Policy JSON: `Effect` / `Action` / `Resource` / `Condition`. Explicit `Deny` always beats `Allow`, and with no matching statement the default is deny.
- **The structural gotcha vs RBAC:** IAM splits *who can assume this identity* (trust policy, attached to the role) from *what the identity can do* (permission policy). K8s RBAC has no equivalent split — a RoleBinding just grants; it doesn't separately gate who can "become" that identity.
- Users vs roles: roles are the default for anything a service assumes (Lambda execution role, ECS task role); long-lived IAM users are mostly a legacy/CI-credential pattern now, not how workloads authenticate.

**Hands-on:** create a role + policy scoped to one S3 bucket only, assume it via `sts assume-role`, confirm calls against a second bucket are denied. Then broaden the policy to `Resource: "*"` and confirm the difference.

**Floci gap:** IAM enforcement is implemented in-process across most services, but coverage isn't uniform — the project's own docs note some services accept a policy but treat it as inert. Don't treat "Floci let it through" as proof a policy is correctly scoped; re-check anything security-sensitive against a real AWS account.

---

## 02 · IaC bootstrap: Terraform (+ CDK exposure) — 10–12 hrs

Pick a primary tool before you build anything else, so phases 3–9 are written as code from the start.

- **Recommendation:** learn Terraform as your primary tool — more portable, more commonly required outside AWS-native shops. Do 2–3 small exercises in CDK (TypeScript or Python) purely for exposure, since AWS-heavy shops lean CDK/CloudFormation.
- Point either tool at Floci the same way as the CLI: custom `endpoints` block (Terraform's `aws` provider) or environment overrides, dummy credentials, any region.
- State file mental model: Terraform state is the one thing with no k8s analogue — it's a separate source of truth Kubernetes doesn't need because the cluster itself is always the truth. Losing/corrupting state is a real, distinct failure mode to internalize now.

**Hands-on:** rewrite phase 01's IAM role + policy as a Terraform module against Floci. `plan`/`apply`/`destroy` it a few times until the state lifecycle feels boring.

> From here on, every "hands-on" step means: write it in Terraform (or CDK), apply against Floci, verify with the CLI — not console clicking.

---

## 03 · Compute: ECS, EKS, Lambda — 14–18 hrs

Your fastest on-ramp — two of these three map almost directly onto Docker/Kubernetes.

| You know | → | AWS equivalent |
|---|---|---|
| Deployment | → | ECS Service |
| Pod template | → | ECS Task Definition |
| Self-managed / managed node pool | → | EC2 vs Fargate launch type |
| EKS cluster (managed control plane) | → | Same — kubectl/Helm work unchanged |
| CronJob / short-lived job | → | Lambda (closest, imperfect analogue) |

- **ECS first** — deploy a small containerized API you already know as an ECS task + service. Floci backs this with a real Docker container.
- **EKS second** — Floci provisions a real k3s cluster per EKS cluster, so this is where your existing kubectl/Helm muscle memory transfers almost directly.
- **Lambda third** — write a function with an execution role (ties back to 01), triggered by an event source you'll build in phases 04–06.

**Naming collision:** "ECS Service" and "Kubernetes Service" are not the same concept. ECS Service ≈ your Deployment (desired count, rolling updates). Service discovery/networking for ECS is a separate concern (Cloud Map or an ALB), closer to k8s's actual Service object.

**IRSA, foreshadowed:** on real EKS, pods authenticate to AWS APIs via IAM Roles for Service Accounts (IRSA) — a k8s ServiceAccount annotated to assume an IAM role via STS, replacing plain RBAC for anything that needs to call S3/DynamoDB from inside a pod.

**Floci gap:** Lambda, ECS, and EKS are all real-Docker/real-k3s backed — the highest-fidelity part of this roadmap. Not testable locally: real Fargate's per-task network isolation, EKS control-plane cost/availability behavior, and Lambda's actual cold-start profile under concurrency.

**Hands-on:** one app, three deploys — same containerized API to ECS, then to EKS-on-Floci via kubectl, then peel off one endpoint into a standalone Lambda behind API Gateway. Compare the three workflows.

---

## 04 · Storage: S3 — 6–8 hrs

Object storage as a distinct primitive — not a filesystem, not a PVC.

- Keys, not paths: S3 has no real directories — the console's "folder" view is just `/`-delimited key prefixes. No partial-file write or append; every `PutObject` replaces the whole object.
- Presigned URLs as the standard way to let a client upload/download directly without routing bytes through your app.
- Event notifications: S3 can invoke Lambda directly on object creation — wire this to the Lambda from phase 03.

**Hands-on:** bucket with a presigned-upload flow, plus an S3 event notification that fires the Lambda from phase 03 on every new object.

**Floci gap:** S3 in Floci is an in-process, protocol-compliant emulation (not a real object store under the hood) — core operations and event notifications should behave correctly, but don't use it to validate performance characteristics (multipart upload throughput, request-rate scaling).

---

## 05 · Databases: RDS & DynamoDB — 14–16 hrs

One database you'll recognize immediately, one that asks you to unlearn Mongo habits.

| You know | → | AWS equivalent |
|---|---|---|
| Self-hosted MySQL/Postgres | → | RDS (managed, same engine) |
| MongoDB flexible queries | → | DynamoDB — access patterns fixed at design time |

- **RDS** should feel closest to home — Floci backs it with a real Postgres/MySQL/MariaDB engine. What's new: parameter groups, subnet groups (ties into 07), and the RDS Data API (SQL-over-HTTPS, no persistent connection needed — handy from Lambda).
- **DynamoDB** is the bigger shift: partition key (+ optional sort key) decided upfront based on access patterns, not queried ad hoc after the fact. No joins, no aggregation pipeline — "single-table design" is the idiomatic pattern, unlike Mongo's one-collection-per-entity default.
- GSIs (Global Secondary Indexes) are your escape hatch for a second access pattern — a materialized alternate index, not a Mongo secondary index you add casually after the fact (they cost separate write capacity).

**Hands-on:** point one of your existing MySQL schemas at Floci RDS unmodified. Separately, take a Mongo collection you know well and redesign its access patterns as a single DynamoDB table + one GSI — write the access-pattern list *before* the table design.

**Floci gap:** RDS runs a real single-instance engine locally, so you can't observe Multi-AZ failover or read-replica lag behavior — both matter for production RDS design.

---

## 06 · Messaging: SQS, SNS, MSK — 10–12 hrs

MSK should feel native — it's your Kafka. SQS/SNS ask you to rethink delivery semantics.

| You know | → | AWS equivalent |
|---|---|---|
| Kafka topic + consumer group | → | MSK (same protocol) — or SQS if you don't need replay |
| Multiple consumer groups on one topic | → | SNS fan-out to several SQS queues |

- SQS is pull-based, at-least-once, and uses a *visibility timeout* instead of consumer offsets — no "replay from offset 0"; once deleted, a message is gone. Design idempotent consumers, not offset-seekable ones.
- SNS is pub/sub with no storage of its own — it fans out to subscribers (typically SQS queues) rather than holding a log the way a Kafka topic does.
- MSK is genuinely Kafka underneath — the AWS-specific layer is IAM/SASL auth on the bootstrap connection and the fact that clients must be network-reachable (VPC-placed or via public bootstrap brokers), unlike Floci where it's one local hostname:port.

**Hands-on:** fan an SNS topic into two SQS queues, each consumed by a small Lambda. Separately, point an existing Kafka producer/consumer pair from your own past work at Floci's MSK-compatible broker — change only the bootstrap servers and confirm it just works.

**Floci gap:** MSK, RDS, and ElastiCache all run real engines in Floci, so wire-protocol behavior should be trustworthy. Not testable locally: MSK's IAM-based SASL auth path and cross-VPC broker reachability.

---

## 07 · Networking: VPC — 8–10 hrs

A genuinely different mental model from k8s networking — and the phase most worth a real-AWS check-in.

| You know | → | AWS equivalent |
|---|---|---|
| Namespace | → | VPC (coarser — whole-account network boundary) |
| NetworkPolicy (label-selector based) | → | Security Group (stateful, IP/CIDR + SG-reference based) |
| Ingress / LoadBalancer Service | → | Internet Gateway + public subnet + ALB |

- Public vs private subnets: public subnets route to an Internet Gateway; private subnets route outbound only through a NAT Gateway — RDS/ElastiCache almost always sit in private subnets, reachable only from your app tier's security group.
- Security groups are stateful firewalls attached to a resource's ENI, and can reference *other security groups* as the allowed source (not just CIDRs) — "allow 5432 from the app-tier SG" is idiomatic, not hardcoded IPs.
- NAT Gateways are a classic surprise line item on a real AWS bill.

**Not a re-skin of NetworkPolicy:** k8s NetworkPolicy is label-selector based and namespace-scoped; AWS security groups are IP/ENI-based and default-deny-inbound, allow-all-outbound. Rebuild the mental model from the AWS side rather than mapping rules 1:1.

**Floci gap — the biggest one in this roadmap:** Floci's docs don't detail VPC network enforcement, and since everything in Floci speaks through one local process on port 4566, you likely can't observe a security group actually *blocking* traffic the way real AWS does. Treat this phase's Floci work as learning the resource shapes and how they reference each other (subnet → route table → SG → ENI) via Terraform, not as proof of correct enforcement.

**Hands-on:** write Terraform for a 2-AZ VPC (public + private subnets, IGW, NAT, app-tier and db-tier security groups) and apply it against Floci to validate the resource graph. Then, cost-aware, apply a minimal version to a real AWS free-tier account and deliberately try to hit the database's private-subnet port from outside its security group — confirm it's actually blocked. This is the one exercise worth spending real-AWS minutes on early rather than saving for the capstone.

---

## 08 · Observability: CloudWatch — 6–8 hrs

Same job as your existing stack, different API shape.

| You know | → | AWS equivalent |
|---|---|---|
| Loki / ELK | → | CloudWatch Logs (log groups per Lambda/ECS task) |
| Prometheus | → | CloudWatch Metrics |
| Alertmanager | → | CloudWatch Alarms |

- Log groups are created automatically per Lambda function / ECS task definition — naming convention, not manual setup.
- Embedded Metric Format (EMF): emit structured log lines that CloudWatch parses into metrics, avoiding a separate metrics SDK call for high-cardinality data — no direct Prometheus equivalent.

**Hands-on:** add structured logging and one custom metric to the Lambda and ECS service from phase 03, then set an alarm on it (error rate or queue depth from phase 06) and confirm it fires.

---

## 09 · Capstone: integrate as one Terraform stack — 14–18 hrs

One realistic system, all previous phases as code, closed out with a real-AWS validation pass.

- Build something with a genuine reason to touch most of the stack — e.g. an event-driven order/task pipeline: an ECS-hosted API accepts requests → writes to DynamoDB → publishes to SNS/SQS → a Lambda worker processes and writes results to S3 → CloudWatch alarms watch queue depth and error rate.
- Every resource gets its own least-privilege IAM role (01), defined in Terraform (02), deployed into the VPC shape from 07.
- Close-out step: deploy a cost-aware, free-tier-eligible subset of the same Terraform to a real AWS account. This validates what Floci structurally can't guarantee — full IAM policy enforcement, actual security group behavior, real quotas and latency — before calling this "AWS ready" for a job.

**Hands-on:** ship the full stack against Floci first end-to-end, then tear down and re-apply a trimmed version against real AWS. Diff what broke — that diff is your personal list of "things Floci didn't warn me about."

---

## Effort at a glance

| Phase | Hours | Fidelity vs real AWS |
|---|---|---|
| 00 · Orientation | 5–8 | n/a |
| 01 · IAM basics | 8–10 | Good, uneven enforcement — verify sensitive policies later |
| 02 · IaC bootstrap | 10–12 | High — same provider/CLI wire protocol |
| 03 · Compute | 14–18 | Highest — real Docker / real k3s backing |
| 04 · S3 storage | 6–8 | Good for correctness, not performance |
| 05 · Databases | 14–16 | High for RDS engine behavior; no Multi-AZ/replica signal |
| 06 · Messaging | 10–12 | High — MSK is real Kafka underneath |
| 07 · VPC networking | 8–10 | Low — resource shapes only, weak enforcement signal |
| 08 · Observability | 6–8 | Good |
| 09 · Capstone | 14–18 | Mixed — closes with a real-AWS pass by design |
| **Total** | **115–145 hrs** | ≈ 8–13 weeks at 10–15 hrs/week |
