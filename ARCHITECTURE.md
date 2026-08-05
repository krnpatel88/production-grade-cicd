# Pipeline Architecture: Branch → Nonprod → Prod

This document describes the three-pipeline delivery model used in this
repository, supersedes the simpler single-pipeline design described in
earlier project history, and is the authoritative reference for how code
moves from a developer's branch to production.

---

## 1. Branch model

```
feature/*  ──push──▶  Branch Pipeline (test/scan, no deploy)
    │
    └──merge──▶ develop ──push──▶ Branch Pipeline (test/scan + build/push + deploy → dev)
                    │
                    └──PR──▶ release/*  ──PR opened──▶ Nonprod Pipeline (deploy → qa → stg)
                                  │
                                  └──merge (merge commit)──▶ main ──push──▶ Prod Pipeline (deploy → prod)
```

| Branch | Purpose |
|---|---|
| `feature/*` | Individual developer work. Pushed for fast feedback only — never deployed anywhere on its own. |
| `develop` | Integration branch. Every push here builds, scans, and deploys to `dev` automatically. |
| `release/*` | Cut when a set of features is ready to validate as a release candidate. Opening a PR from `develop` (or a `feature/*` branch) into a `release/*` branch is what triggers nonprod deployment. |
| `main` | Production source of truth. Merging a `release/*` branch into `main` is what triggers the prod deployment. |

---

## 2. The three pipelines

### Pipeline 1 — `branch-pipeline.yml` ("Branch Pipeline")

**Trigger:** `push` to `feature/**` or `develop`.

