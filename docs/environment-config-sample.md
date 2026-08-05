# Sample GitHub Environment Configuration

Configure these under **Repo → Settings → Environments** for each of the four
GitHub Environments: `dev`, `qa`, `stg`, `prod`. Do this once at the
repository level too, for the values shared by `branch-pipeline.yml`,
`nonprod-pipeline.yml`, and `prod-pipeline.yml`'s `verify-image` jobs
(build/push account access, used to check ECR).

## Repository-level (shared build/push account)

| Type     | Name              | Example value                                              |
|----------|-------------------|--------------------------------------------------------------|
| Variable | `AWS_REGION`      | `us-east-1`                                                   |
| Variable | `ECR_REPOSITORY`  | `fastapi-ecs-sample`                                           |
| Secret   | `AWS_ROLE_ARN`    | `arn:aws:iam::111111111111:role/github-actions-ci-ecr-push`   |
| Secret   | `SONAR_TOKEN`     | *(generated in SonarCloud → My Account → Access Tokens)*      |

## Per-environment (used by `cd.yml`)

Repeat this table for each of `dev`, `qa`, `stg`, `prod`, substituting the
account ID / ARNs / names that belong to that environment's own AWS account.

| Type     | Name                     | Example value (shown for `dev`)                                         |
|----------|--------------------------|--------------------------------------------------------------------------|
| Variable | `AWS_REGION`             | `us-east-1`                                                               |
| Variable | `ECR_REGISTRY`           | `111111111111.dkr.ecr.us-east-1.amazonaws.com` **(bare hostname only — do NOT include the repository name here)** |
| Variable | `ECR_REPOSITORY`         | `fastapi-ecs-sample`                                                      |
| Variable | `ECS_CLUSTER`            | `fastapi-dev-cluster`                                                     |
| Variable | `ECS_SERVICE`            | `fastapi-dev-service`                                                     |
| Variable | `ECS_TASK_DEFINITION`    | `fastapi-ecs-sample-dev`                                                  |
| Variable | `ECS_CONTAINER_NAME`     | `fastapi-ecs-sample`                                                      |
| Variable | `ECS_LOG_GROUP`          | `/ecs/fastapi-ecs-sample-dev`                                             |
| Secret   | `AWS_ROLE_ARN`           | `arn:aws:iam::222222222222:role/github-actions-deploy-dev`                |
| Secret   | `ECS_EXECUTION_ROLE_ARN` | `arn:aws:iam::222222222222:role/ecsTaskExecutionRole`                     |
| Secret   | `ECS_TASK_ROLE_ARN`      | `arn:aws:iam::222222222222:role/fastapi-ecs-sample-task-role`             |

> Note: `ECR_REGISTRY` in each deploy environment points at the **same
> central registry** the CI job pushed to (the build/push account). It does
> not need to be a different registry per environment unless your
> organization replicates images into each account's own ECR — either
> pattern works as long as the deploy role in each account is granted
> `ecr:GetDownloadUrlForLayer` / `ecr:BatchGetImage` on that registry.

> **Common mistake:** `cd.yml` builds the image URI as
> `${ECR_REGISTRY}/${ECR_REPOSITORY}:${tag}`. If `ECR_REGISTRY` already
> includes the repository name (e.g. copy-pasted from
> `aws ecr describe-repositories`'s `repositoryUri`, which returns the
> *combined* value), the repository name ends up duplicated —
> `.../fastapi-ecs-sample/fastapi-ecs-sample:sha` — and ECS fails to pull
> with a "not found" error at deploy time. To get the correct split values:
> ```bash
> aws ecr describe-repositories --repository-names fastapi-ecs-sample \
>   --query "repositories[0].repositoryUri" --output text
> # e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/fastapi-ecs-sample
> # ECR_REGISTRY   = everything before the last "/"
> # ECR_REPOSITORY = everything after the last "/"
> ```

## Protection rules

On the `prod` environment specifically, add a **required reviewers** rule
(Settings → Environments → `prod` → Deployment protection rules). This is
what turns the `deploy-prod` job in `prod-pipeline.yml` (which calls
`cd.yml` with `environment: prod`) into a workflow that pauses and waits
for a human approval before it's allowed to run against the prod AWS
account. Optionally add the same rule to `stg` if you want a human
checkpoint between `qa` and `stg` too (see `nonprod-pipeline.yml`'s
`deploy-stg` job comment).

## Trust policy `sub` claim format

Each environment's `AWS_ROLE_ARN` role needs a trust policy condition
matching the **actual** `sub` claim GitHub sends — this is commonly
different from the plain `repo:<org>/<repo>:environment:<env>` format shown
in older examples/docs. Two things affect the real format:

1. **GitHub now uses immutable numeric IDs**, not the literal org/repo name:
   `repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:<env>`.
2. **Jobs called via a reusable workflow with `environment:` set** (which is
   exactly how every environment deploy in this project works — through
   `cd.yml`) get an additional suffix:
   `...:environment:<env>:job_workflow_ref:<owner>/<repo>/.github/workflows/cd.yml@refs/heads/<branch>`.

Don't guess — decode the actual token once per role using a temporary debug
step in the relevant workflow:
```yaml
- name: Debug OIDC token claims
  run: |
    curl -sSL -H "Authorization: Bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
      "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=sts.amazonaws.com" \
      | jq -r '.value' | cut -d'.' -f2 | base64 -d 2>/dev/null | jq .
  env:
    ACTIONS_ID_TOKEN_REQUEST_TOKEN: ${{ env.ACTIONS_ID_TOKEN_REQUEST_TOKEN }}
    ACTIONS_ID_TOKEN_REQUEST_URL: ${{ env.ACTIONS_ID_TOKEN_REQUEST_URL }}
```
then match the trust policy's `StringEquals` condition to the printed `sub`
value exactly (or `StringLike` with a trailing `*` if you want one role to
work regardless of which branch triggered it).

## Merge strategy requirement for prod promotion

`prod-pipeline.yml` identifies the exact commit to promote by reading the
**second parent** of the merge commit that lands on `main`. This only works
if release branches are merged into `main` using **"Create a merge
commit"**. Go to **Settings → General → Pull Requests** and make sure
**"Allow squash merging"** and **"Allow rebase merging"** are both
unchecked, leaving only **"Allow merge commits"** enabled — otherwise
`resolve-validated-commit` will fail with a clear error telling you the
same thing.
