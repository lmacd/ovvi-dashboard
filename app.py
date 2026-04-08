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
    pw = st.sidebar.text_input("Password", type="password")
    if pw == "":
        st.sidebar.info("Enter the password to access the dashboard.")
        st.stop()
    if pw != st.secrets["password"]:
        st.sidebar.error("Incorrect password.")
        st.stop()

check_password()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")


@st.cache_data
def load_data(filepath: str) -> pd.DataFrame:
    """Load and cache the error data."""
    return load_error_data(filepath)


@st.cache_data
def load_fw(filepath: str) -> pd.DataFrame:
    """Load and cache firmware update timeline."""
    return load_firmware_updates(filepath)


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
    # Save uploaded file temporarily
    DATA_DIR.mkdir(exist_ok=True)
    tmp_path = DATA_DIR / uploaded.name
    tmp_path.write_bytes(uploaded.getvalue())
    data_file = tmp_path
    st.sidebar.success(f"Loaded: {uploaded.name}")
elif data_file:
    st.sidebar.info(f"Using: {data_file.name}")
else:
    st.sidebar.warning("No data file found. Upload a spreadsheet or place one in the `data/` folder.")
    st.title("Ovvi Fleet Error Dashboard")
    st.write("Upload a Fleet QC spreadsheet to get started.")
    st.stop()

# Load data
df = load_data(str(data_file))

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

# Load firmware data if needed
fw_df = load_fw(str(data_file)) if show_fw_lines else pd.DataFrame()


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

tab_overview, tab_by_code, tab_by_unit, tab_heatmap, tab_drilldown, tab_raw = st.tabs([
    "📈 Overview",
    "🔢 By Error Code",
    "📦 By Unit",
    "🗺️ Heatmap",
    "🔍 Unit Drill-Down",
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

    # Unit selector
    unit_names = sorted(df["unit_name"].dropna().unique())
    selected_unit = st.selectbox("Select a unit", options=unit_names)

    if selected_unit:
        df_unit = df[df["unit_name"] == selected_unit]

        # Unit info
        ucol1, ucol2, ucol3, ucol4 = st.columns(4)
        ucol1.metric("Total Errors", f"{df_unit['count'].sum():,}")
        ucol2.metric("Unique Codes", df_unit["error_code"].nunique())
        sn = df_unit["serial_number"].dropna().unique()
        ucol3.metric("Serial Number", sn[0] if len(sn) > 0 else "Unknown")
        fw = df_unit["firmware_version"].dropna().unique()
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
                df_unit[["date", "error_code", "error_name", "count", "firmware_version"]]
                .sort_values("date"),
                use_container_width=True,
            )


# === TAB: Raw Data ===
with tab_raw:
    st.subheader("Filtered Data Table")
    st.write(f"Showing {len(df):,} events after filters")
    st.dataframe(
        df[["date", "error_code", "error_name", "count", "unit_name", "unit_type", "serial_number", "firmware_version"]]
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
