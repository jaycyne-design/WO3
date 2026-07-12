import streamlit as st
import sqlite3
import pandas as pd
import base64
from datetime import datetime
from PIL import Image
import io
import json
import google.generativeai as genai
import os

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
            uploaded_file.seek(0)
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
        st.session_state.main_doc_b64 = None
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

# --- AI NATURAL LANGUAGE QUERY LOGIC ---
def ask_gemini_to_query_db(user_question, api_key):
    if not api_key:
        st.error("Missing Gemini API Key!")
        return None, None
        
    genai.configure(api_key=api_key)
    
    sql_prompt = f"""
    You are a database engineer assistant. Convert this user question into a valid SQLite query.
    
    Our SQLite database schema consists of these two tables:
    
    1. Table: service_reports
       Columns: report_id (TEXT), customer_name (TEXT), date_created (TEXT), equipment_brand (TEXT), 
                equipment_model (TEXT), serial_number (TEXT), truck_number (TEXT), truck_hours (REAL), 
                billable_hours (REAL), date_completed (TEXT), issue (TEXT), diagnosis (TEXT), actions (TEXT)
                
    2. Table: parts_consumables
       Columns: id (INTEGER), report_id (TEXT), part_number (TEXT), quantity (INTEGER), description (TEXT)

    CRITICAL RULES:
    - Respond ONLY with the executable SQL statement string. 
    - Do not wrap the code blocks in markdown fences (no ```sql or ```).
    - If filtering by dates, assume standard text-based string matches (e.g., date_completed LIKE '2026-%').
    
    User Question: "{user_question}"
    """
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    try:
        sql_response = model.generate_content(sql_prompt)
        generated_sql = sql_response.text.strip().replace('```sql', '').replace('```', '')
        
        conn = get_connection()
        query_result_df = pd.read_sql_query(generated_sql, conn)
        conn.close()
        
        interpretation_prompt = f"""
        You are a helpful management assistant for our heavy machinery repair facility.
        The user asked: "{user_question}"
        
        To find out, we ran this SQL Query: {generated_sql}
        And the database gave us this raw result table data:
        {query_result_df.to_string()}
        
        Please read this table and write a conversational response answering the user's question clearly. 
        Be concise, accurate, and professional. Use formatting where appropriate.
        """
        
        final_response = model.generate_content(interpretation_prompt)
        return final_response.text, query_result_df
        
    except Exception as e:
        st.error(f"Failed to process request: {e}")
        return None, None

# --- STREAMLIT UI CONFIG & THEME INJECTION ---
st.set_page_config(page_title="Kaizen Smart Work Orders", layout="wide")

st.markdown("""
<style>
    .stApp {
        background-color: #121418;
        color: #E2E8F0;
    }
    [data-testid="stSidebar"] {
        background-color: #1A1D24;
        border-right: 2px solid #FF6B00;
    }
    div.element-container stMarkdown div div blockquote {
        background-color: #1A1D24;
        border-left: 5px solid #FF6B00;
        padding: 1.2rem;
        border-radius: 6px;
        color: #E2E8F0;
    }
    div[data-testid="stMetric"] {
        background-color: #1A1D24;
        border: 1px solid #2D3139;
        border-top: 3px solid #FF6B00;
        padding: 1rem;
        border-radius: 6px;
    }
    div[data-testid="stMetricLabel"] {
        color: #94A3B8 !important;
        font-weight: 600;
    }
    div[data-testid="stMetricValue"] {
        color: #FF6B00 !important;
    }
    .stButton>button {
        background-color: #1A1D24 !important;
        color: #FF6B00 !important;
        border: 2px solid #FF6B00 !important;
        border-radius: 6px !important;
        font-weight: bold !important;
        transition: all 0.3s ease !important;
    }
    .stButton>button:hover {
        background-color: #FF6B00 !important;
        color: #121418 !important;
        box-shadow: 0 0 12px rgba(255, 107, 0, 0.4);
    }
    div[data-testid="stForm"] {
        background-color: #1A1D24;
        border: 1px solid #2D3139 !important;
        border-radius: 8px;
        padding: 2rem;
    }
    hr {
        border-color: #2D3139 !important;
    }
</style>
""", unsafe_allow_html=True)

