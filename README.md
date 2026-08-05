# FastAPI → AWS ECS: Enterprise CI/CD Sample

A production-shaped sample project: a small FastAPI service plus a complete
CI/CD pipeline that tests it, statically analyzes it (SonarCloud), builds a
hardened Docker image, scans it (Trivy), and promotes one immutable image
across `dev` → `qa` → `stg` → `prod` on AWS ECS via GitHub Actions. See
`ARCHITECTURE.md` for the full three-pipeline (Branch / Nonprod / Prod)
delivery model, benefits, and rollback procedures.

---

## 1. Project structure

```
.
├── app/                        # FastAPI application
│   ├── main.py                 # App factory, router wiring, exception handler
│   ├── config.py                # pydantic-settings based configuration
│   ├── logging_config.py        # Structured logging setup
│   ├── models.py                 # Pydantic request/response schemas
│   ├── store.py                  # In-memory data store
│   └── routers/
│       ├── health.py              # GET /health
│       └── users.py               # GET/POST /users
├── tests/                       # pytest test suite (10 tests, ~98% coverage)
├── ecs/
│   └── task-definition.template.json  # ECS task def, rendered per env by CD
├── docs/
│   └── environment-config-sample.md   # Secrets/vars matrix per environment
├── .github/workflows/
│   ├── branch-pipeline.yml       # Test → SonarCloud → Build → Trivy → Push → Deploy to dev
│   ├── nonprod-pipeline.yml       # PR → release/** triggers deploy to qa → stg
│   ├── prod-pipeline.yml           # main push triggers deploy to prod
│   ├── cd.yml                        # Reusable: register task def + deploy (called by all three above)
│   └── cd-manual.yml                  # workflow_dispatch: ad-hoc deploy / rollback, any env
├── ARCHITECTURE.md                # Branch model, pipeline design, benefits, rollback — read this first
├── Dockerfile                    # Multi-stage, non-root, healthcheck
├── .dockerignore
├── .gitignore
├── requirements.txt / requirements-dev.txt
├── pyproject.toml                # pytest + coverage + ruff config
├── sonar-project.properties       # SonarCloud project config
└── .env.example
```

---

## 2. FastAPI architecture

- **`app/main.py`** builds the `FastAPI()` app, includes routers, and
  registers a catch-all exception handler so unhandled errors return a clean
  `500` JSON body instead of a raw traceback, while still logging the full
  stack trace server-side.
- **`app/config.py`** uses `pydantic-settings` to read configuration from
  environment variables (with `.env` support for local dev). This is what
  lets the exact same Docker image behave differently per environment — the
  ECS Task Definition injects `APP_ENV`, `LOG_LEVEL`, etc. as container
  environment variables, no rebuild required.
- **`app/logging_config.py`** configures structured, single-line JSON-ish
  log output to stdout, which CloudWatch Logs (via the `awslogs` driver)
  picks up automatically.
- **`app/models.py`** defines request/response schemas with validation
  (e.g. `EmailStr`, min-length name) so bad input is rejected with `422`
  before it reaches business logic.
- **Endpoints:**
  - `GET /health` — liveness/readiness probe, used by the Docker
    `HEALTHCHECK`, the ECS container health check, and the ALB target group.
  - `GET /users` — returns the in-memory user list.
  - `POST /users` — validates and appends a new user.
  - `GET /docs` — Swagger UI (enabled by default in FastAPI).

---

## 3. Unit testing strategy

- **Framework:** pytest + `pytest-cov`, with `httpx`'s `TestClient` for
  in-process API calls (no running server needed).
- **Coverage enforcement:** `pyproject.toml` sets
  `--cov-fail-under=80`, so **`pytest` itself exits non-zero** if coverage
  drops below 80% — the CI job doesn't need a separate coverage-check step.
- **What's tested:** all three endpoints, both success and validation-error
  paths (`422` for bad email / empty name), plus `/docs` and `/openapi.json`
  availability.
