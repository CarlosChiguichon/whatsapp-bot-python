from openai import OpenAI
import shelve
from dotenv import load_dotenv
import os
import time
import logging
import json
from app.services.odoo_integration import create_odoo_ticket

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
client = OpenAI(api_key=OPENAI_API_KEY)


def upload_file(path):
    # Upload a file with an "assistants" purpose
    file = client.files.create(
        file=open("../../data/airbnb-faq.pdf", "rb"), purpose="assistants"
    )


def create_assistant(file):
    """
    You currently cannot set the temperature for Assistant via the API.
    """
    assistant = client.beta.assistants.create(
        name="WhatsApp AirBnb Assistant",
        instructions="You're a helpful WhatsApp assistant that can assist guests that are staying in our Paris AirBnb. Use your knowledge base to best respond to customer queries. If you don't know the answer, say simply that you cannot help with question and advice to contact the host directly. Be friendly and funny.",
        tools=[{"type": "retrieval"}],
        model="gpt-4-1106-preview",
        file_ids=[file.id],
    )
    return assistant


# Use context manager to ensure the shelf file is closed properly
def check_if_thread_exists(wa_id):
    with shelve.open("threads_db") as threads_shelf:
        return threads_shelf.get(wa_id, None)


def store_thread(wa_id, thread_id):
    with shelve.open("threads_db", writeback=True) as threads_shelf:
        threads_shelf[wa_id] = thread_id


def handle_function_call(thread_id, run_id, function_call, wa_id, name):
    """
    Maneja las llamadas a funciones del asistente.
    
    Args:
        thread_id: ID del hilo de conversación
        run_id: ID de la ejecución
        function_call: Objeto con la información de la llamada a función
        wa_id: ID de WhatsApp del usuario
        name: Nombre del usuario
        
    Returns:
        str: Respuesta después de procesar la función
    """
    if function_call.name == "create_odoo_ticket":
        try:
            # Extraer los argumentos
            args = json.loads(function_call.arguments)
            logging.info(f"Procesando creación de ticket para {name}: {args.get('subject', '')}")
            
            # Si no se proporciona customer_phone, usar el wa_id
            if not args.get("customer_phone"):
                args["customer_phone"] = wa_id
                
            # Si no se proporciona customer_name, usar el nombre
            if not args.get("customer_name"):
                args["customer_name"] = name
            
            # Llamar a la función de creación de ticket
            result = create_odoo_ticket(
                customer_name=args.get("customer_name", name),
                customer_phone=args.get("customer_phone", wa_id),
                customer_email=args.get("customer_email", ""),
                subject=args.get("subject", ""),
                description=args.get("description", "")
            )
            
            # Enviar el resultado al asistente
            client.beta.threads.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run_id,
                tool_outputs=[
                    {
                        "tool_call_id": function_call.id,
                        "output": json.dumps(result)
                    }
                ]
            )
            
            # Esperar a que el asistente procese el resultado y devuelva una respuesta
            return wait_for_run_completion(thread_id, run_id)
            
        except Exception as e:
            logging.error(f"Error al procesar la creación del ticket: {str(e)}")
            error_result = {
                "success": False,
                "error": f"Error al procesar la creación del ticket: {str(e)}"
            }
            
            # Enviar el error al asistente
            client.beta.threads.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run_id,
                tool_outputs=[
                    {
                        "tool_call_id": function_call.id,
                        "output": json.dumps(error_result)
                    }
                ]
            )
            
            # Esperar la respuesta actualizada
            return wait_for_run_completion(thread_id, run_id)
    
    # Para otras funciones que puedan añadirse en el futuro
    else:
        logging.warning(f"Llamada a función no soportada: {function_call.name}")
        return "Lo siento, no puedo procesar esa solicitud en este momento."


def wait_for_run_completion(thread_id, run_id):
    """
    Espera a que se complete la ejecución y devuelve el mensaje más reciente.
    
    Args:
        thread_id: ID del hilo de conversación
        run_id: ID de la ejecución
        
    Returns:
        str: Contenido del mensaje más reciente
    """
    # Esperar a que se complete la ejecución
    while True:
        time.sleep(0.5)
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        
        if run.status == "completed":
            # Obtener mensajes y devolver el más reciente
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            if messages.data:
                return messages.data[0].content[0].text.value
        
        elif run.status == "requires_action":
            # Si requiere acción pero no podemos manejarla (no debería ocurrir aquí)
            logging.warning("La ejecución requiere acción pero ya estamos en el manejador")
            return "Lo siento, ha ocurrido un error al procesar tu solicitud."
        
        elif run.status in ["failed", "cancelled", "expired"]:
            logging.error(f"La ejecución falló con estado: {run.status}")
            return "Lo siento, ha ocurrido un error al procesar tu solicitud."


def run_assistant(thread, name, wa_id):
    """
    Ejecuta el asistente y maneja posibles llamadas a funciones.
    
    Args:
        thread: Objeto de hilo de conversación
        name: Nombre del usuario
        wa_id: ID de WhatsApp del usuario
        
    Returns:
        str: Respuesta del asistente
    """
    # Retrieve the Assistant
    assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

    # Run the assistant
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id,
        # instructions=f"You are having a conversation with {name}",
    )

    # Poll for completion or required actions
    while True:
        time.sleep(0.5)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        
        # Si la ejecución requiere acciones (llamadas a funciones)
        if run.status == "requires_action":
            # Procesar la llamada a la función
            tool_calls = run.required_action.submit_tool_outputs.tool_calls
            
            # Procesar cada llamada a función (generalmente será solo una)
            for tool_call in tool_calls:
                if tool_call.type == "function":
                    function_call = tool_call.function
                    logging.info(f"Llamada a función detectada: {function_call.name}")
                    return handle_function_call(thread.id, run.id, function_call, wa_id, name)
        
        # Si se completa sin llamadas a funciones
        elif run.status == "completed":
            break
        
        # Si falla la ejecución
        elif run.status in ["failed", "cancelled", "expired"]:
            logging.error(f"La ejecución falló con estado: {run.status}")
            return "Lo siento, ha ocurrido un error al procesar tu solicitud."

    # Retrieve the Messages
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    new_message = messages.data[0].content[0].text.value
    logging.info(f"Generated message: {new_message}")
    return new_message


def generate_response(message_body, wa_id, name):
    # Check if there is already a thread_id for the wa_id
    thread_id = check_if_thread_exists(wa_id)

    # If a thread doesn't exist, create one and store it
    if thread_id is None:
        logging.info(f"Creating new thread for {name} with wa_id {wa_id}")
        thread = client.beta.threads.create()
        store_thread(wa_id, thread.id)
        thread_id = thread.id

    # Otherwise, retrieve the existing thread
    else:
        logging.info(f"Retrieving existing thread for {name} with wa_id {wa_id}")
        thread = client.beta.threads.retrieve(thread_id)

    # Add message to thread
    message = client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=message_body,
    )

    # Run the assistant and get the new message
    new_message = run_assistant(thread, name, wa_id)

    return new_message