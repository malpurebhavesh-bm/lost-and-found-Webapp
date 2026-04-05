from flask import Flask, render_template, request, redirect, url_for, flash, session
import os
from datetime import datetime, timedelta
import secrets
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText
from sentence_transformers import SentenceTransformer, util
import torch
from google import genai
from google.genai import types
import json
import time

from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
import cloudinary
import cloudinary.uploader
import cloudinary.api

load_dotenv(override=True)

# We will lazy-load the heavy local PyTorch model to prevent it from blocking server startup
similarity_model = None

def get_similarity_model():
    global similarity_model
    if similarity_model is None:
        print("⏳ Loading local AI Matching Model (this takes a few seconds)...")
        similarity_model = SentenceTransformer('all-MiniLM-L6-v2')
        print("✅ AI Matching Model loaded!")
    return similarity_model

gemini_client = genai.Client()
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default-secret-key')

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', f"sqlite:///{os.path.join(BASE_DIR, 'database.db')}")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

cloudinary.config(
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
  api_key = os.environ.get('CLOUDINARY_API_KEY'),
  api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Item(db.Model):
    __tablename__ = 'items'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type = db.Column(db.String(50))
    name = db.Column(db.String(100))
    description = db.Column(db.Text)
    date = db.Column(db.String(50))
    submitted_at = db.Column(db.String(50))
    email = db.Column(db.String(120))
    image1 = db.Column(db.String(255))
    image2 = db.Column(db.String(255))
    location = db.Column(db.String(200))
    latitude = db.Column(db.String(50))
    longitude = db.Column(db.String(50))

class PasswordReset(db.Model):
    __tablename__ = 'password_resets'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(100), nullable=False)
    expires_at = db.Column(db.String(50), nullable=False)

with app.app_context():
    db.create_all()
    print("✅ Database initialized")

def find_similar_reports(new_name, new_desc, opposite_type):
    items_objs = Item.query.filter_by(type=opposite_type).all()
    
    # Load the model only when a report is being matched
    model = get_similarity_model()

    # Combine name and description to give the AI more precise context
    combined_new = f"Item: {new_name}. Description: {new_desc}"
    new_emb = model.encode(combined_new, convert_to_tensor=True)
    matched = []

    for item in items_objs:
        combined_item = f"Item: {item.name}. Description: {item.description}"
        item_emb = model.encode(combined_item, convert_to_tensor=True)
        score = util.pytorch_cos_sim(new_emb, item_emb).item()
        print(f"📊 Similarity score with '{item.name}': {score:.4f}")
        if score > 0.85:
            matched.append({'id': item.id, 'name': item.name, 'description': item.description, 'email': item.email, 'score': score})
    return matched

