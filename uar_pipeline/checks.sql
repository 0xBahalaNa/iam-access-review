-- UAR control checks as views. SQL decides; Python applies and prints counts.

-- Identity resolution for app accounts. Salesforce matches identifier_value to
-- idp_users.upn. GitHub matches identifier_value to a derived username
-- (lowercase display_name, spaces to hyphens). Unmatched rows keep NULL
-- idp_user_id / employee_id so later orphan checks can see them.
-- Each view is dropped first so re-running the checks replaces it instead of
-- failing on "view already exists" (CREATE VIEW is not idempotent in SQLite).
DROP VIEW IF EXISTS resolved_accounts;
CREATE VIEW resolved_accounts AS
SELECT
    a.app_name, a.app_account_id, a.entitlement_name, a.account_enabled,
    a.last_activity_date, a.identifier_kind, a.identifier_value,
    CASE WHEN a.app_name = 'Salesforce' THEN sf.idp_user_id
         WHEN a.app_name = 'GitHub' THEN gh.idp_user_id END AS idp_user_id,
    CASE WHEN a.app_name = 'Salesforce' THEN sf.employee_id
         WHEN a.app_name = 'GitHub' THEN gh.employee_id END AS employee_id
FROM app_entitlements AS a
LEFT JOIN idp_users AS sf
    ON a.app_name = 'Salesforce' AND a.identifier_value = sf.upn
LEFT JOIN idp_users AS gh
    ON a.app_name = 'GitHub'
   AND a.identifier_value = lower(replace(gh.display_name, ' ', '-'));

-- Completeness of the extract, not of identities. Every row counted at extract
-- time must still be staged or rejected. A mismatch means a row disappeared
-- between CSV and SQLite, so the population is not defensible. A healthy ingest
-- of this pack is quiet; the planted unparseable date is a reject, not a loss.
DROP VIEW IF EXISTS check_reconciliation;
CREATE VIEW check_reconciliation AS
SELECT
    'check_reconciliation' AS check_name,
    'row_count_mismatch' AS exception_type,
    source_file AS source_system,
    source_file AS record_id,
    'extracted=' || rows_extracted || ' staged=' || rows_staged
        || ' rejected=' || rows_rejected AS detail
FROM ingest_counts
WHERE rows_extracted != rows_staged + rows_rejected;

-- Population completeness against HR. An enabled IdP user with a blank
-- employee_id, or one that is not on the roster, sits in the access system
-- without a join to the workforce file. The review cannot claim it covered
-- every in-scope identity. This pack plants U025 (blank employee_id).
DROP VIEW IF EXISTS check_completeness;
CREATE VIEW check_completeness AS
SELECT
    'check_completeness' AS check_name,
    'missing_hr_identity' AS exception_type,
    'idp_users' AS source_system,
    u.idp_user_id AS record_id,
    'enabled IdP user employee_id=' || coalesce(u.employee_id, '') AS detail
FROM idp_users AS u
LEFT JOIN hr_roster AS h ON h.employee_id = u.employee_id
WHERE u.enabled = '1'
  AND (trim(coalesce(u.employee_id, '')) = '' OR h.employee_id IS NULL);

-- Terminated-but-active. HR says the person left; the IdP or an app still
-- has them enabled. The review must surface that, or disable-on-termination
-- is untested. This pack plants U008 (Morgan Voss / E008, terminated
-- 2026-02-28) and Salesforce sf-006, which resolves to the same employee.
-- E025 never stages, so it cannot appear here.
DROP VIEW IF EXISTS check_terminated_active;
CREATE VIEW check_terminated_active AS
SELECT
    'check_terminated_active' AS check_name,
    'terminated_still_enabled' AS exception_type,
    'idp_users' AS source_system,
    u.idp_user_id AS record_id,
    'enabled IdP user employee_id=' || u.employee_id
        || ' status=terminated' AS detail
FROM idp_users AS u
JOIN hr_roster AS h ON h.employee_id = u.employee_id
WHERE u.enabled = '1'
  AND h.status = 'terminated'
