import streamlit as st
import requests
import json
import ssl
import os
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from datetime import datetime
import time
import sqlite3
import streamlit.components.v1 as components

# --- INITIALIZATION & DATABASE ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
DB_FILE = "accounts_db.json"
DB_STATS = "surveillance_stats.db"
# Notification sound URL (Standard Google Alarm Beep)
ALARM_URL = "https://actions.google.com/sounds/v1/alarms/beep_short.ogg"

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

# --- CORE FUNCTIONS ---
def load_accounts():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            for acc in data:
                acc.setdefault('name', 'N/A')
                acc.setdefault('type', 'Internal')
                # Default threshold: 5% for Internal, 10% for POC
                if 'threshold' not in acc:
                    acc['threshold'] = 5 if acc['type'] == "Internal" else 10
            return data
    return []

def save_accounts(accounts):
    clean = [{"name": a["name"], "email": a["email"], "password": a["password"], 
              "type": a["type"], "threshold": a.get("threshold", 10)} for a in accounts]
    with open(DB_FILE, "w") as f: json.dump(clean, f)

def get_session():
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1))
    session.mount('https://', adapter)
    return session

def login_get_token(email, password):
    url = "https://api.cloud.smartmonitoring.jio.com/auth/oauth/token"
    payload = {'username': email, 'password': password, 'grant_type': 'password', 'client_id': 'jio-android-client'}
    try:
        r = get_session().post(url, data=payload, verify=False, timeout=10)
        return r.json().get('access_token')
    except: return None

# --- UI SETUP ---
st.set_page_config(page_title="JioSecure.ai", layout="wide")
init_stats_db()

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
    for acc in st.session_state.accounts: acc["token"] = None

# --- SIDEBAR: ACCOUNT MANAGEMENT ---
st.sidebar.header("⚙️ Account Management")

with st.sidebar.expander("➕ Add New Account"):
    add_n = st.text_input("Name", key="an")
    add_e = st.text_input("Email", key="ae")
    add_p = st.text_input("Password", type="password", key="ap")
    add_t = st.selectbox("Group", ["Internal", "POC"], key="at")
    # Dynamic default based on group selection
    def_val = 5 if add_t == "Internal" else 10
    add_tr = st.number_input("Offline Threshold %", value=def_val, key="at_tr")
    
    if st.button("Save New Account"):
        st.session_state.accounts.append({
            "name": add_n, "email": add_e, "password": add_p, 
            "type": add_t, "threshold": add_tr, "token": None
        })
        save_accounts(st.session_state.accounts)
        st.rerun()

with st.sidebar.expander("🔄 Update Account"):
    if st.session_state.accounts:
        u_email = st.selectbox("Select Account", [a['email'] for a in st.session_state.accounts], key="u_sel")
        target = next(a for a in st.session_state.accounts if a['email'] == u_email)
        un = st.text_input("Edit Name", value=target.get('name', ''), key="un")
        up = st.text_input("Edit Password", value=target.get('password', ''), type="password", key="up")
        ut = st.selectbox("Edit Group", ["Internal", "POC"], index=0 if target.get('type')=="Internal" else 1, key="ut")
        utr = st.number_input("Edit Threshold %", value=target.get('threshold', 5 if ut=="Internal" else 10), key="utr")
        
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

# --- HEADER SECTION ---
h1, h2, h3 = st.columns([2.5, 1, 1])
with h1:
    st.markdown("""<div style="display: flex; align-items: center; gap: 12px; margin-top: 8px;">
        <img src="https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEiX6TH3MXo-zzVneKFhf0bTdzzLuz_fWp6Ls4F6Z43WP1o7KnFuk3y2oYc3PcKZ9D5ybFksoxL84ZMfiOycWdOJ9DiwTlayyHqriSHba3oand3sqRsqtItMAdcwfrctHVn_p_xyqUbDx9s/s1600/India_flag_with_emblem.gif" width="45">
        <h3 style="margin: 0;">Surveillance Dashboard</h3></div>""", unsafe_allow_html=True)

with h2:
    components.html("""
    <div style="font-family: monospace; font-size: 16px; font-weight: bold; border: 1px solid #ccc; border-radius: 4px; text-align: center; background: white; line-height:38px; margin-top: 20px;">
        🇮🇳 <span id="clock">--:--:--</span>
    </div>
    <script>
        function u(){
            document.getElementById('clock').innerHTML = new Date().toLocaleTimeString('en-GB', {timeZone:'Asia/Kolkata', hour12:false});
        }
        setInterval(u, 1000); u();
    </script>
    """, height=55)

st.markdown("<hr style='margin: 5px 0 15px 0;'>", unsafe_allow_html=True)

# --- LOGIC SEPARATION ---
if view_history:
    st.subheader("📊 History Insights")
    conn = sqlite3.connect(DB_STATS)
    h_df = pd.read_sql_query("SELECT * FROM daily_stats ORDER BY date DESC", conn)
    st.dataframe(h_df, width="stretch", hide_index=True)
    conn.close()
else:
    body = st.empty()
    alarm_placeholder = st.empty() # Placeholder for the audio HTML

    while True:
        with body.container():
            if not st.session_state.accounts:
                st.info("Add accounts in the sidebar to begin.")
            else:
                results = []
                trigger_alarm = False
                
                for acc in st.session_state.accounts:
                    if not acc.get('token'): acc['token'] = login_get_token(acc['email'], acc['password'])
                    try:
                        r = get_session().post("https://api.cloud.jiosurveillance.com/dashboards/main?op=GET", 
                                             headers={'Authorization': f'Bearer {acc["token"]}'}, json={}, verify=False, timeout=15)
                        if r.status_code == 200:
                            s = r.json()["result"]["sections"]["camera_summary"]
                            tot, off = s['total'], s['offline']
                            
                            p = (off/tot*100) if tot > 0 else 0
                            limit = acc.get('threshold', 5 if acc['type'] == "Internal" else 10)
                            
                            # FLAG LOGIC
                            if off == 0: 
                                flag_str = "🟢 0.0%"
                            else:
                                flag_str = f"{'🚩 ' if p > limit else ''}{p:.1f}%"
                            
                            # CRITICAL ALERT LOGIC (Internal > 5%)
                            if not mute_alarm and acc['type'] == "Internal" and p > 5:
                                trigger_alarm = True

                            results.append({"Name": acc['name'], "Account": acc['email'], "Total": tot, "Online": tot-off, "Offline": off, "Offline %": flag_str, "Type": acc['type']})
                        else: acc['token'] = None
                    except: pass

                # Handle Sound Alert
                if trigger_alarm:
                    alarm_placeholder.markdown(f'<audio autoplay src="{ALARM_URL}" type="audio/ogg"></audio>', unsafe_allow_html=True)
                else:
                    alarm_placeholder.empty()

                if results:
                    main_df = pd.DataFrame(results)
                    log_daily_stats(main_df)
                    
                    for g in ["Internal", "POC"]:
                        st.subheader(f"{'🏢' if g=='Internal' else '🧪'} {g} Accounts")
                        sub = main_df[main_df['Type'] == g].drop(columns=['Type']).copy()
                        if not sub.empty:
                            sub.insert(0, 'S/N', range(1, len(sub) + 1))
                            st.table(sub.astype(str))
                    
                    st.caption(f"Last Sync: {datetime.now().strftime('%H:%M:%S')}")
        
        time.sleep(120)
        st.rerun()