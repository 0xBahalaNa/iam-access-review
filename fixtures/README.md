# Fixture pack (synthetic)

As-of date for every date in this pack: **2026-06-30** (Q2 review). Dates are
hand-written literals relative to that day. Nothing is computed from "today".

Load with:

```bash
python -m uar_pipeline.ingest --fixtures fixtures/ --db build/uar.db
```

Three source systems, six CSV files. Ingest stages rows; it does not resolve
identities or run control checks.

## Sources and columns

### `hr_roster.csv` → table `hr_roster`

| Column | Notes |
|---|---|
| `employee_id` | Primary key |
| `full_name` | Display name |
| `email` | `example.com` only |
| `department` | Business unit |
| `manager_employee_id` | Blank for the top of the tree |
| `status` | `active` or `terminated` |
| `hire_date` | `YYYY-MM-DD` |
| `termination_date` | Blank when still employed |

### `idp_users.csv` → table `idp_users`

| Column | Notes |
|---|---|
| `idp_user_id` | Primary key |
| `upn` | Sign-in name |
| `display_name` | Display name |
| `enabled` | `1` or `0` |
| `last_login_date` | `YYYY-MM-DD`; blank allowed |
| `employee_id` | HR join key; **nullable** |

### `idp_groups.csv` → table `idp_groups`

| Column | Notes |
|---|---|
| `group_id` | Primary key |
| `group_name` | Display name |
| `owner_employee_id` | Accountable owner; **nullable** |
| `privileged` | `1` if the group confers privileged access |

### `idp_group_members.csv` → table `idp_group_members`

Edge table. One row per membership.

| Column | Notes |
|---|---|
| `group_id` | Parent group |
| `member_type` | `user` or `group` |
| `member_id` | `idp_user_id` or nested `group_id` |

### `app_entitlements_salesforce.csv` and `app_entitlements_github.csv` → table `app_entitlements`

Same columns, two extracts, one staging table.

| Column | Notes |
|---|---|
| `app_name` | `Salesforce` or `GitHub` |
| `app_account_id` | App-local account id |
| `entitlement_name` | Role or team grant |
| `account_enabled` | `1` or `0` |
| `last_activity_date` | `YYYY-MM-DD` |
| `identifier_kind` | `email` (Salesforce) or `username` (GitHub) |
| `identifier_value` | The identifier of that kind |

## Seeded defects

These rows are planted so later SQL checks have something real to catch.
This milestone only *stages* them. It does not evaluate them, except defect 8.

| # | Defect | Where it lives |
|---|---|---|
| 1 | Nested-group privileged reach (3 hops) | `idp_group_members`: `grp-app-admins` (privileged) contains `grp-finance-analysts`, which contains `grp-finance-all`, which contains user `U007` (Jordan Hale / E007) |
| 2 | Terminated-but-active | `hr_roster` `E008` Morgan Voss `status=terminated`, `termination_date=2026-02-28`; `idp_users` `U008` still `enabled=1` |
| 3 | Orphaned account | Salesforce `sf-016`, `identifier_value=sf-orphan@example.com` — matches no IdP UPN and no HR email |
| 4 | Dormant access | `idp_users` `U012` Riley Chen `enabled=1`, `last_login_date=2025-05-20` (~406 days before as-of). Salesforce `sf-008` last activity `2025-05-18` |
| 5 | Ownerless group | `idp_groups` `grp-shadow-it`, `owner_employee_id` blank |
| 6 | Direct assignment | Salesforce `sf-012` grants `Salesforce System Administrator` to Avery Kim (`avery.kim@example.com` / `U018`). That user is in `grp-engineering` and `grp-everyone` only — not in the `grp-app-admins` nest |
| 7 | Inconsistent identifiers | Salesforce keys by `email`; GitHub keys by `username`. `idp_users` `U025` Sam Ortega has a blank `employee_id`; GitHub `gh-011` is `sortega` |
| 8 | Unstageable row | `hr_roster` `E025` Wei Zhang, `termination_date=2026-13-01` (line 26). Lands in `staging_rejects` with file, line number, and a reason naming the column and value. Not dropped, not skipped |

## Volume

About 25 HR rows (24 stage, 1 reject), 25 IdP users, 8 groups, 40 membership
edges, 28 entitlement rows (16 Salesforce + 12 GitHub).
