from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime
import uuid
import json
import httpx
from groq import Groq
import urllib.parse
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'  # Change this!

# Monkey-patch for Python 3.14
_original_init = httpx.Client.__init__
def _patched_init(self, *args, **kwargs):
    kwargs.pop('proxies', None)
    _original_init(self, *args, **kwargs)
httpx.Client.__init__ = _patched_init

from groq import Groq
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
# ---------- Translations (English + Hindi) ----------
TRANSLATIONS = {
    'en': {
        'title': 'Physio AI Assistant (for Dr. Shubham Singh)',
        'subtitle': 'Helping Dr. Shubham Singh understand your condition',
        'patient_form': 'Patient Information Form',
        'personal_details': 'Personal Details',
        'full_name': 'Full Name',
        'age': 'Age',
        'gender': 'Gender',
        'male': 'Male',
        'female': 'Female',
        'other': 'Other',
        'marital_status': 'Marital Status (if Female)',
        'married': 'Married',
        'unmarried': 'Unmarried',
        'pregnant': 'Are you currently pregnant?',
        'pregnant_yes': 'Yes',
        'pregnant_no': 'No',
        'trimester': 'Trimester',
        'trimester_1': '1st (1-13 weeks)',
        'trimester_2': '2nd (14-27 weeks)',
        'trimester_3': '3rd (28-40 weeks)',
        'pregnancy_complications': 'Any pregnancy complications?',
        'children_count': 'Number of children (if any)',
        'phone': 'Phone Number',
        'clinical_details': 'Clinical Details',
        'chief_complaint': 'Chief Complaint',
        'cause_injury': 'Cause of Injury',
        'past_history': 'Past Medical History',
        'surgical_history': 'Surgical History',
        'start_consult': 'Start AI Consultation',
        'view_history': 'View History',
        'secure': 'Secure & Confidential',
        'loading': 'Loading conversation...',
        'send': 'Send',
        'placeholder': 'Type your answer here...',
        'info_questions': 'The AI assistant will ask questions to help Dr. Shubham Singh',
        'new_patient': 'New Patient',
        'history': 'History',
        'download_pdf': 'Download PDF',
        'share_whatsapp': 'Share on WhatsApp',
        'back_home': 'Back to Home',
        'consult_history': 'Consultation History',
        'no_consultations': 'No Consultations Yet',
        'start_first': 'Start First Consultation',
        'complete': 'Completed',
        'view_full_report': 'View Full Report',
        'print_report': 'Print Report',
        'final_assessment': 'FINAL ASSESSMENT & TREATMENT PLAN (Prepared for Dr. Shubham Singh)'
    },
    'hi': {
        'title': 'फिजियो एआई असिस्टेंट (डॉ. शुभम सिंह के लिए)',
        'subtitle': 'डॉ. शुभम सिंह को आपकी स्थिति समझाने में सहायक',
        'patient_form': 'रोगी सूचना प्रपत्र',
        'personal_details': 'व्यक्तिगत विवरण',
        'full_name': 'पूरा नाम',
        'age': 'आयु',
        'gender': 'लिंग',
        'male': 'पुरुष',
        'female': 'महिला',
        'other': 'अन्य',
        'marital_status': 'वैवाहिक स्थिति (यदि महिला)',
        'married': 'विवाहित',
        'unmarried': 'अविवाहित',
        'pregnant': 'क्या आप वर्तमान में गर्भवती हैं?',
        'pregnant_yes': 'हाँ',
        'pregnant_no': 'नहीं',
        'trimester': 'त्रैमासिक',
        'trimester_1': 'पहली (1-13 सप्ताह)',
        'trimester_2': 'दूसरी (14-27 सप्ताह)',
        'trimester_3': 'तीसरी (28-40 सप्ताह)',
        'pregnancy_complications': 'कोई गर्भावस्था जटिलताएँ?',
        'children_count': 'बच्चों की संख्या (यदि कोई हो)',
        'phone': 'फोन नंबर',
        'clinical_details': 'नैदानिक विवरण',
        'chief_complaint': 'मुख्य समस्या',
        'cause_injury': 'चोट का कारण',
        'past_history': 'पिछला चिकित्सा इतिहास',
        'surgical_history': 'सर्जिकल इतिहास',
        'start_consult': 'एआई परामर्श शुरू करें',
        'view_history': 'इतिहास देखें',
        'secure': 'सुरक्षित और गोपनीय',
        'loading': 'बातचीत लोड हो रही है...',
        'send': 'भेजें',
        'placeholder': 'अपना उत्तर यहाँ लिखें...',
        'info_questions': 'एआई सहायक डॉ. शुभम सिंह की सहायता के लिए प्रश्न पूछेगा',
        'new_patient': 'नया रोगी',
        'history': 'इतिहास',
        'download_pdf': 'पीडीएफ डाउनलोड करें',
        'share_whatsapp': 'व्हाट्सएप पर साझा करें',
        'back_home': 'होम पर वापस जाएं',
        'consult_history': 'परामर्श इतिहास',
        'no_consultations': 'अभी कोई परामर्श नहीं',
        'start_first': 'पहला परामर्श शुरू करें',
        'complete': 'पूर्ण',
        'view_full_report': 'पूरी रिपोर्ट देखें',
        'print_report': 'रिपोर्ट प्रिंट करें',
        'final_assessment': 'अंतिम मूल्यांकन और उपचार योजना (डॉ. शुभम सिंह के लिए तैयार)'
    }
}

