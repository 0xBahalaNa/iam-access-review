-- Staging schema for synthetic UAR extracts.
-- Every source column is TEXT: CSV arrives as strings. Counts are INTEGER.

CREATE TABLE hr_roster (
    employee_id TEXT PRIMARY KEY,
    full_name TEXT,
    email TEXT,
    department TEXT,
    manager_employee_id TEXT,
    status TEXT,
    hire_date TEXT,
    termination_date TEXT
);

CREATE TABLE idp_users (
    idp_user_id TEXT PRIMARY KEY,
    upn TEXT,
    display_name TEXT,
    enabled TEXT,
    last_login_date TEXT,
    employee_id TEXT
);

CREATE TABLE idp_groups (
    group_id TEXT PRIMARY KEY,
    group_name TEXT,
    owner_employee_id TEXT,
    privileged TEXT
);

-- Edge table: users and nested groups both live here.
-- member_type is 'user' or 'group'. This is what the later recursive CTE walks.
CREATE TABLE idp_group_members (
    group_id TEXT,
    member_type TEXT,
    member_id TEXT
);

CREATE TABLE app_entitlements (
    app_name TEXT,
    app_account_id TEXT,
    entitlement_name TEXT,
    account_enabled TEXT,
    last_activity_date TEXT,
    identifier_kind TEXT,
    identifier_value TEXT
);

CREATE TABLE ingest_counts (
    source_file TEXT PRIMARY KEY,
    rows_extracted INTEGER,
    rows_staged INTEGER,
    rows_rejected INTEGER,
    sha256 TEXT
);

CREATE TABLE staging_rejects (
    source_file TEXT,
    line_no INTEGER,
    reason TEXT,
    raw_row TEXT
);
