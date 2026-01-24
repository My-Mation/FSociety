from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import os

def create_app():
    app = Flask(__name__)
    app.config['DEBUG'] = True
    app.config['PROPAGATE_EXCEPTIONS'] = True
    
    # Session Config
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev_key')
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_PERMANENT'] = False
    
    from flask_session import Session
    Session(app)
    
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1
    )
    
    
    # Import and register blueprints
    from .routes.ui import ui_bp
    from .routes.ingest import ingest_bp
    from .routes.profiles import profiles_bp
    from .routes.gemini import gemini_bp
    
    app.register_blueprint(ui_bp)
    app.register_blueprint(ingest_bp)
    app.register_blueprint(profiles_bp)
    app.register_blueprint(gemini_bp)
    
    return app
