from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
from datetime import datetime, timedelta
import secrets
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from sentence_transformers import SentenceTransformer, util
import torch
model = SentenceTransformer('all-MiniLM-L6-v2')

app = Flask(__name__)
app.secret_key = 'secret-key'

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = os.path.join(BASE_DIR, 'database.db')


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT,
    name TEXT,
    description TEXT,
    date TEXT,
    submitted_at TEXT,
    email TEXT,
    image1 TEXT,
    image2 TEXT,
    location TEXT,
    latitude TEXT,
    longitude TEXT
)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        token TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()
    print("✅ database.db initialized")

init_db()

def find_similar_reports(new_desc, opposite_type):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, name, description, email FROM items WHERE type = ?", (opposite_type,))
    items = c.fetchall()
    conn.close()

    new_emb = model.encode(new_desc, convert_to_tensor=True)
    matched = []

    for item in items:
        item_id, name, desc, email = item
        item_emb = model.encode(desc, convert_to_tensor=True)
        score = util.pytorch_cos_sim(new_emb, item_emb).item()
        print(f"🧠 Similarity with {name}: {score:.2f}")
        if score > 0.65:
            matched.append({'id': item_id, 'name': name, 'description': desc, 'email': email})
    return matched

def send_email(to_email, subject, body):
    try:
        sender_email = "bhavesh.malpure24@vit.edu"
        sender_password = "tepqnxulbemuhajl"

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

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)", (name, email, password))
            conn.commit()
            flash("Registration successful. Please login.", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Email already registered.", "danger")
        finally:
            conn.close()

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
        user = c.fetchone()
        conn.close()

        if user:
            session['user_id'] = user[0]
            session['user_name'] = user[1]
            session['user_email'] = user[2]
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
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM items WHERE email = ? ORDER BY submitted_at DESC", (user_email,))
    reports = c.fetchall()
    conn.close()

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

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        if password:
            c.execute("UPDATE users SET name = ?, email = ?, password = ? WHERE id = ?", (name, email, password, user_id))
        else:
            c.execute("UPDATE users SET name = ?, email = ? WHERE id = ?", (name, email, user_id))
        conn.commit()
        conn.close()

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
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM items ORDER BY submitted_at DESC')
    items = c.fetchall()
    conn.close()
    return render_template('items.html', items=items)

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

    # Handle images
    image1 = request.files.get('image1')
    image2 = request.files.get('image2')
    filename1 = secure_filename(image1.filename) if image1 else ''
    filename2 = secure_filename(image2.filename) if image2 else ''
    if filename1:
        image1.save(os.path.join(app.config['UPLOAD_FOLDER'], filename1))
    if filename2:
        image2.save(os.path.join(app.config['UPLOAD_FOLDER'], filename2))

    # Insert new lost item into database
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
    INSERT INTO items (
        type, name, description, date, submitted_at, email,
        image1, image2, location, latitude, longitude
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
    'Lost', name, description, date, submitted_at, email,
    filename1, filename2, location, latitude, longitude
    ))
    conn.commit()
    conn.close()

    # 🔍 AI Matching Logic
    print(f"🔎 Submitted description: {description}")
    matches = find_similar_reports(description, 'Found')
    print(f"📬 Found {len(matches)} match(es)")
    for match in matches:
        if match['email'] != email:
            send_email(
                match['email'],
                "We may have found your item!",
                f"A LOST item was reported that matches what you FOUND:\n\n"
                f"Lost Item: {name}\nDescription: {description}\n\n"
                f"Possible match: {match['name']} - {match['description']}\n"
                f"🧾 Contact: {email}"
            )

    # 📤 Confirm email to reporter
    send_email(
        email,
        "Lost Item Reported",
        f"You reported a LOST item:\n\nItem: {name}\nDescription: {description}\nLocation: {location}"
    )

    # 🔔 Notify all registered users (excluding the reporter)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT email FROM users WHERE email != ?", (email,))
    registered_users = c.fetchall()
    conn.close()

    for entry in registered_users:
      recipient = entry[0]
      if recipient:
        image_links = ""
        if filename1:
            image_links += f"Image 1: http://127.0.0.1:5000/static/uploads/{filename1}\n"
        if filename2:
            image_links += f"Image 2: http://127.0.0.1:5000/static/uploads/{filename2}\n"

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

    # 📦 Save uploaded images
    image1 = request.files.get('image1')
    image2 = request.files.get('image2')
    filename1 = secure_filename(image1.filename) if image1 else ''
    filename2 = secure_filename(image2.filename) if image2 else ''
    if filename1:
        image1.save(os.path.join(app.config['UPLOAD_FOLDER'], filename1))
    if filename2:
        image2.save(os.path.join(app.config['UPLOAD_FOLDER'], filename2))

    # 💾 Insert into database
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
    INSERT INTO items (
        type, name, description, date, submitted_at, email,
        image1, image2, location, latitude, longitude
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
    'Lost', name, description, date, submitted_at, email,
    filename1, filename2, location, latitude, longitude
    ))
    conn.commit()
    conn.close()

    # 🔍 AI Matching with LOST reports
    print(f"🔎 Submitted description: {description}")
    matches = find_similar_reports(description, 'Lost')
    print(f"📬 Found {len(matches)} match(es)")

    for match in matches:
        if match['email'] != email:
            send_email(
                match['email'],
                "Your lost item may have been found!",
                f"A FOUND item was reported that matches your LOST item:\n\n"
                f"Found Item: {name}\nDescription: {description}\n\n"
                f"Possible match: {match['name']} - {match['description']}\n"
                f"🧾 Contact: {email}"
            )

    # 📤 Confirmation email to reporter
    send_email(
        email,
        "Found Item Reported",
        f"You reported a FOUND item:\n\nItem: {name}\nDescription: {description}\nLocation: {location}"
    )

 # 🔔 Notify all registered users (excluding the reporter)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT email FROM users WHERE email != ?", (email,))
    registered_users = c.fetchall()
    conn.close()

    for entry in registered_users:
     recipient = entry[0]
     if recipient:
        image_links = ""
        if filename1:
            image_links += f"Image 1: http://127.0.0.1:5000/static/uploads/{filename1}\n"
        if filename2:
            image_links += f"Image 2: http://127.0.0.1:5000/static/uploads/{filename2}\n"

        send_email(
            recipient,
            "New Found Item Reported",
            f"A FOUND item has been reported:\n\nItem: {name}\nDescription: {description}\n"
            f"Date: {date}\nLocation: {location}\n\n{image_links}🧾 Reported by: {email}"
        )


    return redirect(url_for('items'))


