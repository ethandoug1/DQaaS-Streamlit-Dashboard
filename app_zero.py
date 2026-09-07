import io
import duckdb
import pandas as pd
import requests
import streamlit as st

# 1. Page Configuration
st.set_page_config(
    page_title="Metadata Driven DQ Profile",
    page_icon="📈",
    layout="wide",
)

st.title("📈 Metadata Driven DQ Profile")
st.caption("No pre-loaded data or rules. Upload a dataset and rules.xlsx to execute an audit.")

# =========================================================
# SECTION 1: INGESTION ENGINE (SIDEBAR)
# =========================================================
st.sidebar.header("📥 Ingestion Engine")
source_type = st.sidebar.radio(
    "Select Data Source Type:",
    [
        "Local CSV File",
        "Cloud Database (SQL)",
        "REST API Endpoint",
    ],
)

df_target = None

# OPTION 1: Local File Upload (Strictly No Fallback)
if source_type == "Local CSV File":
    uploaded_file = st.sidebar.file_uploader("Upload CSV Dataset", type=["csv"])
    if uploaded_file is not None:
        try:
            df_target = pd.read_csv(uploaded_file)
            st.sidebar.success(f"Loaded CSV: {len(df_target):,} rows")
        except Exception as e:
            st.sidebar.error(f"Error reading CSV: {e}")

# OPTION 2: Cloud Database (SQL Queries)
elif source_type == "Cloud Database (SQL)":
    st.sidebar.subheader("Database Query Settings")
    db_type = st.sidebar.selectbox(
        "Select Engine", ["PostgreSQL", "Snowflake", "BigQuery", "SQLite"]
    )
    query_str = st.sidebar.text_area(
        "SQL Query", value="SELECT * FROM orders LIMIT 1000;"
    )

    if st.sidebar.button("Run Query"):
        try:
            conn = st.connection("my_cloud_db", type="sql")
            df_target = conn.query(query_str)
            st.sidebar.success(f"Fetched {len(df_target)} rows from {db_type}")
        except Exception as e:
            st.sidebar.error(f"Database connection error: {e}")

# OPTION 3: REST API Endpoint
elif source_type == "REST API Endpoint":
    st.sidebar.subheader("API Request Settings")
    api_url = st.sidebar.text_input("API Endpoint URL")
    api_key = st.sidebar.text_input("API Key / Bearer Token", type="password")

    if st.sidebar.button("Fetch API Data"):
        if api_url:
            try:
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                response = requests.get(api_url, headers=headers, timeout=10)

                if response.status_code == 200:
                    df_target = pd.DataFrame(response.json())
                    st.sidebar.success(f"Retrieved {len(df_target)} records")
                else:
                    st.sidebar.error(f"API Error {response.status_code}")
            except Exception as e:
                st.sidebar.error(f"API request failed: {e}")
        else:
            st.sidebar.warning("Please enter an API Endpoint URL.")

# =========================================================
# SECTION 2: DUCKDB INITIALIZATION
# =========================================================
con = duckdb.connect()
has_data = False

if df_target is not None and not df_target.empty:
    con.register("target_data", df_target)
    total_rows = con.execute("SELECT COUNT(*) FROM target_data").fetchone()[0]
    columns_info = con.execute("DESCRIBE target_data").fetchall()
    all_columns = [col[0] for col in columns_info]
    has_data = True

# =========================================================
# SECTION 3: METADATA EXCEL RULE INGESTION (SIDEBAR)
# =========================================================
metadata_rules_df = None

if has_data:
    st.sidebar.divider()
    st.sidebar.header("📜 Metadata Rules (Excel)")
    
    excel_file = st.sidebar.file_uploader(
        "Upload Metadata Rules (.xlsx)", type=["xlsx"]
    )

    if excel_file is not None:
        try:
            metadata_rules_df = pd.read_excel(excel_file)
            st.sidebar.success("Loaded metadata rules.")
        except Exception as e:
            st.sidebar.error(f"Error reading rules.xlsx: {e}")
    else:
        st.sidebar.info("Upload a rules.xlsx file to trigger quality checks.")

# =========================================================
# SECTION 4: METADATA RULE EVALUATION ENGINE
# =========================================================
audit_log_df = pd.DataFrame()
profile_df = pd.DataFrame()

