# Atomic Commits Reference Examples

This document demonstrates how complex, multi-file changes should be decomposed into clean atomic commits.

---

## Scenario 1: Refactor + New Feature

**Situation:** You refactored the database connection pool in `db/pool.ts` to support retry backoff, and then implemented a new user analytics query in `services/analytics.ts`.

### ❌ Bad (Single Monolithic Commit)
```text
feat(analytics): add user retention query and refactor db pool
```
*Why it's bad:* Mixes infrastructure refactoring with business feature development. If the analytics query has a bug and needs to be reverted, the database pool refactor is lost with it.

### ✅ Good (Atomic Sequence)
1. `refactor(db): extract retry backoff strategy into connection pool`
   - Files: `db/pool.ts`
2. `feat(analytics): add retention metrics endpoint and query service`
   - Files: `services/analytics.ts`, `routes/analytics.ts`
3. `test(analytics): add unit tests for retention metrics calculation`
   - Files: `tests/analytics.test.ts`

---

## Scenario 2: Dependency Upgrade + Code Migration

**Situation:** Upgraded `@tanstack/react-query` from v4 to v5, requiring updates to `useQuery` call sites across UI components.

### ❌ Bad (Vague / Mixed)
```text
chore: update packages and fix ui
```

### ✅ Good (Atomic Sequence)
1. `build(deps): upgrade @tanstack/react-query to v5.0.0`
   - Files: `package.json`, `pnpm-lock.yaml`
2. `refactor(ui): migrate useQuery call sites to object syntax for v5 compatibility`
   - Files: `components/UserProfile.tsx`, `components/Dashboard.tsx`
3. `test(ui): update query client test mocks for v5 api`
   - Files: `tests/setupQueryMocks.ts`

---

## Scenario 3: Bugfix + Regression Test

**Situation:** Found an edge case where empty search queries threw a 500 error in `api/search.py`. Fixed the error and added a test case.

### ✅ Good (Atomic Sequence)
Option A (Combined when small and self-contained):
- `fix(search): return empty list instead of throwing on blank query`
  - Files: `api/search.py`, `tests/test_search.py`

Option B (Separated when test suite is substantial):
1. `fix(search): handle whitespace and empty string query parameters gracefully`
   - Files: `api/search.py`
2. `test(search): add regression tests for blank, whitespace, and null query parameters`
   - Files: `tests/test_search.py`

---

## Scenario 4: Full-Stack Feature Implementation

**Situation:** Added OAuth2 login with GitHub.

### ✅ Good (Topological Flow)
1. `build(deps): add passport-github2 and @types/passport-github2`
   - Files: `package.json`, `package-lock.json`
2. `feat(auth): add GitHub OAuth2 strategy and token exchange logic`
   - Files: `src/auth/github.ts`, `src/auth/passport.ts`
3. `feat(api): add /auth/github and /auth/github/callback routes`
   - Files: `src/routes/auth.ts`
4. `feat(ui): add "Sign in with GitHub" button to LoginView`
   - Files: `src/views/LoginView.tsx`, `src/styles/login.css`
5. `test(auth): add integration tests for OAuth2 callback handler`
   - Files: `tests/auth/github.test.ts`
6. `docs(auth): document required GITHUB_CLIENT_ID environment variables`
   - Files: `README.md`, `.env.example`
