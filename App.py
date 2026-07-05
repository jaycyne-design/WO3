import streamlit as st
import sqlite3
import pandas as pd
import base64
from datetime import datetime
from PIL import Image
import io

# --- DATABASE SETUP ---
DB_FILE = "work_orders.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Main Service Reports Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_reports (
            report_id TEXT PRIMARY KEY,
            customer_name TEXT,
            date_created TEXT,
            equipment_brand TEXT,
            equipment_model TEXT,
            serial_number TEXT,
            truck_number TEXT,
            billable_hours REAL,
            date_completed TEXT,
            issue TEXT,
            diagnosis TEXT,
            actions TEXT
        )
    ''')
    
    # 2. Parts Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parts_consumables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT,
            quantity INTEGER,
            description TEXT,
            FOREIGN KEY(report_id) REFERENCES service_reports(report_id)
        )
    ''')
    
    # 3. Attachments Table (Stores images as base64 strings)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT,
            image_type TEXT, -- 'Work Order' or 'Repair'
            image_data TEXT,
            notes TEXT,
            FOREIGN KEY(report_id) REFERENCES service_reports(report_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- HELPER FUNCTIONS ---
def get_connection():
    return sqlite3.connect(DB_FILE)

def image_to_base64(uploaded_file):
    if uploaded_file is not None:
        bytes_data = uploaded_file.read()
        return base64.b64encode(bytes_data).decode()
    return None

def base64_to_image(base64_string):
    img_data = base64.b64decode(base64_string)
    return Image.open(io.BytesIO(img_data))

# --- STREAMLIT UI ---
st.set_page_config(page_title="Work Order Matrix", layout="wide")
st.title("🔧 Heavy Equipment Work Order Database")

menu = ["Create Work Order", "View & Search Work Orders"]
choice = st.sidebar.selectbox("Navigation Menu", menu)

# ==================== PAGE 1: CREATE WORK ORDER ====================
if choice == "Create Work Order":
    st.header("📝 New Service Report Entry")
    
    with st.form("work_order_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            report_id = st.text_input("Report ID / Ticket #", placeholder="e.g. 5093")
            customer_name = st.text_input("Customer Name", placeholder="e.g. BESTCO")
            date_created = st.date_input("Date Created", value=datetime.today())
        with col2:
            brand = st.text_input("Equipment Brand", placeholder="e.g. CROWN")
            model = st.text_input("Equipment Model", placeholder="e.g. SC5200")
            serial = st.text_input("Serial Number", placeholder="e.g. 9A215812")
        with col3:
            truck_num = st.text_input("Truck Number", placeholder="e.g. 2")
            billable_hours = st.number_input("Billable Hours", min_value=0.0, step=0.5, value=20.0)
            date_completed = st.date_input("Date Completed", value=datetime.today())
            
        st.write("---")
        st.subheader("📋 Remarks & Logs")
        issue = st.text_area("Issue (What's wrong?)", placeholder="GRINDING NOISE WHEN DRIVING")
        diagnosis = st.text_area("Diagnosis", placeholder="TOOK OFF STEER WHEELS & FOUND BEARING BLOWN...")
        actions = st.text_area("Actions Taken (One per line)", placeholder="REMOVE TIRES\nREMOVE HOOD\nINSTALL NEW AXLE")

        st.write("---")
        st.subheader("⚙️ Parts Consumed Quick Entry")
        st.caption("Enter parts as: Quantity, Description (e.g., 4, BEARING)")
        parts_raw = st.text_area("Parts List (One item per line)", placeholder="4, BEARING\n1, STEER SHAFT\n1, LOCK RING")

        st.write("---")
        st.subheader("📸 Media Attachments")
        work_order_pics = st.file_uploader("Upload Scans of Paper Work Orders", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'])
        repair_pics = st.file_uploader("Upload Repair Progress Photos", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'])

        submit_btn = st.form_submit_button("Save Work Order to Database")
        
        if submit_btn:
            if not report_id or not customer_name:
                st.error("❌ Report ID and Customer Name are required fields!")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    # Insert Main Report
                    cursor.execute('''
                        INSERT INTO service_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (report_id, customer_name, str(date_created), brand, model, serial, truck_num, billable_hours, str(date_completed), issue, diagnosis, actions))
                    
                    # Insert Parts
                    if parts_raw.strip():
                        for line in parts_raw.strip().split('\n'):
                            if ',' in line:
                                qty, desc = line.split(',', 1)
                                cursor.execute('INSERT INTO parts_consumables (report_id, quantity, description) VALUES (?, ?, ?)',
                                               (report_id, int(qty.strip()), desc.strip()))
                    
                    # Insert Image Scans
                    if work_order_pics:
                        for f in work_order_pics:
                            b64 = image_to_base64(f)
                            cursor.execute('INSERT INTO attachments (report_id, image_type, image_data) VALUES (?, ?, ?)', (report_id, 'Work Order', b64))
                            
                    # Insert Repair Photos
                    if repair_pics:
                        for f in repair_pics:
                            b64 = image_to_base64(f)
                            cursor.execute('INSERT INTO attachments (report_id, image_type, image_data) VALUES (?, ?, ?)', (report_id, 'Repair', b64))
                    
                    conn.commit()
                    st.success(#🎉 Work Order #{report_id} for {customer_name} successfully saved!")
                except sqlite3.IntegrityError:
                    st.error(f"❌ Database Error: A work order with Report ID '{report_id}' already exists.")
                finally:
                    conn.close()

# ==================== PAGE 2: VIEW & SEARCH WORK ORDERS ====================
elif choice == "View & Search Work Orders":
    st.header("🔍 Search Work Order History")
    
    conn = get_connection()
    search_query = st.text_input("Search by Customer Name, ID, or Serial #", "").strip()
    
    if search_query:
        query = '''SELECT report_id, customer_name, date_completed, equipment_brand, equipment_model, serial_number 
                   FROM service_reports 
                   WHERE customer_name LIKE ? OR report_id LIKE ? OR serial_number LIKE ?'''
        df = pd.read_sql_query(query, conn, params=(f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'))
    else:
        df = pd.read_sql_query("SELECT report_id, customer_name, date_completed, equipment_brand, equipment_model, serial_number FROM service_reports ORDER BY date_completed DESC", conn)
    
    conn.close()
    
    if df.empty:
        st.info("No matching records found.")
    else:
        st.dataframe(df, use_container_width=True)
        st.write("---")
        
        # Selection pane to load full details
        selected_id = st.selectbox("Select a Record ID to open detailed view:", df['report_id'].tolist())
        
        if selected_id:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Fetch report details
            cursor.execute("SELECT * FROM service_reports WHERE report_id = ?", (selected_id,))
            r = cursor.fetchone()
            
            st.subheader(f"📋 Full Details for Ticket #{r[0]}")
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Customer:** {r[1]}")
                st.markdown(f"**Equipment:** {r[3]} ({r[4]})")
                st.markdown(f"**Serial/Truck:** {r[5]} / Truck #{r[6]}")
            with col2:
                st.markdown(f"**Date Created:** {r[2]}")
                st.markdown(f"**Date Completed:** {r[8]}")
                st.markdown(f"**Total Billable Hours:** {r[7]} hrs")
                
            st.write("#### 📝 Operational Notes")
            st.info(f"**Reported Issue:**\n{r[9]}")
            st.warning(f"**Technical Diagnosis:**\n{r[10]}")
            
            st.write("**Action Log Steps:**")
            for idx, action in enumerate(r[11].split('\n'), 1):
                if action.strip():
                    st.write(f" `{idx}` {action}")
                    
            # Fetch Parts
            st.write("#### ⚙️ Parts Used")
            parts_df = pd.read_sql_query("SELECT quantity as Qty, description as Description FROM parts_consumables WHERE report_id = ?", conn, params=(selected_id,))
            if not parts_df.empty:
                st.table(parts_df)
            else:
                st.caption("No custom parts itemized for this repair.")
                
            # Fetch Pictures
            st.write("#### 📸 Associated Media Uploads")
            cursor.execute("SELECT image_type, image_data FROM attachments WHERE report_id = ?", (selected_id,))
            images = cursor.fetchall()
            
            if images:
                wo_cols = st.columns(3)
                rep_cols = st.columns(3)
                wo_idx, rep_idx = 0, 0
                
                for img_type, img_data in images:
                    img = base64_to_image(img_data)
                    if img_type == "Work Order":
                        with wo_cols[wo_idx % 3]:
                            st.image(img, caption=f"📄 Work Order Doc Scan", use_container_width=True)
                            wo_idx += 1
                    else:
                        with rep_cols[rep_idx % 3]:
                            st.image(img, caption=f"👨‍🔧 Repair Photo", use_container_width=True)
                            rep_idx += 1
            else:
                st.caption("No images uploaded to this record.")
                
            conn.close()
