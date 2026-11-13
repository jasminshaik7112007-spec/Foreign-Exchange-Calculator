
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import sqlite3
import os

from datetime import datetime, timezone
from streamlit_autorefresh import st_autorefresh

# ==================================================
# 1. APPLICATION CONFIGURATION
# ==================================================

st.set_page_config(
    page_title="Global FX Calculator",
    page_icon="💱",
    layout="wide"
)

API_URL = "https://api.exchangerate.dev/v1"
DB_FILE = "global_fx.db"

st.markdown("""
<style>
.main {
    background-color: #f5f7fb;
}
.metric-card {
    background: white;
    padding: 15px;
    border-radius: 12px;
}
</style>
""", unsafe_allow_html=True)

st.title("💱 Global FX - Foreign Exchange Calculator")
st.caption(
    "International Currency Conversion and Exchange Rate Monitoring"
)

# ==================================================
# 2. API CONFIGURATION
# ==================================================

try:
    API_KEY = st.secrets.get("EXCHANGERATE_API_KEY", "")
except Exception:
    API_KEY = "exr_live_IRJbRdgsnVigy92h4tpteLzJLeiVutsGvE1cWjQYv50"

HEADERS = {
    "User-Agent": "GlobalFXCalculator/1.0"
}

if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"


# ==================================================
# 3. DATABASE CONNECTION
# ==================================================

def get_connection():
    return sqlite3.connect(DB_FILE)


def create_database():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_currency TEXT,
            to_currency TEXT,
            amount REAL,
            exchange_rate REAL,
            converted_amount REAL,
            conversion_time TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            base TEXT,
            quote TEXT,
            rate REAL,
            observed_at TEXT,
            source TEXT,
            stale INTEGER,
            UNIQUE(base, quote, observed_at)
        )
    """)

    conn.commit()
    conn.close()


create_database()


# ==================================================
# 4. FETCH ALL SUPPORTED CURRENCIES
# ==================================================

@st.cache_data(ttl=86400, show_spinner=False)
def get_currencies(api_key):

    headers = {"User-Agent": "GlobalFXCalculator/1.0"}

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.get(
        f"{API_URL}/currencies",
        headers=headers,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    if data.get("result") == "error":
        raise ValueError(data.get("message", "Currency API error"))

    items = data.get("currencies", data.get("data", []))

    if isinstance(items, dict):
        items = [
            {"code": code, **details}
            for code, details in items.items()
            if isinstance(details, dict)
        ]

    if not isinstance(items, list) or not items:
        raise ValueError("No currency list received from API.")

    currencies = {}

    for item in items:

        if not isinstance(item, dict):
            continue

        code = (
            item.get("code")
            or item.get("currency")
            or item.get("symbol")
        )

        if not code:
            continue

        code = str(code).upper()

        currencies[code] = {
            "Code": code,
            "Name": item.get("name", code),
            "Type": item.get("type", "Unknown"),
            "Decimals": item.get(
                "decimal_places",
                item.get("decimals", 2)
            )
        }

    if not currencies:
        raise ValueError("Could not interpret currency information.")

    return currencies


# ==================================================
# 5. FETCH LATEST EXCHANGE RATES
# ==================================================

@st.cache_data(ttl=55, show_spinner=False)
def get_latest_rates(base, api_key):

    headers = {"User-Agent": "GlobalFXCalculator/1.0"}

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.get(
        f"{API_URL}/latest/{base}",
        headers=headers,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    if data.get("result") != "success":
        raise ValueError(
            data.get("message", "Unable to fetch exchange rates")
        )

    rates = data.get("rates", {})

    if not rates:
        raise ValueError("No exchange rates returned.")

    rates[base] = 1.0

    return data


# ==================================================
# 6. SAVE RATE HISTORY
# ==================================================

def save_rate_history(data):

    conn = get_connection()
    cursor = conn.cursor()

    base = data.get("base", "USD")
    rates = data.get("rates", {})
    effective = data.get("effective_at", {})
    stale_info = data.get("stale", {})
    sources = data.get("sources", {})
    source = data.get("source", "unknown")

    for quote, rate in rates.items():

        if quote == base:
            continue

        observed_at = (
            effective.get(quote)
            or data.get("data_updated_at")
            or data.get("timestamp")
        )

        if not observed_at:
            continue

        cursor.execute("""
            INSERT OR IGNORE INTO rate_history
            (base, quote, rate, observed_at, source, stale)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            base,
            quote,
            float(rate),
            observed_at,
            sources.get(quote, source),
            int(stale_info.get(quote, False))
        ))

    conn.commit()
    conn.close()


# ==================================================
# 7. SAVE CONVERSION HISTORY
# ==================================================

def save_conversion(
    source,
    target,
    amount,
    rate,
    result
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO conversions (
            from_currency,
            to_currency,
            amount,
            exchange_rate,
            converted_amount,
            conversion_time
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        source,
        target,
        amount,
        rate,
        result,
        datetime.now(timezone.utc).isoformat()
    ))

    conn.commit()
    conn.close()