UNION ALL
SELECT
    'check_terminated_active',
    'terminated_still_enabled',
    a.app_name,
    a.app_account_id,
    'enabled ' || a.app_name || ' account employee_id=' || a.employee_id
        || ' status=terminated'
FROM resolved_accounts AS a
JOIN hr_roster AS h ON h.employee_id = a.employee_id
WHERE a.account_enabled = '1'
  AND h.status = 'terminated';

-- Orphaned app accounts. An enabled account whose identifier matches no
-- IdP user is access with no identity in the review population. Salesforce
-- joins on UPN; GitHub joins on a derived username. This pack plants
-- sf-016 (sf-orphan@example.com) and gh-011 (sortega, not sam-ortega).
DROP VIEW IF EXISTS check_orphaned_accounts;
CREATE VIEW check_orphaned_accounts AS
SELECT
    'check_orphaned_accounts' AS check_name,
    'unresolved_identity' AS exception_type,
    a.app_name AS source_system,
    a.app_account_id AS record_id,
    'enabled account identifier=' || a.identifier_value
        || ' resolved to no IdP user' AS detail
FROM resolved_accounts AS a
WHERE a.account_enabled = '1'
  AND a.idp_user_id IS NULL;

-- Dormant access. Enabled IdP users or app accounts with no activity on
-- or after 2026-04-01, or a blank activity date. The cutoff is a fixture
-- literal, not a rolling window, so the packet does not depend on when
-- the check runs. This pack plants U012 (last login 2025-05-20) and
-- sf-008 (last activity 2025-05-18).
DROP VIEW IF EXISTS check_dormant;
CREATE VIEW check_dormant AS
SELECT
    'check_dormant' AS check_name,
    'dormant_access' AS exception_type,
    'idp_users' AS source_system,
    u.idp_user_id AS record_id,
    'enabled IdP user last_login_date=' || coalesce(u.last_login_date, '')
        || ' cutoff=2026-04-01' AS detail
FROM idp_users AS u
WHERE u.enabled = '1'
  AND (trim(coalesce(u.last_login_date, '')) = ''
       OR u.last_login_date < '2026-04-01')
UNION ALL
SELECT
    'check_dormant',
    'dormant_access',
    a.app_name,
    a.app_account_id,
    'enabled account last_activity_date='
        || coalesce(a.last_activity_date, '')
        || ' cutoff=2026-04-01'
FROM resolved_accounts AS a
WHERE a.account_enabled = '1'
  AND (trim(coalesce(a.last_activity_date, '')) = ''
       OR a.last_activity_date < '2026-04-01');

-- Ownerless groups. A group with a blank owner, an owner missing from
-- staged HR, or an owner who is not active has no accountable reviewer.
-- This pack plants grp-shadow-it (blank owner_employee_id).
DROP VIEW IF EXISTS check_ownerless_groups;
CREATE VIEW check_ownerless_groups AS
SELECT
    'check_ownerless_groups' AS check_name,
    'ownerless_group' AS exception_type,
    'idp_groups' AS source_system,
    g.group_id AS record_id,
    'owner_employee_id=' || coalesce(g.owner_employee_id, '')
        || ' not an active staged HR employee' AS detail
FROM idp_groups AS g
LEFT JOIN hr_roster AS h ON h.employee_id = g.owner_employee_id
WHERE trim(coalesce(g.owner_employee_id, '')) = ''
   OR h.employee_id IS NULL
   OR h.status != 'active';

-- Flattened group membership. The edge table stores one hop; this view
-- walks nested groups until no new (group, user) pair appears. UNION
-- (not UNION ALL) discards already-seen rows, so a cyclic membership
-- graph terminates. direct=1 is a listed user edge; direct=0 arrived
-- through a nested group. Not a control — checks 7 and 8 read it.
DROP VIEW IF EXISTS effective_group_members;
CREATE VIEW effective_group_members AS
WITH RECURSIVE walk(group_id, idp_user_id, direct) AS (
    SELECT group_id, member_id, 1
    FROM idp_group_members
    WHERE member_type = 'user'
    UNION
    SELECT parent.group_id, walk.idp_user_id, 0
    FROM idp_group_members AS parent
    JOIN walk ON walk.group_id = parent.member_id
    WHERE parent.member_type = 'group'
)
SELECT group_id, idp_user_id, direct FROM walk;

