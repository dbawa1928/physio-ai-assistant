import os
import sqlite3
import uuid
import json
import time
import hashlib
import re
import csv
import io
import threading
import secrets
import random
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from flask_mail import Mail, Message
from dotenv import load_dotenv
from groq import Groq
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
import bleach
import langdetect
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
app.config['SESSION_TYPE'] = 'filesystem'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300

# Email configuration (optional – if not set, OTPs are printed to console)
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@physioai.com')

mail = Mail(app)

limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])
limiter.init_app(app)
cache = Cache(app)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ---------------------------- Database Setup ----------------------------
def init_db():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()

    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'doctor',
        full_name TEXT,
        profile_pic TEXT,
        created_at TEXT NOT NULL,
        last_login TEXT,
        must_change_password INTEGER DEFAULT 0,
        reset_token TEXT,
        reset_token_expiry TEXT,
        otp_code TEXT,
        otp_expiry TEXT
    )''')

    # Consultations table
    c.execute('''CREATE TABLE IF NOT EXISTS consultations (
        id TEXT PRIMARY KEY,
        user_id INTEGER,
        patient_name TEXT,
        age INTEGER,
        gender TEXT,
        phone TEXT,
        occupation TEXT,
        marital_status TEXT,
        is_pregnant TEXT,
        trimester TEXT,
        pregnancy_complications TEXT,
        children_count INTEGER,
        chief_complaint TEXT,
        cause_of_injury TEXT,
        past_history TEXT,
        surgical_history TEXT,
        chat_history TEXT,
        final_report TEXT,
        structured_recommendations TEXT,
        diagnosis TEXT,
        date TEXT
    )''')

    # Indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_consultations_user_id ON consultations(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consultations_date ON consultations(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consultations_patient_name ON consultations(patient_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")

    # Admin logs
    c.execute('''CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action TEXT,
        target_type TEXT,
        target_id TEXT,
        details TEXT,
        ip_address TEXT,
        timestamp TEXT
    )''')

    # Voice notes
    c.execute('''CREATE TABLE IF NOT EXISTS voice_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        consultation_id TEXT,
        note_type TEXT,
        audio_data TEXT,
        transcript TEXT,
        created_at TEXT
    )''')

    # Async tasks
    c.execute('''CREATE TABLE IF NOT EXISTS async_tasks (
        id TEXT PRIMARY KEY,
        task_type TEXT,
        status TEXT,
        result_path TEXT,
        error TEXT,
        created_at TEXT,
        completed_at TEXT
    )''')

    # Add missing columns for older DBs
    try:
        c.execute("ALTER TABLE consultations ADD COLUMN user_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN reset_token TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN reset_token_expiry TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN otp_code TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN otp_expiry TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()

    # Create default admin if not exists
    c.execute("SELECT id FROM users WHERE role='admin'")
    if not c.fetchone():
        hashed = generate_password_hash("Admin@123")
        c.execute("INSERT INTO users (username, email, phone, password_hash, role, full_name, created_at, must_change_password) VALUES (?,?,?,?,?,?,?,?)",
                  ("admin", "codexyra.connect@gmail.com", "", hashed, "admin", "System Administrator", datetime.now().isoformat(), 1))
        conn.commit()
    conn.close()

    # Feedback DB
    conn2 = sqlite3.connect('feedback.db')
    c2 = conn2.cursor()
    c2.execute('''CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        consultation_id TEXT,
        message_index INTEGER,
        rating TEXT,
        comment TEXT,
        timestamp TEXT
    )''')
    conn2.commit()
    conn2.close()

init_db()

# ---------------------------- Helper Functions ----------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            return "Access denied", 403
        return f(*args, **kwargs)
    return decorated_function

def log_admin_action(action, target_type, target_id, details=""):
    if session.get('role') == 'admin':
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, ip_address, timestamp) VALUES (?,?,?,?,?,?,?)",
                  (session['user_id'], action, target_type, target_id, details, request.remote_addr, datetime.now().isoformat()))
        conn.commit()
        conn.close()

def generate_otp():
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

def send_otp_email(email, otp):
    subject = "PhysioAI - Password Reset OTP"
    body = f"""Hello,

You requested to reset your password for your PhysioAI account.

Your One-Time Password (OTP) is: {otp}

This OTP is valid for 10 minutes.

If you did not request this, please ignore this email.

Best regards,
PhysioAI Team
"""
    # Print to console for development/testing
    print(f"\n=== OTP for {email} ===\n{otp}\n========================\n")
    if app.config['MAIL_USERNAME'] and app.config['MAIL_PASSWORD']:
        try:
            msg = Message(subject, recipients=[email], body=body)
            mail.send(msg)
            return True
        except Exception as e:
            app.logger.error(f"Email send failed: {e}")
            return False
    return True  # OTP printed to console

def save_consultation(consultation_id, patient_info, chat_history, final_report, structured_rec=None, diagnosis=None):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    structured_json = json.dumps(structured_rec) if structured_rec else None
    c.execute('''INSERT OR REPLACE INTO consultations 
                 (id, user_id, patient_name, age, gender, phone, occupation, marital_status, is_pregnant,
                  trimester, pregnancy_complications, children_count, chief_complaint,
                  cause_of_injury, past_history, surgical_history, chat_history, final_report,
                  structured_recommendations, diagnosis, date)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (consultation_id,
               session.get('user_id'),
               patient_info.get('full_name'),
               patient_info.get('age'),
               patient_info.get('gender'),
               patient_info.get('phone'),
               patient_info.get('occupation', ''),
               patient_info.get('marital_status'),
               patient_info.get('is_pregnant', 'No'),
               patient_info.get('trimester'),
               patient_info.get('pregnancy_complications'),
               patient_info.get('children_count', 0),
               patient_info.get('chief_complaint'),
               patient_info.get('cause_of_injury'),
               patient_info.get('past_history'),
               patient_info.get('surgical_history'),
               json.dumps(chat_history),
               final_report,
               structured_json,
               diagnosis,
               datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ---------------------------- AI Functions ----------------------------
def get_cache_key(prompt):
    return hashlib.md5(prompt.encode()).hexdigest()

def ask_ai(prompt, system_msg=None, use_cache=True, max_tokens=250):
    cache_key = get_cache_key(prompt)
    if use_cache and cache_key in cache.cache._cache:
        return cache.cache._cache[cache_key]

    if not system_msg:
        system_msg = """You are Dr. Shubham Singh, a senior physiotherapist with 20 years of clinical experience. 
        You are conducting a consultation with a patient. Your tone is professional, empathetic, and thorough.
        STRICT RULES:
        1. Ask ONLY ONE question per response. No extra text, no advice, no exercises, no diagnosis.
        2. Do NOT include any markdown formatting, bullet points, or numbered lists.
        3. Keep your response very short – just a single, clear question (max 20 words).
        4. Always respond in English.
        5. Do NOT repeat previous questions.
        6. Use the patient's age, gender, occupation, and chief complaint to ask clinically relevant questions.
        7. If you have enough information (after 10-12 exchanges), output exactly "FINAL_REPORT" and nothing else."""

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=max_tokens,
                timeout=25
            )
            answer = response.choices[0].message.content.strip()
            if use_cache:
                cache.cache._cache[cache_key] = answer
            return answer
        except Exception as e:
            app.logger.error(f"AI attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return "Please describe your main problem in more detail."
            time.sleep(2 ** attempt)
    return "Please tell me more about your symptoms."

def generate_diagnosis_options(patient_info, chat_history):
    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history[-8:]])
    prompt = f"""Based on this physiotherapy consultation, provide 3-4 possible diagnoses with confidence percentages.
Patient: {patient_info}
Chief complaint: {patient_info.get('chief_complaint', '')}
Conversation: {conversation}

Output EXACTLY in this JSON format (no extra text):
{{
    "diagnoses": [
        {{"name": "Diagnosis 1", "confidence": 85, "reasoning": "brief reasoning"}},
        {{"name": "Diagnosis 2", "confidence": 60, "reasoning": "brief reasoning"}},
        {{"name": "Diagnosis 3", "confidence": 40, "reasoning": "brief reasoning"}}
    ]
}}

Provide realistic physiotherapy diagnoses."""
    response = ask_ai(prompt, system_msg="Output ONLY valid JSON.", max_tokens=600, use_cache=False)
    response = response.strip()
    if response.startswith('```json'):
        response = response[7:]
    if response.startswith('```'):
        response = response[3:]
    if response.endswith('```'):
        response = response[:-3]
    response = response.strip()
    try:
        data = json.loads(response)
        diagnoses = data.get('diagnoses', [])
    except:
        diagnoses = [
            {"name": "Mechanical Low Back Pain", "confidence": 80, "reasoning": "Based on chief complaint and activity pattern"},
            {"name": "Lumbar Radiculopathy", "confidence": 55, "reasoning": "Possible nerve involvement"},
            {"name": "Muscle Strain", "confidence": 45, "reasoning": "Acute onset with specific movement"}
        ]
    for d in diagnoses:
        if 'confidence' not in d:
            d['confidence'] = 50
        if 'reasoning' not in d:
            d['reasoning'] = "Based on clinical presentation"
    return diagnoses

