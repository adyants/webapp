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

# --- CONFIGURATION & PATHS ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# FIX: Added exist_ok=True to prevent the FileExistsError on Render
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True) 

DB_FILE = os.path.join(DATA_DIR, "accounts_db.json")
DB_STATS = os.path.join(DATA_DIR, "surveillance_stats.db")
ALARM_URL = "https://actions.google.com/sounds/v1/alarms/beep_short.ogg"

# --- UI SETUP ---
st.set_page_config(page_title="JioSecure.ai", layout="wide")

# --- AUTHENTICATION LOGIC ---
if "password_correct" not in st.session_state:
    st.session_state["password_correct"] = False

def check_password():
    if not st.session_state["password_correct"]:
        st.sidebar.markdown("### 🔐 Authentication")
        pwd = st.sidebar.text_input("Master Password", type="password")
        if st.sidebar.button("Unlock Dashboard"):
            # Ensure MASTER_PASSWORD is set in Render Env Vars
            if pwd == os.getenv("MASTER_PASSWORD", "admin123"):
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.sidebar.error("❌ Invalid Password")
        
        st.warning("Please login in the sidebar to manage accounts and view data.")
        return False
    return True

# --- DATABASE & CORE FUNCTIONS ---
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
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                return data
        except: return []
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
init_stats_db()

if check_password():
    # AUTO-REFRESH: Keep this inside the IF block
    st_autorefresh(interval=120 * 1000, key="data_refresh_pulse")

    st.markdown("""
        <style>
        .block-container { padding-top: 1rem; max-width: 98%; }
        thead tr th:first-child, tbody tr th:first-child { display: none; }
        table { width: 100% !important; table-layout: fixed !important; }
        </style>
    """, unsafe_allow_html=True)

    if 'accounts' not in st.session_state:
        st.session_state.accounts = load_accounts()
        for acc in st.session_state.accounts: acc["token"] = None

    # --- SIDEBAR MANAGEMENT ---
    st.sidebar.header("⚙️ Account Control")
    
    with st.sidebar.expander("➕ Add Account"):
        an = st.text_input("Name")
        ae = st.text_input("Email")
        ap = st.text_input("Password", type="password")
        at = st.selectbox("Group", ["Internal", "POC"])
        atr = st.number_input("Threshold %", value=(5 if at=="Internal" else 10))
        if st.button("Save"):
            st.session_state.accounts.append({"name": an, "email": ae, "password": ap, "type": at, "threshold": atr, "token": None})
            save_accounts(st.session_state.accounts)
            st.rerun()

    with st.sidebar.expander("🗑️ Delete Account"):
        if st.session_state.accounts:
            de = st.selectbox("Select to Remove", [a['email'] for a in st.session_state.accounts])
            if st.button("Confirm Delete"):
                st.session_state.accounts = [a for a in st.session_state.accounts if a['email'] != de]
                save_accounts(st.session_state.accounts)
                st.rerun()

    if st.sidebar.button("🔴 Logout"):
        st.session_state["password_correct"] = False
        st.rerun()

    # --- DASHBOARD HEADER ---
    h1, h2 = st.columns([3, 1])
    with h1:
        st.title("🇮🇳 JioSecure.ai Dashboard")
    with h2:
        components.html("""
            <div style="font-family: monospace; font-size: 18px; font-weight: bold; border: 1px solid #ccc; padding: 10px; text-align: center; border-radius: 5px;">
                IST: <span id="clock">--:--:--</span>
            </div>
            <script>
                setInterval(() => {
                    document.getElementById('clock').innerHTML = new Date().toLocaleTimeString('en-GB', {timeZone:'Asia/Kolkata', hour12:false});
                }, 1000);
            </script>
        """, height=70)

    # --- MAIN DATA FETCHING ---
    results = []
    trigger_alarm = False
    
    if not st.session_state.accounts:
        st.info("No accounts linked. Use the sidebar to add accounts.")
    else:
        for acc in st.session_state.accounts:
            if not acc.get('token'): 
                acc['token'] = login_get_token(acc['email'], acc['password'])
            
            try:
                r = requests.post("https://api.cloud.jiosurveillance.com/dashboards/main?op=GET", 
                                headers={'Authorization': f'Bearer {acc["token"]}'}, json={}, verify=False, timeout=10)
                if r.status_code == 200:
                    s = r.json()["result"]["sections"]["camera_summary"]
                    tot, off = s['total'], s['offline']
                    p = (off/tot*100) if tot > 0 else 0
                    limit = acc.get('threshold', 5)
                    
                    flag = "🟢 0.0%" if off == 0 else f"{'🚩 ' if p > limit else ''}{p:.1f}%"
                    if acc['type'] == "Internal" and p > 5: trigger_alarm = True
                    
                    results.append({"Name": acc['name'], "Account": acc['email'], "Total": tot, "Online": tot-off, "Offline": off, "Offline %": flag, "Type": acc['type']})
                else:
                    acc['token'] = None
            except: pass

        if trigger_alarm:
            st.markdown(f'<audio autoplay src="{ALARM_URL}" type="audio/ogg"></audio>', unsafe_allow_html=True)

        if results:
            df = pd.DataFrame(results)
            log_daily_stats(df)
            for group in ["Internal", "POC"]:
                st.subheader(f"{group} Sites")
                sub_df = df[df['Type'] == group].drop(columns=['Type'])
                if not sub_df.empty:
                    st.table(sub_df)
            
            st.caption(f"Last Sync: {datetime.now().strftime('%H:%M:%S')}")
