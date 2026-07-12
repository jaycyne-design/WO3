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
        generated_sql = sql_response.text.strip().replace('```sql', '').replace('