def generate_structured_recommendations(patient_info, chat_history, diagnosis):
    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history])
    prompt = f"""Based on this physiotherapy consultation, provide structured recommendations.

Patient: {patient_info}
Diagnosis: {diagnosis}
Conversation: {conversation}

Output EXACTLY in this JSON format (no extra text):
{{
    "electrotherapy": ["item1", "item2"],
    "home_exercises": ["exercise1 with reps", "exercise2 with reps"],
    "manual_therapy": ["technique1", "technique2"],
    "clinical_exercises": ["exercise1", "exercise2"],
    "dos_donts": ["Do: something", "Don't: something"]
}}"""
    response = ask_ai(prompt, system_msg="Output ONLY valid JSON.", max_tokens=800, use_cache=False)
    response = response.strip()
    if response.startswith('```json'):
        response = response[7:]
    if response.startswith('```'):
        response = response[3:]
    if response.endswith('```'):
        response = response[:-3]
    response = response.strip()
    try:
        recommendations = json.loads(response)
    except:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                recommendations = json.loads(json_match.group())
            except:
                recommendations = None
        else:
            recommendations = None
    if not recommendations:
        recommendations = {
            "electrotherapy": ["TENS for pain relief (20 min, twice daily)", "Ultrasound therapy (5 min)"],
            "home_exercises": ["Gentle stretching: hold 30 sec, 3x/day", "Strengthening: 10 reps, 2 sets"],
            "manual_therapy": ["Soft tissue mobilization", "Joint mobilization as needed"],
            "clinical_exercises": ["Core stabilization", "Postural correction exercises"],
            "dos_donts": ["Do: Maintain good posture", "Don't: Lift heavy weights"]
        }
    for key in ["electrotherapy", "home_exercises", "manual_therapy", "clinical_exercises", "dos_donts"]:
        if key not in recommendations:
            recommendations[key] = []
    return recommendations

# ---------------------------- OTP Forgot Password Routes ----------------------------
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        if not email:
            return render_template('forgot_password.html', error='Email is required')
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        if not user:
            conn.close()
            # Don't reveal if email exists
            return render_template('forgot_password.html', message='If that email is registered, you will receive an OTP.')
        # Generate OTP
        otp = generate_otp()
        expiry = (datetime.now() + timedelta(minutes=10)).isoformat()
        c.execute("UPDATE users SET otp_code = ?, otp_expiry = ? WHERE id = ?", (otp, expiry, user[0]))
        conn.commit()
        conn.close()
        if send_otp_email(email, otp):
            # Store email in session temporarily for OTP verification
            session['reset_email'] = email
            return redirect(url_for('verify_otp'))
        else:
            return render_template('forgot_password.html', error='Failed to send OTP. Please try again.')
    return render_template('forgot_password.html', error=None, message=None)

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if 'reset_email' not in session:
        return redirect(url_for('forgot_password'))
    email = session['reset_email']
    if request.method == 'POST':
        otp = request.form.get('otp')
        if not otp:
            return render_template('verify_otp.html', error='OTP is required', email=email)
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("SELECT id, otp_code, otp_expiry FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()
        if not user:
            session.pop('reset_email', None)
            return redirect(url_for('forgot_password'))
        stored_otp = user[1]
        expiry = datetime.fromisoformat(user[2]) if user[2] else None
        if not stored_otp or not expiry or datetime.now() > expiry:
            return render_template('verify_otp.html', error='OTP expired. Please request a new one.', email=email)
        if otp != stored_otp:
            return render_template('verify_otp.html', error='Invalid OTP. Please try again.', email=email)
        # OTP valid – clear it and proceed to reset password
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("UPDATE users SET otp_code = NULL, otp_expiry = NULL WHERE id = ?", (user[0],))
        conn.commit()
        conn.close()
        session['reset_user_id'] = user[0]
        session.pop('reset_email', None)
        return redirect(url_for('reset_password_with_otp'))
    return render_template('verify_otp.html', email=email)

@app.route('/reset_password_otp', methods=['GET', 'POST'])
def reset_password_with_otp():
    if 'reset_user_id' not in session:
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        if not password or len(password) < 6:
            return render_template('reset_password_otp.html', error='Password must be at least 6 characters.')
        if password != confirm:
            return render_template('reset_password_otp.html', error='Passwords do not match.')
        hashed = generate_password_hash(password)
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?", (hashed, session['reset_user_id']))
        conn.commit()
        conn.close()
        session.pop('reset_user_id', None)
        return redirect(url_for('login'))
    return render_template('reset_password_otp.html')

# ---------------------------- Auth Routes ----------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("SELECT id, password_hash, role, full_name, must_change_password FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['role'] = user[2]
            session['full_name'] = user[3] if user[3] else email.split('@')[0]
            session['email'] = email
            conn = sqlite3.connect('consultations.db')
            c = conn.cursor()
            c.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.now().isoformat(), user[0]))
            conn.commit()
            conn.close()
            if user[4] == 1:
                return redirect(url_for('change_password'))
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid email or password')
    return render_template('login.html', error=None)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        confirm = request.form['confirm_password']
        if password != confirm:
            return render_template('register.html', error='Passwords do not match')
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email = ?", (email,))
        if c.fetchone():
            conn.close()
            return render_template('register.html', error='Email already registered')
        hashed = generate_password_hash(password)
        username = email.split('@')[0]
        base_username = username
        counter = 1
        while True:
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            if not c.fetchone():
                break
            username = f"{base_username}{counter}"
            counter += 1
        c.execute("INSERT INTO users (username, email, phone, password_hash, role, full_name, created_at, must_change_password) VALUES (?,?,?,?,?,?,?,?)",
                  (username, email, phone, hashed, 'doctor', full_name, datetime.now().isoformat(), 0))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------------------------- Profile & Password ----------------------------
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    if request.method == 'POST':
        full_name = request.form['full_name']
        phone = request.form['phone']
        c.execute("UPDATE users SET full_name = ?, phone = ? WHERE id = ?", (full_name, phone, session['user_id']))
        conn.commit()
        session['full_name'] = full_name
        conn.close()
        return redirect(url_for('profile'))
    c.execute("SELECT full_name, email, phone, created_at, profile_pic FROM users WHERE id = ?", (session['user_id'],))
    user = c.fetchone()
    conn.close()
    return render_template('profile.html', user=user)

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form['current_password']
        new = request.form['new_password']
        confirm = request.form['confirm_password']
        if new != confirm:
            return render_template('change_password.html', error='New passwords do not match')
        conn = sqlite3.connect('consultations.db')
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE id = ?", (session['user_id'],))
        stored = c.fetchone()[0]
        if not check_password_hash(stored, current):
            conn.close()
            return render_template('change_password.html', error='Current password is incorrect')
        new_hash = generate_password_hash(new)
        c.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?", (new_hash, session['user_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('profile'))
    return render_template('change_password.html', error=None)