if "main_doc_b64" not in st.session_state:
    st.session_state.main_doc_b64 = None
if "form_data" not in st.session_state:
    st.session_state.form_data = {}
if "form_reset_counter" not in st.session_state:
    st.session_state.form_reset_counter = 0
if "nav_choice" not in st.session_state:
    st.session_state.nav_choice = "🏠 Main Menu"

# --- SIDEBAR BRANDING & LOGO ---
with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    elif os.path.exists("logo.jpg"):
        st.image("logo.jpg", use_container_width=True)
    else:
        st.markdown("<h2 style='color:#FF6B00; text-align:center; font-family:sans-serif;'>KAIZEN LIFT LTD.</h2>", unsafe_allow_html=True)
    
    st.write("---")
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    if not api_key:
        api_key = st.text_input("Enter Gemini API Key", type="password")

menu = ["🏠 Main Menu", "📸 Scan & Create", "🔍 View & Advanced Search", "🤖 Chat & AI Query", "📊 Analytics Dashboard"]
current_index = menu.index(st.session_state.nav_choice) if st.session_state.nav_choice in menu else 0

choice = st.sidebar.selectbox("Navigation Menu", menu, index=current_index, key="sidebar_nav")
st.session_state.nav_choice = choice

# --- 1. MAIN MENU PAGE ---
if choice == "🏠 Main Menu":
    st.title("🔧 Kaizen Work Order System")
    st.markdown("### Welcome back! Select an option from the operations control board below.")
    st.write("---")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown("> ### 📸 Scan & Create\n\nUpload heavy equipment work logs. The AI reads paper scans and structures field text automatically.")
        if st.button("Launch System Scanner", use_container_width=True):
            st.session_state.nav_choice = "📸 Scan & Create"
            st.rerun()
            
    with col2:
        st.markdown("> ### 🔍 Search & View\n\nFilter indexed sheets by asset identifiers, mechanical brands, customer profiles, or custom repair dates.")
        if st.button("Browse Database Engine", use_container_width=True):
            st.session_state.nav_choice = "🔍 View & Advanced Search"
            st.rerun()
            
    with col3:
        st.markdown("> ### 🤖 Chat & AI Query\n\nExecute natural language data mining operations. Ask questions and query deep mechanics directly.")
        if st.button("Initialize Chat Engine", use_container_width=True):
            st.session_state.nav_choice = "🤖 Chat & AI Query"
            st.rerun()
            
    with col4:
        st.markdown("> ### 📊 Analytics Hub\n\nReview asset brand distributions, track mechanics operational hours, and evaluate aggregate client jobs.")
        if st.button("Load Metrics Board", use_container_width=True):
            st.session_state.nav_choice = "📊 Analytics Dashboard"
            st.rerun()

    st.write("---")
    
    try:
        conn = get_connection()
        total_tickets = pd.read_sql_query("SELECT COUNT(*) as count FROM service_reports", conn).iloc[0]['count']
        total_hours = pd.read_sql_query("SELECT SUM(billable_hours) as hours FROM service_reports", conn).iloc[0]['hours']
        conn.close()
    except Exception:
        total_tickets, total_hours = 0, 0.0

    st.subheader("📈 Plant & Fleet Metrics Overview")
    stat1, stat2 = st.columns(2)
    stat1.metric(label="Total Work Order Logs Handled", value=int(total_tickets))
    stat2.metric(label="Total Authorized Service Credit", value=f"{total_hours if total_hours else 0.0:.1f} Machine Hours")


