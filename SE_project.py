
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import sqlite3
import os
import io

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
            conversion_time TEXT,
            user_id INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Upgrade an older database without deleting existing conversion records.
    columns = [row[1] for row in cursor.execute(
        "PRAGMA table_info(conversions)"
    ).fetchall()]
    if "user_id" not in columns:
        cursor.execute("ALTER TABLE conversions ADD COLUMN user_id INTEGER")

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favourites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency_code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, currency_code)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rate_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency_code TEXT NOT NULL,
            condition TEXT NOT NULL,
            target_rate REAL NOT NULL,
            triggered INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            theme TEXT NOT NULL DEFAULT 'Light',
            refresh_seconds INTEGER NOT NULL DEFAULT 900
        )
    """)

    conn.commit()
    conn.close()


create_database()


# ==================================================
# 4. USER AUTHENTICATION
# ==================================================

import hashlib
import hmac
import secrets


def hash_password(password, salt=None):
    """Hash a password with PBKDF2-HMAC-SHA256."""
    salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 310000
    )
    return password_hash.hex(), salt.hex()


def verify_password(password, stored_hash, stored_salt):
    salt = bytes.fromhex(stored_salt)
    calculated_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(calculated_hash, stored_hash)


def register_user(full_name, email, username, password):
    password_hash, password_salt = hash_password(password)
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO users
            (full_name, email, username, password_hash, password_salt, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            full_name.strip(), email.strip().lower(), username.strip().lower(),
            password_hash, password_salt, datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()
        return True, "Registration successful. Please log in."
    except sqlite3.IntegrityError:
        return False, "That email address or username is already registered."
    finally:
        conn.close()


def authenticate_user(login_value, password):
    conn = get_connection()
    row = conn.execute("""
        SELECT id, full_name, username, password_hash, password_salt
        FROM users
        WHERE username = ? OR email = ?
    """, (login_value.strip().lower(), login_value.strip().lower())).fetchone()
    conn.close()

    if row and verify_password(password, row[3], row[4]):
        return {"id": row[0], "full_name": row[1], "username": row[2]}
    return None


if "user" not in st.session_state:
    st.session_state.user = None

if st.session_state.user is None:
    st.title("💱 Global FX Calculator")
    st.caption("Sign in or create an account to access the currency calculator.")

    login_tab, register_tab = st.tabs(["🔐 Login", "📝 Register"])

    with login_tab:
        with st.form("login_form"):
            login_value = st.text_input("Username or email")
            login_password = st.text_input("Password", type="password")
            login_submitted = st.form_submit_button("Login", type="primary")

        if login_submitted:
            if not login_value.strip() or not login_password:
                st.warning("Please enter both username/email and password.")
            else:
                user = authenticate_user(login_value, login_password)
                if user:
                    st.session_state.user = user
                    st.success("Login successful.")
                    st.rerun()
                else:
                    st.error("Invalid username/email or password.")

    with register_tab:
        with st.form("register_form", clear_on_submit=False):
            full_name = st.text_input("Full name")
            email = st.text_input("Email address")
            username = st.text_input("Choose a username")
            new_password = st.text_input("Create password", type="password")
            confirm_password = st.text_input("Confirm password", type="password")
            register_submitted = st.form_submit_button("Create account", type="primary")

        if register_submitted:
            if not all([full_name.strip(), email.strip(), username.strip(),
                        new_password, confirm_password]):
                st.warning("Please complete all registration fields.")
            elif "@" not in email or "." not in email.rsplit("@", 1)[-1]:
                st.warning("Please enter a valid email address.")
            elif len(username.strip()) < 3:
                st.warning("Username must contain at least 3 characters.")
            elif len(new_password) < 8:
                st.warning("Password must contain at least 8 characters.")
            elif new_password != confirm_password:
                st.error("Passwords do not match.")
            else:
                success, message = register_user(
                    full_name, email, username, new_password
                )
                if success:
                    st.success(message)
                else:
                    st.error(message)

    st.stop()


# ==================================================
# 5. FETCH ALL SUPPORTED CURRENCIES
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
    result,
    user_id
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
            conversion_time,
            user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        source,
        target,
        amount,
        rate,
        result,
        datetime.now(timezone.utc).isoformat(),
        user_id
    ))

    conn.commit()
    conn.close()


# ==================================================
# 8. LOAD CONVERSION HISTORY
# ==================================================

def get_conversion_history(user_id):

    conn = get_connection()

    df = pd.read_sql_query(
        "SELECT id, from_currency, to_currency, amount, exchange_rate, "
        "converted_amount, conversion_time FROM conversions "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 500",
        conn,
        params=(user_id,)
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


def get_favourites(user_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT currency_code FROM favourites WHERE user_id = ? ORDER BY currency_code",
        (user_id,)
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def add_favourite(user_id, code):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO favourites(user_id, currency_code, created_at) VALUES (?, ?, ?)",
        (user_id, code, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def remove_favourite(user_id, code):
    conn = get_connection()
    conn.execute(
        "DELETE FROM favourites WHERE user_id = ? AND currency_code = ?",
        (user_id, code)
    )
    conn.commit()
    conn.close()


def get_alerts(user_id):
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT id, currency_code, condition, target_rate, triggered, created_at "
        "FROM rate_alerts WHERE user_id = ? ORDER BY id DESC",
        conn, params=(user_id,)
    )
    conn.close()
    return df


def add_alert(user_id, code, condition, target):
    conn = get_connection()
    conn.execute(
        "INSERT INTO rate_alerts(user_id, currency_code, condition, target_rate, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, code, condition, float(target), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def delete_alert(user_id, alert_id):
    conn = get_connection()
    conn.execute("DELETE FROM rate_alerts WHERE id = ? AND user_id = ?", (int(alert_id), user_id))
    conn.commit()
    conn.close()


def check_rate_alerts(user_id, current_rates):
    """Mark matching alerts as triggered once; the alert is shown in the app."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, currency_code, condition, target_rate FROM rate_alerts "
        "WHERE user_id = ? AND triggered = 0", (user_id,)
    ).fetchall()
    triggered_now = []
    for alert_id, code, condition, target in rows:
        value = current_rates.get(code)
        if value is None:
            continue
        matched = (value >= target) if condition == "Above or equal" else (value <= target)
        if matched:
            conn.execute("UPDATE rate_alerts SET triggered = 1 WHERE id = ?", (alert_id,))
            triggered_now.append((code, condition, target, value))
    conn.commit()
    conn.close()
    return triggered_now


