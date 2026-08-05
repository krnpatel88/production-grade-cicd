# Setup & Validation Runbook

> **⚠️ Superseded by `ARCHITECTURE.md` for pipeline structure.** This
> document was written for an earlier single-pipeline design (`ci.yml` +
> `cd.yml` + `cd-branch.yml`/`cd-manual.yml`, branch mapping
> `develop→dev`/`release/**→stage`/`main→prod`). The project has since
> moved to a three-pipeline model — `branch-pipeline.yml`,
> `nonprod-pipeline.yml`, `prod-pipeline.yml` — with `qa`/`stg` replacing
> `test`/`stage`, and different triggers (PR-to-`release/**` for nonprod,
> merge-commit-to-`main` for prod). **See `ARCHITECTURE.md` for the current
> design, environments, and rollout steps.**
>
> **Phases 0–6 below (local run, Docker, SonarCloud, AWS prerequisites,
> GitHub secrets/environments) are still fully accurate and worth
> following as-is.** Phases 7–9 (deployment testing, branch-based
> promotion) describe the old design — use `ARCHITECTURE.md` §5 instead
> for testing the current pipelines.

Follow these phases **in order**. Each phase ends with a concrete "how to
verify this step worked" check before you move to the next one — don't skip
ahead if a check fails.

---

## Phase 0 — Prerequisites checklist

Before touching AWS or GitHub, make sure you have:

- [ ] A GitHub repository with this project pushed to it (`main` + `develop`
      branches at minimum; create `release/*` branches as needed later)
- [ ] Admin access to that GitHub repo (to configure Environments/secrets)
- [ ] AWS account(s) — minimum one for a quick end-to-end test, ideally four
      (dev/test/stage/prod) for the full multi-account story
- [ ] AWS CLI installed locally and credentials configured
      (`aws sts get-caller-identity` works)
