from flask import Blueprint, send_file, render_template, request, session, redirect, url_for, jsonify
import os
from app.auth import login_required, verify_google_token, get_or_create_user

ui_bp = Blueprint('ui', __name__)

# Base directory for the app (app/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

@ui_bp.route("/login")
def login():
    if 'user_id' in session:
        return redirect(url_for('ui.index'))
    return render_template("login.html", google_client_id=os.getenv('GOOGLE_CLIENT_ID'))

@ui_bp.route("/auth/google", methods=["POST"])
def google_auth():
    data = request.get_json()
    token = data.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'No token provided'}), 400
        
    id_info = verify_google_token(token)
    if not id_info:
        return jsonify({'success': False, 'error': 'Invalid token'}), 401
        
    user_id = get_or_create_user(
        google_id=id_info['sub'],
        email=id_info['email'],
        name=id_info.get('name')
    )
    
    if user_id:
        session['user_id'] = user_id
        session['email'] = id_info['email']
        session['name'] = id_info.get('name')
        return jsonify({'success': True, 'redirect': url_for('ui.index')})
    else:
        return jsonify({'success': False, 'error': 'User creation failed'}), 500

@ui_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('ui.login'))

@ui_bp.route("/")
def root():
    return redirect(url_for('ui.login'))

@ui_bp.route("/app")
@login_required
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        import traceback
        return f"ERROR: {str(e)}\n{traceback.format_exc()}", 500

@ui_bp.route("/esp32")
def esp32_dashboard():
    """Serve ESP32 sensor monitoring dashboard"""
    return send_file(os.path.join(BASE_DIR, "templates", "esp32_dashboard.html"))

@ui_bp.route("/esp32_style.css")
def esp32_style():
    """Serve ESP32 dashboard CSS"""
    return send_file(os.path.join(BASE_DIR, "static", "css", "esp32_style.css"))

@ui_bp.route("/esp32_app.js")
def esp32_app():
    """Serve ESP32 dashboard JavaScript"""
    return send_file(os.path.join(BASE_DIR, "static", "js", "esp32_app.js"))

@ui_bp.route("/session-preview-page")
def session_preview_page():
    """Serve the session preview debug page"""
    return send_file(os.path.join(BASE_DIR, "templates", "session_preview.html"))

@ui_bp.route("/gemini-analysis")
def gemini_analysis_page():
    """Serve the Gemini analysis page"""
    return render_template("gemini_analysis.html")
