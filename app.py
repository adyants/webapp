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

# --- 1. PERMANENT ACCOUNTS (ADD YOURS HERE) ---
# Since Render Free wipes files, list your accounts here to keep them forever.
PERMANENT_ACCOUNTS = [
    # Example: {"name": "Mumbai HQ", "email": "admin@jio.com", "password": "password123", "type": "Internal", "threshold": 5},
    # Copy and paste the line above for each account you want to stay saved.
]

# --- 2. INITIALIZATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True) # Fixed the FileExistsError

DB_FILE = os.path.join(DATA_DIR, "accounts_db.json")
DB_STATS = os.path.join(DATA_DIR, "surveillance_stats.db")
ALARM_URL = "https://actions.google.com/sounds/v1/alarms/beep_short.ogg"

# --- 3. SESSION & LOGIN (RESTORED & IMPROVED) ---
def check_password():
    # If already logged in or URL has the success key, don't ask again
    if st.session_state.get("auth_success") or st.query_params.get("login") == "ok":
        return True

    st.title("🔐 JioSecure.ai Access")
    pwd = st.text_input("Enter Master Password", type="password")
    if st.button("Login"):
        if pwd == os.getenv("MASTER_PASSWORD", "admin123"):
            st.session_state["auth_success"] = True
            st.query_params["login"] = "ok" # Keeps you logged in on manual refresh
            st.rerun()
        else:
            st.error("Incorrect Password")
    return False

# --- 4. CORE DATABASE FUNCTIONS ---
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
    accounts = []
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: accounts = json.load(f)
        except: pass
    
    # Merge with PERMANENT_ACCOUNTS so you never lose them
    existing_emails = [a['email'] for a in accounts]
    for p in PERMANENT_ACCOUNTS:
        if p['email'] not in existing_emails:
            accounts.append(p)
    return accounts

def save_accounts(accounts):
    with open(DB_FILE, "w") as f:
        json.dump(accounts, f)

def login_get_token(email, password):
    url = "https://api.cloud.smartmonitoring.jio.com/auth/oauth/token"
    payload = {'username': email, 'password': password, 'grant_type': 'password', 'client_id': 'jio-android-client'}
    try:
        r = requests.post(url, data=payload, verify=False, timeout=10)
        return r.json().get('access_token')
    except: return None