- [ ] Docker installed locally (Docker Desktop or equivalent)
- [ ] Python 3.12 installed locally
- [ ] A free [SonarCloud](https://sonarcloud.io) account (sign in with GitHub)
- [ ] `jq` and `envsubst` available locally if you want to test the task-def
      rendering step on your machine (`envsubst` ships in the `gettext`
      package; `apt install gettext-base` / `brew install gettext`)

---

## Phase 1 — Run and test the app locally

**1.1 Install and run**

```bash
cd fastapi-ecs-cicd
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

**Verify:** open `http://localhost:8000/docs` — Swagger UI loads and lists
`/health`, `/users` (GET), `/users` (POST).

**1.2 Exercise the endpoints**

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
curl -s http://localhost:8000/users | python3 -m json.tool
curl -s -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{"name":"Test User","email":"test@example.com"}' | python3 -m json.tool
```

**Verify:** `/health` returns `"status":"ok"`; `POST /users` returns `201`
with an `id`; a follow-up `GET /users` includes the new user.

**1.3 Run the test suite**

```bash
pytest
```

**Verify:** all tests pass and the coverage summary shows **≥80%** (it will
also hard-fail the command itself if it doesn't, since `pyproject.toml` sets
`--cov-fail-under=80`).

Stop here and fix any failures before continuing — nothing downstream will
work if the app itself is broken.

---

## Phase 2 — Build and test the Docker image locally

**2.1 Build**

```bash
docker build -t fastapi-ecs-sample:local .
```

**Verify:** build completes; `docker images | grep fastapi-ecs-sample` shows
the image. Check the size is reasonable (should be well under 300MB thanks
to the multi-stage build):

```bash
docker images fastapi-ecs-sample:local --format "{{.Size}}"
```

**2.2 Run and health-check**

```bash
docker run -d --name fastapi-test -p 8000:8000 fastapi-ecs-sample:local
sleep 5
docker inspect --format='{{.State.Health.Status}}' fastapi-test
curl -s http://localhost:8000/health
docker logs fastapi-test
docker rm -f fastapi-test
```

**Verify:** health status reports `healthy` (may show `starting` for the
first ~10s — that's the `start-period`), `/health` responds, logs show
structured JSON-ish log lines, not a crash.

**2.3 Confirm non-root user**

```bash
docker run --rm fastapi-ecs-sample:local id
```

**Verify:** output shows `uid=10001(appuser)`, not `uid=0(root)`.

**2.4 (Optional) Scan locally with Trivy before pushing anything to CI**

```bash
brew install trivy   # or see https://aquasecurity.github.io/trivy for your OS
trivy image --severity HIGH,CRITICAL --ignore-unfixed fastapi-ecs-sample:local
```

**Verify:** no HIGH/CRITICAL vulnerabilities reported (or only ones you've
consciously decided to accept — if so, note them, since CI will fail on
them otherwise).

---

## Phase 3 — SonarCloud setup and local scan (optional but recommended)

**3.1 Create the project**

1. Sign in to <https://sonarcloud.io> with GitHub.
2. **+ → Analyze new project**, select this repository.
3. Choose **"Use the global setting"** for the analysis method — we're
   driving it from GitHub Actions, not SonarCloud's own CI auto-config.
4. Note the **Organization Key** and **Project Key** shown.

**3.2 Update the project config**

Edit `sonar-project.properties`:

```properties
sonar.organization=<your-org-key>
sonar.projectKey=<your-project-key>
```

**3.3 Generate a token**

**My Account → Security → Generate Token** → name it (e.g.
`github-actions-fastapi-ecs`) → copy it immediately (shown once).

**3.4 (Optional) Test the scan locally before wiring up CI**

```bash
pytest   # regenerate coverage.xml
docker run --rm \
  -e SONAR_TOKEN=<paste-token> \
  -v "$(pwd):/usr/src" \
  sonarsource/sonar-scanner-cli
```

**Verify:** scanner exits `0`; SonarCloud project dashboard shows the new
analysis with coverage numbers matching your local `pytest` run.

> **Windows / cross-environment note:** if SonarCloud reports "Invalid
> directory path in 'source' element" or "Cannot resolve the file path" for
> your `app/*.py` files, your `coverage.xml` has absolute host paths baked
> in (e.g. `C:/Users/.../app`) that don't exist inside the scanner's
> container filesystem. This project's `pyproject.toml` sets
> `[tool.coverage.run] relative_files = true` to prevent this — make sure
> you have that setting, then delete and regenerate the report:
> ```bash
> rm -rf .coverage coverage.xml htmlcov
> pytest
> ```
> before re-running the scanner.

**3.5 Quality Gate is not pipeline-enforced**

**Store the token as a GitHub secret** (repository-level, not per-environment
— see Phase 5): `SONAR_TOKEN`.

> **Why the pipeline doesn't fail on Quality Gate status:** two approaches
> were tried and neither worked reliably against SonarCloud in practice:
> (1) a separate `SonarSource/sonarqube-quality-gate-action` step, which is
> built for self-hosted SonarQube **Server** and reliably returned
> `curl: (22) ... 403` against SonarCloud regardless of token type/
> permissions; and (2) passing `-Dsonar.qualitygate.wait=true` as a scanner
> argument on the scan step itself, which also did not resolve it in this
> environment. `ci.yml` therefore only runs the scan for visibility on the
> SonarCloud dashboard — it does not gate the pipeline. If you want gate
> enforcement, check the dashboard manually before promoting a build, or
> revisit `sonarqube-quality-gate-action` once its SonarCloud compatibility
> issue is resolved upstream.

---

## Phase 4 — AWS prerequisites (per environment/account)

Do this once per environment you're setting up. If you're doing a quick
single-environment test first, do this once for `dev` and come back for the
rest later.

**4.1 Create the OIDC identity provider** (once per AWS account)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

**Verify:**

```bash
aws iam list-open-id-connect-providers
```
shows the new provider.

**4.2 Create the deploy IAM role**

Trust policy (`trust-policy.json` — replace `ACCOUNT_ID`, `your-org/your-repo`,
and the environment name):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:your-org/your-repo:environment:dev" }
    }
  }]
}
```

```bash
aws iam create-role \
  --role-name github-actions-deploy-dev \
  --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy \
  --role-name github-actions-deploy-dev \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess   # tighten later; see note below
```

> **Least-privilege note:** `AmazonECS_FullAccess` is a fast way to get
> unblocked. Before treating this as production-ready, replace it with a
> scoped inline policy granting only `ecs:RegisterTaskDefinition`,
> `ecs:DescribeTaskDefinition`, `ecs:UpdateService`, `ecs:DescribeServices`,
> and `iam:PassRole` restricted to the specific execution/task role ARNs.

**Verify:**

```bash
aws iam get-role --role-name github-actions-deploy-dev
```
returns the role with the correct trust policy.

**4.3 Create/confirm pre-existing infrastructure**

This pipeline assumes these already exist per environment — create them
once if this is a fresh account:

```bash
# ECR (build/push account only — typically one shared registry)
aws ecr create-repository --repository-name fastapi-ecs-sample

# CloudWatch log group
aws logs create-log-group --log-group-name /ecs/fastapi-ecs-sample-dev

# ECS cluster
aws ecs create-cluster --cluster-name fastapi-dev-cluster
```

For the ECS **task execution role**, **task role**, **VPC**, **subnets**,
and **ALB + target group**, use your organization's standard setup (or the
AWS Console's "Create Service" wizard once, just to get a working baseline)
— these are genuinely one-time infra, not something this pipeline manages.

**4.4 Register a bootstrap task definition + create the ECS service**

The pipeline only ever *updates* an existing service — it needs one to
already exist:

```bash
aws ecs register-task-definition --cli-input-json file://ecs/task-definition.template.json
# (fill placeholders manually for this one-time bootstrap, or use the AWS Console)

aws ecs create-service \
  --cluster fastapi-dev-cluster \
  --service-name fastapi-dev-service \
  --task-definition fastapi-ecs-sample-dev \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxxx],securityGroups=[sg-xxxx],assignPublicIp=ENABLED}"
```

**Verify:**

```bash
aws ecs describe-services --cluster fastapi-dev-cluster --services fastapi-dev-service \
  --query "services[0].{status:status,running:runningCount,desired:desiredCount}"
```
shows `status: ACTIVE`, `running: 1`, `desired: 1` (it'll be running
whatever bootstrap image you pointed it at — that's fine, CI will replace it).

Repeat 4.1–4.4 for `test`, `stage`, `prod` with their own account
IDs/resource names when you're ready to expand beyond one environment.

---

## Phase 5 — Wire up GitHub configuration

**5.1 Repository secrets/variables** (Settings → Secrets and variables →
Actions → **Repository** tab — these are shared by `ci.yml`):

| Type | Name | Value |
|---|---|---|
| Variable | `AWS_REGION` | e.g. `us-east-1` |
| Variable | `ECR_REPOSITORY` | `fastapi-ecs-sample` |
| Secret | `AWS_ROLE_ARN` | the CI build role you created (grants ECR push) |
| Secret | `SONAR_TOKEN` | token from Phase 3.3 |

**5.2 GitHub Environments** (Settings → Environments → **New environment**)
— create `dev`, `test`, `stage`, `prod`. For each, add its own
Environment-scoped secrets/variables per `docs/environment-config-sample.md`
(don't reuse the repository-level ones — these are separate, per-account
values).

**5.3 Protection rule on `prod`**

Settings → Environments → `prod` → **Required reviewers** → add yourself or
your team. Leave `dev`/`test`/`stage` unprotected for faster iteration.

**Verify Phase 5:** Settings → Environments should list all four, each
showing its secret/variable count; `prod` should show a "Required reviewers"
badge.

---

## Phase 6 — Test the CI pipeline stage by stage

**6.1 Push a trivial change to `develop` and watch the Actions tab**

```bash
git checkout -b develop
git push -u origin develop
```

Go to **Actions** in GitHub and click into the running `CI` workflow.

**Verify each job in order:**

| Job | What "pass" looks like |
|---|---|
| `Unit Tests & Coverage` | green check; `coverage-report` artifact attached |
| `SonarCloud Analysis` | green check; SonarCloud dashboard shows a new analysis with coverage/issues (this step no longer blocks on Quality Gate status) |
| `Build Image, Scan (Trivy), Push to ECR` | image builds; `trivy-report` artifact attached; final step logs a successful `docker push` |

If any job fails, **stop and fix it here** — don't move to Phase 7 with a
broken CI job, since CD depends on its output.

**6.2 Confirm the image landed in ECR**

```bash
aws ecr describe-images --repository-name fastapi-ecs-sample \
  --query "imageDetails[*].imageTags" --output table
```

**Verify:** you see a tag matching the first 12 characters of your commit
SHA (`git rev-parse HEAD | cut -c1-12`).

---

## Phase 7 — Test automatic deployment (branch strategy)

**7.1 Watch the `resolve-environment` and `deploy` jobs run** in the same
`CI` workflow run, right after `build-scan-push` (Actions tab — same run,
scroll down to see all jobs; no separate workflow run to wait for).

> **Note:** an earlier version of this pipeline used a separate
> `cd-branch.yml` workflow triggered by `on: workflow_run` after `ci.yml`
> completed. That trigger type only fires based on the workflow file as it
> exists on the repo's **default branch**, so it silently does nothing
> until that file is merged there — a common gotcha. The `deploy` job in
> `ci.yml` now calls `cd.yml` directly via `workflow_call` (a reusable
> workflow) instead — this is GitHub's documented pattern for chaining CI
> into CD reliably. Deploy logic still lives in its own file (`cd.yml`,
> the renamed former `reusable-deploy.yml`) — it's just triggered as a job
> dependency rather than a separate top-level workflow run, so there's
> nothing to wait for separately and no default-branch requirement.

**Verify each step in the `deploy` job:**

| Step | What "pass" looks like |
|---|---|
| Configure AWS credentials | green — confirms OIDC AssumeRole worked |
| Render ECS task definition from template | logs show a fully-substituted JSON with no leftover `${...}` |
| Register new ECS Task Definition revision | logs show a new revision number |
| Deploy new Task Definition to ECS Service | eventually shows "steady state" reached, not a timeout |
| Verify ECS deployment health | table output shows `running == desired` |

**7.2 Hit the deployed service**

```bash
aws ecs describe-tasks --cluster fastapi-dev-cluster \
  --tasks $(aws ecs list-tasks --cluster fastapi-dev-cluster --service-name fastapi-dev-service --query "taskArns[0]" --output text) \
  --query "tasks[0].attachments[0].details"
# find the ENI's public IP via the attachment's networkInterfaceId, or go through your ALB DNS name

curl http://<alb-dns-name-or-task-ip>:8000/health
```

**Verify:** returns `{"status":"ok",...}` — same response as your local
Phase 1 test, now running as an ECS Fargate task.

---

## Phase 8 — Test manual promotion (covers `test`, and re-promotion generally)

**8.1 Grab the image tag** that Phase 6 pushed (first 12 chars of the commit
SHA).

**8.2 Actions → CD - Manual Promotion → Run workflow**

- `environment`: `test`
- `image-tag`: the SHA you grabbed

**Verify:** same checklist as Phase 7.1, but note this time **no new image
was built** — check the job logs contain no `docker build`/`docker push`
step at all, confirming true "build once, deploy many."

**8.3 Test the `prod` approval gate**

Run the same manual workflow with `environment: prod`. 

**Verify:** the workflow run pauses at the `deploy` job with a banner asking
for review (matching the required-reviewers rule from Phase 5.3) — it must
**not** proceed to assume the prod AWS role until someone approves it.
Approve it, then confirm the deployment completes and `/health` responds
from the prod ECS service.

---

## Phase 9 — Regression check: promote to `stage` via branch push

```bash
git checkout -b release/1.0.0
git push -u origin release/1.0.0
```

**Verify:** `CI` runs and pushes a **new** image tag (this is a different
commit, so build-once still applies — it's a new artifact for this new
commit, not a rebuild of the same one); the `resolve-environment`/`deploy`
jobs at the end of that same run map `release/**` → `stage` and deploy
automatically without a manual step.

---

## Troubleshooting quick-reference

See the **Common troubleshooting** table in `README.md` §13 for fixes to
the most frequent failure points (coverage drop, SonarCloud scan issues,
timeout, Trivy CVE failures, OIDC trust policy mismatches, ECS stability
timeouts, and `workflow_run` not firing).