def send_email(to_email, subject, body):
    try:
        sender_email = os.environ.get("MAIL_USERNAME")
        sender_password = os.environ.get("MAIL_PASSWORD")

        if not sender_email or not sender_password:
            print("❌ Email credentials not set in environment variables.")
            return

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_email

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)

        print(f"✅ Email sent to {to_email}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

def extract_item_details_from_text(user_text):
    prompt = f"""
    You are an assistant for a campus Lost and Found portal. Extract the following details from the user's report:
    - Item Name (e.g., 'MacBook Pro', 'Keys', 'Wallet'). Correct any spelling or grammar errors.
    - Description (color, brand, any identifying features). Correct any spelling or grammar errors to make it clear and professional.
    - Location (Text based location of where it was lost or found). Correct any spelling or grammar errors.
    - Latitude (Floating point number, approximate coordinate based on the location. Default to VIT Pune area around 18.4578 if unsure)
    - Longitude (Floating point number, approximate coordinate based on the location. Default to VIT Pune area around 73.8509 if unsure)
    - Date (Extract the date if mentioned like 'March 13' or 'yesterday'. You MUST format it strictly as YYYY-MM-DD using the current year. If not found, leave blank.)
    
    Current Year Context: {datetime.now().year}
    User Report: "{user_text}"
    
    Return the result ONLY as a valid JSON object with keys: "name", "description", "location", "latitude", "longitude", "date".
    Make sure date is empty string if not found. Do NOT include markdown code blocks.
    """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            clean_text = response.text.replace('```json', '').replace('```', '').strip()
            return json.loads(clean_text)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"Gemini API rate limited. Retrying in 2 seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(2)
            else:
                print("Error with Gemini:", e)
                return {"error": str(e)}
    
    return {"error": "Max retries exceeded"}

def is_spam_report(description):
    """Uses Gemini to determine if a report is likely fake, a joke, or spam."""
    prompt = f"""
    You are a content moderator for a university campus Lost and Found portal.
    Analyze the following item description and determine if it is a genuine lost/found report or if it is spam, a joke, or inappropriate.
    
    Examples of genuine: "Airpods", "keys", "wallet", "Black leather wallet with my student ID", "Blue hydroflask", "MacBook pro silver"
    Examples of spam/fake: "I lost my mind", "Found a unicorn", "jfkdsla;fjdk", "Selling cheap shoes click here"
    
    IMPORTANT: Be extremely lenient. Short descriptions like "watch", "phone", "bag", "glasses" are GENUINE and NOT SPAM. Only flag explicit jokes, random gibberish, or obvious advertisements as spam.
    
    Description: "{description}"
    
    Respond strictly with a JSON object containing a single key "is_spam" mapped to a boolean (true or false). Do NOT include markdown formatting.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            clean_text = response.text.replace('```json', '').replace('```', '').strip()
            result = json.loads(clean_text)
            return result.get('is_spam', False)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"Spam check rate limited. Retrying in 2 seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(2)
            else:
                print("Spam check failed, allowing report:", e)
                return False
                
    return False

@app.route('/ai-parse-report', methods=['POST'])
@limiter.limit("5 per minute")
def ai_parse_report():
    data = request.get_json()
    user_text = data.get('text', '')
    
    if not user_text:
         return {"success": False, "error": "No text provided"}
         
    # 🛑 First, check if the input is spam
    if is_spam_report(user_text):
        return {"success": False, "error": "Flagged as spam or inappropriate. Please provide a genuine description."}
         
    try:
        extracted_data = extract_item_details_from_text(user_text)
        if "error" in extracted_data:
             return {"success": False, "error": extracted_data["error"]}
        return {"success": True, "data": extracted_data}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        user = User(name=name, email=email, password=password)
        db.session.add(user)
        try:
            db.session.commit()
            flash("Registration successful. Please login.", "success")
            return redirect(url_for('login'))
        except Exception:
            db.session.rollback()
            flash("Email already registered.", "danger")

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_email'] = user.email
            flash("Login successful.", "success")
            return redirect(url_for('profile'))
        else:
            flash("Invalid email or password.", "danger")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('home'))

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        flash("Please login first.", "warning")
        return redirect(url_for('login'))

    user_email = session['user_email']
    items_objs = Item.query.filter_by(email=user_email).order_by(Item.submitted_at.desc()).all()
    reports = [(i.id, i.type, i.name, i.description, i.date, i.submitted_at, i.email, i.image1, i.image2, i.location, i.latitude, i.longitude) for i in items_objs]

    return render_template('profile.html', name=session['user_name'], email=session['user_email'], reports=reports)

@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_id' not in session:
        flash("Please login first.", "warning")
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        user = User.query.get(user_id)
        if user:
            if password:
                user.password = generate_password_hash(password)
            user.name = name
            user.email = email
            db.session.commit()

            session['user_name'] = name
            session['user_email'] = email
            flash("Profile updated!", "success")
            return redirect(url_for('profile'))

    return render_template('edit_profile.html', name=session['user_name'], email=session['user_email'])

@app.route('/lost')
def lost():
    return render_template('lost.html')

@app.route('/found')
def found():
    return render_template('found.html')

@app.route('/items')
def items():
    items_objs = Item.query.order_by(Item.submitted_at.desc()).all()
    items = [(i.id, i.type, i.name, i.description, i.date, i.submitted_at, i.email, i.image1, i.image2, i.location, i.latitude, i.longitude) for i in items_objs]
    return render_template('items.html', items=items)

def _save_image(image_file):
    """Helper to save image to Cloudinary or fallback to local"""
    if not image_file or not image_file.filename:
        return ''
    try:
        if os.environ.get('CLOUDINARY_CLOUD_NAME'):
            upload_result = cloudinary.uploader.upload(image_file)
            return upload_result.get('secure_url', '')
    except Exception as e:
        print("Cloudinary upload failed:", e)
    
    # Fallback to local
    filename = secure_filename(image_file.filename)
    image_file.seek(0)
    image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return filename

@app.route('/submit-lost', methods=['POST'])
def submit_lost():
    name = request.form.get('item-name')
    description = request.form.get('item-description')
    date = request.form.get('lost-date')
    email = request.form.get('email')
    location = request.form.get('location')
    submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

    if is_spam_report(description):
        flash("Your report was flagged as spam or inappropriate. Please provide a genuine description.", "danger")
        return redirect(url_for('lost'))

    image1 = request.files.get('image1')
    image2 = request.files.get('image2')
    filename1 = _save_image(image1)
    filename2 = _save_image(image2)

    existing = Item.query.filter_by(type='Lost', name=name, description=description, email=email, date=date, location=location).first()
    
    if existing:
        flash("This report has already been submitted.", "warning")
        return redirect(url_for('items'))

    new_item = Item(
        type='Lost', name=name, description=description, date=date, submitted_at=submitted_at, email=email,
        image1=filename1, image2=filename2, location=location, latitude=latitude, longitude=longitude
    )
    db.session.add(new_item)
    db.session.commit()

    print(f"🔎 Submitted description: {description}")
    matches = find_similar_reports(name, description, 'Found')
    print(f"📬 Found {len(matches)} match(es)")
    for match in matches:
        if match['email'] != email:
            if match['score'] > 0.85:
                subject = "🚨 GREAT MATCH! We likely found your item!"
                prefix = "An EXACT or highly probable match"
            else:
                subject = "We may have found your item!"
                prefix = "A possible match"
                
            send_email(
                match['email'],
                subject,
                f"{prefix} was reported for your FOUND item:\n\n"
                f"Lost Item: {name}\nDescription: {description}\n\n"
                f"Match: {match['name']} - {match['description']} (Confidence: {match['score']:.2f})\n"
                f"🧾 Contact: {email}"
            )

    send_email(email, "Lost Item Reported", f"You reported a LOST item:\n\nItem: {name}\nDescription: {description}\nLocation: {location}")

    users = User.query.filter(User.email != email).all()
    for user_record in users:
        recipient = user_record.email
        if recipient:
            image_links = ""
            if filename1:
                # Basic check to format URL correctly
                prefix = "" if "http" in filename1 else "http://127.0.0.1:5000/static/uploads/"
                image_links += f"Image 1: {prefix}{filename1}\n"
            if filename2:
                prefix = "" if "http" in filename2 else "http://127.0.0.1:5000/static/uploads/"
                image_links += f"Image 2: {prefix}{filename2}\n"

            send_email(
                recipient,
                "New Lost Item Reported",
                f"A LOST item has been reported:\n\nItem: {name}\nDescription: {description}\n"
                f"Date: {date}\nLocation: {location}\n\n{image_links}🧾 Reported by: {email}"
            )

    return redirect(url_for('items'))

@app.route('/submit-found', methods=['POST'])
def submit_found():
    name = request.form.get('item-name')
    description = request.form.get('item-description')
    date = request.form.get('found-date')
    email = request.form.get('email')
    location = request.form.get('location')
    submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

    if is_spam_report(description):
        flash("Your report was flagged as spam or inappropriate. Please provide a genuine description.", "danger")
        return redirect(url_for('found'))

    image1 = request.files.get('image1')
    image2 = request.files.get('image2')
    filename1 = _save_image(image1)
    filename2 = _save_image(image2)

    existing = Item.query.filter_by(type='Found', name=name, description=description, email=email, date=date, location=location).first()
    
    if existing:
        flash("This report has already been submitted.", "warning")
        return redirect(url_for('items'))

    new_item = Item(
        type='Found', name=name, description=description, date=date, submitted_at=submitted_at, email=email,
        image1=filename1, image2=filename2, location=location, latitude=latitude, longitude=longitude
    )
    db.session.add(new_item)
    db.session.commit()

    print(f"🔎 Submitted description: {description}")
    matches = find_similar_reports(name, description, 'Lost')
    print(f"📬 Found {len(matches)} match(es)")

    for match in matches:
        if match['email'] != email:
            if match['score'] > 0.85:
                subject = "🚨 GREAT MATCH! Your exact item may have been found!"
                prefix = "An EXACT or highly probable match"
            else:
                subject = "Your lost item may have been found!"
                prefix = "A possible match"
                
            send_email(
                match['email'],
                subject,
                f"{prefix} was reported for your LOST item:\n\n"
                f"Found Item: {name}\nDescription: {description}\n\n"
                f"Match: {match['name']} - {match['description']} (Confidence: {match['score']:.2f})\n"
                f"🧾 Contact: {email}"
            )

    send_email(email, "Found Item Reported", f"You reported a FOUND item:\n\nItem: {name}\nDescription: {description}\nLocation: {location}")

    users = User.query.filter(User.email != email).all()
    for user_record in users:
        recipient = user_record.email
        if recipient:
            image_links = ""
            if filename1:
                prefix = "" if "http" in filename1 else "http://127.0.0.1:5000/static/uploads/"
                image_links += f"Image 1: {prefix}{filename1}\n"
            if filename2:
                prefix = "" if "http" in filename2 else "http://127.0.0.1:5000/static/uploads/"
                image_links += f"Image 2: {prefix}{filename2}\n"

            send_email(
                recipient,
                "New Found Item Reported",
                f"A FOUND item has been reported:\n\nItem: {name}\nDescription: {description}\n"
                f"Date: {date}\nLocation: {location}\n\n{image_links}🧾 Reported by: {email}"
            )

    return redirect(url_for('items'))

@app.route('/delete/<int:item_id>', methods=['POST'])
def delete_item(item_id):
    item = Item.query.get(item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for('items'))

@app.route('/edit-report/<int:item_id>', methods=['GET', 'POST'])
def edit_item(item_id):
    if 'user_id' not in session:
        flash("Login required to edit items.", "warning")
        return redirect(url_for('login'))

    item = Item.query.get(item_id)
    if not item:
        flash("Item not found.", "danger")
        return redirect(url_for('profile'))

    if request.method == 'POST':
        item.name = request.form['item-name']
        item.description = request.form['item-description']
        item.date = request.form['date']
        item.location = request.form['location']

        image1 = request.files.get('image1')
        image2 = request.files.get('image2')
        if image1 and image1.filename:
            item.image1 = _save_image(image1)
        if image2 and image2.filename:
            item.image2 = _save_image(image2)

        db.session.commit()
        flash("Report updated successfully.", "success")
        return redirect(url_for('profile'))

    item_dict = {
        'id': item.id, 'type': item.type, 'name': item.name, 'description': item.description,
        'date': item.date, 'submitted_at': item.submitted_at, 'email': item.email,
        'image1': item.image1, 'image2': item.image2, 'location': item.location
    }

    return render_template('edit_report.html', item=item_dict)

@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        
        if user:
            token = secrets.token_hex(16)
            expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            pr = PasswordReset(email=email, token=token, expires_at=expires_at)
            db.session.add(pr)
            db.session.commit()
            
            reset_link = url_for('reset_password', token=token, _external=True)
            send_email(email, "Password Reset Request", f"Click the link to reset your password: {reset_link}")
            flash("A password reset link has been sent to your email.", "info")
            return redirect(url_for('login'))
        else:
            flash("If that email is registered, you will receive a reset link.", "info")
            return redirect(url_for('login'))
            
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    reset_request = PasswordReset.query.filter_by(token=token).first()
    
    if not reset_request:
        flash("Invalid or expired token.", "danger")
        return redirect(url_for('login'))
        
    email, expires_at = reset_request.email, reset_request.expires_at
    if datetime.now().strftime("%Y-%m-%d %H:%M:%S") > expires_at:
        flash("Token has expired.", "danger")
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        password = generate_password_hash(request.form['password'])
        user = User.query.filter_by(email=email).first()
        if user:
            user.password = password
        db.session.delete(reset_request)
        db.session.commit()
        flash("Your password has been reset successfully.", "success")
        return redirect(url_for('login'))
        
    return render_template('reset_password.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