- **Reports:** `coverage.xml` (machine-readable, consumed by SonarCloud) and
  `htmlcov/` (human-readable, uploaded as a workflow artifact).

Run locally:

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 4. SonarCloud setup (analysis only — no Quality Gate enforcement)

This project uses **SonarCloud** (SonarSource's hosted SaaS) rather than a
self-hosted SonarQube instance, so there's no `docker-compose.yml` to run —
one less piece of infrastructure to operate, and the free tier covers public
repos.

**One-time setup:**

1. Go to <https://sonarcloud.io>, sign in with GitHub, and import this
   repository as a new project under your organization.
2. Note the **Organization Key** and **Project Key** SonarCloud assigns, and
   put them into `sonar-project.properties` (`sonar.organization`,
   `sonar.projectKey`).
3. Generate a **User Token**: **My Account → Access Tokens → Generate Tokens**
   (set Type to `User Token`), and store it as the repository secret
   `SONAR_TOKEN`.
4. In **Administration → Analysis Method**, make sure **Automatic Analysis**
   is turned **off** — it conflicts with the CI-based analysis this pipeline
   pushes via `SONAR_TOKEN`.

**How it's wired into CI (`branch-pipeline.yml`'s `sonarcloud` job):**

1. Tests run again (coverage.xml regenerated in this job so the path in
   `sonar-project.properties` resolves).
2. `SonarSource/sonarqube-scan-action` runs the scanner, uploading source +
   `coverage.xml` to SonarCloud, so you get a full dashboard: coverage,
   bugs, code smells, vulnerabilities, duplication.

> **Quality Gate enforcement has been intentionally removed from this
> pipeline.** Two approaches were tried and neither worked reliably against
> SonarCloud in this environment: (1) the separate
> `SonarSource/sonarqube-quality-gate-action`, built for self-hosted
> SonarQube Server, which consistently returned `curl: (22) ... 403`; and
> (2) passing `-Dsonar.qualitygate.wait=true` as a scanner argument, which
> also did not resolve it here. The scan still runs and publishes results
> to the SonarCloud dashboard on every push — it's informational rather
> than pipeline-blocking for now. To re-introduce enforcement later, check
> the dashboard manually before promoting to `stage`/`prod`, or revisit
> `sonarqube-quality-gate-action` once its SonarCloud compatibility issue
> is resolved upstream.


---

## 5. Docker build process

`Dockerfile` is a two-stage build:

1. **`builder`** stage: installs dependencies into an isolated virtualenv
   (`/opt/venv`) using `python:3.12-slim`. No app source is compiled here —
   this stage exists purely so build-time pip cache/wheels never reach the
   final image.
2. **`runtime`** stage: starts fresh from `python:3.12-slim`, copies in only
   the venv and `app/` source, creates a dedicated non-root user
   (`appuser`, uid 10001) and runs as that user, exposes port 8000, and
   defines a `HEALTHCHECK` that curls `/health`.

Build and run locally:

```bash
docker build -t fastapi-ecs-sample:local .
docker run --rm -p 8000:8000 fastapi-ecs-sample:local
curl http://localhost:8000/health
```

---

## 6. Trivy image scanning

The `build-scan-push` job in `branch-pipeline.yml` scans the freshly built image with
`aquasecurity/trivy-action`:

- `severity: HIGH,CRITICAL` — only these severities are considered.
- `exit-code: "0"` — **informational only; does not currently fail the
  build.** An earlier version of this pipeline also ran a second,
  gating scan with `exit-code: "1"` (SARIF output) that failed the job on
  any HIGH/CRITICAL finding; that step was removed to simplify the
  pipeline while other issues were being worked through. To restore
  enforcement, add back a second Trivy step with `exit-code: "1"` and
  `format: sarif` — see project history / `ARCHITECTURE.md` for where it
  fits alongside the human-readable step.
- `ignore-unfixed: true` — avoids flagging CVEs with no available fix yet
  (a common, pragmatic policy — tighten this if your organization requires
  zero-tolerance).
