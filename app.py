"""
app.py — Ovvi Fleet Error Dashboard

Run with: streamlit run app.py

This dashboard reads the Fleet QC spreadsheet and provides interactive
visualizations of error trends across the Ovvi fleet.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from parsers import load_error_data, load_firmware_updates, IGNORE_CODES, get_error_name


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Ovvi Fleet Error Dashboard",
    page_icon="🐱",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def check_password():
    try:
        secret = st.secrets["password"]
    except (KeyError, FileNotFoundError):
        return  # running locally without secrets configured — skip auth
    pw = st.sidebar.text_input("Password", type="password")
    if pw == "":
        st.sidebar.info("Enter the password to access the dashboard.")
        st.stop()
    if pw != secret:
        st.sidebar.error("Incorrect password.")
        st.stop()

check_password()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")


@st.cache_data
def load_data(filepath: str | None, file_bytes: bytes | None = None) -> pd.DataFrame:
    """Load and cache the error data."""
    import io
    source = io.BytesIO(file_bytes) if file_bytes is not None else filepath
    return load_error_data(source)


@st.cache_data
def load_fw() -> pd.DataFrame:
    """Load firmware update timeline (from hardcoded release table)."""
    return load_firmware_updates()


def find_data_file() -> Path | None:
    """Find the most recent .xlsx file in the data directory."""
    if not DATA_DIR.exists():
        return None
    xlsx_files = sorted(DATA_DIR.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
    return xlsx_files[0] if xlsx_files else None


# ---------------------------------------------------------------------------
# Sidebar: data source + filters
# ---------------------------------------------------------------------------

st.sidebar.title("Ovvi Error Dashboard")

# File selection: either from data/ folder or upload
data_file = find_data_file()
uploaded = st.sidebar.file_uploader("Upload spreadsheet", type=["xlsx"])

if uploaded:
    st.sidebar.success(f"Loaded: {uploaded.name}")
    df = load_data(None, file_bytes=uploaded.getvalue())
elif data_file:
    st.sidebar.info(f"Using: {data_file.name}")
    df = load_data(str(data_file))
else:
    st.sidebar.warning("No data file found. Upload a spreadsheet or place one in the `data/` folder.")
    st.title("Ovvi Fleet Error Dashboard")
    st.write("Upload a Fleet QC spreadsheet to get started.")
    st.stop()

if df.empty:
    st.error("No error data found in the spreadsheet. Check that the file has 'Error Code Trend' sheets.")
    st.stop()

# --- Filters ---
st.sidebar.markdown("---")
st.sidebar.header("Filters")

# Date range
min_date = df["date"].min().date()
max_date = df["date"].max().date()
date_range = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)
if len(date_range) == 2:
    df = df[(df["date"].dt.date >= date_range[0]) & (df["date"].dt.date <= date_range[1])]

# Unit type filter
all_types = sorted(df["unit_type"].unique())
selected_types = st.sidebar.multiselect(
    "Unit groups",
    options=all_types,
    default=all_types,
    help="Filter by unit type: Customer, F&F Tester, Influencer, etc.",
)
if selected_types:
    df = df[df["unit_type"].isin(selected_types)]

# Error category filter
error_cats = sorted(df["error_category"].unique())
selected_cats = st.sidebar.multiselect(
    "Error categories",
    options=error_cats,
    default=error_cats,
    help="A-Codes are app alerts, E-Codes are firmware errors",
)
if selected_cats:
    df = df[df["error_category"].isin(selected_cats)]

# Ignore codes toggle
exclude_ignore = st.sidebar.checkbox(
    "Exclude routine messages",
    value=True,
    help=f"Hide: {', '.join(sorted(IGNORE_CODES))}",
)
if exclude_ignore:
    df = df[~df["error_code"].isin(IGNORE_CODES)]

# Aggregation period
agg_period = st.sidebar.radio(
    "Time aggregation",
    options=["Daily", "Weekly", "Monthly"],
    index=1,
    horizontal=True,
)

# Show firmware lines toggle
show_fw_lines = st.sidebar.checkbox("Show firmware version lines", value=True)

# Always load firmware data (used by fw lines and predictor tab)
fw_df = load_fw()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def resample_key(period: str) -> str:
    """Convert period name to pandas resample key."""
    return {"Daily": "D", "Weekly": "W", "Monthly": "MS"}[period]


def add_fw_lines(fig, fw_df, date_min=None, date_max=None):
    """Add vertical lines for firmware version first appearances."""
    if fw_df.empty:
        return
    # Only draw lines within the data's date range
    visible = fw_df.copy()
    if date_min is not None:
        visible = visible[visible["first_seen_date"] >= pd.Timestamp(date_min)]
    if date_max is not None:
        visible = visible[visible["first_seen_date"] <= pd.Timestamp(date_max)]
    if visible.empty:
        return

    colors = px.colors.qualitative.Set2
    # Stagger label y positions to reduce overlap
    y_positions = [0.99, 0.88, 0.77, 0.66]
    for j, (_, row) in enumerate(visible.iterrows()):
        x = row["first_seen_date"].strftime("%Y-%m-%d")
        color = colors[j % len(colors)]
        label = row["version"].replace("ovvi-fw-", "")
        fig.add_shape(
            type="line",
            x0=x, x1=x, y0=0, y1=1,
            xref="x", yref="paper",
            line=dict(dash="dash", color=color, width=1),
            opacity=0.6,
        )
        fig.add_annotation(
            x=x, y=y_positions[j % len(y_positions)],
            xref="x", yref="paper",
            text=label, showarrow=False,
            font=dict(size=10, color=color),
            xanchor="left", yanchor="middle",
            bgcolor="rgba(0,0,0,0.4)",
            borderpad=2,
        )


RANGE_SELECTOR = dict(
    buttons=[
        dict(count=7,  label="1W", step="day",   stepmode="backward"),
        dict(count=1,  label="1M", step="month", stepmode="backward"),
        dict(count=3,  label="3M", step="month", stepmode="backward"),
        dict(count=6,  label="6M", step="month", stepmode="backward"),
        dict(step="all", label="All"),
    ]
)


def make_time_series(df_plot, value_col="count", color_col=None, title="", agg="sum"):
    """Create a time-series chart with zoom range selector. Base resolution set by sidebar."""
    key = resample_key(agg_period)

    if color_col and color_col in df_plot.columns:
        grouped = (
            df_plot.set_index("date")
            .groupby([pd.Grouper(freq=key), color_col])[value_col]
            .sum()
            .reset_index()
        )
        fig = px.line(grouped, x="date", y=value_col, color=color_col, title=title, markers=True)
    else:
        grouped = (
            df_plot.set_index("date")
            .resample(key)[value_col]
            .sum()
            .fillna(0)
            .reset_index()
        )
        fig = px.bar(grouped, x="date", y=value_col, title=title)

    fig.update_layout(
        xaxis=dict(
            rangeselector=RANGE_SELECTOR,
            rangeslider=dict(visible=True, thickness=0.05),
            type="date",
        ),
        xaxis_title="",
        yaxis_title="Error Count",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=60, b=40),
        height=450,
    )

    if show_fw_lines:
        add_fw_lines(fig, fw_df, df_plot["date"].min(), df_plot["date"].max())

    return fig


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------

st.title("Ovvi Fleet Error Dashboard")

# --- Summary metrics ---
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Errors", f"{df['count'].sum():,}")
col2.metric("Unique Codes", df["error_code"].nunique())
col3.metric("Units Affected", df["unit_name"].nunique())
col4.metric("Date Range", f"{(df['date'].max() - df['date'].min()).days}d")
col5.metric(
    "Errors/Week",
    f"{df['count'].sum() / max(1, (df['date'].max() - df['date'].min()).days / 7):.1f}",
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Tab layout for different views
# ---------------------------------------------------------------------------

tab_overview, tab_by_code, tab_by_unit, tab_heatmap, tab_drilldown, tab_predictor, tab_fw_compare, tab_raw = st.tabs([
    "📈 Overview",
    "🔢 By Error Code",
    "📦 By Unit",
    "🗺️ Heatmap",
    "🔍 Unit Drill-Down",
    "🎯 New Unit Predictor",
    "⚖️ FW Comparison",
    "📋 Raw Data",
])


# === TAB: Overview ===
with tab_overview:
    st.subheader("Total Errors Over Time")

    overview_grouped = (
        df.set_index("date")
        .resample(resample_key(agg_period))["count"]
        .sum()
        .fillna(0)
        .reset_index()
    )
    fig_total = px.bar(overview_grouped, x="date", y="count", title="All Errors Over Time")
    fig_total.update_layout(
        xaxis=dict(
            rangeselector=dict(
                buttons=[
                    dict(count=7,  label="1W", step="day",   stepmode="backward"),
                    dict(count=1,  label="1M", step="month", stepmode="backward"),
                    dict(count=3,  label="3M", step="month", stepmode="backward"),
                    dict(count=6,  label="6M", step="month", stepmode="backward"),
                    dict(step="all", label="All"),
                ]
            ),
            rangeslider=dict(visible=True, thickness=0.05),
            type="date",
        ),
        xaxis_title="",
        yaxis_title="Error Count",
        margin=dict(t=60, b=40),
        height=450,
    )
    if show_fw_lines:
        add_fw_lines(fig_total, fw_df, df["date"].min(), df["date"].max())
    st.plotly_chart(fig_total, use_container_width=True)

    # By unit type
    st.subheader("Errors by Unit Group")
    fig2 = make_time_series(df, color_col="unit_type", title="Errors by Unit Group")
    st.plotly_chart(fig2, use_container_width=True)

    # By error category
    st.subheader("A-Codes vs E-Codes Over Time")
    fig3 = make_time_series(df, color_col="error_category", title="Error Category Trends")
    st.plotly_chart(fig3, use_container_width=True)


# === TAB: By Error Code ===
with tab_by_code:
    st.subheader("Error Code Frequency")

    # Top N selector
    top_n = st.slider("Show top N codes", 5, 30, 15)

    # Bar chart of total counts
    code_counts = (
        df.groupby(["error_code", "error_name"])["count"]
        .sum()
        .reset_index()
        .sort_values("count", ascending=False)
        .head(top_n)
    )
    code_counts["label"] = code_counts["error_code"] + " — " + code_counts["error_name"]

    fig = px.bar(
        code_counts,
        x="count",
        y="label",
        orientation="h",
        title=f"Top {top_n} Error Codes by Total Occurrences",
        color="count",
        color_continuous_scale="Reds",
    )
    fig.update_layout(
        yaxis=dict(autorange="reversed"),
        yaxis_title="",
        xaxis_title="Total Count",
        height=max(400, top_n * 28),
        margin=dict(l=250),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Trend for selected codes
    st.subheader("Error Code Trends Over Time")
    top_codes = code_counts["error_code"].tolist()
    selected_codes = st.multiselect(
        "Select codes to plot",
        options=sorted(df["error_code"].unique()),
        default=top_codes[:5],
    )
    if selected_codes:
        df_codes = df[df["error_code"].isin(selected_codes)]
        fig2 = make_time_series(df_codes, color_col="error_code", title="Selected Error Code Trends")
        st.plotly_chart(fig2, use_container_width=True)


# === TAB: By Unit ===
with tab_by_unit:
    st.subheader("Error Counts by Unit")

    # Units ranked by total errors
    unit_counts = (
        df.groupby(["unit_name", "unit_type"])
        .agg(total_errors=("count", "sum"), unique_codes=("error_code", "nunique"), first_error=("date", "min"), last_error=("date", "max"))
        .reset_index()
        .sort_values("total_errors", ascending=False)
    )

    top_n_units = st.slider("Show top N units", 10, 50, 25, key="unit_slider")
    unit_display = unit_counts.head(top_n_units)

    fig = px.bar(
        unit_display,
        x="total_errors",
        y="unit_name",
        color="unit_type",
        orientation="h",
        title=f"Top {top_n_units} Units by Error Count",
    )
    fig.update_layout(
        yaxis=dict(autorange="reversed"),
        yaxis_title="",
        xaxis_title="Total Error Count",
        height=max(400, top_n_units * 25),
        margin=dict(l=250),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Summary table
    st.dataframe(
        unit_counts.style.format({"first_error": "{:%Y-%m-%d}", "last_error": "{:%Y-%m-%d}"}),
        use_container_width=True,
        height=400,
    )


# === TAB: Heatmap ===
with tab_heatmap:
    st.subheader("Error Heatmap: Code vs Time")

    # Resample to chosen period
    key = resample_key(agg_period)
    heatmap_df = (
        df.set_index("date")
        .groupby([pd.Grouper(freq=key), "error_code"])["count"]
        .sum()
        .reset_index()
    )

    # Only show codes that have meaningful data
    min_total = st.slider("Minimum total count to show", 1, 50, 3)
    code_totals = heatmap_df.groupby("error_code")["count"].sum()
    keep_codes = code_totals[code_totals >= min_total].index
    heatmap_df = heatmap_df[heatmap_df["error_code"].isin(keep_codes)]

    if not heatmap_df.empty:
        pivot = heatmap_df.pivot_table(
            index="error_code", columns="date", values="count", fill_value=0
        )
        # Sort by total
        pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]

        fig = px.imshow(
            pivot,
            labels=dict(x="Date", y="Error Code", color="Count"),
            title="Error Heatmap",
            color_continuous_scale="YlOrRd",
            aspect="auto",
        )
        fig.update_layout(height=max(400, len(pivot) * 22), margin=dict(l=120))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data matches the current filters.")


# === TAB: Unit Drill-Down ===
with tab_drilldown:
    st.subheader("Individual Unit Drill-Down")

    # Unit selector — search by name or serial number
    search = st.text_input("Search by name or serial number", placeholder="e.g. Patrick or A1B2C3D4E5F6")
    if search:
        mask = (
            df["unit_name"].str.contains(search, case=False, na=False) |
            df["serial_number"].str.contains(search, case=False, na=False)
        )
        matches = sorted(df[mask]["unit_name"].dropna().unique())
    else:
        matches = sorted(df["unit_name"].dropna().unique())

    selected_unit = st.selectbox("Select a unit", options=matches)

    if selected_unit:
        df_unit = df[df["unit_name"] == selected_unit]

        # Unit info
        ucol1, ucol2, ucol3, ucol4 = st.columns(4)
        ucol1.metric("Total Errors", f"{df_unit['count'].sum():,}")
        ucol2.metric("Unique Codes", df_unit["error_code"].nunique())
        sn = df_unit["serial_number"].dropna().unique()
        ucol3.metric("Serial Number", sn[0] if len(sn) > 0 else "Unknown")
        fw = df_unit["inferred_firmware"].dropna().unique()
        ucol4.metric("Firmware", ", ".join(fw) if len(fw) > 0 else "Unknown")

        # Timeline
        fig = make_time_series(
            df_unit,
            color_col="error_code",
            title=f"Error Timeline: {selected_unit}",
        )
        st.plotly_chart(fig, use_container_width=True)

        # Error breakdown
        unit_code_counts = (
            df_unit.groupby(["error_code", "error_name"])["count"]
            .sum()
            .reset_index()
            .sort_values("count", ascending=False)
        )
        st.dataframe(unit_code_counts, use_container_width=True)

        # Full event log for this unit
        with st.expander("Full event log"):
            st.dataframe(
                df_unit[["date", "error_code", "error_name", "count", "inferred_firmware"]]
                .sort_values("date"),
                use_container_width=True,
            )


# === TAB: New Unit Predictor ===
with tab_predictor:
    st.subheader("New Unit Error Risk Profile")

    TODAY = pd.Timestamp.today().normalize()
    MIN_DEPLOYMENT_DAYS = 14

    # Find the reference firmware: most recent version deployed >= 14 days ago
    eligible = fw_df[fw_df["first_seen_date"] <= TODAY - pd.Timedelta(days=MIN_DEPLOYMENT_DAYS)]

    if eligible.empty:
        st.warning("No firmware version has been deployed for at least 14 days. Check back later.")
    else:
        ref_fw = eligible.sort_values("first_seen_date").iloc[-1]
        ref_version = ref_fw["version"]
        ref_date = ref_fw["first_seen_date"]
        days_deployed = (TODAY - ref_date).days

        # --- Methodology box ---
        st.info(
            f"""