def get_text(key, lang='en'):
    return TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)

def generate_final_report(patient_info, conversation_history, lang='en'):
    """Generate a proper final report based on the actual conversation."""
    # Build Q&A pairs from conversation
    qa_pairs = []
    for i in range(0, len(conversation_history), 2):
        if i+1 < len(conversation_history):
            qa_pairs.append((conversation_history[i]['content'], conversation_history[i+1]['content']))
    
    qa_text = "\n".join([f"Q: {q}\nA: {a}" for q, a in qa_pairs])
    
    pregnancy = ""
    if patient_info.get('is_pregnant') == 'Yes':
        pregnancy = f", Pregnant ({patient_info.get('trimester')} trimester), Complications: {patient_info.get('pregnancy_complications', 'None')}"
    
    prompt = f"""You are an AI assistant for Dr. Shubham Singh, a physiotherapist. Based on the patient's details and the conversation below, write a detailed physiotherapy prescription. Use professional language. Include sections: Expected Problems, Electrotherapy, Manual Therapy, Clinical Exercises, Precautions, Treatment Plan, Conditions & Chances.

PATIENT:
- Name: {patient_info['full_name']}, Age: {patient_info['age']}, Gender: {patient_info['gender']}{pregnancy}
- Phone: {patient_info['phone']}
- Chief Complaint: {patient_info['chief_complaint']}
- Cause of Injury: {patient_info['cause_of_injury']}
- Past History: {patient_info['past_history']}
- Surgical History: {patient_info['surgical_history']}

CONVERSATION (Q&A between patient and AI assistant):
{qa_text}

Now produce the final prescription. Be specific to the patient's problem (e.g., headache, knee pain, back pain, etc.). Do NOT use generic knee pain if the complaint is different. Use bullet points and clear headings."""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"**Error generating report:** {str(e)}"

def get_next_action(patient_info, conversation_history, lang='en', q_count=0):
    """Decide whether to ask another question or give final report."""
    # Limit to 7 questions max
    if q_count >= 7:
        return {'type': 'report', 'content': generate_final_report(patient_info, conversation_history, lang)}
    
    # Build conversation history string
    history = ""
    for msg in conversation_history:
        role = "Patient" if msg['role'] == 'user' else "Assistant"
        history += f"{role}: {msg['content']}\n"
    
    pregnancy = ""
    if patient_info.get('is_pregnant') == 'Yes':
        pregnancy = f", Pregnant ({patient_info.get('trimester')} trimester)"
    
    prompt = f"""You are an AI assistant for Dr. Shubham Singh, a physiotherapist. Your job is to gather information from the patient. Based on the conversation so far, decide whether you have enough information to write a final prescription.

Patient details:
- Age: {patient_info['age']}, Gender: {patient_info['gender']}{pregnancy}
- Chief complaint: {patient_info['chief_complaint']}
- Cause: {patient_info['cause_of_injury']}
- Past history: {patient_info['past_history']}
- Surgical history: {patient_info['surgical_history']}

Conversation:
{history}

You have asked {q_count} questions so far.

If you have enough information (pain characteristics, aggravating/easing factors, functional limitations, red flags screened), respond with: {{"type": "report"}}

If you need one more specific piece of information, ask a short, open-ended question that builds on the patient's last answer. Respond with: {{"type": "question", "content": "Your question here"}}

Return ONLY valid JSON, no extra text."""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500
        )
        result = json.loads(response.choices[0].message.content.strip())
        if result.get('type') == 'report':
            return {'type': 'report', 'content': generate_final_report(patient_info, conversation_history, lang)}
        else:
            return result
    except Exception as e:
        print(f"AI error: {e}")
        # Fallback: ask a generic question
        return {'type': 'question', 'content': 'Can you describe your symptoms in more detail?'}