- Because it's `format: table` (human-readable), the CVE ID, affected
  package, installed version, and fixed version are all printed directly
  in this step's Actions log — no separate artifact needed to see what was
  found.

**Worked example — fixing a flagged Python dependency:**

Trivy will catch OS-level and Python-level (pip) vulnerabilities alike.
When it flags a `LanguageSpecificPackageVulnerability` — e.g. `starlette
0.38.6` vulnerable to `CVE-2024-47874` (a form-data DoS), fixed in
`0.40.0` — the fix is a `requirements.txt` bump, not a Dockerfile change:

```bash
python3 -m venv /tmp/check && /tmp/check/bin/pip install fastapi
/tmp/check/bin/pip show fastapi starlette   # confirms the resolved, patched version
```

Update the pin in `requirements.txt`, re-run `pytest` to confirm nothing
broke, then rebuild + re-scan the image. This project's `fastapi` /
`starlette` pins were bumped this way in response to exactly this finding
— see the comment in `requirements.txt`.

---

## 7. GitHub Actions workflow

> **This section describes the current design. For the full rationale,
> benefits, and rollback procedures, see [`ARCHITECTURE.md`](ARCHITECTURE.md)
> — this README section is a summary.**

Five workflow files, each with a distinct responsibility:

| Workflow | Trigger | Responsibility |
|---|---|---|
| `branch-pipeline.yml` | push to `feature/**` / `develop` | Test → SonarCloud (informational) → Build → Trivy scan → Push to ECR → deploy to `dev` (only on `develop`) |
| `nonprod-pipeline.yml` | PR opened/updated targeting `release/**` | Verify image exists → deploy to `qa` → deploy to `stg` |
| `prod-pipeline.yml` | push to `main` | Resolve the exact release-branch commit already validated in nonprod → verify image exists → deploy to `prod` (approval-gated) |
| `cd.yml` | `workflow_call` only | Render task def → register revision → update ECS service → wait for stability → verify health |
| `cd-manual.yml` | `workflow_dispatch` | Deploy any existing image tag to any environment — used for ad-hoc deploys and rollback |

**Enterprise practices applied:**

- **Build once, deploy many, structurally enforced:** only `branch-pipeline.yml` runs `docker build`/`docker push`. Every other pipeline *verifies* an image already exists in ECR rather than building one — it's not just convention, there's no code path in `nonprod-pipeline.yml` or `prod-pipeline.yml` capable of building an image.
- **Provable artifact traceability:** `prod-pipeline.yml` resolves the exact commit `qa`/`stg` validated via the merge commit's second parent (see `ARCHITECTURE.md` §2), not just whatever's on `main` — guaranteeing prod runs the identical, already-tested image.
- **Least privilege:** `permissions:` blocks are scoped to exactly `contents: read` + `id-token: write` (plus `pull-requests: write` only where SonarCloud needs to decorate a PR).
- **No long-lived AWS keys:** every AWS interaction uses `aws-actions/configure-aws-credentials` with `role-to-assume` + `id-token: write`, i.e. GitHub OIDC → STS `AssumeRoleWithWebIdentity`.
- **Pinned action versions:** every third-party action is pinned to a specific major/version tag (e.g. `@v4`, `@0.24.0`), not `@main`/`@latest`.
- **Caching:** `actions/setup-python` uses `cache: pip`; the Docker build uses `cache-from/cache-to: type=gha`; Trivy's vulnerability DB is cached daily.
- **Fail-fast, with clear errors:** `verify-image` jobs in both `nonprod-pipeline.yml` and `prod-pipeline.yml` check preconditions explicitly and fail with a specific `::error::` message rather than letting a missing image surface as a confusing AWS-side error later.
- **Modular/reusable:** all ECS deployment logic lives in exactly one place (`cd.yml`) — every pipeline calls it with different inputs rather than duplicating deployment steps.

---

## 8. Multi-environment deployment strategy

