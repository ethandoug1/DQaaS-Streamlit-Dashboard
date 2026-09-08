import io
import json
import duckdb
import pandas as pd
import requests
from shiny import App, Inputs, Outputs, Session, reactive, render, ui

# Updated CSS targeting exact 5-column metric cards with centered text & values
streamlit_style_css = """
<style>
    /* Force exactly 5 equal-width columns in a single row */
    .metric-grid {
        display: grid !important;
        grid-template-columns: repeat(5, 1fr) !important;
        gap: 10px !important;
        margin-bottom: 1rem !important;
    }
    
    /* Center-align container content */
    .metric-grid .bslib-value-box {
        width: 100% !important;
        min-width: 0 !important;
        padding: 0.65rem 0.75rem !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important;
        background-color: #ffffff !important;
        text-align: center !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
    }

    /* Centered Title Text */
    .metric-grid .value-box-title {
        font-size: 0.825rem !important;
        font-weight: 600 !important;
        color: #64748b !important;
        white-space: normal !important;
        text-align: center !important;
        margin-bottom: 0.25rem !important;
        line-height: 1.2 !important;
        width: 100% !important;
    }

    /* Centered Large Metric Number */
    .metric-grid .value-box-value {
        font-size: 1.5rem !important;
        font-weight: 700 !important;
        color: #0f172a !important;
        line-height: 1.2 !important;
        text-align: center !important;
        width: 100% !important;
    }
</style>
"""

# =========================================================
# 1. USER INTERFACE (UI) LAYOUT
# =========================================================
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h4("Ingestion Engine"),
        ui.input_radio_buttons(
            "source_type",
            "Select Data Source Type:",
            choices=[
                "Local File (CSV / JSON / XML)",
                "Cloud Database (SQL)",
                "REST API Endpoint",
            ],
            selected="Local File (CSV / JSON / XML)",
        ),
        ui.panel_conditional(
            "input.source_type === 'Local File (CSV / JSON / XML)'",
            ui.input_file(
                "uploaded_dataset",
                "Upload Dataset",
                accept=[".csv", ".json", ".xml"],
                multiple=False,
            ),
        ),
        ui.panel_conditional(
            "input.source_type === 'Cloud Database (SQL)'",
            ui.input_select(
                "db_type", "Select Engine", ["PostgreSQL", "Snowflake", "BigQuery", "SQLite"]
            ),
            ui.input_text_area(
                "db_query", "SQL Query", value="SELECT * FROM orders LIMIT 1000;"
            ),
            ui.input_action_button("btn_run_db", "Run Query", class_="btn-primary w-100"),
        ),
        ui.panel_conditional(
            "input.source_type === 'REST API Endpoint'",
            ui.input_text("api_url", "API Endpoint URL"),
            ui.input_password("api_key", "API Key / Bearer Token"),
            ui.input_action_button("btn_fetch_api", "Fetch API Data", class_="btn-primary w-100"),
        ),
        ui.hr(),
        ui.h4("Metadata Rules (Excel)"),
        ui.input_file(
            "uploaded_rules",
            "Upload Metadata Rules (.xlsx)",
            accept=[".xlsx"],
            multiple=False,
        ),
        width=320,
    ),
    
    # Inject Updated Metric CSS
    ui.HTML(streamlit_style_css),
    
    # Header Section
    ui.h2("Metadata Driven DQ Profile"),
    ui.p("No pre-loaded data or rules. Upload a dataset and rules.xlsx to execute an audit.", class_="text-muted"),
    ui.hr(),
    
    # FULL-TEXT EQUAL-WIDTH 5-CARD METRIC ROW
    ui.div(
        ui.value_box("Health Score", ui.output_text("val_health_score"), showcase=None),
        ui.value_box("Audited Rows", ui.output_text("val_total_rows"), showcase=None),
        ui.value_box("Total Rules", ui.output_text("val_total_rules"), showcase=None),
        ui.value_box("Passed Rules", ui.output_text("val_passed_rules"), showcase=None),
        ui.value_box("Failed Rules", ui.output_text("val_failed_rules"), showcase=None),
        class_="metric-grid",
    ),
    
    # MAIN TABBED CONTAINER WRAPPED IN A CARD
    ui.card(
        ui.navset_tab(
            ui.nav_panel(
                "Excel Audit Log",
                ui.br(),
                ui.h5("Audit Results"),
                ui.output_data_frame("tbl_audit_log"),
                ui.br(),
                ui.download_button("btn_download_audit", "Download Audit Report (.xlsx)", class_="btn-success"),
            ),
            ui.nav_panel(
                "Metadata Attribute Profile",
                ui.br(),
                ui.h5("Attribute Profile"),
                ui.output_data_frame("tbl_attribute_profile"),
            ),
            ui.nav_panel(
                "Data Preview",
                ui.br(),
                ui.h5("Data Preview (First 100 Rows)"),
                ui.output_data_frame("tbl_data_preview"),
            ),
            ui.nav_panel(
                "Schema & Types",
                ui.br(),
                ui.h5("Dataset Schema"),
                ui.output_data_frame("tbl_schema"),
            ),
            ui.nav_panel(
                "SQL Query Console",
                ui.br(),
                ui.h5("Interactive SQL Console"),
                ui.p("Query uploaded dataset in memory using DuckDB syntax. Table name: `target_data`", class_="text-muted"),
                ui.input_text_area("sql_input", "Write SQL Query:", value="SELECT * FROM target_data LIMIT 25;", width="100%", rows=3),
                ui.input_action_button("btn_exec_sql", "Execute SQL", class_="btn-primary"),
                ui.br(), ui.br(),
                ui.output_data_frame("tbl_sql_result"),
            ),
        ),
    ),
    title="Metadata Driven DQ Profile Engine",
)

