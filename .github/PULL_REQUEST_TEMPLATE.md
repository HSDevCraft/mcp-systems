# Pull Request

## Summary
<!-- What does this PR do? One paragraph max. -->

## Type of Change
- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behaviour)
- [ ] New module/plugin
- [ ] Documentation update
- [ ] Refactoring (no functional change)
- [ ] CI/CD / infrastructure change

## Related Issues
Closes # <!-- issue number -->

## Changes Made
<!-- Bullet points of what changed -->
-
-

## Testing
- [ ] Unit tests added / updated (`tests/unit/`)
- [ ] Integration tests added / updated (`tests/integration/`)
- [ ] `make test-fast` passes locally
- [ ] `make lint` passes with no errors
- [ ] `make type-check` passes with no errors

## If Adding a New Module
- [ ] Implements `MCPModule` interface fully
- [ ] `on_load()` handles missing dependencies gracefully
- [ ] `health_check()` verifies external dependencies
- [ ] Input/output schemas have field descriptions
- [ ] Registered in `src/modules/plugins/__init__.py`
- [ ] Unit tests cover all input variations

## Checklist
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No secrets or credentials in code
- [ ] Documentation updated if public API changed
- [ ] Breaking changes noted in PR description
