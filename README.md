# production-grade-cicd
A production-shaped sample project: a small FastAPI service plus a complete CI/CD pipeline that tests it, statically analyzes it (SonarCloud), builds a hardened Docker image, scans it (Trivy), and promotes one immutable image across `dev` → `qa` → `stg` → `prod` on AWS ECS via GitHub Actions