# ==================================================
# 8. LOAD CONVERSION HISTORY
# ==================================================

def get_conversion_history():

    conn = get_connection()

    df = pd.read_sql_query(
        "SELECT * FROM conversions ORDER BY id DESC LIMIT 500",
        conn
    )

    conn.close()

    return df


# ==================================================
# 9. LOAD RATE HISTORY
# ==================================================

def get_rate_history(base, quote):

    conn = get_connection()

    df = pd.read_sql_query("""
        SELECT observed_at, rate
        FROM rate_history
        WHERE base = ? AND quote = ?
        ORDER BY observed_at
    """, conn, params=(base, quote))

    conn.close()

    return df


# ==================================================
# 10. LOAD CURRENCY DATA AND LATEST RATES
# ==================================================

try:

    currencies = get_currencies(API_KEY)

    latest_data = get_latest_rates("USD", API_KEY)

    rates = latest_data["rates"]

    # Keep only currencies known to the currency directory
    supported_codes = set(currencies.keys())

    rates = {
        code: value
        for code, value in rates.items()
        if code in supported_codes
    }

    rates["USD"] = 1.0

    latest_data["rates"] = rates

    save_rate_history(latest_data)

except requests.RequestException as error:

    st.error(f"API connection failed: {error}")
    st.info("Check your internet connection or API service.")
    st.stop()

except (ValueError, KeyError, TypeError) as error:

    st.error(f"Data loading error: {error}")
    st.stop()


# ==================================================
# 11. SIDEBAR SETTINGS
# ==================================================

st.sidebar.title("⚙️ Settings")

plan = st.sidebar.selectbox(
    "Select your API plan",
    [
        "Free - 15 minute delayed data",
        "Paid - Live data"
    ]
)

if plan.startswith("Paid") and not API_KEY:

    st.sidebar.warning(
        "A paid API key is required for live data. "
        "Using free refresh interval instead."
    )

    refresh_seconds = 900

elif plan.startswith("Paid"):

    refresh_seconds = 60

else:

    refresh_seconds = 900

st.sidebar.caption(
    f"Dashboard refresh: {refresh_seconds} seconds"
)

st.sidebar.caption(
    f"Loaded currencies: {len(currencies)}"
)

st.sidebar.caption(
    "Rates are indicative, not settlement quotes."
)

# Automatic refresh
st_autorefresh(
    interval=refresh_seconds * 1000,
    key="global_fx_refresh"
)

if st.sidebar.button("🔄 Refresh Now"):

    get_currencies.clear()
    get_latest_rates.clear()

    st.rerun()


# ==================================================
# 12. RATE INFORMATION
# ==================================================

st.subheader("🌍 Global Exchange Overview")

col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "Supported currencies",
    len(currencies)
)

col2.metric(
    "Available rates",
    len(rates)
)

col3.metric(
    "Market session",
    latest_data.get("market_session", "Unknown")
)

col4.metric(
    "Rate source",
    latest_data.get("source", "Unknown")
)

st.caption(
    f"Provider data updated: "
    f"{latest_data.get('data_updated_at', 'Not available')} UTC"
)

st.caption(
    f"API response time: "
    f"{latest_data.get('timestamp', 'Not available')} UTC"
)


# ==================================================
# 13. DASHBOARD TABS
# ==================================================

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏠 Dashboard",
    "💱 Converter",
    "🌐 Currencies",
    "📊 Comparison",
    "📜 History"
])


# ==================================================
# 14. DASHBOARD
# ==================================================

with tab1:

    st.subheader("Popular Currency Rates")

    popular = [
        "INR", "EUR", "GBP", "JPY",
        "AUD", "CAD", "CHF", "AED",
        "SGD", "CNY"
    ]

    cols = st.columns(5)

    for index, code in enumerate(popular):

        if code in rates:

            cols[index % 5].metric(
                f"USD / {code}",
                f"{rates[code]:,.4f}"
            )

    st.divider()

    st.subheader("All Exchange Rates")

    table = []

    for code, rate in rates.items():

        info = currencies.get(code, {})

        table.append({
            "Currency Code": code,
            "Currency Name": info.get("Name", code),
            "Type": info.get("Type", "Unknown"),
            "Rate (per USD)": rate,
            "Source": latest_data.get(
                "sources", {}
            ).get(code, latest_data.get("source", "Unknown")),
            "Stale": latest_data.get(
                "stale", {}
            ).get(code, False)
        })

    rates_df = pd.DataFrame(table)

    st.dataframe(
        rates_df.sort_values("Currency Code"),
        use_container_width=True,
        hide_index=True
    )

    csv = rates_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        "⬇️ Download Exchange Rates CSV",
        data=csv,
        file_name="global_exchange_rates.csv",
        mime="text/csv"
    )


# ==================================================
# 15. CURRENCY CONVERTER
# ==================================================