# --- 5. MAIN UI & DASHBOARD ---
if check_password():
    st.set_page_config(page_title="JioSecure.ai", layout="wide")
    init_stats_db()
    st_autorefresh(interval=120 * 1000, key="refresh_sync")

    # CSS - EXACTLY AS PER YOUR ORIGINAL CODE
    st.markdown("""
        <style>
        .block-container { padding-top: 1rem; max-width: 98%; }
        thead tr th:first-child, tbody tr th:first-child { display: none; }
        table { width: 100% !important; table-layout: fixed !important; }
        th:nth-child(2), td:nth-child(2) { width: 5%; } 
        th:nth-child(3), td:nth-child(3) { width: 15%; }
        th:nth-child(4), td:nth-child(4) { width: 40%; }
        th:nth-child(5), td:nth-child(5) { width: 8%; } 
        th:nth-child(6), td:nth-child(6) { width: 8%; } 
        th:nth-child(7), td:nth-child(7) { width: 8%; } 
        th:nth-child(8), td:nth-child(8) { width: 16%; }
        </style>
    """, unsafe_allow_html=True)

    if 'accounts' not in st.session_state:
        st.session_state.accounts = load_accounts()

    # --- SIDEBAR: FULL FEATURES RESTORED ---
    st.sidebar.header("⚙️ Account Management")
    
    with st.sidebar.expander("➕ Add New Account"):
        an, ae, ap = st.text_input("Name", key="an"), st.text_input("Email", key="ae"), st.text_input("Password", type="password", key="ap")
        at = st.selectbox("Group", ["Internal", "POC"], key="at")
        atr = st.number_input("Threshold %", value=(5 if at=="Internal" else 10), key="at_tr")
        if st.button("Save New Account"):
            st.session_state.accounts.append({"name": an, "email": ae, "password": ap, "type": at, "threshold": atr, "token": None})
            save_accounts(st.session_state.accounts)
            st.rerun()

    with st.sidebar.expander("🔄 Update Account"):
        if st.session_state.accounts:
            u_email = st.selectbox("Select Account", [a['email'] for a in st.session_state.accounts], key="u_sel")
            target = next(a for a in st.session_state.accounts if a['email'] == u_email)
            un = st.text_input("Edit Name", value=target['name'], key="un")
            up = st.text_input("Edit Password", value=target['password'], type="password", key="up")
            ut = st.selectbox("Edit Group", ["Internal", "POC"], index=0 if target['type']=="Internal" else 1, key="ut")
            utr = st.number_input("Edit Threshold %", value=target.get('threshold', 5), key="utr")
            if st.button("Commit Update"):
                target.update({"name": un, "password": up, "type": ut, "threshold": utr, "token": None})
                save_accounts(st.session_state.accounts)
                st.rerun()

    with st.sidebar.expander("🗑️ Delete Account"):
        if st.session_state.accounts:
            de = st.selectbox("Remove Account", [a['email'] for a in st.session_state.accounts], key="d_sel")
            if st.button("Confirm Delete"):
                st.session_state.accounts = [a for a in st.session_state.accounts if a['email'] != de]
                save_accounts(st.session_state.accounts)
                st.rerun()

    st.sidebar.markdown("---")
    view_history = st.sidebar.toggle("📊 View History Insights", value=False)
    mute_alarm = st.sidebar.toggle("🔇 Mute Audio Alert", value=False)
    if st.sidebar.button("Logout"):
        st.query_params.clear()
        st.session_state.auth_success = False
        st.rerun()

    # --- HEADER & CLOCK (RESTORED) ---
    h1, h2, h3 = st.columns([2.5, 1, 1])
    with h1:
        st.markdown('<h3>Surveillance Dashboard</h3>', unsafe_allow_html=True)
    with h2:
        components.html("""
            <div style="font-family: monospace; font-size: 16px; border: 1px solid #ccc; text-align: center; background: white; line-height:38px;">
                🇮🇳 <span id="clock">--:--:--</span>
            </div>
            <script>
                setInterval(() => { document.getElementById('clock').innerHTML = new Date().toLocaleTimeString('en-GB', {timeZone:'Asia/Kolkata', hour12:false}); }, 1000);
            </script>
        """, height=55)

    # --- LOGIC SEPARATION ---
    if view_history:
        st.subheader("📊 History Insights")
        conn = sqlite3.connect(DB_STATS)
        h_df = pd.read_sql_query("SELECT * FROM daily_stats ORDER BY date DESC", conn)
        st.dataframe(h_df, use_container_width=True, hide_index=True)
        conn.close()
    else:
        results = []
        trigger_alarm = False
        for acc in st.session_state.accounts:
            if not acc.get('token'): acc['token'] = login_get_token(acc['email'], acc['password'])
            try:
                r = requests.post("https://api.cloud.jiosurveillance.com/dashboards/main?op=GET", 
                                 headers={'Authorization': f'Bearer {acc["token"]}'}, json={}, verify=False, timeout=12)
                if r.status_code == 200:
                    s = r.json()["result"]["sections"]["camera_summary"]
                    tot, off = s['total'], s['offline']
                    p = (off/tot*100) if tot > 0 else 0
                    flag = "🟢 0.0%" if off == 0 else f"{'🚩 ' if p > acc.get('threshold', 5) else ''}{p:.1f}%"
                    if not mute_alarm and acc['type'] == "Internal" and p > 5: trigger_alarm = True
                    results.append({"Name": acc['name'], "Account": acc['email'], "Total": tot, "Online": tot-off, "Offline": off, "Offline %": flag, "Type": acc['type']})
                else: acc['token'] = None
            except: pass

        if trigger_alarm:
            st.markdown(f'<audio autoplay src="{ALARM_URL}" type="audio/ogg"></audio>', unsafe_allow_html=True)

        if results:
            df = pd.DataFrame(results)
            log_daily_stats(df)
            for g in ["Internal", "POC"]:
                st.subheader(f"{g} Accounts")
                sub = df[df['Type'] == g].drop(columns=['Type']).copy()
                if not sub.empty:
                    sub.insert(0, 'S/N', range(1, len(sub) + 1))
                    st.table(sub.astype(str))
            st.caption(f"Last Sync: {datetime.now().strftime('%H:%M:%S')}")