# ---------------------------- Admin Dashboard & Management ----------------------------
@app.route('/admin')
@admin_required
def admin():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM users WHERE role='doctor'")
    total_doctors = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM consultations")
    total_consultations = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM consultations WHERE date LIKE ?", (datetime.now().strftime('%Y-%m-%d') + '%',))
    month_consultations = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT patient_name) FROM consultations")
    unique_patients = c.fetchone()[0]

    conn2 = sqlite3.connect('feedback.db')
    c2 = conn2.cursor()
    c2.execute("SELECT rating, COUNT(*) FROM feedback GROUP BY rating")
    feedback_stats = dict(c2.fetchall())
    conn2.close()

    up = feedback_stats.get('up', 0)
    down = feedback_stats.get('down', 0)
    total_fb = up + down
    avg_rating = (up / total_fb) * 5 if total_fb > 0 else 0

    c.execute("SELECT id, full_name, email, phone, created_at, last_login FROM users WHERE role='doctor' ORDER BY created_at DESC")
    doctors = []
    for doc in c.fetchall():
        doc_id, full_name, email, phone, created_at, last_login = doc
        c.execute("SELECT COUNT(*) FROM consultations WHERE user_id=?", (doc_id,))
        total_patients = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT patient_name) FROM consultations WHERE user_id=?", (doc_id,))
        unique_patients_count = c.fetchone()[0]
        doctors.append({
            'id': doc_id,
            'full_name': full_name,
            'email': email,
            'phone': phone or '—',
            'created_at': created_at,
            'last_login': last_login or 'Never',
            'total_patients': total_patients,
            'unique_patients': unique_patients_count
        })

    c.execute("SELECT u.full_name, COUNT(c.id) FROM users u LEFT JOIN consultations c ON u.id = c.user_id WHERE u.role='doctor' GROUP BY u.id")
    chart_data = [{'doctor': row[0] or 'Unknown', 'count': row[1]} for row in c.fetchall()]

    conn.close()
    return render_template('admin.html',
                         total_doctors=total_doctors,
                         total_consultations=total_consultations,
                         month_consultations=month_consultations,
                         unique_patients=unique_patients,
                         avg_rating=round(avg_rating, 1),
                         doctors=doctors,
                         chart_data=chart_data,
                         feedback_stats=feedback_stats)

@app.route('/admin/update_doctor', methods=['POST'])
@admin_required
def admin_update_doctor():
    data = request.json
    doctor_id = data.get('id')
    full_name = data.get('full_name')
    phone = data.get('phone')
    if not doctor_id:
        return jsonify({'error': 'Missing doctor ID'}), 400
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("UPDATE users SET full_name = ?, phone = ? WHERE id = ? AND role = 'doctor'", (full_name, phone, doctor_id))
    conn.commit()
    conn.close()
    log_admin_action('edit_doctor', 'doctor', doctor_id, f"Updated name: {full_name}, phone: {phone}")
    return jsonify({'status': 'ok'})

@app.route('/admin/doctor_details/<int:doctor_id>')
@admin_required
def admin_doctor_details(doctor_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT id, username, full_name, email, phone, created_at, last_login FROM users WHERE id = ? AND role = 'doctor'", (doctor_id,))
    doctor = c.fetchone()
    if not doctor:
        return jsonify({'error': 'Doctor not found'}), 404
    c.execute("SELECT COUNT(*) FROM consultations WHERE user_id = ?", (doctor_id,))
    total_consults = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT patient_name) FROM consultations WHERE user_id = ?", (doctor_id,))
    unique_patients = c.fetchone()[0]
    conn.close()
    return jsonify({
        'id': doctor[0],
        'username': doctor[1],
        'full_name': doctor[2],
        'email': doctor[3],
        'phone': doctor[4] or '',
        'created_at': doctor[5],
        'last_login': doctor[6] or '',
        'total_consultations': total_consults,
        'unique_patients': unique_patients
    })

@app.route('/admin/delete_doctor', methods=['POST'])
@admin_required
def admin_delete_doctor():
    data = request.json
    doctor_id = data.get('id')
    if not doctor_id:
        return jsonify({'error': 'Missing doctor ID'}), 400
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("DELETE FROM consultations WHERE user_id = ?", (doctor_id,))
    c.execute("DELETE FROM users WHERE id = ? AND role = 'doctor'", (doctor_id,))
    conn.commit()
    conn.close()
    log_admin_action('delete_doctor', 'doctor', doctor_id, f"Deleted doctor and all their consultations")
    return jsonify({'status': 'ok'})

