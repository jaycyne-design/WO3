import streamlit as st
import sqlite3
import pandas as pd
import base64
from datetime import datetime
from PIL import Image
import io
import json
import google.generativeai as genai

# --- DATABASE SETUP ---
DB_FILE = "work_orders.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_reports (
            report_id TEXT PRIMARY KEY, customer_name TEXT, date_created TEXT,
            equipment_brand TEXT, equipment_model TEXT, serial_number TEXT,
            truck_number TEXT, truck_hours REAL, billable_hours REAL, date_completed TEXT,
            issue TEXT, diagnosis TEXT, actions TEXT
        )
    ''')
    
    # Migration helper to ensure truck_hours exists in older database files
    cursor.execute("PRAGMA table_info(service_reports)")
    columns = [col[1] for col in cursor.fetchall()]
    if "truck_hours" not in columns:
        try:
            cursor.execute("ALTER TABLE service_reports ADD COLUMN truck_hours REAL")
            conn.commit()
        except Exception:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parts_consumables (
            id INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT,
            part_number TEXT, quantity INTEGER, description TEXT,
            FOREIGN KEY(report_id) REFERENCES service_reports(report_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT,
            image_type TEXT, image_data TEXT, FOREIGN KEY(report_id) REFERENCES service_reports(report_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_connection():
    return sqlite3.connect(DB_FILE)

# --- IMAGE STORAGE HELPERS ---
def file_to_base64(uploaded_file):
    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)  # Reset stream pointer
            return base64.b64encode(uploaded_file.getvalue()).decode()
        except Exception as e:
            st.error(f"Error encoding image string: {e}")
            return None
    return None

def base64_to_image(base64_str):
    try:
        img_data = base64.b64decode(base64_str)
        return Image.open(io.BytesIO(img_data))
    except Exception:
        return None

# --- POPUP DIALOG FUNCTION ---
@st.dialog("Success Confirmation")
def show_success_popup(ticket_id):
    st.write(f"🎉 Complete record for Ticket **#{ticket_id}** has been successfully saved to the database!")
    if st.button("OK", use_container_width=True):
        st.session_state.form_data = {}
        st.session_state.main_doc_b64 = None  # Clear the persistent image cache
        if "form_reset_counter" not in st.session_state:
            st.session_state.form_reset_counter = 0
        st.session_state.form_reset_counter += 1
        st.rerun()

# --- AI OCR PARSING FUNCTION ---
def parse_work_order_with_ai(uploaded_file, api_key):
    if not api_key:
        st.error("Missing Gemini API Key!")
        return None
        
    genai.configure(api_key=api_key)
    uploaded_file.seek(0)
    image = Image.open(uploaded_file)
    
    prompt = """
    You are an expert data entry assistant for a heavy machinery repair shop. 
    Analyze this service report image. Extract the following text fields accurately. 
    If a field is empty or unreadable, return null or empty string.

    CRITICAL BUSINESS RULE FOR HOURS:
    Look at the 'Billable Hours' field on the form. 
    - If a number is written (e.g. '3.5'), extract it as a float.
    - If the word 'minimum', 'min', 'minimum charge', or similar text is written instead of a number, 
      automatically translate and extract this value as 2.0.

    Pay special attention to the 'Truck Hours' field on the form (representing engine/equipment hours).
    Also evaluate the service type check boxes labeled 'SM', 'LDI', and 'Ser' 
    located in the header metadata row. Determine if they are checked (e.g., with a checkmark, X, or filled).

    Return the result strictly as a valid JSON object with these keys (do not add markdown code blocks like ```json):
    {
      "report_id": "string",
      "customer_name": "string",
      "date_created": "YYYY-MM-DD",
      "equipment_brand": "string",
      "equipment_model": "string",
      "serial_number": "string",
      "truck_number": "string",
      "truck_hours": float,
      "billable_hours": float,
      "sm_checked": boolean,
      "ldi_checked": boolean,
      "date_completed": "YYYY-MM-DD",
      "issue": "string",
      "diagnosis": "string",
      "actions": "string (bullet points or lines separated by newlines)",
      "parts": [{"quantity": int, "description": "string", "part_number": "string"}]
    }
    """
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    with st.spinner("🤖 AI is reading your handwritten work order..."):
        try:
            response = model.generate_content([prompt, image])
            cleaned_text = response.text
            cleaned_text = cleaned_text.replace('```json', '')
            cleaned_text = cleaned_text.replace('```', '')
            cleaned_text = cleaned_text.strip()
            return json.loads(cleaned_text)
        except Exception as e:
            st.error(f"AI Extraction Failed: {e}")
            return None

# --- STREAMLIT UI ---
st.set_page_config(page_title="Smart Work Orders", layout="wide")
st.title("🔧 Kaizen Work Order System")

# Initialize session state tracking properties
if "main_doc_b64" not in st.session_state:
    st.session_state.main_doc_b64 = None
if "form_data" not in st.session_state:
    st.session_state.form_data = {}
if "form_reset_counter" not in st.session_state:
    st.session_state.form_reset_counter = 0

api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

menu = ["Scan & Create", "View & Advanced Search"]
choice = st.sidebar.selectbox("Navigation Menu", menu)

if choice == "Scan & Create":
    st.header("📸 Step 1: Upload Paper Work Order Scan")
    
    # --- FIXED: Wrapping Step 1 in its own form forces Streamlit to lock the picture stream ---
    with st.form("uploader_form"):
        main_doc = st.file_uploader("Drop a photo or scan of the work order here:", type=['png', 'jpg', 'jpeg'], key=f"uploader_{st.session_state.form_reset_counter}")
        scan_btn = st.form_submit_button("✨ Run AI Extraction Scan", use_container_width=True)
        
        if scan_btn:
            if not main_doc:
                st.warning("⚠️ Please select an image from your gallery first!")
            elif not api_key:
                st.warning("⚠️ Please add your Gemini API Key in the sidebar first!")
            else:
                # Process and safely save image to memory immediately upon form submission
                st.session_state.main_doc_b64 = file_to_base64(main_doc)
                extracted = parse_work_order_with_ai(main_doc, api_key)
                if extracted:
                    st.session_state.form_data = extracted
                    st.success("🎉 Data successfully extracted below! Please verify accuracy.")

    # Show a small preview if the image is successfully loaded in session memory
    if st.session_state.main_doc_b64:
        preview_img = base64_to_image(st.session_state.main_doc_b64)
        if preview_img:
            st.image(preview_img, caption="Active Selected Document Buffer", width=300)

    st.write("---")
    st.header("📝 Step 2: Verify & Edit Extracted Data")

    fd = st.session_state.form_data
    
    with st.form("main_verify_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            report_id = st.text_input("Report ID / Ticket #", value=str(fd.get("report_id", "")))
            customer_name = st.text_input("Customer Name", value=str(fd.get("customer_name", "")))
            date_created = st.text_input("Date Created (YYYY-MM-DD)", value=str(fd.get("date_created", "")))
        with col2:
            brand = st.text_input("Equipment Brand", value=str(fd.get("equipment_brand", "")))
            model = st.text_input("Equipment Model", value=str(fd.get("equipment_model", "")))
            serial = st.text_input("Serial Number", value=str(fd.get("serial_number", "")))
        with col3:
            truck_num = st.text_input("Truck Number", value=str(fd.get("truck_number", "")))
            truck_hours = st.number_input("Truck Hours (Machine Odometer)", value=float(fd.get("truck_hours", 0.0)) if fd.get("truck_hours") else 0.0, step=1.0)
            base_hours = st.number_input("Base Handwritten Hours", value=float(fd.get("billable_hours", 0.0)) if fd.get("billable_hours") else 0.0, step=0.5)
            date_completed = st.text_input("Date Completed (YYYY-MM-DD)", value=str(fd.get("date_completed", "")))
            
        st.write("---")
        st.subheader("📋 Service Box Checkbox Overrides")
        col_sm, col_ldi = st.columns(2)
        with col_sm:
            sm_checked = st.checkbox("SM (Scheduled Maintenance) (+1 hr)", value=bool(fd.get("sm_checked", False)))
        with col_ldi:
            ldi_checked = st.checkbox("LDI (Lift Device Inspection) (+1 hr)", value=bool(fd.get("ldi_checked", False)))

        st.write("---")
        issue = st.text_area("Extracted Issue", value=str(fd.get("issue", "")))
        diagnosis = st.text_area("Extracted Diagnosis", value=str(fd.get("diagnosis", "")))
        actions = st.text_area("Extracted Action Logs", value=str(fd.get("actions", "")))

        st.write("---")
        st.subheader("⚙️ Step 1.5: Verify Extracted Parts & Consumables")
        
        extracted_parts = fd.get("parts", [])
        if not extracted_parts:
            extracted_parts = [{"quantity": 1, "description": "", "part_number": ""}]
            
        parts_df = pd.DataFrame(extracted_parts)
        
        edited_parts_df = st.data_editor(
            parts_df, 
            num_rows="dynamic", 
            use_container_width=True,
            key=f"parts_editor_{st.session_state.form_reset_counter}",
            column_config={
                "quantity": st.column_config.NumberColumn("Qty", min_value=1, step=1, default=1),
                "part_number": st.column_config.TextColumn("Part Number"),
                "description": st.column_config.TextColumn("Description")
            }
        )

        st.write("---")
        st.subheader("📸 Step 3: Add On-Site Repair Images")
        repair_pics = st.file_uploader("Upload additional photos of physical machine parts/repairs", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'], key=f"repair_pics_{st.session_state.form_reset_counter}")

        submit_btn = st.form_submit_button("💾 Commit Approved Data to Database")
        
        if submit_btn:
            if not report_id or not customer_name:
                st.error("❌ Cannot submit: Report ID and Customer Name cannot be empty.")
            else:
                additional_hours = 0.0
                if sm_checked:
                    additional_hours += 1.0
                if ldi_checked:
                    additional_hours += 1.0
                
                final_billable_hours = base_hours + additional_hours

                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute('''
                        INSERT INTO service_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (report_id, customer_name, date_created, brand, model, serial, truck_num, truck_hours, final_billable_hours, date_completed, issue, diagnosis, actions))
                    
                    # Save main work order document image using persistent session cache string
                    if st.session_state.main_doc_b64:
                        cursor.execute('INSERT INTO attachments (report_id, image_type, image_data) VALUES (?, ?, ?)', (report_id, 'Work Order', st.session_state.main_doc_b64))
                    
                    final_parts_list = edited_parts_df.to_dict(orient="records")
                    for part in final_parts_list:
                        if part.get("part_number") or part.get("description"):
                            cursor.execute('''
                                INSERT INTO parts_consumables (report_id, part_number, quantity, description) 
                                VALUES (?, ?, ?, ?)
                            ''', (report_id, str(part.get("part_number", "")), int(part.get("quantity", 1)), str(part.get("description", ""))))
                    
                    if repair_pics:
                        for f in repair_pics:
                            img_b64 = file_to_base64(f)
                            if img_b64:
                                cursor.execute('INSERT INTO attachments (report_id, image_type, image_data) VALUES (?, ?, ?)', (report_id, 'Repair', img_b64))
                    
                    conn.commit()
                    show_success_popup(report_id)
                    
                except sqlite3.IntegrityError:
                    st.error(f"❌ Database Key Conflict: A ticket with ID '{report_id}' already exists.")
                finally:
                    conn.close()