def get_user_settings(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT theme, refresh_seconds FROM user_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO user_settings(user_id, theme, refresh_seconds) VALUES (?, 'Light', 900)", (user_id,))
        conn.commit()
        row = ("Light", 900)
    conn.close()
    return {"theme": row[0], "refresh_seconds": row[1]}


def save_user_settings(user_id, theme, refresh_seconds):
    conn = get_connection()
    conn.execute(
        "INSERT INTO user_settings(user_id, theme, refresh_seconds) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET theme=excluded.theme, refresh_seconds=excluded.refresh_seconds",
        (user_id, theme, int(refresh_seconds))
    )
    conn.commit()
    conn.close()


def update_profile(user_id, full_name, email):
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET full_name = ?, email = ? WHERE id = ?",
                     (full_name.strip(), email.strip().lower(), user_id))
        conn.commit()
        return True, "Profile updated successfully."
    except sqlite3.IntegrityError:
        return False, "That email address is already used by another account."
    finally:
        conn.close()


def change_password(user_id, current_password, new_password):
    conn = get_connection()
    row = conn.execute("SELECT password_hash, password_salt FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not verify_password(current_password, row[0], row[1]):
        conn.close()
        return False, "Current password is incorrect."
    password_hash, password_salt = hash_password(new_password)
    conn.execute("UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
                 (password_hash, password_salt, user_id))
    conn.commit()
    conn.close()
    return True, "Password changed successfully."


def build_pdf_report(user, conversions, rates_frame):
    """Create a simple downloadable PDF report in memory."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=15*mm, leftMargin=15*mm)
    styles = getSampleStyleSheet()
    story = [Paragraph("Global FX Calculator - User Report", styles["Title"]),
             Paragraph(f"Prepared for: {user['full_name']}", styles["Normal"]),
             Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M')} (local system time)", styles["Normal"]),
             Spacer(1, 8*mm), Paragraph("Latest Available Exchange Rates (per USD)", styles["Heading2"])]
    rate_rows = [["Currency", "Name", "Rate per USD"]]
    for _, row in rates_frame.head(35).iterrows():
        rate_rows.append([str(row.get("Currency Code", "")), str(row.get("Currency Name", ""))[:35], f"{float(row.get('Rate (per USD)', 0)):,.6f}"])
    rt = Table(rate_rows, repeatRows=1, colWidths=[35*mm, 85*mm, 45*mm])
    rt.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0),colors.HexColor("#183153")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.35,colors.grey),("PADDING",(0,0),(-1,-1),5)]))
    story += [rt, Spacer(1, 8*mm), Paragraph("My Conversion History", styles["Heading2"])]
    conv_rows = [["From", "To", "Amount", "Rate", "Converted", "Date"]]
    for _, row in conversions.head(40).iterrows():
        conv_rows.append([str(row.get("from_currency", "")), str(row.get("to_currency", "")),
                          f"{float(row.get('amount', 0)):,.2f}", f"{float(row.get('exchange_rate', 0)):,.6f}",
                          f"{float(row.get('converted_amount', 0)):,.2f}", str(row.get("conversion_time", ""))[:19]])
    if len(conv_rows) == 1:
        conv_rows.append(["No conversion records", "", "", "", "", ""])
    ct = Table(conv_rows, repeatRows=1, colWidths=[20*mm,20*mm,30*mm,30*mm,35*mm,65*mm])
    ct.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0),colors.HexColor("#183153")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.35,colors.grey),("PADDING",(0,0),(-1,-1),5)]))
    story.append(ct)
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("Note: Rates are indicative reference data, not guaranteed transaction or settlement prices.", styles["Italic"]))
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


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
# 11. PROFESSIONAL NAVIGATION, SETTINGS AND THEME
# ==================================================

current_user = st.session_state.user
user_settings = get_user_settings(current_user["id"])

if "theme" not in st.session_state:
    st.session_state.theme = user_settings["theme"]
if "refresh_seconds" not in st.session_state:
    st.session_state.refresh_seconds = int(user_settings["refresh_seconds"])

if st.session_state.theme == "Dark":
    st.markdown("""
    <style>
    .stApp { background: #101827; color: #edf2f7; }
    [data-testid="stSidebar"] { background: #172338; }
    [data-testid="stMetric"] { background: #1d2b42; padding: 15px; border-radius: 12px; }
    div[data-testid="stDataFrame"] { border: 1px solid #334155; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <style>
    .stApp { background: #f5f7fb; }
    [data-testid="stSidebar"] { background: #edf2f8; }
    [data-testid="stMetric"] { background: white; padding: 15px; border-radius: 12px; border: 1px solid #e5eaf1; }
    div[data-testid="stDataFrame"] { border: 1px solid #e5eaf1; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

st.sidebar.title("💱 GLOBAL FX")
st.sidebar.caption("Foreign Exchange Calculator")
st.sidebar.success(f"Welcome, {current_user['full_name']}")

page_groups = {
    "OVERVIEW": ["🏠 Dashboard", "📈 Live Exchange Rates"],
    "TOOLS": ["💱 Currency Converter", "🌐 Currency Explorer", "🔄 Currency Comparison", "📊 Exchange Rate Analytics"],
    "PERSONAL": ["⭐ Favourite Currencies", "🔔 Rate Alerts", "📜 Conversion History", "👤 My Profile"],
    "MORE": ["⚙️ Settings", "ℹ️ About & Help"]
}
page_options = [item for group in page_groups.values() for item in group]
page = st.sidebar.radio("NAVIGATION", page_options, label_visibility="collapsed")
st.sidebar.divider()
st.sidebar.caption(f"Currencies available: {len(currencies)}")
st.sidebar.caption(f"Refresh interval: {st.session_state.refresh_seconds} sec")
if st.sidebar.button("🔄 Refresh rates", use_container_width=True):
    get_currencies.clear()
    get_latest_rates.clear()
    st.rerun()
if st.sidebar.button("🚪 Logout", use_container_width=True):
    st.session_state.user = None
    st.rerun()
st.sidebar.caption("Indicative reference rates only")

# Auto-refresh is user-configurable. It refreshes the Streamlit page, not the provider's market feed.
st_autorefresh(interval=int(st.session_state.refresh_seconds) * 1000, key="global_fx_refresh")

currency_codes = sorted(currencies.keys())
rate_rows = []
for code, rate in rates.items():
    info = currencies.get(code, {})
    rate_rows.append({
        "Currency Code": code,
        "Currency Name": info.get("Name", code),
        "Type": info.get("Type", "Unknown"),
        "Rate (per USD)": float(rate),
        "Source": latest_data.get("sources", {}).get(code, latest_data.get("source", "Unknown")),
        "Stale": latest_data.get("stale", {}).get(code, False)
    })
rates_df = pd.DataFrame(rate_rows).sort_values("Currency Code")

triggered_alerts = check_rate_alerts(current_user["id"], rates)
for code, condition, target, actual in triggered_alerts:
    st.toast(f"Rate alert: USD/{code} is {actual:.6f} ({condition} {target:.6f})", icon="🔔")

st.title("💱 Global FX Calculator")
st.caption("A personal currency conversion and exchange-rate monitoring workspace")

# ==================================================
# 12. DASHBOARD
# ==================================================
if page == "🏠 Dashboard":
    st.subheader("Your currency dashboard")
    a, b, c, d = st.columns(4)
    a.metric("Supported currencies", f"{len(currencies):,}")
    b.metric("Available exchange rates", f"{len(rates):,}")
    c.metric("Your conversions", len(get_conversion_history(current_user["id"])))
    d.metric("Favourite currencies", len(get_favourites(current_user["id"])))
    st.caption(f"Provider update: {latest_data.get('data_updated_at', 'Not supplied')} UTC  |  Source: {latest_data.get('source', 'Unknown')}")
    st.divider()
    st.subheader("Popular exchange rates · 1 USD equals")
    popular = [code for code in ["INR", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "AED", "SGD", "CNY"] if code in rates]
    cols = st.columns(5)
    for i, code in enumerate(popular):
        cols[i % 5].metric(f"{code}", f"{rates[code]:,.4f}")
    left, right = st.columns([1.3, 1])
    with left:
        st.subheader("Exchange rate overview")
        st.dataframe(rates_df[["Currency Code", "Currency Name", "Rate (per USD)", "Stale"]], use_container_width=True, hide_index=True, height=360)
    with right:
        st.subheader("Compare popular currencies")
        chart_df = rates_df[rates_df["Currency Code"].isin(popular)]
        if not chart_df.empty:
            fig = px.bar(chart_df, x="Currency Code", y="Rate (per USD)", title="Value of 1 USD", text_auto=".3f")
            st.plotly_chart(fig, use_container_width=True)
    st.info("Use the navigation menu to convert currencies, manage favourites, set rate alerts and view your reports.")

# ==================================================
# 13. LIVE EXCHANGE RATES
# ==================================================
elif page == "📈 Live Exchange Rates":
    st.subheader("Latest available exchange rates")
    st.caption("Rates are the latest values returned by the configured API. They are not guaranteed tick-by-tick live market prices.")
    x1, x2 = st.columns([2, 1])
    with x1:
        rate_search = st.text_input("Search currency code or name", key="live_rate_search")
    with x2:
        show_stale = st.checkbox("Include stale-marked rates", value=True)
    view = rates_df.copy()
    if rate_search:
        mask = view["Currency Code"].str.contains(rate_search, case=False, na=False) | view["Currency Name"].str.contains(rate_search, case=False, na=False)
        view = view[mask]
    if not show_stale:
        view = view[~view["Stale"].astype(bool)]
    st.metric("Rates displayed", len(view))
    st.dataframe(view, use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download rates CSV", view.to_csv(index=False).encode("utf-8"), "latest_exchange_rates.csv", "text/csv")
    st.caption(f"Provider updated at: {latest_data.get('data_updated_at', 'Not available')} UTC")

# ==================================================
# 14. CURRENCY CONVERTER
# ==================================================
elif page == "💱 Currency Converter":
    st.subheader("Currency converter")
    c1, c2 = st.columns(2)
    with c1:
        from_currency = st.selectbox("From currency", currency_codes, index=currency_codes.index("INR") if "INR" in currency_codes else 0, format_func=lambda code: f"{code} — {currencies[code]['Name']}")
    with c2:
        to_currency = st.selectbox("To currency", currency_codes, index=currency_codes.index("USD") if "USD" in currency_codes else 0, format_func=lambda code: f"{code} — {currencies[code]['Name']}")
    amount = st.number_input("Amount to convert", min_value=0.0, value=1000.0, step=100.0)
    if st.button("💱 Convert now", type="primary"):
        if amount <= 0:
            st.warning("Enter an amount greater than zero.")
        elif from_currency not in rates or to_currency not in rates or rates[from_currency] <= 0:
            st.error("Rate unavailable for the selected currency pair.")
        else:
            exchange_rate = rates[to_currency] / rates[from_currency]
            result = amount * exchange_rate
            st.success("Conversion calculated using the latest available reference rates.")
            m1, m2 = st.columns(2)
            m1.metric("Converted amount", f"{result:,.4f} {to_currency}")
            m2.metric("Exchange rate", f"1 {from_currency} = {exchange_rate:.8f} {to_currency}")
            save_conversion(from_currency, to_currency, amount, exchange_rate, result, current_user["id"])
            st.caption("Actual bank, card or exchange-counter rates may include spreads and fees.")

# ==================================================
# 15. CURRENCY EXPLORER
# ==================================================
elif page == "🌐 Currency Explorer":
    st.subheader("International currency directory")
    search = st.text_input("Search by currency name or code")
    directory = pd.DataFrame(list(currencies.values()))
    if search:
        mask = directory["Code"].str.contains(search, case=False, na=False) | directory["Name"].str.contains(search, case=False, na=False)
        directory = directory[mask]
    st.write(f"Found {len(directory)} currencies")
    st.dataframe(directory, use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download currency directory", directory.to_csv(index=False).encode("utf-8"), "currency_directory.csv", "text/csv")

# ==================================================
# 16. CURRENCY COMPARISON
# ==================================================
elif page == "🔄 Currency Comparison":
    st.subheader("Compare currencies against USD")
    selected = st.multiselect("Select currencies", sorted(rates.keys()), default=[x for x in ["INR", "EUR", "GBP", "JPY"] if x in rates])
    comparison = pd.DataFrame([{"Currency": code, "Currency Name": currencies.get(code, {}).get("Name", code), "Value of 1 USD": rates[code]} for code in selected])
    if not comparison.empty:
        st.dataframe(comparison, use_container_width=True, hide_index=True)
        fig = px.bar(comparison, x="Currency", y="Value of 1 USD", color="Currency", text_auto=".4f", title="Selected currency values against USD")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Select at least one currency to compare.")

# ==================================================
# 17. EXCHANGE RATE ANALYTICS
# ==================================================
elif page == "📊 Exchange Rate Analytics":
    st.subheader("Exchange rate analytics")
    st.caption("The chart uses snapshots collected by this application while it is running. It is not a complete historical market-data feed.")
    available_quotes = sorted([code for code in rates if code != "USD"])
    if available_quotes:
        quote = st.selectbox("Choose a currency", available_quotes, key="analytics_quote")
        hist = get_rate_history("USD", quote)
        if not hist.empty:
            hist["observed_at"] = pd.to_datetime(hist["observed_at"], utc=True, errors="coerce")
            hist = hist.dropna(subset=["observed_at", "rate"])
            fig = px.line(hist, x="observed_at", y="rate", markers=True, title=f"USD / {quote} collected snapshots")
            st.plotly_chart(fig, use_container_width=True)
            st.metric("Latest stored snapshot", f"{float(hist.iloc[-1]['rate']):,.6f}")
            st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.info("No historical snapshots are stored for this currency yet. Keep the application running to collect snapshots.")
    else:
        st.warning("No quote currencies are available.")

# ==================================================
# 18. FAVOURITE CURRENCIES
# ==================================================
elif page == "⭐ Favourite Currencies":
    st.subheader("Your favourite currencies")
    favourites = get_favourites(current_user["id"])
    add_options = [code for code in currency_codes if code not in favourites]
    if add_options:
        fav_choice = st.selectbox("Choose a currency to add", add_options, format_func=lambda code: f"{code} — {currencies[code]['Name']}")
        if st.button("⭐ Add to favourites", type="primary"):
            add_favourite(current_user["id"], fav_choice)
            st.rerun()
    else:
        st.success("All available currencies are already in your favourites.")
    if favourites:
        fav_rows = [{"Code": code, "Name": currencies.get(code, {}).get("Name", code), "Rate per USD": rates.get(code, None)} for code in favourites]
        st.dataframe(pd.DataFrame(fav_rows), use_container_width=True, hide_index=True)
        remove_choice = st.selectbox("Remove a favourite", favourites)
        if st.button("Remove selected favourite"):
            remove_favourite(current_user["id"], remove_choice)
            st.rerun()
    else:
        st.info("You have not saved any favourites yet.")

# ==================================================
# 19. RATE ALERTS
# ==================================================
elif page == "🔔 Rate Alerts":
    st.subheader("Exchange rate alerts")
    st.caption("Alerts are checked whenever the app loads fresh rates. They are displayed in the app; they do not send email, SMS or push notifications.")
    alert_code = st.selectbox("Currency pair", sorted([x for x in rates if x != "USD"]), format_func=lambda code: f"USD / {code}")
    alert_condition = st.selectbox("Alert when USD / selected currency is", ["Above or equal", "Below or equal"])
    alert_target = st.number_input("Target rate", min_value=0.000001, value=float(rates.get(alert_code, 1.0)), format="%.6f")
    if st.button("Create rate alert", type="primary"):
        add_alert(current_user["id"], alert_code, alert_condition, alert_target)
        st.success("Rate alert saved.")
        st.rerun()
    alerts_df = get_alerts(current_user["id"])
    if not alerts_df.empty:
        display_alerts = alerts_df.copy()
        display_alerts["Status"] = display_alerts["triggered"].map({0: "Waiting", 1: "Triggered"})
        st.dataframe(display_alerts.drop(columns=["triggered", "created_at"]), use_container_width=True, hide_index=True)
        alert_ids = alerts_df["id"].tolist()
        selected_alert_id = st.selectbox("Select alert to delete", alert_ids)
        if st.button("Delete selected alert"):
            delete_alert(current_user["id"], selected_alert_id)
            st.rerun()
    else:
        st.info("You have not created any rate alerts.")
    for code, condition, target, actual in triggered_alerts:
        st.success(f"Triggered: USD/{code} is {actual:.6f}; condition: {condition} {target:.6f}.")

# ==================================================
# 20. CONVERSION HISTORY + PDF REPORT
# ==================================================
elif page == "📜 Conversion History":
    st.subheader("Your conversion history")
    conversion_df = get_conversion_history(current_user["id"])
    if not conversion_df.empty:
        st.dataframe(conversion_df, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download conversion history CSV", conversion_df.to_csv(index=False).encode("utf-8"), "my_conversion_history.csv", "text/csv")
    else:
        st.info("Your conversion history will appear here after your first conversion.")
    pdf_bytes = build_pdf_report(current_user, conversion_df, rates_df)
    st.download_button("📄 Download PDF report", pdf_bytes, "global_fx_report.pdf", "application/pdf", type="primary")

# ==================================================
# 21. MY PROFILE
# ==================================================
elif page == "👤 My Profile":
    st.subheader("My profile")
    conn = get_connection()
    profile = conn.execute("SELECT full_name, email, username, created_at FROM users WHERE id = ?", (current_user["id"],)).fetchone()
    conn.close()
    if profile:
        st.write(f"**Username:** {profile[2]}")
        st.write(f"**Member since:** {profile[3][:10]}")
        with st.form("profile_form"):
            new_name = st.text_input("Full name", value=profile[0])
            new_email = st.text_input("Email address", value=profile[1])
            profile_submit = st.form_submit_button("Save profile")
        if profile_submit:
            if not new_name.strip() or "@" not in new_email:
                st.error("Enter a name and valid email address.")
            else:
                ok, msg = update_profile(current_user["id"], new_name, new_email)
                if ok:
                    st.session_state.user["full_name"] = new_name.strip()
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    st.divider()
    st.subheader("Change password")
    with st.form("password_form"):
        current_pw = st.text_input("Current password", type="password")
        new_pw = st.text_input("New password (minimum 8 characters)", type="password")
        confirm_pw = st.text_input("Confirm new password", type="password")
        pw_submit = st.form_submit_button("Update password")
    if pw_submit:
        if len(new_pw) < 8:
            st.error("New password must contain at least 8 characters.")
        elif new_pw != confirm_pw:
            st.error("New passwords do not match.")
        else:
            ok, msg = change_password(current_user["id"], current_pw, new_pw)
            st.success(msg) if ok else st.error(msg)

# ==================================================
# 22. SETTINGS
# ==================================================
elif page == "⚙️ Settings":
    st.subheader("Application settings")
    with st.form("settings_form"):
        theme_choice = st.selectbox("Appearance", ["Light", "Dark"], index=0 if st.session_state.theme == "Light" else 1)
        refresh_choice = st.selectbox("Automatic page refresh", [60, 300, 900, 1800], index=[60, 300, 900, 1800].index(int(st.session_state.refresh_seconds)) if int(st.session_state.refresh_seconds) in [60, 300, 900, 1800] else 2, format_func=lambda seconds: f"Every {seconds} seconds")
        settings_submit = st.form_submit_button("Save settings", type="primary")
    if settings_submit:
        st.session_state.theme = theme_choice
        st.session_state.refresh_seconds = int(refresh_choice)
        save_user_settings(current_user["id"], theme_choice, refresh_choice)
        st.success("Settings saved. The selected theme and refresh interval will be applied.")
        st.rerun()
    st.caption("The refresh setting controls how often this page reruns. It does not make the exchange-rate provider update more frequently.")
    st.write(f"Current theme: **{st.session_state.theme}**")
    st.write(f"Current refresh interval: **{st.session_state.refresh_seconds} seconds**")

# ==================================================
# 23. ABOUT AND HELP
# ==================================================
elif page == "ℹ️ About & Help":
    st.subheader("About Global FX Calculator")
    st.write("Global FX Calculator is a web-based currency conversion and exchange-rate monitoring application developed using Python and Streamlit. It retrieves the latest available exchange-rate information from a configured API and provides conversion, comparison, analytics and personal tracking tools.")
    st.markdown("**Technology used**")
    st.write("Python · Streamlit · Requests · Pandas · Plotly · SQLite · Exchange-rate API")
    st.markdown("**How to use the application**")
    st.markdown("1. Create an account using Register and sign in.\n2. Open Currency Converter, choose source and target currencies, and enter an amount.\n3. Use Live Exchange Rates and Currency Comparison to inspect available rates.\n4. Save commonly used currencies in Favourite Currencies.\n5. Create a target in Rate Alerts. Alerts are checked when the app loads rate data.\n6. Review your previous conversions and download a PDF report from Conversion History.")
    st.markdown("**Important information**")
    st.warning("Exchange rates are indicative reference values. The application does not transfer money, execute trades or include bank fees, card spreads, taxes or remittance charges. Data freshness and currency coverage depend on the API provider and subscription plan.")
    st.markdown("**Troubleshooting**")
    st.write("• If rates do not load, check your internet connection and API credentials.\n• If a currency is unavailable, it may not be supported by the selected API.\n• If analytics has no chart, keep the application running so it can collect snapshots.\n• If you forget your password, this prototype currently requires a developer/admin-assisted database reset; it does not yet provide email-based password recovery.")

# ==================================================
# 24. FOOTER
# ==================================================
st.divider()
st.caption("Global FX Calculator | Python + Streamlit | Exchange-rate data supplied by the configured API provider")
st.caption("Rates are indicative reference data, not guaranteed transaction or settlement prices. Provider freshness depends on its service, plan, market session and currency pair.")