@app.route('/delete/<int:item_id>', methods=['POST'])
def delete_item(item_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('items'))

@app.route('/edit-report/<int:item_id>', methods=['GET', 'POST'])
def edit_item(item_id):
    if 'user_id' not in session:
        flash("Login required to edit items.", "warning")
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    if request.method == 'POST':
        name = request.form['item-name']
        description = request.form['item-description']
        date = request.form['date']
        location = request.form['location']

        image1 = request.files.get('image1')
        image2 = request.files.get('image2')
        filename1 = secure_filename(image1.filename) if image1 and image1.filename else None
        filename2 = secure_filename(image2.filename) if image2 and image2.filename else None

        if filename1:
            image1.save(os.path.join(app.config['UPLOAD_FOLDER'], filename1))
            c.execute("UPDATE items SET image1 = ? WHERE id = ?", (filename1, item_id))
        if filename2:
            image2.save(os.path.join(app.config['UPLOAD_FOLDER'], filename2))
            c.execute("UPDATE items SET image2 = ? WHERE id = ?", (filename2, item_id))

        c.execute("UPDATE items SET name=?, description=?, date=?, location=? WHERE id=?",
                  (name, description, date, location, item_id))
        conn.commit()
        conn.close()
        flash("Report updated successfully.", "success")
        return redirect(url_for('profile'))

    c.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        flash("Item not found.", "danger")
        return redirect(url_for('profile'))

    item = {
        'id': row[0], 'type': row[1], 'name': row[2], 'description': row[3],
        'date': row[4], 'submitted_at': row[5], 'email': row[6],
        'image1': row[7], 'image2': row[8], 'location': row[9]
    }

    return render_template('edit_report.html', item=item)
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        
        if user:
            token = secrets.token_hex(16)
            expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO password_resets (email, token, expires_at) VALUES (?, ?, ?)", (email, token, expires_at))
            conn.commit()
            
            reset_link = url_for('reset_password', token=token, _external=True)
            send_email(email, "Password Reset Request", f"Click the link to reset your password: {reset_link}")
            flash("A password reset link has been sent to your email.", "info")
            conn.close()
            return redirect(url_for('login'))
        else:
            flash("If that email is registered, you will receive a reset link.", "info")
            conn.close()
            return redirect(url_for('login'))
            
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT email, expires_at FROM password_resets WHERE token = ?", (token,))
    reset_request = c.fetchone()
    
    if not reset_request:
        flash("Invalid or expired token.", "danger")
        conn.close()
        return redirect(url_for('login'))
        
    email, expires_at = reset_request
    if datetime.now().strftime("%Y-%m-%d %H:%M:%S") > expires_at:
        flash("Token has expired.", "danger")
        conn.close()
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        password = request.form['password']
        c.execute("UPDATE users SET password = ? WHERE email = ?", (password, email))
        c.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        conn.commit()
        conn.close()
        flash("Your password has been reset successfully.", "success")
        return redirect(url_for('login'))
        
    conn.close()
    return render_template('reset_password.html')

if __name__ == '__main__':
    app.run(debug=True)