@app.route('/admin/export/<string:type>')
@admin_required
def export_data(type):
    conn = sqlite3.connect('consultations.db')
    output = io.StringIO()
    writer = csv.writer(output)
    if type == 'consultations':
        c = conn.cursor()
        c.execute("SELECT id, patient_name, age, gender, phone, diagnosis, date FROM consultations ORDER BY date DESC")
        writer.writerow(['ID', 'Patient Name', 'Age', 'Gender', 'Phone', 'Diagnosis', 'Date'])
        writer.writerows(c.fetchall())
        filename = f"consultations_{datetime.now().strftime('%Y%m%d')}.csv"
    elif type == 'doctors':
        c = conn.cursor()
        c.execute("SELECT id, full_name, email, phone, created_at, last_login FROM users WHERE role='doctor'")
        writer.writerow(['ID', 'Full Name', 'Email', 'Phone', 'Registered', 'Last Login'])
        writer.writerows(c.fetchall())
        filename = f"doctors_{datetime.now().strftime('%Y%m%d')}.csv"
    elif type == 'feedback':
        conn2 = sqlite3.connect('feedback.db')
        c2 = conn2.cursor()
        c2.execute("SELECT consultation_id, rating, comment, timestamp FROM feedback")
        writer.writerow(['Consultation ID', 'Rating', 'Comment', 'Timestamp'])
        writer.writerows(c2.fetchall())
        conn2.close()
        filename = f"feedback_{datetime.now().strftime('%Y%m%d')}.csv"
    else:
        return "Invalid export type", 400
    conn.close()
    output.seek(0)
    log_admin_action('export', type, 'all', f"Exported {type}")
    return send_file(io.BytesIO(output.getvalue().encode('utf-8')), as_attachment=True, download_name=filename, mimetype='text/csv')

@app.route('/admin/bulk_delete', methods=['POST'])
@admin_required
def bulk_delete():
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    placeholders = ','.join('?' * len(ids))
    c.execute(f"DELETE FROM consultations WHERE id IN ({placeholders})", ids)
    deleted = c.rowcount
    conn.commit()
    conn.close()
    log_admin_action('bulk_delete', 'consultations', ','.join(ids), f"Deleted {deleted} consultations")
    return jsonify({'status': 'ok', 'deleted': deleted})

@app.route('/admin/logs')
@admin_required
def admin_logs():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT timestamp, action, target_type, target_id, details, ip_address FROM admin_logs ORDER BY timestamp DESC LIMIT 100")
    logs = [{'timestamp': r[0], 'action': r[1], 'target_type': r[2], 'target_id': r[3], 'details': r[4], 'ip_address': r[5]} for r in c.fetchall()]
    conn.close()
    return render_template('admin_logs.html', logs=logs)

@app.route('/admin/health')
@admin_required
def health_monitor():
    start = time.time()
    try:
        client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": "ping"}], max_tokens=1, timeout=2)
        api_latency = round((time.time() - start) * 1000, 2)
    except:
        api_latency = None
    db_size = os.path.getsize('consultations.db') / (1024*1024) if os.path.exists('consultations.db') else 0
    cache_hits = len(cache.cache._cache) if hasattr(cache.cache, '_cache') else 0
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    conn.close()
    return jsonify({
        'api_latency_ms': api_latency,
        'database_size_mb': round(db_size, 2),
        'cache_entries': cache_hits,
        'total_users': total_users,
        'server_time': datetime.now().isoformat()
    })

@app.route('/admin/dashboard_data')
@admin_required
def dashboard_data():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT diagnosis, COUNT(*) as cnt FROM consultations WHERE diagnosis IS NOT NULL GROUP BY diagnosis ORDER BY cnt DESC LIMIT 5")
    top_diagnoses = [{'name': row[0][:50], 'count': row[1]} for row in c.fetchall()]
    c.execute("SELECT age FROM consultations WHERE age IS NOT NULL")
    ages = [row[0] for row in c.fetchall()]
    age_groups = {'0-18': 0, '19-30': 0, '31-50': 0, '51+': 0}
    for a in ages:
        if a <= 18: age_groups['0-18'] += 1
        elif a <= 30: age_groups['19-30'] += 1
        elif a <= 50: age_groups['31-50'] += 1
        else: age_groups['51+'] += 1
    c.execute("SELECT gender, COUNT(*) FROM consultations WHERE gender IS NOT NULL GROUP BY gender")
    gender_data = [{'gender': row[0], 'count': row[1]} for row in c.fetchall()]
    conn.close()
    return jsonify({
        'top_diagnoses': top_diagnoses,
        'age_groups': age_groups,
        'gender_data': gender_data
    })

@app.route('/admin/doctor_performance')
@admin_required
def doctor_performance():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT u.id, u.full_name, COUNT(c.id) as total_consults, COUNT(DISTINCT c.patient_name) as unique_patients FROM users u LEFT JOIN consultations c ON u.id = c.user_id WHERE u.role='doctor' GROUP BY u.id")
    doctors = []
    for row in c.fetchall():
        doc_id, name, total, unique = row
        conn2 = sqlite3.connect('feedback.db')
        c2 = conn2.cursor()
        c2.execute("SELECT rating, COUNT(*) FROM feedback WHERE consultation_id IN (SELECT id FROM consultations WHERE user_id=?) GROUP BY rating", (doc_id,))
        fb = dict(c2.fetchall())
        up = fb.get('up', 0)
        down = fb.get('down', 0)
        total_fb = up + down
        avg_rating = (up / total_fb * 5) if total_fb > 0 else 0
        conn2.close()
        doctors.append({
            'name': name,
            'total_consultations': total,
            'unique_patients': unique,
            'avg_rating': round(avg_rating, 1)
        })
    conn.close()
    return jsonify(doctors)