with tab2:

    st.subheader("Currency Converter")

    currency_codes = sorted(currencies.keys())

    col1, col2 = st.columns(2)

    with col1:

        from_currency = st.selectbox(
            "From Currency",
            currency_codes,
            index=currency_codes.index("INR")
            if "INR" in currency_codes else 0,
            format_func=lambda code:
                f"{code} - {currencies[code]['Name']}"
        )

    with col2:

        default_target = (
            currency_codes.index("USD")
            if "USD" in currency_codes else 0
        )

        to_currency = st.selectbox(
            "To Currency",
            currency_codes,
            index=default_target,
            format_func=lambda code:
                f"{code} - {currencies[code]['Name']}"
        )

    amount = st.number_input(
        "Enter Amount",
        min_value=0.0,
        value=1000.0,
        step=100.0
    )

    if st.button("💱 Convert Currency", type="primary"):

        if amount <= 0:

            st.warning("Enter an amount greater than zero.")

        elif from_currency == to_currency:

            exchange_rate = 1.0
            result = amount

            st.success(
                f"{amount:,.4f} {to_currency}"
            )

            save_conversion(
                from_currency,
                to_currency,
                amount,
                exchange_rate,
                result
            )

        elif (
            from_currency not in rates
            or to_currency not in rates
            or rates[from_currency] <= 0
        ):

            st.error(
                "Rate unavailable for the selected currency pair."
            )

        else:

            # Cross-rate calculation using USD as common base
            exchange_rate = (
                rates[to_currency] / rates[from_currency]
            )

            result = amount * exchange_rate

            st.success("Conversion successful!")

            st.metric(
                "Converted Amount",
                f"{result:,.4f} {to_currency}"
            )

            st.write(
                f"1 {from_currency} = "
                f"{exchange_rate:.8f} {to_currency}"
            )

            st.caption(
                "Indicative rate. Actual bank or exchange "
                "counter rates may differ."
            )

            save_conversion(
                from_currency,
                to_currency,
                amount,
                exchange_rate,
                result
            )


# ==================================================
# 16. CURRENCY DIRECTORY
# ==================================================

with tab3:

    st.subheader("International Currency Directory")

    search = st.text_input(
        "🔍 Search by currency name or code"
    )

    directory = pd.DataFrame(
        list(currencies.values())
    )

    if search:

        mask = (
            directory["Code"].str.contains(
                search, case=False, na=False
            )
            |
            directory["Name"].str.contains(
                search, case=False, na=False
            )
        )

        directory = directory[mask]

    st.write(
        f"Showing {len(directory)} currencies"
    )

    st.dataframe(
        directory,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "⬇️ Download Currency Directory",
        directory.to_csv(index=False).encode("utf-8"),
        "currency_directory.csv",
        "text/csv"
    )


# ==================================================
# 17. CURRENCY COMPARISON
# ==================================================

with tab4:

    st.subheader("Currency Comparison")

    selected_currencies = st.multiselect(
        "Choose currencies to compare against USD",
        options=sorted(rates.keys()),
        default=[
            code for code in ["INR", "EUR", "GBP", "JPY"]
            if code in rates
        ]
    )

    comparison = []

    for code in selected_currencies:

        comparison.append({
            "Currency": code,
            "Currency Name": currencies.get(
                code, {}
            ).get("Name", code),
            "Value of 1 USD": rates[code]
        })

    if comparison:

        compare_df = pd.DataFrame(comparison)

        st.dataframe(
            compare_df,
            use_container_width=True,
            hide_index=True
        )

        fig = px.bar(
            compare_df,
            x="Currency",
            y="Value of 1 USD",
            title="Currency Values Against USD",
            text_auto=".4f"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )


# ==================================================
# 18. HISTORICAL RATE CHART
# ==================================================

with tab5:

    st.subheader("Historical Exchange Rates")

    history_currency = st.selectbox(
        "Select currency to view against USD",
        sorted([
            code for code in rates
            if code != "USD"
        ])
    )

    history_df = get_rate_history(
        "USD",
        history_currency
    )

    if not history_df.empty:

        history_df["observed_at"] = pd.to_datetime(
            history_df["observed_at"],
            utc=True
        )

        fig = px.line(
            history_df,
            x="observed_at",
            y="rate",
            title=f"USD / {history_currency} Rate History",
            markers=True
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    else:

        st.info(
            "No historical snapshots collected yet. "
            "Keep the application running to build your own history."
        )

    st.divider()

    st.subheader("Conversion History")

    conversion_df = get_conversion_history()

    if not conversion_df.empty:

        st.dataframe(
            conversion_df,
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "⬇️ Download Conversion History",
            conversion_df.to_csv(index=False).encode("utf-8"),
            "conversion_history.csv",
            "text/csv"
        )

    else:

        st.info(
            "Your conversion history will appear here."
        )


# ==================================================
# 19. FOOTER
# ==================================================

st.divider()

st.caption(
    "Global FX Calculator | Python + Streamlit | "
    "Exchange-rate data by exchangerate.dev"
)

st.caption(
    "Rates are indicative reference data, not guaranteed "
    "transaction or settlement prices."
)

st.caption(
    "Data freshness depends on the API provider, plan, "
    "market session and currency pair."
)
