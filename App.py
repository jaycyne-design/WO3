import streamlit as st
import sqlite3
import pandas as pd
import base64
from datetime import datetime
from PIL import Image
import io
import json
import google.generativeai as genai



# Import Google GenAI library
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
            truck_number TEXT, billable_hours REAL, date_completed TEXT,
            issue TEXT, diagnosis TEXT, actions TEXT
        )
    ''')
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

def image_to_base64(uploaded_file):
    if uploaded_file is not None:
        return base64.b64encode(uploaded_file.getvalue()).decode()
    return None

# --- AI OCR PARSING FUNCTION ---
def parse_work_order_with_ai(uploaded_file, api_key):
    """Sends the image to Gemini to extract all form fields as clean JSON."""
    if not api_key:
        st.error("Missing Gemini API Key!")
        return None
        
    genai.configure(api_key=api_key)
    
    # Load image bytes into PIL
    image = Image.open(uploaded_file)
    
    # Strictly instruct the AI to return data in a predictable format
    prompt = """
    You are an expert data entry assistant for a heavy machinery repair shop. 
    Analyze this service report image. Extract the following text fields accurately. 
    If a field is empty or unreadable, return null or empty string.

    Return the result strictly as a valid JSON object with these keys (do not add markdown code blocks like ```json):
    {
      "report_id": "string",
      "customer_name": "string",
      "date_created": "YYYY-MM-DD",
      "equipment_brand": "string",
      "equipment_model": "string",
      "serial_number": "string",
      "truck_number": "string",
      "billable_hours": float,
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
            # Clean up potential markdown formatting wrapping the JSON response
            raw_text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(raw_text)
        except Exception as e:
            st.error(f"AI Extraction Failed: {e}")
            return None

# --- STREAMLIT UI ---
st.set_page_config(page_title="Smart Work Orders", layout="wide")
st.title("🔧 Kaizen Work Order")

# Securely grab the API Key from Streamlit Secrets or a Sidebar fallback input
api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

menu = ["Scan & Create", "View & Search"]
choice = st.sidebar.selectbox("Navigation Menu", menu)

if choice == "Scan & Create":
    st.header("📸 Step 1: Upload Paper Work Order Scan")
    
    # Initialize form variables in session state so they persist and change with the upload
    if "form_data" not in st.session_state:
        st.session_state.form_data = {}
        
    main_doc = st.file_uploader("Drop a photo or scan of the work order here:", type=['png', 'jpg', 'jpeg'])
    
    if main_doc:
        st.image(main_doc, caption="Uploaded Original Document", width=400)
        if st.button("✨ Run AI Extraction Scan", use_container_width=True):
            if not api_key:
                st.warning("Please add your Gemini API Key in the sidebar first!")
            else:
                extracted = parse_work_order_with_ai(main_doc, api_key)
                if extracted:
                    st.session_state.form_data = extracted
                    st.success("🎉 Data successfully extracted below! Please verify accuracy.")

    st.write("---")
    st.header("📝 Step 2: Verify & Edit Extracted Data")

    # Fallback to defaults if AI hasn't scanned anything yet
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
            billable_hours = st.number_input("Billable Hours", value=float(fd.get("billable_hours", 0.0)) if fd.get("billable_hours") else 0.0, step=0.5)
            date_completed = st.text_input("Date Completed (YYYY-MM-DD)", value=str(fd.get("date_completed", "")))
            
        st.write("---")
        issue = st.text_area("Extracted Issue", value=str(fd.get("issue", "")))
        diagnosis = st.text_area("Extracted Diagnosis", value=str(fd.get("diagnosis", "")))
        actions = st.text_area("Extracted Action Logs", value=str(fd.get("actions", "")))

        st.write("---")
        st.subheader("📸 Step 3: Add On-Site Repair Images")
        repair_pics = st.file_uploader("Upload additional photos of physical machine parts/repairs", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'])

        submit_btn = st.form_submit_button("💾 Commit Approved Data to Database")
        
        if submit_btn:
            if not report_id or not customer_name:
                st.error("❌ Cannot submit: Report ID and Customer Name cannot be empty.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    # Save main card data
                    cursor.execute('''
                        INSERT INTO service_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (report_id, customer_name, date_created, brand, model, serial, truck_num, billable_hours, date_completed, issue, diagnosis, actions))
                    
                    # Save extracted parts dynamically
                    if "parts" in fd and fd["parts"]:
                        for part in fd["parts"]:
                            cursor.execute('INSERT INTO parts_consumables (report_id, part_number, quantity, description) VALUES (?, ?, ?, ?)',
                                           (report_id, part.get("part_number", ""), part.get("quantity", 1), part.get("description", "")))
                    
                    # Save original document image attachment automatically
                    if main_doc:
                        cursor.execute('INSERT INTO attachments (report_id, image_type, image_data) VALUES (?, ?, ?)', (report_id, 'Work Order', image_to_base64(main_doc)))
                    
                    # Save supplemental progress files
                    if repair_pics:
                        for f in repair_pics:
                            cursor.execute('INSERT INTO attachments (report_id, image_type, image_data) VALUES (?, ?, ?)', (report_id, 'Repair', image_to_base64(f)))
                    
                    conn.commit()
                    st.success(f"🎉 Complete record for Ticket #{report_id} successfully finalized in your database!")
                    st.session_state.form_data = {} # Clear cache
                except sqlite3.IntegrityError:
                    st.error(f"❌ Database Key Conflict: A ticket with ID '{report_id}' already exists.")
                finally:
                    conn.close()

elif choice == "View & Search":
    st.header("🔍 Database Viewer")
    conn = get_connection()
    df = pd.read_sql_query("SELECT report_id, customer_name, date_completed FROM service_reports ORDER BY report_id DESC", conn)
    st.dataframe(df, use_container_width=True)
    conn.close()
