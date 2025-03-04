import logging
from flask import current_app, jsonify
import json
import requests
import re
import os
from app.services.openai_service import generate_response
from app.services.session_manager import SessionManager
from app.services.odoo_integration import create_odoo_ticket

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

# Almacenar estos valores globalmente para que estén disponibles fuera del contexto de la aplicación
whatsapp_config = {
    'access_token': None,
    'version': None,
    'phone_number_id': None
}

def init_whatsapp_config(app):
    """
    Inicializa la configuración de WhatsApp para uso fuera del contexto de la aplicación.
    Esta función debe llamarse durante el inicio de la aplicación.
    """
    with app.app_context():
        whatsapp_config['access_token'] = app.config['ACCESS_TOKEN']
        whatsapp_config['version'] = app.config['VERSION']
        whatsapp_config['phone_number_id'] = app.config['PHONE_NUMBER_ID']
        logging.info("Configuración de WhatsApp inicializada para uso en hilos separados")

def send_whatsapp_message_background(recipient, text):
    """
    Función para enviar mensajes de WhatsApp desde hilos en segundo plano.
    Utiliza la configuración almacenada globalmente en lugar de current_app.
    """
    message_data = get_text_message_input(recipient, text)
    
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {whatsapp_config['access_token']}",
    }
    url = f"https://graph.facebook.com/{whatsapp_config['version']}/{whatsapp_config['phone_number_id']}/messages"
    
    try:
        response = requests.post(
            url, data=message_data, headers=headers, timeout=10
        )
        response.raise_for_status()
        logging.info(f"Mensaje de inactividad enviado a {recipient}")
        return response
    except Exception as e:
        logging.error(f"Error al enviar mensaje de inactividad: {str(e)}")
        return None

# Configurar la función de envío de mensajes en el SessionManager
session_manager.set_send_message_function(send_whatsapp_message_background)

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
        "issue", "bug", "help", "support", "not working", "broken", "doesn't work",
        "reportar", "reporte", "report", "queja", "complaint"
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
        # Proceso de creación de ticket en múltiples pasos
        context = session['context']
        
        # Paso 1: Recopilar el asunto/título del ticket
        if 'ticket_step' not in context or context['ticket_step'] == 'subject':
            context['ticket_subject'] = message_body
            context['ticket_step'] = 'description'
            response = "Gracias. Por favor describe el problema en detalle."
        
        # Paso 2: Recopilar la descripción detallada
        elif context['ticket_step'] == 'description':
            context['ticket_description'] = message_body
            context['ticket_step'] = 'email'
            response = "Gracias por la información. Para poder dar seguimiento a tu caso, ¿podrías proporcionarme tu correo electrónico? (Si prefieres no compartirlo, puedes responder 'no')."
        
        # Paso 3: Solicitar email del cliente
        elif context['ticket_step'] == 'email':
            # Verificar si es un email válido o el usuario prefirió no compartirlo
            if message_body.lower() in ['no', 'n', 'paso', 'skip', 'omitir']:
                context['ticket_email'] = ""
            else:
                context['ticket_email'] = message_body
            
            context['ticket_step'] = 'confirmation'
            
            # Mostrar resumen y pedir confirmación
            email_info = f"*Email:* {context['ticket_email']}" if context['ticket_email'] else "*Email:* No proporcionado"
            response = (
                "Por favor, confirma los detalles del ticket:\n\n"
                f"*Asunto:* {context['ticket_subject']}\n"
                f"*Descripción:* {context['ticket_description']}\n"
                f"{email_info}\n\n"
                "¿Deseas crear este ticket? (responde 'sí' o 'no')"
            )
        
        # Paso 4: Confirmar y crear el ticket
        elif context['ticket_step'] == 'confirmation':
            if message_body.lower() in ['si', 'sí', 'yes', 'confirmar', 'aceptar', 'ok']:
                # Crear el ticket en Odoo
                ticket_result = create_odoo_ticket(
                    customer_name=name,
                    customer_phone=wa_id,
                    customer_email=context.get('ticket_email', ""),
                    subject=context['ticket_subject'],
                    description=context['ticket_description']
                )
                
                if ticket_result.get('success', False):
                    response = "¡Ticket creado con éxito! Un agente de soporte se pondrá en contacto contigo pronto. ¿Puedo ayudarte con algo más?"
                else:
                    error_msg = ticket_result.get('error', 'Error desconocido')
                    response = f"Lo siento, hubo un problema al crear el ticket: {error_msg}. Por favor, inténtalo más tarde o contacta directamente con soporte."
            else:
                response = "Ticket cancelado. ¿En qué más puedo ayudarte?"
            
            # Restablecer el estado para nuevas consultas
            session_manager.update_session(wa_id, state='AWAITING_QUERY', context={})
        
        else:
            # Si por alguna razón el paso no está definido correctamente
            response = "Lo siento, hubo un problema con el proceso de creación del ticket. ¿Puedes intentarlo de nuevo?"
            session_manager.update_session(wa_id, state='AWAITING_QUERY', context={})
        
        # Actualizar el contexto
        if session['state'] == 'TICKET_CREATION':  # Solo si no hemos cambiado de estado
            session_manager.update_session(wa_id, context=context)
    
    # Check if message indicates ticket creation intent
    elif detect_ticket_intent(message_body) and session['state'] != 'TICKET_CREATION':
        response = "Parece que necesitas ayuda con un problema. Me gustaría crear un ticket de soporte para que nuestro equipo pueda asistirte. Por favor, proporciona un breve título que describa el problema:"
        session_manager.update_session(
            wa_id, 
            state='TICKET_CREATION', 
            context={'ticket_step': 'subject'}
        )
    
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