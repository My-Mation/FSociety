from flask import session, redirect, url_for, request, jsonify
from functools import wraps
import os
import requests
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from app.db import get_db

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')

def verify_api_key(api_key):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE api_key = %s", (api_key,))
        user = cur.fetchone()
        if user:
            return user[0]
        return None
    except Exception as e:
        print(f"[AUTH ERROR] API Key verification failed: {e}")
        return None
    finally:
        cur.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check for API Key (Bearer Token) - For ESP32/Automated Clients
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            api_key = auth_header.split(' ')[1]
            user_id = verify_api_key(api_key)
            if user_id:
                # Mock session availability for the request context
                session['user_id'] = user_id
                return f(*args, **kwargs)
            else:
                 return jsonify({"error": "Invalid API Key"}), 401

        # 2. Check for Session Cookie - For Browser/UI
        if 'user_id' not in session:
            # Check for AJAX/API request
            if request.is_json or request.path.startswith('/ingest'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('ui.login'))
        return f(*args, **kwargs)
    return decorated_function

def verify_google_token(token):
    try:
        id_info = id_token.verify_oauth2_token(
            token, 
            google_requests.Request(), 
            GOOGLE_CLIENT_ID
        )
        return id_info
    except ValueError as e:
        print(f"[AUTH ERROR] Token verification failed: {e}")
        return None

def get_or_create_user(google_id, email, name):
    conn = get_db()
    cur = conn.cursor()
    try:
        # Check if user exists
        cur.execute("SELECT id FROM users WHERE google_id = %s", (google_id,))
        user = cur.fetchone()
        
        if user:
            return user[0]
        
        # Create new user
        cur.execute(
            "INSERT INTO users (google_id, email, name) VALUES (%s, %s, %s) RETURNING id",
            (google_id, email, name)
        )
        new_user_id = cur.fetchone()[0]
        conn.commit()
        print(f"[AUTH] New user created: {email} (ID: {new_user_id})")
        return new_user_id
    except Exception as e:
        conn.rollback()
        print(f"[AUTH ERROR] DB Error: {e}")
        return None
    finally:
        cur.close()