elif choice == "View & Advanced Search":
    st.header("🔍 Advanced Search & Database Viewer")
    
    st.subheader("Filter Parameters")
    c1, c2, c3, c4 = st.columns(4)
    
    with c1:
        search_customer = st.text_input("👤 Customer Name")
    with c2:
        search_brand = st.text_input("🚜 Equipment Brand")
    with c3:
        search_keyword = st.text_input("🔑 Keyword (Issue/Diagnosis)")
    with c4:
        search_date = st.text_input("📅 Date Completed (YYYY-MM-DD)")

    query = "SELECT report_id, customer_name, equipment_brand, equipment_model, date_completed, truck_hours, billable_hours FROM service_reports WHERE 1=1"
    params = []
    
    if search_customer:
        query += " AND customer_name LIKE ?"
        params.append(f"%{search_customer}%")
    if search_brand:
        query += " AND equipment_brand LIKE ?"
        params.append(f"%{search_brand}%")
    if search_date:
        query += " AND date_completed LIKE ?"
        params.append(f"%{search_date}%")
    if search_keyword:
        query += " AND (issue LIKE ? OR diagnosis LIKE ? OR actions LIKE ?)"
        params.extend([f"%{search_keyword}%", f"%{search_keyword}%", f"%{search_keyword}%"])
        
    query += " ORDER BY date_completed DESC"

    conn = get_connection()
    df = pd.read_sql_query(query, conn, params=params)
    
    if df.empty:
        st.warning("No records matched your search parameters.")
    else:
        st.write(f"### Found {len(df)} matching records:")
        st.dataframe(df, use_container_width=True)
        
        st.write("---")
        st.subheader("📋 Select a Ticket to Inspect Details")
        
        ticket_options = df['report_id'].tolist()
        selected_ticket = st.selectbox("Choose Ticket ID to view full details:", ticket_options)
        
        if selected_ticket:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM service_reports WHERE report_id = ?", (selected_ticket,))
            report_data = cursor.fetchone()
            
            parts_df = pd.read_sql_query("SELECT quantity, part_number, description FROM parts_consumables WHERE report_id = ?", conn, params=[selected_ticket])
            
            cursor.execute("SELECT image_type, image_data FROM attachments WHERE report_id = ?", (selected_ticket,))
            attachments = cursor.fetchall()
            
            if report_data:
                st.markdown(f"## 🎫 Ticket Details: {report_data[0]}")
                
                det_col1, det_col2, det_col3 = st.columns(3)
                with det_col1:
                    st.write(f"**Customer:** {report_data[1]}")
                    st.write(f"**Date Created:** {report_data[2]}")
                    st.write(f"**Date Completed:** {report_data[9]}")
                with det_col2:
                    st.write(f"**Brand:** {report_data[3]}")
                    st.write(f"**Model:** {report_data[4]}")
                    st.write(f"**Serial #:** {report_data[5]}")
                with det_col3:
                    st.write(f"**Truck #:** {report_data[6]}")
                    st.write(f"**Truck Hours:** {report_data[7]} hrs")
                    st.write(f"**Billable Hours:** {report_data[8]} hrs")
                
                st.write("---")
                st.markdown(f"#### 🛑 Issue Reported:\n{report_data[10]}")
                st.markdown(f"#### 🔍 Diagnosis:\n{report_data[11]}")
                st.markdown(f"#### 🛠️ Actions Taken:\n{report_data[12]}")
                
                st.write("---")
                st.markdown("#### ⚙️ Parts & Consumables Used")
                if not parts_df.empty:
                    st.table(parts_df)
                else:
                    st.info("No explicit parts documented for this assignment.")
                
                st.write("---")
                st.markdown("#### 🖼️ Document & Progress Images")
                if attachments:
                    img_cols = st.columns(len(attachments))
                    for idx, (img_type, b64_data) in enumerate(attachments):
                        with img_cols[idx]:
                            pil_img = base64_to_image(b64_data)
                            if pil_img:
                                st.image(pil_img, caption=f"Type: {img_type}", use_container_width=True)
                else:
                    st.info("No file attachments linked with this ticket.")
    conn.close()



