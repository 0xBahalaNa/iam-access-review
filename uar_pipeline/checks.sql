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

DROP VIEW IF EXISTS exceptions;
CREATE VIEW exceptions AS
SELECT * FROM check_reconciliation
UNION ALL
SELECT * FROM check_completeness;

-- Not a control. Catalog LEFT JOIN so a quiet check still counts as 0.
DROP VIEW IF EXISTS check_summary;
CREATE VIEW check_summary AS
SELECT catalog.check_name, count(exceptions.record_id) AS exception_count
FROM (
    SELECT 'check_reconciliation' AS check_name
    UNION ALL
    SELECT 'check_completeness'
) AS catalog
LEFT JOIN exceptions ON exceptions.check_name = catalog.check_name
GROUP BY catalog.check_name
ORDER BY catalog.check_name;