**Does:**
1. Unit tests + coverage (fails below 80%)
2. SonarCloud static analysis — code quality + security feedback (informational; see `README.md` §4 for why it isn't a hard gate)
3. Docker build
4. Trivy image scan — vulnerability feedback (informational; tighten to blocking by restoring `exit-code: "1"` if your org wants a hard gate here)
5. Push the image to ECR, tagged with the 12-character Git SHA
6. **Only if the branch is `develop`:** deploy that image to `dev`

This is exactly your requirement 1: *"build and static-testing to provide feedback on security vulnerability and code quality and also deploy to dev environment."* Feature branches get the feedback (steps 1–4) without the deploy; `develop` gets everything including the deploy.

### Pipeline 2 — `nonprod-pipeline.yml` ("Nonprod Pipeline")

**Trigger:** `pull_request` opened/updated, targeting `release/**`.

**Does:**
1. Computes the PR head commit's image tag and **verifies it already exists in ECR** (built by Pipeline 1 when that commit was pushed to its source branch) — fails clearly if not, rather than a confusing error later
2. Deploys that exact image to `qa`
3. Deploys that exact same image to `stg` (after `qa` succeeds)

This is your requirement 2: *"Non prod should start deployment when I create an MR from feature to release branch."* No image is rebuilt here — this is the same artifact `qa` and `stg` both run, which is what makes a `stg` sign-off actually mean something.

### Pipeline 3 — `prod-pipeline.yml` ("Prod Pipeline")

**Trigger:** `push` to `main` (i.e. a release branch was just merged in).

**Does:**
1. Reads the merge commit's **second parent** — the release branch's tip, i.e. the exact commit `qa`/`stg` already validated — rather than trusting `main`'s own new merge-commit SHA (which is a different, brand-new commit object)
2. Verifies that commit's image still exists in ECR
3. Deploys it to `prod`, gated by the `prod` GitHub Environment's required-reviewers approval rule

This is your requirement 3: *"Prod should start deployment when I merge release branch to main branch."* Critically, **the image that reaches production is bit-for-bit identical to the image `qa` and `stg` already tested** — nothing is rebuilt at any promotion step, from `dev` all the way to `prod`.

> **Requires "Create a merge commit" as your only allowed merge strategy for PRs into `main`.** See `docs/environment-config-sample.md` for exactly why and how to enforce it.

### Supporting workflow — `cd-manual.yml` ("CD - Manual Promotion / Rollback")

Not part of the automatic flow — a manual `workflow_dispatch` you can point at any environment with any already-pushed image tag. Used for ad-hoc deploys and, primarily, **rollback** (see §4).

### Shared building block — `cd.yml`

Called by all three pipelines above (and by `cd-manual.yml`). Contains the actual "render task def → register revision → update service → wait for stability → verify health" logic, parameterized by `environment` and `image-tag`. This is the *only* place ECS deployment logic lives — every pipeline reuses it rather than duplicating deployment steps.

---

## 3. Benefits of this strategy

**Fast, cheap feedback for developers.** `feature/*` pushes get full test/quality/security feedback in minutes, with no deployment overhead — developers don't wait on infrastructure to find out their code has a bug or a vulnerable dependency.

**True "build once, deploy many," enforced structurally, not by convention.** Every pipeline after Pipeline 1 *verifies* an image exists rather than building one. It's structurally impossible for `qa`, `stg`, or `prod` to run code that wasn't already tested at an earlier stage — there's no code path that lets them build their own image.

**What passed staging is what reaches production, provably.** Because `prod-pipeline.yml` resolves the release branch's tip commit via the merge commit's actual parent (not just "whatever's on `main` now"), there's a cryptographic guarantee (same Git SHA, same image digest) that production is running the exact artifact `stg` signed off on. This eliminates an entire class of "worked in staging, broke in prod" incidents caused by environment drift or accidental rebuilds.

**Deployment intent is explicit and auditable.** A `qa`/`stg` deployment can only happen via a real PR (with a number, an author, a diff) targeting `release/**`. A `prod` deployment can only happen via a real merge to `main`, with a full approval trail (the required-reviewers gate) and a traceable link back to that PR. Anyone can answer "why did this deploy to prod" by looking at Git/GitHub history alone.

**Reduced blast radius.** Nothing reaches `prod` without first passing through `dev` (implicitly, since `develop` is the source of `release/*` branches) and explicitly through `qa` + `stg`, plus a human approval. A bad image is very unlikely to reach production without being caught somewhere in that chain.

**Environment-specific AWS account isolation still holds.** Each environment keeps its own AWS account, IAM role, and OIDC trust policy (per `docs/environment-config-sample.md`) — a compromised `dev` deploy role has zero ability to touch `prod` infrastructure.

---

## 4. Rollback strategy

Because every deployed image is an immutable, Git-SHA-tagged artifact sitting permanently in ECR (subject to your repository's lifecycle policy), rolling back never requires reverting code or rebuilding anything. Three options, fastest first:

### Option A — Native ECS rollback (fastest, use in a genuine emergency)

Every deploy through `cd.yml` registers a **new task definition revision** — it never deletes old revisions. The previous revision is still sitting right there:

```bash
# Find the previous good revision number
aws ecs list-task-definitions --family-prefix fastapi-ecs-sample-prod --sort DESC

# Point the service straight at it — bypasses GitHub Actions entirely
aws ecs update-service \
  --cluster fastapi-prod-cluster \
  --service fastapi-prod-service \
  --task-definition fastapi-ecs-sample-prod:<previous-revision-number> \
  --force-new-deployment
```
This is the fastest possible rollback — no CI run, no approval wait, direct AWS API call. Use it when production is actively broken and every minute matters.

### Option B — `cd-manual.yml` (fastest *auditable* rollback)

```
Actions → CD - Manual Promotion / Rollback → Run workflow
  environment: prod
  image-tag: <the last known-good SHA — check ECR or your deployment history>
```
Same effect as Option A, but goes through the normal pipeline (including the `prod` approval gate), so it's logged in Actions history like any other deployment — the recommended default unless you're in a true "stop the bleeding right now" situation.

### Option C — Automatic rollback on failed deployment (proactive, set up once)

Add ECS's built-in deployment circuit breaker to each service, so a *new* bad deployment automatically reverts itself without anyone needing to notice and act:

```bash
aws ecs update-service \
  --cluster fastapi-prod-cluster \
  --service fastapi-prod-service \
  --deployment-configuration \
    "deploymentCircuitBreaker={enable=true,rollback=true},maximumPercent=200,minimumHealthyPercent=100"
```
With this enabled, if new tasks keep failing their health check, ECS itself detects the failure and rolls the service back to the last-known-good task definition — before `cd.yml`'s `wait-for-service-stability` step would even time out. Worth enabling on every environment, not just `prod`.

### What NOT to do for rollback

Don't "revert the commit and re-merge" as your rollback mechanism — that re-triggers the full pipeline (new PR, new nonprod validation, new approval) when what you actually want is to *immediately* restore a state you already know was good. Save code reverts for the follow-up fix; use Options A/B for the immediate rollback.

---

## 5. Step-by-step rollout instructions

### Step 1 — Update branch protection & merge settings

1. **Settings → Branches** — add protection rules for `develop` and `main` (require PR review, require status checks, etc., per your org's standards).
2. **Settings → General → Pull Requests** — uncheck **"Allow squash merging"** and **"Allow rebase merging"**, leave only **"Allow merge commits"** checked. *(Required for `prod-pipeline.yml`'s commit-resolution logic — see §2, Pipeline 3.)*

### Step 2 — Set up the four GitHub Environments

Repeat Phase 4/5 from `SETUP.md` for each of `dev`, `qa`, `stg`, `prod` — same secrets/variables checklist, same OIDC trust-policy pattern (updated format — see `docs/environment-config-sample.md`'s "Trust policy `sub` claim format" section), just with `qa`/`stg` as two new environments alongside your existing `dev`/`prod`. Add the required-reviewers rule to `prod` (and optionally `stg`).

### Step 3 — Push the pipeline files

```bash
git add .github/workflows/
git commit -m "Restructure to Branch/Nonprod/Prod pipeline architecture"
git push origin develop
```
Also merge these files to `main` at some point soon (a small infra-only PR is fine) — `prod-pipeline.yml` only triggers from a push to `main`, so it needs to exist there.

### Step 4 — Test Pipeline 1 end to end

```bash
git checkout -b feature/test-pipeline1
echo "# test" >> README.md
git add . && git commit -m "test branch pipeline"
git push -u origin feature/test-pipeline1
```
**Verify:** `Branch Pipeline` runs test/sonar/build/scan, pushes an image, but **does not** deploy anywhere (check the Actions run — there should be no `Deploy to dev` job at all, since `if: github.ref_name == 'develop'` should have skipped it).

Merge that branch into `develop` (or push directly to `develop` for this test):
```bash
git checkout develop
git merge feature/test-pipeline1
git push origin develop
```
**Verify:** this time `Deploy to dev` job runs and completes; `dev`'s ECS service updates.

### Step 5 — Test Pipeline 2 end to end

```bash
git checkout -b release/1.0.0 develop
git push -u origin release/1.0.0
```
Open a PR: `develop` → `release/1.0.0` (or use `gh pr create --base release/1.0.0 --head develop`).

**Verify:** `Nonprod Pipeline` triggers immediately on PR creation — `verify-image` confirms the image exists (it does, from Step 4), then `deploy-qa` runs, then `deploy-stg` runs after it. Check both `qa` and `stg` ECS services updated.

### Step 6 — Test Pipeline 3 end to end

Merge that PR using **"Create a merge commit"** (confirm the merge button/dropdown shows this option, per Step 1).

**Verify:** `Prod Pipeline` triggers on the push to `main`. `resolve-validated-commit` should print the release branch's tip SHA (matching the tag `qa`/`stg` just ran); `verify-image` confirms it; `deploy-prod` pauses for approval (if you added the required-reviewers rule) — approve it, then confirm `prod`'s ECS service updates to that exact image.

### Step 7 — Test rollback

Pick any environment and any earlier image tag you've already deployed, and run:
```
Actions → CD - Manual Promotion / Rollback → Run workflow
  environment: dev (or whichever)
  image-tag: <an earlier tag>
```
**Verify:** the service redeploys to that earlier image without any rebuild — confirms your rollback path works *before* you ever need it in a real incident.

### Step 8 — Set up the ECS deployment circuit breaker (recommended, one-time)

Run the `aws ecs update-service ... deploymentCircuitBreaker` command from §4 Option C against each environment's service, so future bad deployments roll back automatically.