-- Direct-assigned privileged entitlements. An admin-named app role
-- whose holder is not an effective member of any privileged IdP group
-- is access that skipped the group-governed path. Privileged groups
-- are the privileged column (data), not a name pattern; entitlements
-- match '%admin%' case-insensitive. This pack plants sf-012
-- (Salesforce System Administrator on Avery Kim / U018).
DROP VIEW IF EXISTS check_direct_assignment;
CREATE VIEW check_direct_assignment AS
SELECT
    'check_direct_assignment' AS check_name,
    'direct_admin_assignment' AS exception_type,
    a.app_name AS source_system,
    a.app_account_id AS record_id,
    'enabled ' || a.app_name || ' entitlement=' || a.entitlement_name
        || ' idp_user_id=' || a.idp_user_id
        || ' has no privileged group membership' AS detail
FROM resolved_accounts AS a
WHERE a.account_enabled = '1'
  AND lower(a.entitlement_name) LIKE '%admin%'
  AND a.idp_user_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM effective_group_members AS e
      JOIN idp_groups AS g ON g.group_id = e.group_id
      WHERE e.idp_user_id = a.idp_user_id
        AND g.privileged = '1'
  );

-- Nested privileged reach. A user who is not a listed member of a
-- privileged group but who reaches it through nested groups holds
-- that privilege without appearing on the group's roster. This pack
-- plants a three-hop chain: grp-app-admins (privileged) contains
-- grp-finance-analysts contains grp-finance-all contains U007.
-- Two-hop members U014/U017 and three-hop members U004/U007/U008/U022
-- all reach grp-app-admins without a direct edge.
DROP VIEW IF EXISTS check_nested_privileged_reach;
CREATE VIEW check_nested_privileged_reach AS
SELECT
    'check_nested_privileged_reach' AS check_name,
    'nested_privileged_reach' AS exception_type,
    'idp_users' AS source_system,
    e.idp_user_id AS record_id,
    'effective member of privileged group ' || e.group_id
        || ' via nested group, not a direct member' AS detail
FROM effective_group_members AS e
JOIN idp_groups AS g ON g.group_id = e.group_id
WHERE g.privileged = '1'
  AND e.direct = 0
  AND NOT EXISTS (
      SELECT 1
      FROM idp_group_members AS m
      WHERE m.group_id = e.group_id
        AND m.member_type = 'user'
        AND m.member_id = e.idp_user_id
  );

DROP VIEW IF EXISTS exceptions;
CREATE VIEW exceptions AS
SELECT * FROM check_reconciliation
UNION ALL
SELECT * FROM check_completeness
UNION ALL
SELECT * FROM check_terminated_active
UNION ALL
SELECT * FROM check_orphaned_accounts
UNION ALL
SELECT * FROM check_dormant
UNION ALL
SELECT * FROM check_ownerless_groups
UNION ALL
SELECT * FROM check_direct_assignment
UNION ALL
SELECT * FROM check_nested_privileged_reach;

-- Not a control. Catalog LEFT JOIN so a quiet check still counts as 0.
DROP VIEW IF EXISTS check_summary;
CREATE VIEW check_summary AS
SELECT catalog.check_name, count(exceptions.record_id) AS exception_count
FROM (
    SELECT 'check_reconciliation' AS check_name
    UNION ALL
    SELECT 'check_completeness'
    UNION ALL
    SELECT 'check_terminated_active'
    UNION ALL
    SELECT 'check_orphaned_accounts'
    UNION ALL
    SELECT 'check_dormant'
    UNION ALL
    SELECT 'check_ownerless_groups'
    UNION ALL
    SELECT 'check_direct_assignment'
    UNION ALL
    SELECT 'check_nested_privileged_reach'
) AS catalog
LEFT JOIN exceptions ON exceptions.check_name = catalog.check_name
GROUP BY catalog.check_name
ORDER BY catalog.check_name;
