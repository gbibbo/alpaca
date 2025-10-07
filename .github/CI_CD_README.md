# CI/CD Documentation

This document describes the continuous integration and deployment pipelines for the Algorithmic Trading Platform.

## Overview

The platform uses GitHub Actions for automated testing, building, and deployment. Three main workflows are configured:

1. **CI Pipeline** (`ci.yml`) - Runs on every push and pull request
2. **Release Pipeline** (`release.yml`) - Triggers on version tags
3. **Nightly Tests** (`nightly.yml`) - Scheduled comprehensive testing

---

## CI Pipeline (`ci.yml`)

### Triggers
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- Manual workflow dispatch

### Jobs

#### 1. Lint & Format Check
Validates code quality and formatting:
- **Black** - Code formatting
- **isort** - Import sorting
- **flake8** - Linting and style

```bash
# Run locally
black --check .
isort --check-only --profile black .
flake8 . --exclude=old_reference
```

#### 2. Type Checking
Static type analysis with mypy:
- Checks type annotations
- Ignores missing imports (for now)
- Non-blocking (continue-on-error)

```bash
# Run locally
mypy apps/ lib/ --ignore-missing-imports
```

#### 3. Unit Tests
Comprehensive unit test suite:
- Runs all tests in `tests/`
- Code coverage reporting
- Upload to Codecov
- Requires Redis service

```bash
# Run locally
export REDIS_URL=redis://localhost:6379/15
export BUS_BACKEND=streams
pytest tests/ -v --cov=apps --cov=lib
```

#### 4. Epic 6 Tests
Market hours and calendar validation:
- Tests NYSE/NASDAQ calendar
- Holiday detection
- Early close validation
- Timezone handling

```bash
# Run locally
pytest tests/test_epic6_market_hours.py -v
```

#### 5. Epic 7 Tests
Persistence and reproducibility:
- SQLite persistence
- CSV/Parquet export
- SHA256 verification
- Run management

```bash
# Run locally
pytest tests/test_epic7_persistence.py -v
```

#### 6. Integration Tests
End-to-end integration testing:
- Multi-component interactions
- Message bus integration
- Service communication

```bash
# Run locally
pytest tests/integration/ -v
```

#### 7. System Health Check
Comprehensive health validation:
- 13 system health tests
- Component integration
- Message bus health

```bash
# Run locally
python scripts/test_system_health.py
```

#### 8. Security Scanning
Security vulnerability detection:
- **Safety** - Known vulnerability check
- **Bandit** - Security linting
- Generates security reports

```bash
# Run locally
safety check
bandit -r apps/ lib/
```

#### 9. Build Status
Final status check and reporting

---

## Release Pipeline (`release.yml`)

### Triggers
- Push tags matching `v*.*.*` (e.g., `v1.0.0`)
- Manual workflow dispatch with version input

### Jobs

#### 1. Validate Version
Ensures version format is correct:
- Validates semver format (`X.Y.Z`)
- Checks for existing tags
- Sets version environment variable

#### 2. Test All
Runs complete test suite:
- All unit tests
- Epic 6 & 7 tests
- System health checks
- Must pass before release

#### 3. Build Artifacts
Creates release packages:
- Python distribution packages (wheel, sdist)
- Compressed source archive
- Validates packages with twine

#### 4. Create GitHub Release
Automated release creation:
- Generates changelog from commits
- Lists epic status
- Uploads artifacts
- Creates release notes

#### 5. Docker Build
Builds Docker images:
- Risk Manager image
- Executor image
- Simulator image
- Tags with version and `latest`

**Note**: Docker push disabled by default. Configure `DOCKER_USERNAME` and `DOCKER_PASSWORD` secrets to enable.

#### 6. Deploy Documentation
Documentation deployment:
- Builds with MkDocs
- Deploys to GitHub Pages

**Note**: Placeholder implementation. Configure when documentation is ready.

#### 7. Notify Release
Final status notification

---

## Nightly Tests (`nightly.yml`)

### Schedule
Runs daily at 2:00 AM UTC

### Jobs

#### 1. Test Matrix
Multi-version compatibility testing:
- **Python versions**: 3.9, 3.10, 3.11
- **Redis versions**: 6.0, 6.2, 7.0
- Complete test suite for each combination

```yaml
Strategy Matrix:
  python: [3.9, 3.10, 3.11]
  redis: [6.0, 6.2, 7.0]
  Total: 9 combinations
```

#### 2. Stress Tests
Load and performance testing:
- High-volume message processing
- Concurrent operations
- Resource usage validation

#### 3. Long-Running Backtest
Multi-year backtest validation:
- Backtest from 2020-present
- Validates long-term stability
- Generates performance reports

```bash
# Example
python scripts/sim_random.py \
  --symbol GOOGL \
  --start 2020-01-01 \
  --seed 42
```

#### 4. Persistence Verification
Reproducibility validation:
- Runs identical backtest twice
- Compares SHA256 hashes
- Ensures deterministic results

Expected: `Hash1 == Hash2` for same seed

#### 5. Market Hours Validation
Calendar accuracy verification:
- Validates all 2024 holidays
- Checks early close days
- Ensures correct market hours

#### 6. Dependency Audit
Security scanning:
- Safety vulnerability check
- pip-audit analysis
- Generates security reports

#### 7. Report Summary
Consolidated test results

---

## Local Development

### Running CI Checks Locally

**Quick validation** (before commit):
```bash
# Format check
black --check .
isort --check-only --profile black .

# Quick tests
make test-quick
```

