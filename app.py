import os
import sqlite3
import uuid
import json
import time
import hashlib
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from dotenv import load_dotenv
from groq import Groq
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import bleach
import langdetect
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
import io

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
app.config['SESSION_TYPE'] = 'filesystem'

limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])
limiter.init_app(app)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
cache = {}

# ---------------------------- Database Setup ----------------------------
def init_db():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS consultations
                 (id TEXT PRIMARY KEY,
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
                  date TEXT)''')
    conn2 = sqlite3.connect('feedback.db')
    c2 = conn2.cursor()
    c2.execute('''CREATE TABLE IF NOT EXISTS feedback
                  (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   consultation_id TEXT,
                   message_index INTEGER,
                   rating TEXT,
                   comment TEXT,
                   timestamp TEXT)''')
    conn.commit()
    conn2.commit()
    conn.close()
    conn2.close()

init_db()

# ---------------------------- Helper: Save consultation ----------------------------
def save_consultation(consultation_id, patient_info, chat_history, final_report, structured_rec=None, diagnosis=None):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    structured_json = json.dumps(structured_rec) if structured_rec else None
    c.execute('''INSERT OR REPLACE INTO consultations 
                 (id, patient_name, age, gender, phone, occupation, marital_status, is_pregnant,
                  trimester, pregnancy_complications, children_count, chief_complaint,
                  cause_of_injury, past_history, surgical_history, chat_history, final_report,
                  structured_recommendations, diagnosis, date)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (consultation_id,
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

# ---------------------------- AI Call ----------------------------
def get_cache_key(prompt):
    return hashlib.md5(prompt.encode()).hexdigest()

def ask_ai(prompt, system_msg=None, use_cache=True, max_tokens=250):
    cache_key = get_cache_key(prompt)
    if use_cache and cache_key in cache:
        return cache[cache_key]
    
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
                cache[cache_key] = answer
            return answer
        except Exception as e:
            app.logger.error(f"AI attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return "Please describe your main problem in more detail."
            time.sleep(2 ** attempt)
    return "Please tell me more about your symptoms."

# ---------------------------- Diagnosis & Recommendations Generation ----------------------------
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

# ---------------------------- Routes ----------------------------
@app.route('/')
def index():
    lang = session.get('lang', 'en')
    return render_template('index.html', lang=lang, get_text=get_text)

@app.route('/set_language', methods=['POST'])
def set_language():
    session['lang'] = request.form.get('lang', 'en')
    return redirect(url_for('index'))

@app.route('/submit_patient', methods=['POST'])
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
def chat():
    if 'patient_info' not in session:
        return redirect(url_for('index'))
    lang = session.get('lang', 'en')
    return render_template('chat.html', lang=lang, get_text=get_text, patient_info=session['patient_info'])

@app.route('/get_chat_state')
def get_chat_state():
    if 'patient_info' not in session:
        return jsonify({'error': 'No active session'}), 400
    return jsonify({
        'messages': session.get('chat_history', []),
        'is_complete': session.get('is_complete', False),
        'consultation_id': session.get('consultation_id')
    })

@app.route('/send_answer', methods=['POST'])
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
        # Save consultation without final report yet (will be added after diagnosis)
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
    return jsonify({'is_complete': False, 'messages': session['chat_history']})

@app.route('/diagnosis_selection')
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
def report_edit():
    if 'consultation_id' not in session:
        return redirect(url_for('index'))
    consultation_id = session['consultation_id']
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT patient_name, age, gender, phone, occupation, chief_complaint, diagnosis, structured_recommendations, date FROM consultations WHERE id=?", (consultation_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return redirect(url_for('index'))
    patient_info = {'full_name': row[0], 'age': row[1], 'gender': row[2], 'phone': row[3], 'occupation': row[4], 'chief_complaint': row[5]}
    diagnosis = row[6] or "Not specified"
    structured_rec = {}
    if row[7]:
        try:
            structured_rec = json.loads(row[7])
        except:
            structured_rec = {}
    date = row[8]
    # Patient history (previous consultations)
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT id, patient_name, diagnosis, date FROM consultations WHERE patient_name=? AND id!=? ORDER BY date DESC LIMIT 5", (patient_info['full_name'], consultation_id))
    history_rows = c.fetchall()
    conn.close()
    history = [{'id': h[0], 'diagnosis': h[2], 'date': h[3]} for h in history_rows]
    lang = session.get('lang', 'en')
    return render_template('report_edit.html', patient_info=patient_info, diagnosis=diagnosis, recommendations=structured_rec,
                         consultation_id=consultation_id, date=date, history=history, lang=lang, get_text=get_text)

@app.route('/save_structured_recommendations', methods=['POST'])
def save_structured_recommendations():
    data = request.json
    consultation_id = data.get('consultation_id')
    recommendations = data.get('recommendations')
    diagnosis = data.get('diagnosis')
    if not consultation_id or not recommendations:
        return jsonify({'error': 'Missing data'}), 400
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("UPDATE consultations SET structured_recommendations = ?, diagnosis = ? WHERE id = ?", 
              (json.dumps(recommendations), diagnosis, consultation_id))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/download_structured_pdf/<consultation_id>')
def download_structured_pdf(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT patient_name, age, gender, phone, occupation, chief_complaint, diagnosis, structured_recommendations, date FROM consultations WHERE id=?", (consultation_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Not found", 404
    patient = {'name': row[0], 'age': row[1], 'gender': row[2], 'phone': row[3], 'occupation': row[4], 'chief_complaint': row[5]}
    diagnosis = row[6] or "Not specified"
    structured_rec = json.loads(row[7]) if row[7] else {}
    date_str = row[8] if row[8] else datetime.now().strftime("%Y-%m-%d %H:%M")
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=0.6*inch, rightMargin=0.6*inch, topMargin=0.6*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=16, textColor=colors.HexColor('#4F46E5'), alignment=1, spaceAfter=8)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#1F2937'), spaceAfter=6, spaceBefore=10)
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=9, leading=12)
    doctor_style = ParagraphStyle('Doctor', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor('#4F46E5'), alignment=2, spaceAfter=4)
    
    story = []
    story.append(Paragraph("PhysioAI", title_style))
    story.append(Paragraph("Smart Physiotherapy Consultation", styles['Normal']))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Dr. Shubham Singh (PT)</b><br/>Senior Physiotherapist, 20+ years experience", doctor_style))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("<b>Patient Information</b>", heading_style))
    patient_data = [[f"Name: {patient['name']}", f"Age: {patient['age']}", f"Gender: {patient['gender']}"],
                    [f"Phone: {patient['phone']}", f"Occupation: {patient['occupation']}", ""],
                    [f"Chief Complaint: {patient['chief_complaint']}", "", ""]]
    t = Table(patient_data, colWidths=[2.2*inch, 1.8*inch, 1.8*inch])
    t.setStyle(TableStyle([('FONTSIZE', (0,0), (-1,-1), 8), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story.append(t)
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("<b>Diagnosis</b>", heading_style))
    story.append(Paragraph(diagnosis, normal_style))
    story.append(Spacer(1, 10))
    
    sections = [('Electrotherapy', structured_rec.get('electrotherapy', [])),
                ('Home Exercises', structured_rec.get('home_exercises', [])),
                ('Manual Therapy', structured_rec.get('manual_therapy', [])),
                ('Clinical Exercises', structured_rec.get('clinical_exercises', [])),
                ("Do's & Don'ts", structured_rec.get('dos_donts', []))]
    for title, items in sections:
        if items:
            story.append(Paragraph(f"<b>{title}</b>", heading_style))
            for item in items:
                story.append(Paragraph(f"• {item}", normal_style))
            story.append(Spacer(1, 6))
    
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"Date: {datetime.now().strftime('%Y-%m-%d')}", normal_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Dr. Shubham Singh (PT)", doctor_style))
    story.append(Paragraph("<i>(Digital Signature)</i>", normal_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<i>Medical Disclaimer: This report is AI-assisted. Consult a qualified physiotherapist.</i>", styles['Italic']))
    
    def add_watermark(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 45)
        canvas_obj.setFillColorRGB(0.8, 0.8, 0.8, alpha=0.25)
        canvas_obj.rotate(45)
        canvas_obj.drawString(250, 100, "PhysioAI")
        canvas_obj.restoreState()
    
    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"prescription_{consultation_id}.pdf", mimetype='application/pdf')

@app.route('/submit_feedback', methods=['POST'])
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
def reset():
    session.clear()
    return redirect(url_for('index'))

@app.route('/history')
def history():
    lang = session.get('lang', 'en')
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT id, patient_name, age, diagnosis, date FROM consultations ORDER BY date DESC")
    rows = c.fetchall()
    consultations = [{'id': r[0], 'patient_name': r[1], 'age': r[2], 'diagnosis': r[3] or 'N/A', 'date': r[4]} for r in rows]
    conn.close()
    return render_template('history.html', consultations=consultations, lang=lang, get_text=get_text, view_report=None)

@app.route('/view_report/<report_id>')
def view_report(report_id):
    lang = session.get('lang', 'en')
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT * FROM consultations WHERE id=?", (report_id,))
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
def share_whatsapp(report_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT phone, diagnosis FROM consultations WHERE id=?", (report_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        return "Phone number not found", 404
    phone = row[0]
    text = f"Physiotherapy Report: {row[1] if row[1] else 'Diagnosis available'} - View on PhysioAI"
    wa_link = f"https://wa.me/{phone}?text={text.replace(' ', '%20')}"
    return redirect(wa_link)

@app.route('/delete_consultation/<consultation_id>', methods=['POST'])
def delete_consultation(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("DELETE FROM consultations WHERE id = ?", (consultation_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('history'))

@app.route('/admin')
def admin():
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT id, patient_name, date FROM consultations ORDER BY date DESC LIMIT 50")
    consultations = c.fetchall()
    conn.close()
    conn2 = sqlite3.connect('feedback.db')
    c2 = conn2.cursor()
    c2.execute("SELECT rating, COUNT(*) FROM feedback GROUP BY rating")
    feedback_stats = dict(c2.fetchall())
    conn2.close()
    return render_template('admin.html', consultations=consultations, feedback_stats=feedback_stats)

@app.route('/get_patient_phone/<consultation_id>')
def get_patient_phone(consultation_id):
    conn = sqlite3.connect('consultations.db')
    c = conn.cursor()
    c.execute("SELECT phone FROM consultations WHERE id=?", (consultation_id,))
    row = c.fetchone()
    conn.close()
    return jsonify({'phone': row[0] if row else ''})

# Multi-language helper
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