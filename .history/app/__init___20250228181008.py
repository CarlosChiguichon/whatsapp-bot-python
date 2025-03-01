from flask import Flask
from app.config import load_configurations, configure_logging
from .views import webhook_blueprint
from app.utils.whatsapp_utils import init_whatsapp_config

def create_app():
    app = Flask(__name__)
    
    # Load configurations and logging settings
    load_configurations(app)
    configure_logging()
    
    # Initialize WhatsApp config for background threads
    init_whatsapp_config(app)
    
    # Import and register blueprints, if any
    app.register_blueprint(webhook_blueprint)
    
    return app