# ---------------------------- Asynchronous PDF Generation ----------------------------
def generate_pdf_async(consultation_id, user_id, patient_info, diagnosis, structured_rec, doctor_name):
    time.sleep(0.5)
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import inch
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=0.75*inch, rightMargin=0.75*inch, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=18, textColor=colors.HexColor('#4F46E5'), alignment=1, spaceAfter=12)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=14, textColor=colors.HexColor('#1F2937'), spaceAfter=8, spaceBefore=12, fontName='Helvetica-Bold')
    subheading_style = ParagraphStyle('SubHeading', parent=styles['Heading3'], fontSize=12, textColor=colors.HexColor('#4F46E5'), spaceAfter=6)
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=10, leading=14)
    doctor_style = ParagraphStyle('Doctor', parent=styles['Normal'], fontSize=11, textColor=colors.HexColor('#4F46E5'), alignment=2, spaceAfter=4, fontName='Helvetica-Bold')
    footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, textColor=colors.grey, alignment=1)
    story = []
    story.append(Paragraph("🏥 PhysioAI", title_style))
    story.append(Paragraph("Smart Physiotherapy Consultation & Report", styles['Normal']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<b>{doctor_name} (PT)</b><br/>Senior Physiotherapist", doctor_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Patient Information", heading_style))
    patient_data = [
        [f"<b>Name:</b> {patient_info.get('name', '')}", f"<b>Age:</b> {patient_info.get('age', '')}", f"<b>Gender:</b> {patient_info.get('gender', '')}"],
        [f"<b>Phone:</b> {patient_info.get('phone', '')}", f"<b>Occupation:</b> {patient_info.get('occupation', '')}", ""],
        [f"<b>Chief Complaint:</b> {patient_info.get('chief_complaint', '')}", "", ""]
    ]
    t = Table(patient_data, colWidths=[2.2*inch, 1.8*inch, 1.8*inch])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey)
    ]))
    story.append(t)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Diagnosis", heading_style))
    story.append(Paragraph(diagnosis, normal_style))
    story.append(Spacer(1, 12))
    sections = [
        ('Electrotherapy', structured_rec.get('electrotherapy', []), '⚡'),
        ('Home Exercises', structured_rec.get('home_exercises', []), '🏠'),
        ('Manual Therapy', structured_rec.get('manual_therapy', []), '✋'),
        ('Clinical Exercises', structured_rec.get('clinical_exercises', []), '🏋️'),
        ("Do's & Don'ts", structured_rec.get('dos_donts', []), '📋')
    ]
    for title, items, icon in sections:
        if items:
            story.append(Paragraph(f"{icon} <b>{title}</b>", subheading_style))
            for item in items:
                story.append(Paragraph(f"• {item}", normal_style))
            story.append(Spacer(1, 8))
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"<b>Date:</b> {datetime.now().strftime('%B %d, %Y')}", normal_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Doctor's Signature:</b> ___________________", normal_style))
    story.append(Paragraph(f"<b>Dr. {doctor_name}</b> (PT)", normal_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<i>Medical Disclaimer: This report is AI-assisted. Please consult a qualified physiotherapist for personalized advice.</i>", footer_style))
    def add_watermark(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 50)
        canvas_obj.setFillColorRGB(0.8, 0.8, 0.8, alpha=0.3)
        canvas_obj.rotate(45)
        canvas_obj.drawString(250, 100, "PhysioAI")
        canvas_obj.restoreState()
    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    buffer.seek(0)
    os.makedirs('static/temp', exist_ok=True)
    filename = f"report_{consultation_id}.pdf"
    filepath = os.path.join('static', 'temp', filename)
    with open(filepath, 'wb') as f:
        f.write(buffer.getvalue())
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("UPDATE async_tasks SET status='completed', result_path=?, completed_at=? WHERE id=?", (filepath, datetime.now().isoformat(), consultation_id))
    conn.commit()
    conn.close()

@app.route('/request_pdf/<consultation_id>')
@login_required
def request_pdf(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT status, result_path FROM async_tasks WHERE id=?", (consultation_id,))
    task = c.fetchone()
    if task:
        if task[0] == 'completed':
            return jsonify({'status': 'ready', 'url': f'/static/temp/report_{consultation_id}.pdf'})
        else:
            return jsonify({'status': 'pending'})
    c.execute("SELECT patient_name, age, gender, phone, occupation, chief_complaint, diagnosis, structured_recommendations FROM consultations WHERE id=?", (consultation_id,))
    row = c.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    patient_info = {'name': row[0], 'age': row[1], 'gender': row[2], 'phone': row[3], 'occupation': row[4], 'chief_complaint': row[5]}
    diagnosis = row[6] or "Not specified"
    structured_rec = json.loads(row[7]) if row[7] else {}
    doctor_name = session.get('full_name', 'Dr. Shubham Singh')
    c.execute("INSERT INTO async_tasks (id, task_type, status, created_at) VALUES (?,?,?,?)", (consultation_id, 'pdf_generation', 'pending', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    thread = threading.Thread(target=generate_pdf_async, args=(consultation_id, session['user_id'], patient_info, diagnosis, structured_rec, doctor_name))
    thread.start()
    return jsonify({'status': 'started'})

# ---------------------------- Voice Notes ----------------------------
@app.route('/save_voice_note', methods=['POST'])
@login_required
def save_voice_note():
    data = request.json
    consultation_id = data.get('consultation_id')
    audio_data = data.get('audio_data')
    transcript = data.get('transcript', '')
    if not consultation_id or not audio_data:
        return jsonify({'error': 'Missing data'}), 400
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("INSERT INTO voice_notes (consultation_id, note_type, audio_data, transcript, created_at) VALUES (?,?,?,?,?)",
              (consultation_id, 'doctor_note', audio_data, transcript, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

# ---------------------------- Print-friendly Report ----------------------------
@app.route('/print_report/<consultation_id>')
@login_required
def print_report(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    if session.get('role') == 'admin':
        c.execute("SELECT * FROM consultations WHERE id=?", (consultation_id,))
    else:
        c.execute("SELECT * FROM consultations WHERE id=? AND user_id=?", (consultation_id, session['user_id']))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Report not found", 404
    columns = ['id','patient_name','age','gender','phone','occupation','marital_status','is_pregnant',
               'trimester','pregnancy_complications','children_count','chief_complaint',
               'cause_of_injury','past_history','surgical_history','chat_history',
               'final_report','structured_recommendations','diagnosis','date']
    report = dict(zip(columns, row))
    if report.get('structured_recommendations'):
        report['structured_recommendations'] = json.loads(report['structured_recommendations'])
    else:
        report['structured_recommendations'] = {}
    return render_template('print_report.html', report=report, doctor_name=session.get('full_name', 'Dr. Shubham Singh'))

# ---------------------------- Main Consultation Routes ----------------------------
@app.route('/')
@login_required
def index():
    lang = session.get('lang', 'en')
    return render_template('index.html', lang=lang, get_text=get_text)

@app.route('/set_language', methods=['POST'])
@login_required
def set_language():
    session['lang'] = request.form.get('lang', 'en')
    return redirect(url_for('index'))

@app.route('/submit_patient', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def submit_patient():
    patient_info = {
        'full_name': bleach.clean(request.form.get('full_name', '')),
        'age': int(request.form.get('age', 0)),
        'gender': bleach.clean(request.form.get('gender', '')),
        'phone': bleach.clean(request.form.get('phone', '')),
        'occupation': bleach.clean(request.form.get('occupation', '')),
        'marital_status': bleach.clean(request.form.get('marital_status', '')),
        'is_pregnant': bleach.clean(request.form.get('is_pregnant', 'No')),
        'trimester': bleach.clean(request.form.get('trimester', '')),
        'pregnancy_complications': bleach.clean(request.form.get('pregnancy_complications', '')),
        'children_count': int(request.form.get('children_count', 0)),
        'chief_complaint': bleach.clean(request.form.get('chief_complaint', '')),
        'cause_of_injury': bleach.clean(request.form.get('cause_of_injury', '')),
        'past_history': bleach.clean(request.form.get('past_history', '')),
        'surgical_history': bleach.clean(request.form.get('surgical_history', ''))
    }
    session['patient_info'] = patient_info
    session['chat_history'] = []
    session['is_complete'] = False
    session['user_message_count'] = 0
    session['consultation_id'] = str(uuid.uuid4())
    first_question = get_initial_question(patient_info)
    session['chat_history'].append({'role': 'assistant', 'content': first_question})
    session.modified = True
    return redirect(url_for('chat'))

def get_initial_question(patient_info):
    context = f"""Patient: {patient_info['full_name']}, age {patient_info['age']}, {patient_info['gender']}.
Occupation: {patient_info.get('occupation', 'unknown')}.
Chief complaint: {patient_info['chief_complaint']}.
Cause of injury: {patient_info.get('cause_of_injury', 'unknown')}.
Pregnancy: {patient_info.get('is_pregnant', 'No')}.
Ask the very first question as Dr. Shubham Singh (20 years experience). Be empathetic, professional, and consider the patient's occupation. Only one question."""
    return ask_ai(context)

@app.route('/chat')
@login_required
def chat():
    if 'patient_info' not in session:
        return redirect(url_for('index'))
    lang = session.get('lang', 'en')
    max_questions = 12
    current_step = session.get('user_message_count', 0) + 1
    return render_template('chat.html', lang=lang, get_text=get_text, patient_info=session['patient_info'],
                           max_questions=max_questions, current_step=current_step)

@app.route('/get_chat_state')
@login_required
def get_chat_state():
    if 'patient_info' not in session:
        return jsonify({'error': 'No active session'}), 400
    return jsonify({
        'messages': session.get('chat_history', []),
        'is_complete': session.get('is_complete', False),
        'consultation_id': session.get('consultation_id'),
        'current_step': session.get('user_message_count', 0) + 1
    })

@app.route('/send_answer', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def send_answer():
    if 'patient_info' not in session:
        return jsonify({'error': 'Session expired'}), 400
    user_answer = request.json.get('answer', '').strip()
    if not user_answer:
        return jsonify({'error': 'Empty message'}), 400
    session['chat_history'].append({'role': 'user', 'content': user_answer})
    session['user_message_count'] = session.get('user_message_count', 0) + 1
    user_msg_count = session['user_message_count']
    MAX_QUESTIONS = 12
    if session.get('is_complete', False):
        return jsonify({'is_complete': True, 'messages': session['chat_history']})
    if user_msg_count >= MAX_QUESTIONS:
        save_consultation(session['consultation_id'], session['patient_info'], session['chat_history'], "", None, None)
        session['is_complete'] = True
        session.modified = True
        return jsonify({'is_complete': True, 'messages': session['chat_history'], 'consultation_id': session['consultation_id']})
    last_few = session['chat_history'][-6:]
    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in last_few])
    prompt = f"""You are Dr. Shubham Singh, senior physiotherapist (20 years experience). Continue the consultation.
Patient occupation: {session['patient_info'].get('occupation', 'unknown')}
Patient has answered {user_msg_count} questions so far. Max {MAX_QUESTIONS}.
Ask only ONE follow-up question. Be professional, empathetic, and consider their job.
Conversation:
{conversation}
Your next question:"""
    ai_response = ask_ai(prompt)
    session['chat_history'].append({'role': 'assistant', 'content': ai_response})
    session.modified = True
    return jsonify({'is_complete': False, 'messages': session['chat_history'], 'current_step': user_msg_count + 1})

@app.route('/diagnosis_selection')
@login_required
def diagnosis_selection():
    if 'consultation_id' not in session:
        return redirect(url_for('index'))
    consultation_id = session['consultation_id']
    patient_info = session.get('patient_info', {})
    chat_history = session.get('chat_history', [])
    diagnoses = generate_diagnosis_options(patient_info, chat_history)
    return render_template('diagnosis_selection.html',
                         patient_info=patient_info,
                         diagnoses=diagnoses,
                         consultation_id=consultation_id,
                         lang=session.get('lang', 'en'),
                         get_text=get_text)

@app.route('/save_selected_diagnoses', methods=['POST'])
@login_required
def save_selected_diagnoses():
    data = request.json
    consultation_id = data.get('consultation_id')
    selected_diagnoses = data.get('selected_diagnoses', [])
    custom_diagnosis = data.get('custom_diagnosis', '').strip()
    if not consultation_id:
        return jsonify({'error': 'Missing consultation ID'}), 400
    if custom_diagnosis:
        primary_diagnosis = custom_diagnosis
    elif selected_diagnoses:
        primary_diagnosis = selected_diagnoses[0]
        if len(selected_diagnoses) > 1:
            primary_diagnosis = f"{primary_diagnosis} (differential: {', '.join(selected_diagnoses[1:])})"
    else:
        return jsonify({'error': 'Please select or enter a diagnosis'}), 400
    session['diagnosis'] = primary_diagnosis
    patient_info = session.get('patient_info', {})
    chat_history = session.get('chat_history', [])
    structured_rec = generate_structured_recommendations(patient_info, chat_history, primary_diagnosis)
    session['structured_recommendations'] = structured_rec
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("UPDATE consultations SET diagnosis = ?, structured_recommendations = ? WHERE id = ?",
              (primary_diagnosis, json.dumps(structured_rec), consultation_id))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'redirect': url_for('report_edit')})

@app.route('/report_edit')
@login_required
def report_edit():
    if 'consultation_id' not in session:
        return redirect(url_for('index'))
    consultation_id = session['consultation_id']
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT patient_name, age, gender, phone, occupation, chief_complaint, diagnosis, structured_recommendations, date FROM consultations WHERE id=? AND (user_id=? OR ?=1)", 
              (consultation_id, session['user_id'], session.get('role')=='admin'))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Report not found", 404
    patient_info = {'full_name': row[0], 'age': row[1], 'gender': row[2], 'phone': row[3], 'occupation': row[4], 'chief_complaint': row[5]}
    diagnosis = row[6] or "Not specified"
    structured_rec = {}
    if row[7]:
        try:
            structured_rec = json.loads(row[7])
        except:
            structured_rec = {}
    date = row[8]
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT id, patient_name, diagnosis, date FROM consultations WHERE patient_name=? AND id!=? AND user_id=? ORDER BY date DESC LIMIT 5", 
              (patient_info['full_name'], consultation_id, session['user_id']))
    history_rows = c.fetchall()
    conn.close()
    history = [{'id': h[0], 'diagnosis': h[2], 'date': h[3]} for h in history_rows]
    lang = session.get('lang', 'en')
    return render_template('report_edit.html', patient_info=patient_info, diagnosis=diagnosis, recommendations=structured_rec,
                         consultation_id=consultation_id, date=date, history=history, lang=lang, get_text=get_text)

@app.route('/save_structured_recommendations', methods=['POST'])
@login_required
def save_structured_recommendations():
    data = request.json
    consultation_id = data.get('consultation_id')
    recommendations = data.get('recommendations')
    diagnosis = data.get('diagnosis')
    if not consultation_id or not recommendations:
        return jsonify({'error': 'Missing data'}), 400
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("UPDATE consultations SET structured_recommendations = ?, diagnosis = ? WHERE id = ? AND (user_id=? OR ?=1)", 
              (json.dumps(recommendations), diagnosis, consultation_id, session['user_id'], session.get('role')=='admin'))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/download_structured_pdf/<consultation_id>')
@login_required
def download_structured_pdf(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT patient_name, age, gender, phone, occupation, chief_complaint, diagnosis, structured_recommendations, date FROM consultations WHERE id=? AND (user_id=? OR ?=1)", 
              (consultation_id, session['user_id'], session.get('role')=='admin'))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Not found", 404
    patient = {'name': row[0], 'age': row[1], 'gender': row[2], 'phone': row[3], 'occupation': row[4], 'chief_complaint': row[5]}
    diagnosis = row[6] or "Not specified"
    structured_rec = json.loads(row[7]) if row[7] else {}
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=0.75*inch, rightMargin=0.75*inch, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=18, textColor=colors.HexColor('#4F46E5'), alignment=1, spaceAfter=12)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=14, textColor=colors.HexColor('#1F2937'), spaceAfter=8, spaceBefore=12, fontName='Helvetica-Bold')
    subheading_style = ParagraphStyle('SubHeading', parent=styles['Heading3'], fontSize=12, textColor=colors.HexColor('#4F46E5'), spaceAfter=6)
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=10, leading=14)
    doctor_style = ParagraphStyle('Doctor', parent=styles['Normal'], fontSize=11, textColor=colors.HexColor('#4F46E5'), alignment=2, spaceAfter=4, fontName='Helvetica-Bold')
    footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, textColor=colors.grey, alignment=1)
    story = []
    story.append(Paragraph("🏥 PhysioAI", title_style))
    story.append(Paragraph("Smart Physiotherapy Consultation & Report", styles['Normal']))
    story.append(Spacer(1, 6))
    doctor_name = session.get('full_name', 'Dr. Shubham Singh')
    story.append(Paragraph(f"<b>{doctor_name} (PT)</b><br/>Senior Physiotherapist", doctor_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Patient Information", heading_style))
    patient_data = [
        [f"<b>Name:</b> {patient['name']}", f"<b>Age:</b> {patient['age']}", f"<b>Gender:</b> {patient['gender']}"],
        [f"<b>Phone:</b> {patient['phone']}", f"<b>Occupation:</b> {patient['occupation']}", ""],
        [f"<b>Chief Complaint:</b> {patient['chief_complaint']}", "", ""]
    ]
    t = Table(patient_data, colWidths=[2.2*inch, 1.8*inch, 1.8*inch])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey)
    ]))
    story.append(t)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Diagnosis", heading_style))
    story.append(Paragraph(diagnosis, normal_style))
    story.append(Spacer(1, 12))
    sections = [
        ('Electrotherapy', structured_rec.get('electrotherapy', []), '⚡'),
        ('Home Exercises', structured_rec.get('home_exercises', []), '🏠'),
        ('Manual Therapy', structured_rec.get('manual_therapy', []), '✋'),
        ('Clinical Exercises', structured_rec.get('clinical_exercises', []), '🏋️'),
        ("Do's & Don'ts", structured_rec.get('dos_donts', []), '📋')
    ]
    for title, items, icon in sections:
        if items:
            story.append(Paragraph(f"{icon} <b>{title}</b>", subheading_style))
            for item in items:
                story.append(Paragraph(f"• {item}", normal_style))
            story.append(Spacer(1, 8))
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"<b>Date:</b> {datetime.now().strftime('%B %d, %Y')}", normal_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Doctor's Signature:</b> ___________________", normal_style))
    story.append(Paragraph(f"<b>Dr. {doctor_name}</b> (PT)", normal_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<i>Medical Disclaimer: This report is AI-assisted. Please consult a qualified physiotherapist for personalized advice.</i>", footer_style))
    def add_watermark(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 50)
        canvas_obj.setFillColorRGB(0.8, 0.8, 0.8, alpha=0.3)
        canvas_obj.rotate(45)
        canvas_obj.drawString(250, 100, "PhysioAI")
        canvas_obj.restoreState()
    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"physioai_report_{consultation_id}.pdf", mimetype='application/pdf')

@app.route('/submit_feedback', methods=['POST'])
@login_required
def submit_feedback():
    data = request.json
    conn = sqlite3.connect('feedback.db')
    c = conn.cursor()
    c.execute("INSERT INTO feedback (consultation_id, message_index, rating, comment, timestamp) VALUES (?,?,?,?,?)",
              (data.get('consultation_id'), data.get('message_index'), data.get('rating'), data.get('comment', ''), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/reset')
@login_required
def reset():
    session.pop('patient_info', None)
    session.pop('chat_history', None)
    session.pop('is_complete', None)
    session.pop('consultation_id', None)
    session.pop('diagnosis', None)
    return redirect(url_for('index'))

@app.route('/history')
@login_required
def history():
    lang = session.get('lang', 'en')
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    if session.get('role') == 'admin':
        c.execute("SELECT id, patient_name, age, diagnosis, date FROM consultations ORDER BY date DESC")
    else:
        c.execute("SELECT id, patient_name, age, diagnosis, date FROM consultations WHERE user_id=? ORDER BY date DESC", (session['user_id'],))
    rows = c.fetchall()
    consultations = [{'id': r[0], 'patient_name': r[1], 'age': r[2], 'diagnosis': r[3] or 'N/A', 'date': r[4]} for r in rows]
    conn.close()
    return render_template('history.html', consultations=consultations, lang=lang, get_text=get_text, view_report=None)

@app.route('/view_report/<report_id>')
@login_required
def view_report(report_id):
    lang = session.get('lang', 'en')
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    if session.get('role') == 'admin':
        c.execute("SELECT * FROM consultations WHERE id=?", (report_id,))
    else:
        c.execute("SELECT * FROM consultations WHERE id=? AND user_id=?", (report_id, session['user_id']))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Report not found", 404
    columns = ['id','patient_name','age','gender','phone','occupation','marital_status','is_pregnant',
               'trimester','pregnancy_complications','children_count','chief_complaint',
               'cause_of_injury','past_history','surgical_history','chat_history',
               'final_report','structured_recommendations','diagnosis','date']
    report = dict(zip(columns, row))
    report['patient_info'] = {'age': report['age'], 'gender': report['gender'], 'phone': report['phone'], 'occupation': report.get('occupation', '')}
    if report.get('structured_recommendations'):
        try:
            report['structured_recommendations'] = json.loads(report['structured_recommendations'])
        except:
            report['structured_recommendations'] = {}
    else:
        report['structured_recommendations'] = {}
    return render_template('history.html', view_report=report, lang=lang, get_text=get_text, consultations=None)

@app.route('/share_whatsapp/<report_id>')
@login_required
def share_whatsapp(report_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    if session.get('role') == 'admin':
        c.execute("SELECT phone, diagnosis FROM consultations WHERE id=?", (report_id,))
    else:
        c.execute("SELECT phone, diagnosis FROM consultations WHERE id=? AND user_id=?", (report_id, session['user_id']))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        return "Phone number not found", 404
    phone = row[0]
    text = f"Physiotherapy Report: {row[1] if row[1] else 'Diagnosis available'} - View on PhysioAI"
    wa_link = f"https://wa.me/{phone}?text={text.replace(' ', '%20')}"
    return redirect(wa_link)

@app.route('/delete_consultation/<consultation_id>', methods=['POST'])
@login_required
def delete_consultation(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    if session.get('role') == 'admin':
        c.execute("DELETE FROM consultations WHERE id = ?", (consultation_id,))
    else:
        c.execute("DELETE FROM consultations WHERE id = ? AND user_id = ?", (consultation_id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect(url_for('history'))

# ---------------------------- Multi-language helper ----------------------------
def get_text(key, lang):
    texts = {
        'title': {'en': 'PhysioAI', 'hi': 'फिजियोएआई'},
        'subtitle': {'en': 'Smart Physiotherapy Consultation', 'hi': 'स्मार्ट फिजियोथेरेपी परामर्श'},
        'patient_form': {'en': 'Patient Information', 'hi': 'रोगी की जानकारी'},
        'personal_details': {'en': 'Personal Details', 'hi': 'व्यक्तिगत विवरण'},
        'full_name': {'en': 'Full Name', 'hi': 'पूरा नाम'},
        'age': {'en': 'Age', 'hi': 'आयु'},
        'gender': {'en': 'Gender', 'hi': 'लिंग'},
        'phone': {'en': 'Phone', 'hi': 'फ़ोन'},
        'occupation': {'en': 'Occupation', 'hi': 'व्यवसाय'},
        'clinical_details': {'en': 'Clinical Details', 'hi': 'नैदानिक विवरण'},
        'chief_complaint': {'en': 'Chief Complaint', 'hi': 'मुख्य समस्या'},
        'cause_injury': {'en': 'Cause of Injury', 'hi': 'चोट का कारण'},
        'past_history': {'en': 'Past History', 'hi': 'पिछला इतिहास'},
        'surgical_history': {'en': 'Surgical History', 'hi': 'सर्जिकल इतिहास'},
        'start_consult': {'en': 'Start Consultation', 'hi': 'परामर्श शुरू करें'},
        'view_history': {'en': 'View History', 'hi': 'इतिहास देखें'},
        'consult_history': {'en': 'Consultation History', 'hi': 'परामर्श इतिहास'},
        'back_home': {'en': 'Back to Home', 'hi': 'होम पर जाएं'},
        'new_patient': {'en': 'New Patient', 'hi': 'नया रोगी'},
        'complete': {'en': 'Complete', 'hi': 'पूर्ण'},
        'view_full_report': {'en': 'View Full Report', 'hi': 'पूरी रिपोर्ट देखें'},
        'share_whatsapp': {'en': 'Share on WhatsApp', 'hi': 'व्हाट्सएप पर शेयर करें'},
        'download_pdf': {'en': 'Download PDF', 'hi': 'पीडीएफ डाउनलोड करें'},
        'no_consultations': {'en': 'No consultations found', 'hi': 'कोई परामर्श नहीं मिला'},
        'start_first': {'en': 'Start your first consultation', 'hi': 'अपना पहला परामर्श शुरू करें'},
        'placeholder': {'en': 'Type your answer...', 'hi': 'अपना उत्तर लिखें...'},
        'send': {'en': 'Send', 'hi': 'भेजें'},
        'info_questions': {'en': 'Answer Dr. Shubham Singh\'s questions to get a complete report', 'hi': 'पूरी रिपोर्ट प्राप्त करने के लिए डॉ. शुभम सिंह के प्रश्नों का उत्तर दें'},
        'loading': {'en': 'Loading conversation...', 'hi': 'बातचीत लोड हो रही है...'},
        'final_assessment': {'en': 'Final Assessment Report', 'hi': 'अंतिम मूल्यांकन रिपोर्ट'},
        'male': {'en': 'Male', 'hi': 'पुरुष'},
        'female': {'en': 'Female', 'hi': 'महिला'},
        'other': {'en': 'Other', 'hi': 'अन्य'},
        'married': {'en': 'Married', 'hi': 'विवाहित'},
        'unmarried': {'en': 'Unmarried', 'hi': 'अविवाहित'},
        'pregnant_yes': {'en': 'Yes', 'hi': 'हाँ'},
        'pregnant_no': {'en': 'No', 'hi': 'नहीं'},
        'trimester_1': {'en': '1st Trimester', 'hi': 'पहली तिमाही'},
        'trimester_2': {'en': '2nd Trimester', 'hi': 'दूसरी तिमाही'},
        'trimester_3': {'en': '3rd Trimester', 'hi': 'तीसरी तिमाही'},
        'pregnant': {'en': 'Pregnant', 'hi': 'गर्भवती'},
        'trimester': {'en': 'Trimester', 'hi': 'तिमाही'},
        'pregnancy_complications': {'en': 'Pregnancy Complications', 'hi': 'गर्भावस्था जटिलताएं'},
        'children_count': {'en': 'Number of Children', 'hi': 'बच्चों की संख्या'},
        'marital_status': {'en': 'Marital Status', 'hi': 'वैवाहिक स्थिति'},
        'secure': {'en': 'Your data is secure and confidential', 'hi': 'आपका डेटा सुरक्षित और गोपनीय है'},
        'edit_prescription': {'en': 'Edit Prescription & Recommendations', 'hi': 'प्रिस्क्रिप्शन और सिफारिशें संपादित करें'},
        'save_changes': {'en': 'Save Changes', 'hi': 'बदलाव सहेजें'},
        'download_pdf_final': {'en': 'Download Final PDF', 'hi': 'अंतिम पीडीएफ डाउनलोड करें'},
        'patient_history': {'en': 'Patient History', 'hi': 'रोगी का इतिहास'},
        'previous_reports': {'en': 'Previous Reports', 'hi': 'पिछली रिपोर्टें'},
        'electrotherapy': {'en': 'Electrotherapy', 'hi': 'इलेक्ट्रोथेरेपी'},
        'home_exercises': {'en': 'Home Exercises', 'hi': 'घरेलू व्यायाम'},
        'manual_therapy': {'en': 'Manual Therapy', 'hi': 'मैनुअल थेरेपी'},
        'clinical_exercises': {'en': 'Clinical Exercises', 'hi': 'क्लिनिकल व्यायाम'},
        'dos_donts': {'en': 'Do\'s & Don\'ts', 'hi': 'करें और न करें'},
        'diagnosis_label': {'en': 'Diagnosis', 'hi': 'निदान'},
        'add_item': {'en': '+ Add item', 'hi': '+ आइटम जोड़ें'}
    }
    return texts.get(key, {}).get(lang, texts.get(key, {}).get('en', key))

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)