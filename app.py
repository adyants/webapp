import streamlit as st
import requests
import json
import os
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from datetime import datetime
import sqlite3
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# --- CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Render Persistence Path (Use /data/ if you attach a Render Disk)
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

DB_FILE = os.path.join(DATA_DIR, "accounts_db.json")
DB_STATS = os.path.join(DATA_DIR, "surveillance_stats.db")
ALARM_URL = "https://actions.google.com/sounds/v1/alarms/beep_short.ogg"

# --- AUTHENTICATION CHECK ---
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        # Fetch password from Render Env Var, default to 'admin123' for local testing
        if st.session_state["password"] == os.getenv("MASTER_PASSWORD", "admin123"):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("🔐 JioSecure.ai Login")
        st.text_input("Enter Master Password", type="password", on_change=password_entered, key="password")
        st.info("Note: Password is managed via Render Environment Variables.")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Enter Master Password", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True

# --- CORE LOGIC FUNCTIONS ---
def init_stats_db():
    conn = sqlite3.connect(DB_STATS)
    conn.execute('''CREATE TABLE IF NOT EXISTS daily_stats
                 (date TEXT, account TEXT, name TEXT, total INTEGER, online INTEGER, offline INTEGER, type TEXT,
                 PRIMARY KEY (date, account))''')
    conn.commit()
    conn.close()

def log_daily_stats(df):
    if df.empty: return
    conn = sqlite3.connect(DB_STATS)
    curr_date = datetime.now().strftime('%Y-%m-%d')
    for _, r in df.iterrows():
        try:
            total = pd.to_numeric(r['Total'], errors='coerce') or 0
            online = pd.to_numeric(r['Online'], errors='coerce') or 0
            offline = pd.to_numeric(r['Offline'], errors='coerce') or 0
            clean_name = str(r['Name']).replace("🚩", "").replace("🟢", "").strip()
            with conn:
                conn.execute('''INSERT OR REPLACE INTO daily_stats VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (curr_date, r['Account'], clean_name, int(total), int(online), int(offline), r['Type']))
        except: pass
    conn.close()

def load_accounts():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            for acc in data:
                acc.setdefault('name', 'N/A')
                acc.setdefault('type', 'Internal')
                acc.setdefault('threshold', 5 if acc['type'] == "Internal" else 10)
            return data
    return []

def save_accounts(accounts):
    clean = [{"name": a["name"], "email": a["email"], "password": a["password"], 
              "type": a["type"], "threshold": a.get("threshold", 10)} for a in accounts]
    with open(DB_FILE, "w") as f: json.dump(clean, f)

def login_get_token(email, password):
    url = "https://api.cloud.smartmonitoring.jio.com/auth/oauth/token"
    payload = {'username': email, 'password': password, 'grant_type': 'password', 'client_id': 'jio-android-client'}
    try:
        session = requests.Session()
        session.mount('https://', HTTPAdapter(max_retries=Retry(total=2)))
        r = session.post(url, data=payload, verify=False, timeout=10)
        return r.json().get('access_token')
    except: return None

# --- MAIN APP FLOW ---
if check_password():
    st.set_page_config(page_title="JioSecure.ai", layout="wide")
    init_stats_db()
    
    # Refresh every 120 seconds
    st_autorefresh(interval=120 * 1000, key="data_refresh_pulse")

    if 'accounts' not in st.session_state:
        st.session_state.accounts = load_accounts()
        for acc in st.session_state.accounts: acc["token"] = None

    # --- SIDEBAR & UI ---
    st.sidebar.header("⚙️ Settings")
    
    with st.sidebar.expander("➕ Add Account"):
        an, ae, ap = st.text_input("Name"), st.text_input("Email"), st.text_input("Pass", type="password")
        at = st.selectbox("Group", ["Internal", "POC"])
        atr = st.number_input("Threshold %", value=(5 if at=="Internal" else 10))
        if st.button("Save Account"):
            st.session_state.accounts.append({"name": an, "email": ae, "password": ap, "type": at, "threshold": atr, "token": None})
            save_accounts(st.session_state.accounts)
            st.rerun()

    # (Previous Delete/Update logic remains the same...)
    if st.sidebar.button("Logout"):
        st.session_state["password_correct"] = False
        st.rerun()

    view_history = st.sidebar.toggle("📊 History Insights")
    mute_alarm = st.sidebar.toggle("🔇 Mute Audio Alert")

    # --- DASHBOARD LOGIC ---
    if view_history:
        st.subheader("📊 History Insights")
        conn = sqlite3.connect(DB_STATS)
        st.dataframe(pd.read_sql_query("SELECT * FROM daily_stats ORDER BY date DESC", conn), use_container_width=True)
        conn.close()
    else:
        results = []
        trigger_alarm = False
        
        if not st.session_state.accounts:
            st.info("Add accounts in the sidebar.")
        else:
            for acc in st.session_state.accounts:
                if not acc.get('token'): acc['token'] = login_get_token(acc['email'], acc['password'])
                try:
                    r = requests.post("https://api.cloud.jiosurveillance.com/dashboards/main?op=GET", 
                                    headers={'Authorization': f'Bearer {acc["token"]}'}, json={}, verify=False, timeout=10)
                    if r.status_code == 200:
                        s = r.json()["result"]["sections"]["camera_summary"]
                        tot, off = s['total'], s['offline']
                        p = (off/tot*100) if tot > 0 else 0
                        limit = acc.get('threshold', 5)
                        
                        flag = f"🟢 0.0%" if off == 0 else f"{'🚩 ' if p > limit else ''}{p:.1f}%"
                        if not mute_alarm and acc['type'] == "Internal" and p > 5: trigger_alarm = True
                        
                        results.append({"Name": acc['name'], "Account": acc['email'], "Total": tot, "Online": tot-off, "Offline": off, "Offline %": flag, "Type": acc['type']})
                    else: acc['token'] = None
                except: pass

            if trigger_alarm:
                st.markdown(f'<audio autoplay src="{ALARM_URL}" type="audio/ogg"></audio>', unsafe_allow_html=True)

            if results:
                main_df = pd.DataFrame(results)
                log_daily_stats(main_df)
                for g in ["Internal", "POC"]:
                    st.subheader(f"{g} Accounts")
                    st.table(main_df[main_df['Type'] == g].drop(columns=['Type']))
                st.caption(f"Last Sync: {datetime.now().strftime('%H:%M:%S')}")
