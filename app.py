import io
import os
import duckdb
import matplotlib.pyplot as plt
import pandas as pd
import requests
import seaborn as sns
import streamlit as st

# 1. Page Configuration
st.set_page_config(
    page_title="DQaaS - Metadata-Driven DuckDB Suite",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Metadata-Driven Data Quality & Profiling Suite")

# =========================================================
# SECTION 1: INGESTION ENGINE (SIDEBAR)
# =========================================================
st.sidebar.header("📥 Ingestion Engine")
source_type = st.sidebar.radio(
    "Select Data Source Type:",
    [
        "Local CSV File",
        "Cloud Database (SQL)",
        "Cloud Storage (S3/GCS)",
        "REST API Endpoint",
    ],
)

target_file_path = None
df_temp = None

# OPTION 1: Local File Upload or Default Fallback
if source_type == "Local CSV File":
    uploaded_file = st.sidebar.file_uploader("Upload CSV", type=["csv"])
    default_path = "sample_orders.csv"  # Relative path for Cloud compatibility

    if uploaded_file is not None:
        temp_path = "temp_uploaded.csv"  # Relative temp file for Cloud compatibility
        with open(temp_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        target_file_path = temp_path
        st.sidebar.success("Loaded uploaded CSV into temp buffer.")
    elif os.path.exists(default_path):
        target_file_path = default_path
        st.sidebar.info("Using default local dataset.")

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
            df_temp = conn.query(query_str)
            st.sidebar.success(f"Fetched {len(df_temp)} rows from {db_type}")
        except Exception as e:
            st.sidebar.error(f"Database connection error: {e}")

# OPTION 3: Cloud Storage Selectors (S3/GCS)
elif source_type == "Cloud Storage (S3/GCS)":
    st.sidebar.subheader("Cloud Storage Settings")
    cloud_provider = st.sidebar.selectbox(
        "Provider", ["AWS S3", "Google Cloud Storage"]
    )
    cloud_url = st.sidebar.text_input(
        "Bucket Path / URL",
        value=(
            "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/penguins.csv"
        ),
    )

    if st.sidebar.button("Fetch Cloud File"):
        target_file_path = cloud_url
        st.sidebar.success(f"Targeting cloud object from {cloud_provider}")

# OPTION 4: REST API Endpoint
elif source_type == "REST API Endpoint":
    st.sidebar.subheader("API Request Settings")
    api_url = st.sidebar.text_input(
        "API Endpoint URL", value="https://jsonplaceholder.typicode.com/posts"
    )
    api_key = st.sidebar.text_input("API Key / Bearer Token", type="password")

    if st.sidebar.button("Fetch API Data"):
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            response = requests.get(api_url, headers=headers, timeout=10)

            if response.status_code == 200:
                df_temp = pd.DataFrame(response.json())
                st.sidebar.success(f"Retrieved {len(df_temp)} records")
            else:
                st.sidebar.error(f"API Error {response.status_code}")
        except Exception as e:
            st.sidebar.error(f"API request failed: {e}")

# =========================================================
# SECTION 2: DUCKDB INITIALIZATION & VIEW CREATION
# =========================================================
con = duckdb.connect()

if target_file_path:
    try:
        con.execute(
            f"CREATE OR REPLACE VIEW target_data AS SELECT * FROM"
            f" read_csv_auto('{target_file_path}')"
        )
    except Exception as e:
        st.error(f"DuckDB File Read Error: {e}")
elif df_temp is not None and not df_temp.empty:
    con.register("target_data", df_temp)

# Verify if target_data view exists in DuckDB
has_data = False
try:
    total_rows = con.execute("SELECT COUNT(*) FROM target_data").fetchone()[0]
    columns_info = con.execute("DESCRIBE target_data").fetchall()
    all_columns = [col[0] for col in columns_info]
    has_data = True
except Exception:
    has_data = False

# =========================================================
# SECTION 3: METADATA EXCEL RULE INGESTION (SIDEBAR)
# =========================================================
metadata_rules_df = None

if has_data:
    st.sidebar.divider()
    st.sidebar.header("📜 Metadata Configuration (Excel)")
    
    excel_file = st.sidebar.file_uploader(
        "Upload Metadata Rules (.xlsx)", type=["xlsx"]
    )
    default_rules_path = "rules.xlsx"  # Relative path for Cloud compatibility

    if excel_file is not None:
        metadata_rules_df = pd.read_excel(excel_file)
        st.sidebar.success("Loaded uploaded rules.xlsx.")
    elif os.path.exists(default_rules_path):
        metadata_rules_df = pd.read_excel(default_rules_path)
        st.sidebar.info("Using default rules.xlsx.")

# =========================================================
# SECTION 4: METADATA RULE EVALUATION ENGINE (CASE-INSENSITIVE & STRICT)
# =========================================================
audit_log_df = pd.DataFrame()
profile_df = pd.DataFrame()

if has_data:

    audit_records = []

    # Map lower-case dataset column names to exact DuckDB identifiers
    col_map = {c.strip().lower(): c for c in all_columns}

    # 1. Evaluate ONLY rules declared inside rules.xlsx
    if metadata_rules_df is not None and not metadata_rules_df.empty:
        # Standardize metadata column header strings
        metadata_rules_df.columns = [
            c.strip().lower() for c in metadata_rules_df.columns
        ]

        for _, row in metadata_rules_df.iterrows():
            raw_col = str(row.get("column_name", "")).strip()
            col_key = raw_col.lower()
            rule_type = str(row.get("rule_type", "")).strip().lower()
            severity = str(row.get("severity", "MEDIUM")).strip().upper()

            # Strict Filter: Process ONLY if column exists in target dataset (case-insensitive check)
            if col_key in col_map:
                col_name = col_map[col_key]  # Use exact column case from DuckDB
                c = f'"{col_name}"'

                # --- RULE 1: COMPLETENESS ---
                if rule_type == "completeness":
                    null_cnt = con.execute(
                        f"SELECT COUNT(*) - COUNT({c}) FROM target_data"
                    ).fetchone()[0]
                    passed = (null_cnt == 0)
                    details = f"Found {null_cnt:,} null values"

                # --- RULE 2: UNIQUENESS ---
                elif rule_type == "uniqueness":
                    dup_cnt = con.execute(
                        f"SELECT COUNT({c}) - COUNT(DISTINCT {c}) FROM target_data"
                    ).fetchone()[0]
                    passed = (dup_cnt == 0)
                    details = f"Found {dup_cnt:,} duplicate keys"

                # --- RULE 3: RANGE ---
                elif rule_type == "range":
                    p_min = row.get("parameter_min", 0)
                    p_max = row.get("parameter_max", 1000000)
                    violations = con.execute(
                        f"SELECT COUNT(*) FROM target_data WHERE {c} < {p_min} OR {c} > {p_max}"
                    ).fetchone()[0]
                    passed = (violations == 0)
                    details = f"Found {violations:,} records outside range [{p_min}, {p_max}]"

                # --- RULE 4: ALLOWED VALUES ---
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

    # 2. Generate Attribute Profile ONLY for columns declared in rules.xlsx
    if metadata_rules_df is not None and not metadata_rules_df.empty:
        target_cols = []
        for raw_c in metadata_rules_df["column_name"].dropna().unique():
            k = str(raw_c).strip().lower()
            if k in col_map:
                target_cols.append(col_map[k])
    else:
        target_cols = []

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

    # 3. Compute Metrics strictly for metadata-defined scope
    if not audit_log_df.empty:
        total_rules = len(audit_log_df)
        passed_rules = len(audit_log_df[audit_log_df["Status"] == "PASSED"])
        failed_rules = total_rules - passed_rules
        health_score = int((passed_rules / total_rules) * 100)
    else:
        total_rules, passed_rules, failed_rules, health_score = 0, 0, 0, 100

    # 4. Top Banner Display
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Metadata Health Score", f"{health_score}%")
    col2.metric("Total Rows Audited", f"{total_rows:,}")
    col3.metric("Evaluated Rules", total_rules)
    col4.metric("Passed Rules", passed_rules)
    col5.metric("Failed Rules", failed_rules)

    st.divider()

    # 5. Tabbed Interface
    tab_excel, tab_profile, tab_console, tab_preview, tab_schema = st.tabs(
        [
            "📑 Excel Audit Log",
            "📊 Metadata Attribute Profile",
            "💻 DuckDB SQL Console",
            "📋 Data Preview & Visuals",
            "📐 Schema & Types",
        ]
    )

    # TAB 1: EXCEL METADATA AUDIT LOG
    with tab_excel:
        st.subheader("🚨 Metadata-Driven Excel Audit Results")

        if not audit_log_df.empty:
            st.dataframe(audit_log_df, use_container_width=True)

            # Export Audit Log to Excel Buffer
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                audit_log_df.to_excel(writer, index=False, sheet_name="Audit_Report")
                if not profile_df.empty:
                    profile_df.to_excel(writer, index=False, sheet_name="Attribute_Profile")
            processed_data = output.getvalue()

            st.download_button(
                label="📥 Download Audit Log as Excel (.xlsx)",
                data=processed_data,
                file_name="data_quality_audit_log.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.info("Upload or provide a valid rules.xlsx file to generate the metadata audit log.")

    # TAB 2: METADATA ATTRIBUTE PROFILE
    with tab_profile:
        st.subheader("Profile for Metadata-Defined Attributes Only")
        
        if not profile_df.empty:
            st.dataframe(profile_df, use_container_width=True)

            st.divider()
            st.subheader("🔝 Most Frequent Values Inspector")
            freq_col = st.selectbox(
                "Select Metadata Attribute:", options=target_cols
            )
            top_n = st.slider("Select Top N values", 3, 20, 5)

            freq_query = f"""
                SELECT 
                    "{freq_col}" AS Value,
                    COUNT(*) AS Frequency,
                    ROUND((COUNT(*) * 100.0 / {total_rows}), 2) AS Percentage
                FROM target_data
                GROUP BY "{freq_col}"
                ORDER BY Frequency DESC
                LIMIT {top_n}
            """
            freq_df = con.execute(freq_query).df()
            st.dataframe(freq_df, use_container_width=True)
        else:
            st.info("No matching attributes from rules.xlsx were found in the current dataset.")

    # TAB 3: SQL CONSOLE
    with tab_console:
        st.subheader("💻 Interactive DuckDB SQL Console")
        user_query = st.text_area(
            "Write SQL Query:",
            value="SELECT * FROM target_data LIMIT 10;",
            height=120,
        )

        if st.button("Execute SQL"):
            try:
                res_df = con.execute(user_query).df()
                st.success(f"Returned {len(res_df)} rows")
                st.dataframe(res_df, use_container_width=True)
            except Exception as e:
                st.error(f"SQL Error: {e}")

    # TAB 4: PREVIEW & VISUALS
    with tab_preview:
        st.subheader("First 100 Records (Sampled via DuckDB)")
        sample_df = con.execute("SELECT * FROM target_data LIMIT 100").df()
        st.dataframe(sample_df, use_container_width=True)

    # TAB 5: SCHEMA
    with tab_schema:
        st.subheader("Full Dataset Schema")
        schema_df = pd.DataFrame(
            columns_info,
            columns=["Column Name", "Type", "Null?", "Key", "Default", "Extra"],
        )
        st.dataframe(
            schema_df[["Column Name", "Type"]], use_container_width=True
        )

else:
    st.info(
        "Select a data source from the sidebar and fetch/upload a dataset to"
        " trigger metadata auditing."
    )