Four GitHub Environments — `dev`, `qa`, `stg`, `prod` — each configured
with **environment-scoped secrets and variables** (see
`docs/environment-config-sample.md` for the full list): its own
`AWS_ROLE_ARN`, ECS cluster/service/task-family names, log group, etc.
Because `cd.yml` reads all of these from `secrets.*`/`vars.*` rather than
hardcoding anything, the *same workflow code* deploys to four different
AWS accounts.

See `ARCHITECTURE.md` §1–2 for the full branch model and how each pipeline
triggers; the short version:

1. `feature/*` push → feedback only, no deploy
2. `develop` push → deploy to `dev`
3. PR opened, any branch → `release/**` → deploy to `qa` then `stg`
4. `release/**` merged into `main` (merge commit) → deploy to `prod` (approval-gated)
5. `cd-manual.yml` → deploy/roll back any tag to any environment, any time

**Immutable artifact promotion:** at every step above, the workflow only
ever receives (or resolves) a Git-SHA image tag that `branch-pipeline.yml`
already pushed — nothing ever rebuilds the image mid-promotion, so what
you tested in `qa`/`stg` is bit-for-bit what reaches `prod`.

**Production approval gate:** configure the `prod` GitHub Environment with a
*required reviewers* protection rule (Settings → Environments → `prod`).
Since `cd.yml`'s job declares `environment: ${{ inputs.environment }}`, any
call with `environment: prod` will pause and wait for an approver before
it's allowed to assume the prod AWS role — no workflow YAML changes needed
to enforce this.

**Rollback:** see `ARCHITECTURE.md` §4 for the full options (native ECS
rollback, `cd-manual.yml`, and the ECS deployment circuit breaker for
automatic rollback on a failed deployment).

---

## 9. GitHub OIDC authentication & AWS IAM AssumeRole

No AWS access keys are stored anywhere in this repository. Instead:

1. GitHub Actions requests a short-lived OIDC token, identifying the
   workflow/repo/branch that's running.
2. `aws-actions/configure-aws-credentials` presents that token to AWS STS
   (`sts:AssumeRoleWithWebIdentity`) against the IAM role named in
   `secrets.AWS_ROLE_ARN` for that environment.
3. Each such IAM role must have a **trust policy** restricting who can
   assume it — scoped to this repository and (ideally) to specific
   branches/environments, e.g.:

```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
    "StringLike": { "token.actions.githubusercontent.com:sub": "repo:your-org/fastapi-ecs-cicd:environment:prod" }
  }
}
```

4. The role's **permissions policy** should be scoped to only what that
   stage needs — e.g. the CI build role needs `ecr:PutImage` etc. on one
   repository; each deploy role needs `ecs:RegisterTaskDefinition`,
   `ecs:UpdateService`, `ecs:DescribeServices`, and `iam:PassRole` on the
   task/execution roles only.

---

## 10. Amazon ECR & Amazon ECS deployment process

**Assumed pre-existing infrastructure** (this pipeline does not create any
of it): ECR repository, ECS cluster, ECS service, an initial ECS task
definition, IAM roles, VPC, ALB, CloudWatch log group — one set per
environment/account.

**What the pipeline actually does, per environment:**

1. `envsubst` renders `ecs/task-definition.template.json` using that
   environment's vars/secrets (execution role, task role, log group,
   region, `APP_ENV`) plus the image URI being promoted.
2. `aws-actions/amazon-ecs-render-task-definition` swaps in the container
   image and produces a task-definition JSON ready to register.
3. `aws-actions/amazon-ecs-deploy-task-definition` registers this as a
   **new revision** of the existing task family, updates the ECS service to
   use it, and — with `wait-for-service-stability: true` — blocks until ECS
   reports the service has reached a steady state (or fails after its
   timeout).
4. A final `aws ecs describe-services` call prints running/desired task
   counts and each deployment's rollout state, as an explicit
   human-readable verification step.

---

## 11. Running the project locally