# --- 2. SCAN & CREATE ---
elif choice == "📸 Scan & Create":
    st.title("🔧 Operations Deck: Work Order Digitization")
    st.header("📸 Step 1: Upload Paper Work Order Scan")
    
    main_doc = st.file_uploader(
        "Drop a photo or scan of the work order here:", 
        type=['png', 'jpg', 'jpeg'], 
        key=f"uploader_{st.session_state.form_reset_counter}"
    )
    
    with st.form("uploader_form"):
        scan_btn = st.form_submit_button("✨ Run AI Extraction Scan", use_container_width=True)
        
        if scan_btn:
            if not main_doc:
                st.warning("⚠️ Please select an image from your gallery first!")
            elif not api_key:
                st.warning("⚠️ Please add your Gemini API Key in the sidebar first!")
            else:
                st.session_state.main_doc_b64 = file_to_base64(main_doc)
                extracted = parse_work_order_with_ai(main_doc, api_key)
                if extracted:
                    st.session_state.form_data = extracted
                    st.success("🎉 Data successfully extracted below! Please verify accuracy.")

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


# --- 3. VIEW & ADVANCED SEARCH ---
elif choice == "🔍 View & Advanced Search":
    st.title("🔧 Search & Audit Records Console")
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


# --- 4. CHAT & AI QUERY ---
elif choice == "🤖 Chat & AI Query":
    st.title("🔧 Intelligence Terminal: Natural Language Data Mining")
    st.header("🤖 Talk directly with your Database")
    st.markdown("Ask Gemini questions about your tickets, parts, repairs, or totals using normal sentences.")
    
    st.info("💡 **Examples you can try:** \n"
            "- *'What are the details of the tickets that worked on Caterpillar equipment?'*\n"
            "- *'Show me a list of all parts used across all customer tickets.'*\n"
            "- *'Who is our top customer based on billable hours?'*")
    
    user_query = st.text_input("💬 Ask Gemini to search your records:")
    
    if user_query:
        if not api_key:
            st.warning("⚠️ Please provide your Gemini API key in the sidebar first!")
        else:
            with st.spinner("🧠 Gemini is analyzing your question and executing a lookup row match..."):
                answer, raw_data_df = ask_gemini_to_query_db(user_query, api_key)
                
                if answer:
                    st.success("🤖 Gemini Response:")
                    st.markdown(answer)
                    
                    if raw_data_df is not None and not raw_data_df.empty:
                        with st.expander("📊 View Data Engine Row Return"):
                            st.dataframe(raw_data_df, use_container_width=True)


# --- 5. ANALYTICS DASHBOARD ---
elif choice == "📊 Analytics Dashboard":
    st.title("🔧 Analytical Insight Deck")
    st.header("📊 Performance & Asset Analytics")
    
    conn = get_connection()
    all_reports_df = pd.read_sql_query("SELECT * FROM service_reports", conn)
    conn.close()
    
    if all_reports_df.empty:
        st.info("No data available to parse charts yet. Create some work order tickets first!")
    else:
        a1, a2, a3 = st.columns(3)
        with a1:
            st.metric("Unique Customer Accounts", len(all_reports_df['customer_name'].unique()))
        with a2:
            st.metric("Average Billable Session", f"{all_reports_df['billable_hours'].mean():.2f} hrs")
        with a3:
            st.metric("Max Active Machine Odometer", f"{all_reports_df['truck_hours'].max():.1f} hrs")
            
        st.write("---")
        
        chart_col1, chart_col2 = st.columns(2)
        
        with chart_col1:
            st.subheader("🚜 Job Count by Equipment Brand")
            brand_counts = all_reports_df['equipment_brand'].value_counts()
            st.bar_chart(brand_counts)
            
        with chart_col2:
            st.subheader("💰 Billable Hours Accumulation by Customer")
            customer_hours = all_reports_df.groupby('customer_name')['billable_hours'].sum().sort_values(ascending=False)
            st.bar_chart(customer_hours)