if has_data and metadata_rules_df is not None and not metadata_rules_df.empty:

    audit_records = []
    col_map = {c.strip().lower(): c for c in all_columns}

    # Normalize metadata headers
    metadata_rules_df.columns = [
        c.strip().lower() for c in metadata_rules_df.columns
    ]

    # Evaluate rules matching dataset
    for _, row in metadata_rules_df.iterrows():
        raw_col = str(row.get("column_name", "")).strip()
        col_key = raw_col.lower()
        rule_type = str(row.get("rule_type", "")).strip().lower()
        severity = str(row.get("severity", "MEDIUM")).strip().upper()

        if col_key in col_map:
            col_name = col_map[col_key]
            c = f'"{col_name}"'

            # Completeness
            if rule_type == "completeness":
                null_cnt = con.execute(
                    f"SELECT COUNT(*) - COUNT({c}) FROM target_data"
                ).fetchone()[0]
                passed = (null_cnt == 0)
                details = f"Found {null_cnt:,} null values"

            # Uniqueness
            elif rule_type == "uniqueness":
                dup_cnt = con.execute(
                    f"SELECT COUNT({c}) - COUNT(DISTINCT {c}) FROM target_data"
                ).fetchone()[0]
                passed = (dup_cnt == 0)
                details = f"Found {dup_cnt:,} duplicate keys"

            # Range
            elif rule_type == "range":
                p_min = row.get("parameter_min", 0)
                p_max = row.get("parameter_max", 1000000)
                violations = con.execute(
                    f"SELECT COUNT(*) FROM target_data WHERE {c} < {p_min} OR {c} > {p_max}"
                ).fetchone()[0]
                passed = (violations == 0)
                details = f"Found {violations:,} records outside range [{p_min}, {p_max}]"

            # Allowed Values
            elif rule_type == "allowed_values":
                raw_vals = str(row.get("allowed_values", "")).split(",")
                vals_formatted = ", ".join([f"'{v.strip()}'" for v in raw_vals])
                invalid_cnt = con.execute(
                    f"SELECT COUNT(*) FROM target_data WHERE {c} NOT IN ({vals_formatted}) AND {c} IS NOT NULL"
                ).fetchone()[0]
                passed = (invalid_cnt == 0)
                details = f"Found {invalid_cnt:,} invalid value entries"
            else:
                passed, details = True, f"Rule type '{rule_type}' unhandled"

            audit_records.append(
                {
                    "Column Name": col_name,
                    "Rule Type": rule_type.upper(),
                    "Severity": severity,
                    "Status": "PASSED" if passed else "FAILED",
                    "Audit Details": details,
                }
            )

    audit_log_df = pd.DataFrame(audit_records)

    # Generate Attribute Profile strictly for metadata columns
    target_cols = []
    for raw_c in metadata_rules_df["column_name"].dropna().unique():
        k = str(raw_c).strip().lower()
        if k in col_map:
            target_cols.append(col_map[k])

    query_parts = []
    for col in target_cols:
        c = f'"{col}"'
        query_parts.append(f"""
            SELECT 
                '{col}' AS attribute,
                COUNT(*) - COUNT({c}) AS null_count,
                SUM(CASE WHEN {c} IS NOT NULL AND TRIM(CAST({c} AS VARCHAR)) = '' THEN 1 ELSE 0 END) AS blank_count,
                ROUND(((COUNT(*) - COUNT({c})) * 100.0 / COUNT(*)), 2) AS missing_pct,
                COUNT(DISTINCT {c}) AS distinct_count,
                MIN(LENGTH(CAST({c} AS VARCHAR))) AS min_len,
                MAX(LENGTH(CAST({c} AS VARCHAR))) AS max_len,
                MIN(TRY_CAST({c} AS DOUBLE)) AS min_numeric,
                MAX(TRY_CAST({c} AS DOUBLE)) AS max_numeric
            FROM target_data
        """)

    if query_parts:
        full_profile_query = " UNION ALL ".join(query_parts)
        profile_df = con.execute(full_profile_query).df()

    # Metrics computation
    if not audit_log_df.empty:
        total_rules = len(audit_log_df)
        passed_rules = len(audit_log_df[audit_log_df["Status"] == "PASSED"])
        failed_rules = total_rules - passed_rules
        health_score = int((passed_rules / total_rules) * 100)
    else:
        total_rules, passed_rules, failed_rules, health_score = 0, 0, 0, 100

    # Top Banner Display
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Metadata Health Score", f"{health_score}%")
    col2.metric("Total Rows Audited", f"{total_rows:,}")
    col3.metric("Evaluated Rules", total_rules)
    col4.metric("Passed Rules", passed_rules)
    col5.metric("Failed Rules", failed_rules)

    st.divider()

    # Tabbed Display
    tab_excel, tab_profile, tab_preview, tab_schema = st.tabs(
        [
            "📑 Excel Audit Log",
            "📊 Metadata Attribute Profile",
            "📋 Data Preview",
            "📐 Schema & Types",
        ]
    )

    with tab_excel:
        st.subheader("🚨 Audit Results")
        if not audit_log_df.empty:
            st.dataframe(audit_log_df, use_container_width=True)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                audit_log_df.to_excel(writer, index=False, sheet_name="Audit_Report")
                if not profile_df.empty:
                    profile_df.to_excel(writer, index=False, sheet_name="Attribute_Profile")
            processed_data = output.getvalue()

            st.download_button(
                label="📥 Download Audit Report (.xlsx)",
                data=processed_data,
                file_name="audit_log.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with tab_profile:
        st.subheader("Attribute Profile")
        if not profile_df.empty:
            st.dataframe(profile_df, use_container_width=True)

    with tab_preview:
        st.subheader("Data Preview (First 100 Rows)")
        st.dataframe(con.execute("SELECT * FROM target_data LIMIT 100").df(), use_container_width=True)

    with tab_schema:
        st.subheader("Dataset Schema")
        schema_df = pd.DataFrame(
            columns_info, columns=["Column Name", "Type", "Null?", "Key", "Default", "Extra"]
        )
        st.dataframe(schema_df[["Column Name", "Type"]], use_container_width=True)

elif has_data:
    st.info("👈 Dataset loaded! Please upload a `rules.xlsx` file in the sidebar to run checks.")
    st.subheader("📋 Dataset Preview")
    st.dataframe(con.execute("SELECT * FROM target_data LIMIT 10").df(), use_container_width=True)

else:
    st.info("👈 Please upload a CSV dataset in the sidebar to begin.")