# ---------- Routes ----------
@app.route('/')
def index():
    lang = session.get('lang', 'en')
    return render_template('index.html', lang=lang, get_text=get_text)

@app.route('/set_language', methods=['POST'])
def set_language():
    session['lang'] = request.form.get('lang', 'en')
    return redirect(url_for('index'))

@app.route('/submit_patient', methods=['POST'])
def submit_patient():
    lang = session.get('lang', 'en')
    gender = request.form.get('gender')
    patient_info = {
        'full_name': request.form.get('full_name'),
        'age': request.form.get('age'),
        'gender': gender,
        'phone': request.form.get('phone'),
        'chief_complaint': request.form.get('chief_complaint'),
        'cause_of_injury': request.form.get('cause_of_injury'),
        'past_history': request.form.get('past_history'),
        'surgical_history': request.form.get('surgical_history'),
        'marital_status': None,
        'is_pregnant': 'No',
        'trimester': None,
        'pregnancy_complications': None,
        'children_count': '0'
    }
    if gender == 'Female':
        marital = request.form.get('marital_status')
        patient_info['marital_status'] = marital
        if marital == 'Married':
            patient_info['is_pregnant'] = request.form.get('is_pregnant', 'No')
            if patient_info['is_pregnant'] == 'Yes':
                patient_info['trimester'] = request.form.get('trimester')
                patient_info['pregnancy_complications'] = request.form.get('pregnancy_complications', '')
            patient_info['children_count'] = request.form.get('children_count', '0')
    
    # First question – always ask about the chief complaint
    first_q = f"Hello {patient_info['full_name']}, I'm the AI assistant for Dr. Shubham Singh. To help him understand your condition better, please tell me more about your {patient_info['chief_complaint']}. When did it start and how does it affect your daily activities?"
    consultation_id = str(uuid.uuid4())
    session['current_consultation'] = {
        'id': consultation_id,
        'patient_info': patient_info,
        'messages': [{'role': 'assistant', 'content': first_q}],
        'is_complete': False,
        'final_report': None,
        'timestamp': datetime.now().isoformat(),
        'lang': lang,
        'question_count': 0
    }
    return redirect(url_for('chat'))

@app.route('/chat')
def chat():
    if 'current_consultation' not in session:
        return redirect(url_for('index'))
    lang = session.get('lang', 'en')
    return render_template('chat.html', patient_info=session['current_consultation']['patient_info'], lang=lang, get_text=get_text)

@app.route('/get_chat_state')
def get_chat_state():
    if 'current_consultation' not in session:
        return jsonify({'error': 'No active consultation'}, 404)
    cons = session['current_consultation']
    return jsonify({
        'messages': cons['messages'],
        'is_complete': cons['is_complete'],
        'final_report': cons['final_report'],
        'questions_remaining': max(0, 7 - cons.get('question_count', 0)) if not cons['is_complete'] else 0,
        'lang': cons.get('lang', 'en')
    })