```bash
# 1. Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 2. Configure environment
cp .env.example .env

# 3. Run the API
uvicorn app.main:app --reload

# 4. Explore
open http://localhost:8000/docs      # Swagger UI
curl http://localhost:8000/health
curl http://localhost:8000/users
curl -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{"name": "Test User", "email": "test@example.com"}'

# 5. Run tests
pytest
```

---

## 12. Deploying to each environment

**Automatic:**

| Action | Deploys to |
|---|---|
| Push to `develop` | `dev` |
| Open/update a PR targeting `release/**` | `qa`, then `stg` |
| Merge that PR into `main` ("Create a merge commit" only) | `prod` (waits for required-reviewer approval) |

**Manual (any environment, and rollback):**

1. Find the Git SHA tag you want (first 12 chars of a commit SHA;
   visible in the `build-scan-push` job logs, or `aws ecr describe-images`).
2. Go to **Actions → CD - Manual Promotion / Rollback → Run workflow**.
3. Pick the target environment and paste the image tag.
4. Approve if prompted (`prod` only).

See `ARCHITECTURE.md` §5 for the full step-by-step rollout/testing sequence
for all three pipelines, and §4 for rollback options.

---

## 13. Common troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `pytest` fails with coverage error | A change reduced coverage below 80%; add tests or check `htmlcov/` (uploaded artifact) for uncovered lines. |
| SonarCloud step shows a red X or scan doesn't appear on dashboard | Check `SONAR_TOKEN` is a valid **User Token** and Automatic Analysis is disabled (Administration → Analysis Method); Quality Gate status is no longer enforced by CI, so this step only fails if the scan itself errors, not on gate status. |
| Trivy step fails the build | A HIGH/CRITICAL CVE with a fix exists in your base image or a pinned pip package; bump the affected dependency/base image tag and re-run. Check `trivy-report` artifact for the CVE ID. |
| `configure-aws-credentials` fails with "not authorized to perform sts:AssumeRoleWithWebIdentity" | The IAM role's trust policy `sub` condition doesn't match the actual claim GitHub sends — see `docs/environment-config-sample.md`'s "Trust policy `sub` claim format" section; don't assume the plain `repo:<org>/<repo>:environment:<env>` format still applies. |
| ECS deployment "times out waiting for the service to stabilize" | New tasks are failing their health check (check the container's `/health` via CloudWatch Logs) or there's a capacity/subnet issue in that environment's VPC. |
| `Nonprod Pipeline` doesn't trigger after opening a PR | Confirm the PR's **base** branch matches `release/**` exactly — a PR into `develop` or any other branch won't trigger it. |
| `Nonprod Pipeline`'s `verify-image` job fails immediately | `Branch Pipeline` hasn't finished (or failed) for the PR's head commit yet — check that workflow's run for the source branch first. |
| `Prod Pipeline`'s `resolve-validated-commit` job fails with "only 1 parent commit" | The release branch was merged into `main` via squash or rebase merge instead of a merge commit — see `docs/environment-config-sample.md`'s "Merge strategy requirement" section. |
| Image tag not found when manually promoting | Double-check you used the first 12 characters of the SHA exactly as the pipeline computed it (`${GITHUB_SHA::12}`), not the full 40-character SHA. |

---

## Expected outcomes checklist

- [x] FastAPI application runs locally (`uvicorn app.main:app --reload`)
- [x] All unit tests pass, ≥80% coverage enforced by `pytest`
- [x] SonarCloud analysis runs and publishes to the dashboard (informational; Quality Gate is not pipeline-enforced)
- [x] Docker image builds via multi-stage, non-root, healthchecked Dockerfile
- [x] Trivy scans the image and reports HIGH/CRITICAL findings (informational)
- [x] Image pushed to Amazon ECR via GitHub OIDC (no static keys)
- [x] Same immutable Git-SHA image promoted across dev/qa/stg/prod
- [x] ECS service updated and waited-on for stability
- [x] `/health`, `/users` (GET/POST), and `/docs` all reachable