**Complete CI simulation**:
```bash
# Linting
flake8 . --exclude=old_reference

# Type checking
mypy apps/ lib/ --ignore-missing-imports

# All tests
export REDIS_URL=redis://localhost:6379/15
export BUS_BACKEND=streams
pytest tests/ -v

# Epic tests
make test-epic6
make test-epic7

# System health
python scripts/test_system_health.py

# Security
safety check
bandit -r apps/ lib/
```

### Pre-commit Hooks

Install pre-commit hooks for automatic checks:

```bash
pip install pre-commit
pre-commit install
```

Create `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.12.0
    hooks:
      - id: black
  - repo: https://github.com/pycqa/isort
    rev: 5.13.0
    hooks:
      - id: isort
  - repo: https://github.com/pycqa/flake8
    rev: 6.1.0
    hooks:
      - id: flake8
```

---

## Creating a Release

### Automatic Release (Recommended)

1. Ensure all tests pass:
```bash
make test-regression
```

2. Create and push version tag:
```bash
git tag v1.0.0
git push origin v1.0.0
```

3. GitHub Actions automatically:
   - Runs all tests
   - Builds artifacts
   - Creates GitHub release
   - Builds Docker images
   - Deploys documentation

### Manual Release

Use workflow dispatch:
```bash
# Via GitHub UI:
# Actions → Release Pipeline → Run workflow
# Input version: 1.0.0
```

---

## Environment Variables

### CI Pipeline
```bash
PYTHON_VERSION=3.11
REDIS_VERSION=7
REDIS_URL=redis://localhost:6379/15
BUS_BACKEND=streams
USE_FAKE_REDIS=0
```

### Secrets Required

**For Docker Push** (optional):
- `DOCKER_USERNAME` - Docker Hub username
- `DOCKER_PASSWORD` - Docker Hub password/token

**For Release** (automatic):
- `GITHUB_TOKEN` - Automatically provided by GitHub

**For Codecov** (optional):
- `CODECOV_TOKEN` - Codecov upload token

---

## Monitoring CI/CD

### GitHub Actions Dashboard
View workflow runs:
```
https://github.com/<owner>/<repo>/actions
```

### Build Status Badges

Add to README.md:
```markdown
![CI](https://github.com/<owner>/<repo>/workflows/CI%20Pipeline/badge.svg)
![Release](https://github.com/<owner>/<repo>/workflows/Release%20Pipeline/badge.svg)
![Nightly](https://github.com/<owner>/<repo>/workflows/Nightly%20Tests/badge.svg)
```

### Coverage Reporting

View coverage:
```
https://codecov.io/gh/<owner>/<repo>
```

---

## Troubleshooting

### CI Failures

**Lint failures**:
```bash
# Auto-fix formatting
black .
isort --profile black .
```

**Type check failures**:
```bash
# Add type annotations
mypy apps/ lib/ --ignore-missing-imports --show-error-codes
```

**Test failures**:
```bash
# Run specific test
pytest tests/test_file.py::test_name -v

# Debug mode
pytest tests/test_file.py -vv --tb=long
```

**Redis connection issues**:
```bash
# Check Redis is running
redis-cli ping

# Use FakeRedis fallback
export USE_FAKE_REDIS=1
```

### Release Failures

**Invalid version tag**:
- Must match `v*.*.*` format
- Example: `v1.0.0`, `v2.1.3`

**Tests failing on release**:
- All tests must pass
- Check CI pipeline first
- Fix issues before tagging

**Docker build failures**:
- Ensure Dockerfiles exist in `docker/`
- Check Docker Hub credentials
- Review build logs

---

## Caching Strategy

### Pip Dependencies
Caches `~/.cache/pip` based on:
- OS
- Python version
- `requirements.txt` hash

Cache is automatically:
- Restored on each run
- Updated when requirements change
- Cleared when no longer needed

---

## Performance Optimization

### Parallel Jobs
Most jobs run in parallel:
- Lint (independent)
- Type checking (independent)
- Unit tests (independent)
- Epic 6 tests (independent)
- Epic 7 tests (independent)

Sequential when needed:
- Integration tests (after unit tests)
- Release (after all tests)

### Test Execution Time

| Job | Typical Duration |
|-----|-----------------|
| Lint | ~30 seconds |
| Type Check | ~45 seconds |
| Unit Tests | ~2 minutes |
| Epic 6 Tests | ~1 minute |
| Epic 7 Tests | ~1 minute |
| System Health | ~1 minute |
| Security Scan | ~1 minute |
| **Total** | **~7 minutes** |

---

## Best Practices

1. **Commit Often**: Small, focused commits
2. **Test Locally**: Run tests before pushing
3. **Use Pre-commit**: Catch issues early
4. **Monitor CI**: Check build status
5. **Fix Quickly**: Don't accumulate failures
6. **Tag Properly**: Follow semver
7. **Document Changes**: Update CHANGELOG

---

## Future Enhancements

- [ ] Add performance benchmarking
- [ ] Deploy to staging environment
- [ ] Automated integration testing
- [ ] Slack/Discord notifications
- [ ] Custom test reporters
- [ ] Multi-platform builds (Windows, macOS)
- [ ] Container vulnerability scanning
- [ ] Automated dependency updates

---

## Support

For CI/CD issues:
1. Check workflow logs in GitHub Actions
2. Review this documentation
3. Run tests locally first
4. Check Redis connectivity
5. Verify environment variables

Contact: See main README.md for support information