**How this is calculated**

- **Reference firmware:** `{ref_version}` — the most recent version deployed at least {MIN_DEPLOYMENT_DAYS} days ago.
- **Deployed:** {ref_date.strftime('%B %d, %Y')} ({days_deployed} days ago). Versions deployed less than {MIN_DEPLOYMENT_DAYS} days ago are excluded to avoid incomplete error data from early deployment.
- **Sample:** all units that logged at least one event while running `{ref_version}`, filtered to only rows recorded on that firmware version.
- **Model:** errors are assumed to occur at a constant rate (Poisson process). The observed rate per unit per day is calculated from the historical data and projected forward to a 30-day window.
- **Exposure days:** each unit's exposure is counted from when it *first appeared on the reference firmware*, not from the firmware release date. Units that received the update late are not penalised — their shorter exposure is accounted for in the denominator.
- **% Units Affected (empirical):** the proportion of sampled units that experienced this error at least once — the most direct measure of how widespread an error is.
- **Avg Occurrences if Affected:** mean total occurrences among units that had the error — shows severity when it does occur. High values with low % affected means a concentrated problem on a few units.
- **Expected Occurrences (30d fleet avg):** Poisson projection of `rate × 30` averaged across all units including those unaffected — useful for fleet-level support load estimation, but can be misleading for errors concentrated on few units.
- **Threshold:** errors affecting fewer than 5% of units are excluded.
- **Assumption:** the model assumes a steady error rate with no early burn-in or wear-out effects. Treat projections as indicative, not precise.
- Sidebar filters (unit group, error category, routine message exclusion) are applied before this analysis.
            """
        )

        # Filter to reference firmware rows only, using the *unfiltered* df
        # but respecting sidebar filters already applied to df
        df_ref = df[df["inferred_firmware"] == ref_version].copy()

        if df_ref.empty:
            st.warning(f"No error data found for `{ref_version}` after current filters.")
        else:
            all_units = df_ref["unit_name"].dropna().unique()
            n_units = len(all_units)

            end_date = df_ref["date"].max()
            unit_first_date = df_ref.groupby("unit_name")["date"].min()
            unit_exposure_days = ((end_date - unit_first_date).dt.days + 1)
            total_exposure_days = unit_exposure_days.sum()
            avg_exposure_days = unit_exposure_days.mean()

            pcol1, pcol2, pcol3 = st.columns(3)
            pcol1.metric("Units in Sample", n_units)
            pcol2.metric("Avg Exposure per Unit", f"{avg_exposure_days:.0f} days")
            pcol3.metric("Total Unit-Days", f"{total_exposure_days:,}")

            import math

            # Per-unit totals for each error code
            per_unit = (
                df_ref.groupby(["unit_name", "error_code", "error_name"])["count"]
                .sum()
                .reset_index()
            )

            # Aggregate stats per error code
            stats = (
                per_unit.groupby(["error_code", "error_name"])
                .agg(
                    units_affected=("unit_name", "nunique"),
                    avg_per_affected=("count", "mean"),
                    total=("count", "sum"),
                )
                .reset_index()
            )

            # Poisson projections using actual exposure days as denominator
            stats["rate_per_unit_per_day"] = stats["total"] / total_exposure_days
            stats["expected_30d"] = stats["rate_per_unit_per_day"] * 30
            stats["pct_units_affected"] = stats["units_affected"] / n_units * 100
            stats = stats.sort_values("pct_units_affected", ascending=False)
            stats = stats[stats["pct_units_affected"] >= 5.0]

            # Chart: % units affected (empirical)
            labels = stats["error_code"] + " — " + stats["error_name"]
            fig = go.Figure(go.Bar(
                x=stats["pct_units_affected"],
                y=labels,
                orientation="h",
                marker=dict(
                    color=stats["pct_units_affected"],
                    colorscale="Reds",
                    showscale=False,
                ),
            ))
            fig.add_trace(go.Scatter(
                x=[100], y=[labels.iloc[0]],
                mode="markers", marker=dict(opacity=0, size=0),
                showlegend=False, hoverinfo="skip",
            ))
            fig.update_layout(
                title="% of Units Affected by Each Error (Empirical)",
                xaxis=dict(
                    range=[0, 100],
                    ticksuffix="%",
                    dtick=10,
                    showgrid=True,
                    gridcolor="rgba(255,255,255,0.15)",
                    gridwidth=1,
                    title="% Units Affected",
                ),
                yaxis=dict(autorange="reversed"),
                height=max(400, len(stats) * 28),
                margin=dict(l=280),
            )
            st.plotly_chart(fig, use_container_width=True)

            # Table with full stats
            st.subheader("Detailed Projections")
            display = stats[[
                "error_code", "error_name",
                "pct_units_affected", "avg_per_affected", "expected_30d", "units_affected"
            ]].copy()
            display.columns = [
                "Code", "Name",
                "% Units Affected", "Avg Occurrences if Affected",
                "Expected Occurrences (30d fleet avg)", "Units Affected"
            ]
            st.dataframe(
                display.style.format({
                    "% Units Affected": "{:.1f}%",
                    "Avg Occurrences if Affected": "{:.1f}",
                    "Expected Occurrences (30d fleet avg)": "{:.2f}",
                }),
                use_container_width=True,
                height=400,
            )


# === TAB: FW Comparison ===
with tab_fw_compare:
    st.subheader("Firmware Version Comparison")

    fw_versions = sorted(df["inferred_firmware"].dropna().unique())

    if len(fw_versions) < 2:
        st.warning("Not enough firmware versions in the current data to compare.")
    else:
        col_a, col_b = st.columns(2)
        default_a = fw_versions[-2] if len(fw_versions) >= 2 else fw_versions[0]
        default_b = fw_versions[-1]
        fw_a = col_a.selectbox("Firmware A", options=fw_versions, index=fw_versions.index(default_a))
        fw_b = col_b.selectbox("Firmware B", options=fw_versions, index=fw_versions.index(default_b))

        all_codes = sorted(df["error_code"].unique())
        selected_compare_codes = st.multiselect(
            "Error codes to compare",
            options=all_codes,
            default=[c for c in ["A8-002", "A7-002", "A5-002"] if c in all_codes] or all_codes[:3],
            help="Pick one or more error codes to compare across the two firmware versions.",
        )

        if fw_a == fw_b:
            st.warning("Select two different firmware versions.")
        elif not selected_compare_codes:
            st.info("Select at least one error code.")
        else:
            def fw_stats(version, codes):
                d = df[(df["inferred_firmware"] == version) & (df["error_code"].isin(codes))]
                all_units_on_fw = df[df["inferred_firmware"] == version]["unit_name"].dropna().unique()
                n_units = len(all_units_on_fw)

                if d.empty or n_units == 0:
                    return pd.DataFrame(), n_units, 0

                # Exposure: days each unit was active on this firmware
                unit_first = d.groupby("unit_name")["date"].min()
                unit_last = d.groupby("unit_name")["date"].max()
                # Use the full fw window for units with any activity
                end_date = df[df["inferred_firmware"] == version]["date"].max()
                start_date = df[df["inferred_firmware"] == version]["date"].min()
                total_unit_days = (end_date - start_date).days * n_units or 1

                per_unit = d.groupby(["error_code", "unit_name"])["count"].sum().reset_index()
                stats = per_unit.groupby("error_code").agg(
                    total=("count", "sum"),
                    units_affected=("unit_name", "nunique"),
                ).reset_index()
                stats["pct_units_affected"] = stats["units_affected"] / n_units * 100
                stats["rate_per_unit_day"] = stats["total"] / total_unit_days
                return stats, n_units, total_unit_days

            stats_a, n_a, days_a = fw_stats(fw_a, selected_compare_codes)
            stats_b, n_b, days_b = fw_stats(fw_b, selected_compare_codes)

            # Merge for side-by-side
            merged = pd.DataFrame({"error_code": selected_compare_codes})
            for label, stats in [(fw_a, stats_a), (fw_b, stats_b)]:
                short = label.replace("ovvi-fw-", "")
                if stats.empty:
                    merged[f"total_{short}"] = 0
                    merged[f"pct_{short}"] = 0.0
                    merged[f"rate_{short}"] = 0.0
                else:
                    s = stats.set_index("error_code")
                    merged[f"total_{short}"] = merged["error_code"].map(s["total"]).fillna(0).astype(int)
                    merged[f"pct_{short}"] = merged["error_code"].map(s["pct_units_affected"]).fillna(0)
                    merged[f"rate_{short}"] = merged["error_code"].map(s["rate_per_unit_day"]).fillna(0)

            short_a = fw_a.replace("ovvi-fw-", "")
            short_b = fw_b.replace("ovvi-fw-", "")

            # Context metrics
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(f"{short_a} — Units", n_a)
            m2.metric(f"{short_b} — Units", n_b)
            m3.metric(f"{short_a} — Unit-Days", f"{days_a:,}")
            m4.metric(f"{short_b} — Unit-Days", f"{days_b:,}")

            st.caption("Rates are normalized by unit-days so versions with more units or longer deployment periods are fairly compared.")

            # Chart 1: % units affected
            fig_pct = go.Figure()
            fig_pct.add_trace(go.Bar(name=short_a, x=merged["error_code"], y=merged[f"pct_{short_a}"], marker_color="#636EFA"))
            fig_pct.add_trace(go.Bar(name=short_b, x=merged["error_code"], y=merged[f"pct_{short_b}"], marker_color="#EF553B"))
            fig_pct.update_layout(
                barmode="group",
                title="% of Units Affected",
                yaxis=dict(ticksuffix="%", title="% Units Affected"),
                xaxis_title="Error Code",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                height=380,
                margin=dict(t=60),
            )
            st.plotly_chart(fig_pct, use_container_width=True)

            # Chart 2: rate per unit-day (normalized)
            fig_rate = go.Figure()
            fig_rate.add_trace(go.Bar(name=short_a, x=merged["error_code"], y=merged[f"rate_{short_a}"], marker_color="#636EFA"))
            fig_rate.add_trace(go.Bar(name=short_b, x=merged["error_code"], y=merged[f"rate_{short_b}"], marker_color="#EF553B"))
            fig_rate.update_layout(
                barmode="group",
                title="Error Rate (occurrences per unit per day, normalized)",
                yaxis_title="Rate / unit-day",
                xaxis_title="Error Code",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                height=380,
                margin=dict(t=60),
            )
            st.plotly_chart(fig_rate, use_container_width=True)

            # Summary table
            st.subheader("Summary Table")
            display = merged.copy()
            display.columns = [
                "Error Code",
                f"Total ({short_a})", f"% Affected ({short_a})", f"Rate/unit-day ({short_a})",
                f"Total ({short_b})", f"% Affected ({short_b})", f"Rate/unit-day ({short_b})",
            ]
            st.dataframe(
                display.style.format({
                    f"% Affected ({short_a})": "{:.1f}%",
                    f"% Affected ({short_b})": "{:.1f}%",
                    f"Rate/unit-day ({short_a})": "{:.5f}",
                    f"Rate/unit-day ({short_b})": "{:.5f}",
                }),
                use_container_width=True,
            )


# === TAB: Raw Data ===
with tab_raw:
    st.subheader("Filtered Data Table")
    st.write(f"Showing {len(df):,} events after filters")
    st.dataframe(
        df[["date", "error_code", "error_name", "count", "unit_name", "unit_type", "serial_number", "inferred_firmware"]]
        .sort_values("date", ascending=False),
        use_container_width=True,
        height=600,
    )

    # Download button
    csv = df.to_csv(index=False)
    st.download_button(
        label="Download filtered data as CSV",
        data=csv,
        file_name="ovvi_error_data.csv",
        mime="text/csv",
    )