@app.route('/send_answer', methods=['POST'])
def send_answer():
    if 'current_consultation' not in session:
        return jsonify({'error': 'No active consultation'}, 404)
    data = request.get_json()
    user_answer = data.get('answer', '').strip()
    if not user_answer:
        return jsonify({'error': 'Answer cannot be empty'}, 400)
    cons = session['current_consultation']
    lang = cons.get('lang', 'en')
    cons['messages'].append({'role': 'user', 'content': user_answer})
    cons['question_count'] = cons.get('question_count', 0) + 1
    # Get next action from AI
    action = get_next_action(cons['patient_info'], cons['messages'], lang, cons['question_count'])
    if action['type'] == 'question':
        cons['messages'].append({'role': 'assistant', 'content': action['content']})
        session['current_consultation'] = cons
        return jsonify({
            'message': action['content'],
            'is_complete': False,
            'questions_remaining': max(0, 7 - cons['question_count'])
        })
    else:
        final_report = action['content']
        cons['final_report'] = final_report
        cons['is_complete'] = True
        final_title = "**FINAL ASSESSMENT & TREATMENT PLAN (Prepared for Dr. Shubham Singh)**" if lang == 'en' else "**अंतिम मूल्यांकन और उपचार योजना (डॉ. शुभम सिंह के लिए तैयार)**"
        cons['messages'].append({'role': 'assistant', 'content': f"{final_title}\n\n{final_report}\n\n---\n*This report has been saved for Dr. Shubham Singh.*"})
        # Save to history
        if 'consultations_history' not in session:
            session['consultations_history'] = []
        history_entry = {
            'id': cons['id'],
            'patient_name': cons['patient_info']['full_name'],
            'age': cons['patient_info']['age'],
            'gender': cons['patient_info']['gender'],
            'marital_status': cons['patient_info'].get('marital_status'),
            'is_pregnant': cons['patient_info'].get('is_pregnant'),
            'trimester': cons['patient_info'].get('trimester'),
            'pregnancy_complications': cons['patient_info'].get('pregnancy_complications'),
            'children_count': cons['patient_info'].get('children_count'),
            'phone': cons['patient_info']['phone'],
            'chief_complaint': cons['patient_info']['chief_complaint'],
            'date': cons['timestamp'],
            'final_report': final_report,
            'patient_info': cons['patient_info'],
            'lang': lang
        }
        session['consultations_history'].insert(0, history_entry)
        session['current_consultation'] = cons
        return jsonify({
            'message': cons['messages'][-1]['content'],
            'is_complete': True,
            'final_report': final_report
        })

@app.route('/reset')
def reset():
    session.pop('current_consultation', None)
    return redirect(url_for('index'))

@app.route('/history')
def history():
    consultations = session.get('consultations_history', [])
    lang = session.get('lang', 'en')
    return render_template('history.html', consultations=consultations, lang=lang, get_text=get_text)

@app.route('/view_report/<consult_id>')
def view_report(consult_id):
    consultations = session.get('consultations_history', [])
    consultation = next((c for c in consultations if c['id'] == consult_id), None)
    if not consultation:
        return redirect(url_for('history'))
    lang = session.get('lang', 'en')
    return render_template('history.html', view_report=consultation, lang=lang, get_text=get_text)

@app.route('/share_whatsapp/<consult_id>')
def share_whatsapp(consult_id):
    consultations = session.get('consultations_history', [])
    consultation = next((c for c in consultations if c['id'] == consult_id), None)
    if not consultation:
        return jsonify({'error': 'Not found'}), 404
    phone = consultation['patient_info'].get('phone', '').strip()
    phone = ''.join(filter(str.isdigit, phone))
    if len(phone) == 10:
        phone = '+91' + phone
    elif phone and not phone.startswith('+'):
        phone = '+' + phone
    patient_name = consultation['patient_name']
    report_text = consultation['final_report']
    if len(report_text) > 4000:
        report_text = report_text[:4000] + "...\n(Report truncated)"
    message = f"*Physiotherapy Prescription for {patient_name}* (Prepared for Dr. Shubham Singh)\n\n{report_text}\n\n---\nGenerated by Physio AI Assistant for Dr. Shubham Singh"
    encoded = urllib.parse.quote(message)
    whatsapp_url = f"https://wa.me/{phone}?text={encoded}"
    return redirect(whatsapp_url)

if __name__ == '__main__':
    app.run(debug=True, port=5000)