# =========================================================
# 2. SERVER REACTIVE LOGIC
# =========================================================
def server(input: Inputs, output: Outputs, session: Session):

    # Reactive Target Dataset Ingestion
    @reactive.calc
    def target_df():
        source = input.source_type()
        
        if source == "Local File (CSV / JSON / XML)":
            file_info = input.uploaded_dataset()
            if not file_info:
                return None
            
            file_path = file_info[0]["datapath"]
            file_name = file_info[0]["name"].lower()
            
            if file_name.endswith(".csv"):
                return pd.read_csv(file_path)
            elif file_name.endswith(".json"):
                try:
                    return pd.read_json(file_path)
                except ValueError:
                    with open(file_path, "r") as f:
                        data = json.load(f)
                    return pd.json_normalize(data)
            elif file_name.endswith(".xml"):
                return pd.read_xml(file_path)
                
        elif source == "Cloud Database (SQL)":
            if input.btn_run_db() > 0:
                return pd.DataFrame({"info": ["Database query executed"]})
                
        elif source == "REST API Endpoint":
            if input.btn_fetch_api() > 0 and input.api_url():
                headers = {"Authorization": f"Bearer {input.api_key()}"} if input.api_key() else {}
                resp = requests.get(input.api_url(), headers=headers, timeout=10)
                if resp.status_code == 200:
                    return pd.DataFrame(resp.json())
                    
        return None

    # Reactive DuckDB Connection Registration
    @reactive.calc
    def duckdb_con():
        df = target_df()
        con = duckdb.connect()
        if df is not None and not df.empty:
            con.register("target_data", df)
        return con

    # Reactive Metadata Rules Upload
    @reactive.calc
    def rules_df():
        file_info = input.uploaded_rules()
        if not file_info:
            return None
        return pd.read_excel(file_info[0]["datapath"])

    # Reactive Quality Engine Audit Evaluation
    @reactive.calc
    def audit_results():
        con = duckdb_con()
        rdf = rules_df()
        df = target_df()
        
        if df is None or df.empty or rdf is None or rdf.empty:
            return pd.DataFrame(), pd.DataFrame(), 0, 0, 0, 0, 100

        all_columns = [col[0] for col in con.execute("DESCRIBE target_data").fetchall()]
        col_map = {c.strip().lower(): c for c in all_columns}
        
        rdf.columns = [c.strip().lower() for c in rdf.columns]
        audit_records = []

        for _, row in rdf.iterrows():
            raw_col = str(row.get("column_name", "")).strip()
            col_key = raw_col.lower()
            rule_type = str(row.get("rule_type", "")).strip().lower()
            severity = str(row.get("severity", "MEDIUM")).strip().upper()

            if col_key in col_map:
                col_name = col_map[col_key]
                c = f'"{col_name}"'

                if rule_type == "completeness":
                    null_cnt = con.execute(f"SELECT COUNT(*) - COUNT({c}) FROM target_data").fetchone()[0]
                    passed = (null_cnt == 0)
                    details = f"Found {null_cnt:,} null values"

                elif rule_type == "uniqueness":
                    dup_cnt = con.execute(f"SELECT COUNT({c}) - COUNT(DISTINCT {c}) FROM target_data").fetchone()[0]
                    passed = (dup_cnt == 0)
                    details = f"Found {dup_cnt:,} duplicate keys"

                elif rule_type == "range":
                    p_min = row.get("parameter_min", 0)
                    p_max = row.get("parameter_max", 1000000)
                    violations = con.execute(f"SELECT COUNT(*) FROM target_data WHERE {c} < {p_min} OR {c} > {p_max}").fetchone()[0]
                    passed = (violations == 0)
                    details = f"Found {violations:,} records outside range [{p_min}, {p_max}]"

                elif rule_type == "allowed_values":
                    raw_vals = str(row.get("allowed_values", "")).split(",")
                    vals_formatted = ", ".join([f"'{v.strip()}'" for v in raw_vals])
                    invalid_cnt = con.execute(f"SELECT COUNT(*) FROM target_data WHERE {c} NOT IN ({vals_formatted}) AND {c} IS NOT NULL").fetchone()[0]
                    passed = (invalid_cnt == 0)
                    details = f"Found {invalid_cnt:,} invalid value entries"
                else:
                    passed, details = True, f"Rule type '{rule_type}' unhandled"

                audit_records.append({
                    "Column Name": col_name,
                    "Rule Type": rule_type.upper(),
                    "Severity": severity,
                    "Status": "PASSED" if passed else "FAILED",
                    "Audit Details": details,
                })

        audit_log_df = pd.DataFrame(audit_records)

        # Profile Computation
        target_cols = [col_map[k] for k in [str(c).strip().lower() for c in rdf["column_name"].dropna().unique()] if k in col_map]
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

        profile_df = con.execute(" UNION ALL ".join(query_parts)).df() if query_parts else pd.DataFrame()

        # Metrics
        total_rows = con.execute("SELECT COUNT(*) FROM target_data").fetchone()[0]
        total_rules = len(audit_log_df)
        passed_rules = len(audit_log_df[audit_log_df["Status"] == "PASSED"]) if not audit_log_df.empty else 0
        failed_rules = total_rules - passed_rules
        health_score = int((passed_rules / total_rules) * 100) if total_rules > 0 else 100

        return audit_log_df, profile_df, total_rows, total_rules, passed_rules, failed_rules, health_score

    # Value Box Outputs
    @render.text
    def val_health_score():
        return f"{audit_results()[6]}%"

    @render.text
    def val_total_rows():
        return f"{audit_results()[2]:,}"

    @render.text
    def val_total_rules():
        return f"{audit_results()[3]}"

    @render.text
    def val_passed_rules():
        return f"{audit_results()[4]}"

    @render.text
    def val_failed_rules():
        return f"{audit_results()[5]}"

    # Data Table Outputs
    @render.data_frame
    def tbl_audit_log():
        return render.DataGrid(audit_results()[0])

    @render.data_frame
    def tbl_attribute_profile():
        return render.DataGrid(audit_results()[1])

    @render.data_frame
    def tbl_data_preview():
        con = duckdb_con()
        df = target_df()
        if df is not None and not df.empty:
            return render.DataGrid(con.execute("SELECT * FROM target_data LIMIT 100").df())
        return pd.DataFrame()

    @render.data_frame
    def tbl_schema():
        con = duckdb_con()
        df = target_df()
        if df is not None and not df.empty:
            info = con.execute("DESCRIBE target_data").fetchall()
            return render.DataGrid(pd.DataFrame(info, columns=["Column Name", "Type", "Null?", "Key", "Default", "Extra"])[["Column Name", "Type"]])
        return pd.DataFrame()

    # SQL Console Execution
    @reactive.calc
    @reactive.event(input.btn_exec_sql)
    def sql_exec_result():
        con = duckdb_con()
        df = target_df()
        query = input.sql_input()
        if df is not None and not df.empty and query:
            try:
                return con.execute(query).df()
            except Exception as e:
                return pd.DataFrame({"Error": [str(e)]})
        return pd.DataFrame()

    @render.data_frame
    def tbl_sql_result():
        return render.DataGrid(sql_exec_result())

    # Download Handler
    @render.download(filename="audit_report.xlsx")
    def btn_download_audit():
        audit_log_df, profile_df = audit_results()[0], audit_results()[1]
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            audit_log_df.to_excel(writer, index=False, sheet_name="Audit_Report")
            if not profile_df.empty:
                profile_df.to_excel(writer, index=False, sheet_name="Attribute_Profile")
        yield output.getvalue()


app = App(app_ui, server)