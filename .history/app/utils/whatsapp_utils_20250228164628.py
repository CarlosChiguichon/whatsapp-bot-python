import logging
from flask import current_app, jsonify
import json
import requests
from app.services.openai_service import generate_response
import re
from app.services.session_manager import SessionManager

# Create a global instance of SessionManager
# Set session timeout to 10 minutes (600 seconds)
session_manager = SessionManager(session_timeout=600)

def log_http_response(response):
    logging.info(f"Status: {response.status_code}")
    logging.info(f"Content-type: {response.headers.get('content-type')}")
    logging.info(f"Body: {response.text}")

def get_text_message_input(recipient, text):
    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
    )

def send_message(data):
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
    }
    url = f"https://graph.facebook.com/{current_app.config['VERSION']}/{current_app.config['PHONE_NUMBER_ID']}/messages"
    try:
        response = requests.post(
            url, data=data, headers=headers, timeout=10
        )
        response.raise_for_status()
    except requests.Timeout:
        logging.error("Timeout occurred while sending message")
        return jsonify({"status": "error", "message": "Request timed out"}), 408
    except requests.RequestException as e:
        logging.error(f"Request failed due to: {e}")
        return jsonify({"status": "error", "message": "Failed to send message"}), 500
    else:
        log_http_response(response)
        return response

def send_whatsapp_message(recipient, text):
    """
    Función helper para enviar mensajes de WhatsApp directamente.
    Utilizada por el SessionManager para enviar recordatorios de inactividad.
    """
    message_data = get_text_message_input(recipient, text)
    return send_message(message_data)

# Configurar la función de envío de mensajes en el SessionManager
session_manager.set_send_message_function(send_whatsapp_message)

def process_text_for_whatsapp(text):
    # Remove brackets
    pattern = r"\【.*?\】"
    # Substitute the pattern with an empty string
    text = re.sub(pattern, "", text).strip()
    # Pattern to find double asterisks including the word(s) in between
    pattern = r"\*\*(.*?)\*\*"
    # Replacement pattern with single asterisks
    replacement = r"*\1*"
    # Substitute occurrences of the pattern with the replacement
    whatsapp_style_text = re.sub(pattern, replacement, text)
    return whatsapp_style_text

def detect_ticket_intent(message):
    """
    Detecta si el usuario tiene la intención de crear un ticket de soporte.
    
    Args:
        message (str): Mensaje del usuario
        
    Returns:
        bool: True si se detecta intención de crear ticket, False en caso contrario
    """
    # Palabras clave que podrían indicar la intención de crear un ticket
    ticket_keywords = [
        "problema", "error", "falla", "ticket", "ayuda", "soporte", "no funciona",
        "issue", "bug", "help", "support", "not working", "broken", "doesn't work"
    ]
    
    message_lower = message.lower()
    
    # Buscar coincidencias con palabras clave
    for keyword in ticket_keywords:
        if keyword in message_lower:
            return True
    
    return False

def process_whatsapp_message(body):
    # Extract user information
    wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
    name = body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]
    message = body["entry"][0]["changes"][0]["value"]["messages"][0]
    message_body = message["text"]["body"]
    
    # Get or create session for this user
    session = session_manager.get_session(wa_id)
    
    # Add message to history
    session_manager.add_message_to_history(wa_id, 'user', message_body)
    
    # Process based on session state
    if session['state'] == 'INITIAL' and message_body.lower() in ['hola', 'hi', 'hello']:
        # Welcome message for new users
        response = f"¡Hola {name}! Bienvenido. ¿En qué puedo ayudarte hoy?"
        session_manager.update_session(wa_id, state='AWAITING_QUERY')
    
    elif session['state'] == 'TICKET_CREATION':
        # If we're in the process of creating a ticket, continue that flow
        if 'ticket_subject' not in session['context']:
            session['context']['ticket_subject'] = message_body
            response = "Gracias. Por favor describe el problema en detalle."
            session_manager.update_session(wa_id, context=session['context'])
        else:
            # Complete ticket creation with collected information
            subject = session['context']['ticket_subject']
            description = message_body
            
            # Here you would call your function to create a ticket in Odoo
            # You can replace this implementation with your actual ticket creation logic
            # from app.services.odoo_integration import create_odoo_ticket
            # ticket_result = create_odoo_ticket(name, wa_id, "", subject, description)
            
            response = "¡Gracias! Tu ticket ha sido creado. Un agente de soporte te contactará pronto."
            # Reset state for new queries
            session_manager.update_session(wa_id, state='AWAITING_QUERY', context={})
    
    # Check if message indicates ticket creation intent
    elif detect_ticket_intent(message_body) and session['state'] != 'TICKET_CREATION':
        response = "Parece que necesitas ayuda con un problema. ¿Podrías proporcionar un breve título o asunto para tu ticket de soporte?"
        session_manager.update_session(wa_id, state='TICKET_CREATION', context={})
    
    else:
        # For any other state or message, process with OpenAI
        # Use thread_id if available to maintain conversation
        thread_id = session.get('thread_id')
        
        # Call your existing OpenAI integration with context preserved
        response = generate_response(message_body, wa_id, name)
        
        # Check if we should update session with a new thread_id
        # If your generate_response returns a thread_id, you can save it:
        # if thread_id is None:
        #     session_manager.update_session(wa_id, thread_id=new_thread_id)
    
    # Process response for WhatsApp formatting
    response = process_text_for_whatsapp(response)
    
    # Add response to history
    session_manager.add_message_to_history(wa_id, 'assistant', response)
    
    # Send response to WhatsApp
    data = get_text_message_input(wa_id, response)
    send_message(data)
    
    # Periodically save sessions
    session_manager.save_sessions('sessions.json')

def is_valid_whatsapp_message(body):
    """
    Check if the incoming webhook event has a valid WhatsApp message structure.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )