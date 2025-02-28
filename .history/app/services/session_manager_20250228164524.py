import time
import threading
import json
from datetime import datetime, timedelta


class SessionManager:
    """
    Gestor de sesiones para el chatbot de WhatsApp.
    Mantiene el estado de las conversaciones con los usuarios y maneja la expiración de sesiones.
    """
    
    def __init__(self, session_timeout=600):  # 10 minutos por defecto
        """
        Inicializa el gestor de sesiones.
        
        Args:
            session_timeout (int): Tiempo en segundos antes de que una sesión expire por inactividad
        """
        self.sessions = {}
        self.session_timeout = session_timeout
        self.inactivity_warning = session_timeout // 2  # Advertencia a la mitad del timeout (5 min)
        self.lock = threading.RLock()  # Para operaciones thread-safe
        
        # Función para enviar mensajes
        self.send_message_func = None
        
        # Iniciar thread de limpieza en segundo plano
        self.cleanup_thread = threading.Thread(target=self._cleanup_expired_sessions, daemon=True)
        self.cleanup_thread.start()
    
    def set_send_message_function(self, func):
        """
        Establece la función para enviar mensajes a WhatsApp.
        
        Args:
            func: Función que acepta user_id y message como parámetros
        """
        self.send_message_func = func
    
    def get_session(self, user_id):
        """
        Obtiene la sesión de un usuario. Si no existe, crea una nueva.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
            
        Returns:
            dict: Objeto de sesión del usuario
        """
        with self.lock:
            if user_id not in self.sessions:
                # Crear nueva sesión
                self.sessions[user_id] = {
                    'created_at': datetime.now(),
                    'last_activity': datetime.now(),
                    'state': 'INITIAL',
                    'context': {},
                    'thread_id': None,  # Para OpenAI Assistants API
                    'message_history': [],
                    'inactivity_warning_sent': False,
                    'closing_notice_sent': False
                }
            else:
                # Actualizar timestamp de última actividad
                self.sessions[user_id]['last_activity'] = datetime.now()
                
                # Resetear los indicadores de advertencia cuando hay actividad
                self.sessions[user_id]['inactivity_warning_sent'] = False
                self.sessions[user_id]['closing_notice_sent'] = False
            
            return self.sessions[user_id]
    
    def update_session(self, user_id, **kwargs):
        """
        Actualiza propiedades específicas de la sesión de un usuario.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
            **kwargs: Pares clave-valor para actualizar la sesión
        """
        with self.lock:
            if user_id in self.sessions:
                session = self.sessions[user_id]
                for key, value in kwargs.items():
                    if key in session:
                        session[key] = value
                
                # Actualizar timestamp de última actividad
                session['last_activity'] = datetime.now()
                
                # Resetear los indicadores de advertencia
                session['inactivity_warning_sent'] = False
                session['closing_notice_sent'] = False
    
    def end_session(self, user_id):
        """
        Finaliza la sesión de un usuario.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
        """
        with self.lock:
            if user_id in self.sessions:
                del self.sessions[user_id]
    
    def is_session_active(self, user_id):
        """
        Verifica si la sesión de un usuario está activa y no ha expirado.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
            
        Returns:
            bool: True si la sesión está activa, False en caso contrario
        """
        with self.lock:
            if user_id not in self.sessions:
                return False
            
            session = self.sessions[user_id]
            expiration_time = session['last_activity'] + timedelta(seconds=self.session_timeout)
            return datetime.now() < expiration_time
    
    def _cleanup_expired_sessions(self):
        """
        Thread en segundo plano que limpia periódicamente las sesiones expiradas
        y envía notificaciones de inactividad.
        """
        while True:
            time.sleep(30)  # Verificar cada 30 segundos
            
            with self.lock:
                current_time = datetime.now()
                users_to_process = []
                
                # Recopilar usuarios para procesar fuera del lock
                for user_id, session in self.sessions.items():
                    users_to_process.append(user_id)
            
            # Procesar usuarios fuera del lock para evitar bloqueos largos
            for user_id in users_to_process:
                self._process_user_activity(user_id, current_time)
    
    def _process_user_activity(self, user_id, current_time):
        """
        Procesa la actividad de un usuario y envía mensajes según corresponda.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
            current_time (datetime): Tiempo actual
        """
        with self.lock:
            if user_id not in self.sessions:
                return
            
            session = self.sessions[user_id]
            inactive_time = (current_time - session['last_activity']).total_seconds()
            
            # Verificar nivel de inactividad y enviar mensajes apropiados
            if inactive_time >= self.session_timeout and not session['closing_notice_sent']:
                # Tiempo de expiración alcanzado, cerrar sesión
                if self.send_message_func:
                    self.send_message_func(
                        user_id, 
                        "Tu sesión ha sido cerrada debido a inactividad. Puedes iniciar una nueva conversación cuando lo necesites."
                    )
                    session['closing_notice_sent'] = True
                    
                # Registrar mensaje en el historial
                session['message_history'].append({
                    'role': 'assistant',
                    'content': "Tu sesión ha sido cerrada debido a inactividad. Puedes iniciar una nueva conversación cuando lo necesites.",
                    'timestamp': current_time.isoformat()
                })
                
                # Eliminar la sesión
                del self.sessions[user_id]
                
            elif inactive_time >= self.inactivity_warning and not session['inactivity_warning_sent']:
                # Enviar advertencia de inactividad
                if self.send_message_func:
                    self.send_message_func(
                        user_id, 
                        "¿Sigues ahí? Tu sesión se cerrará por inactividad en 5 minutos."
                    )
                    session['inactivity_warning_sent'] = True
                    
                # Registrar mensaje en el historial
                session['message_history'].append({
                    'role': 'assistant',
                    'content': "¿Sigues ahí? Tu sesión se cerrará por inactividad en 5 minutos.",
                    'timestamp': current_time.isoformat()
                })
    
    def add_message_to_history(self, user_id, role, content):
        """
        Agrega un mensaje al historial de la sesión.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
            role (str): Rol del mensaje ('user' o 'assistant')
            content (str): Contenido del mensaje
        """
        with self.lock:
            if user_id in self.sessions:
                self.sessions[user_id]['message_history'].append({
                    'role': role,
                    'content': content,
                    'timestamp': datetime.now().isoformat()
                })
                self.sessions[user_id]['last_activity'] = datetime.now()
                
                # Resetear los indicadores de advertencia
                self.sessions[user_id]['inactivity_warning_sent'] = False
                self.sessions[user_id]['closing_notice_sent'] = False
    
    def get_message_history(self, user_id, limit=10):
        """
        Obtiene el historial de mensajes recientes de un usuario.
        
        Args:
            user_id (str): ID de WhatsApp del usuario
            limit (int): Número máximo de mensajes a devolver
            
        Returns:
            list: Lista de mensajes recientes
        """
        with self.lock:
            if user_id in self.sessions:
                # Devolver los últimos 'limit' mensajes
                return self.sessions[user_id]['message_history'][-limit:]
            return []
    
    def save_sessions(self, filepath):
        """
        Guarda todas las sesiones activas en un archivo JSON.
        
        Args:
            filepath (str): Ruta del archivo donde guardar las sesiones
        """
        with self.lock:
            # Convertir objetos datetime a strings para JSON
            serializable_sessions = {}
            for user_id, session in self.sessions.items():
                serializable_session = session.copy()
                serializable_session['created_at'] = session['created_at'].isoformat()
                serializable_session['last_activity'] = session['last_activity'].isoformat()
                serializable_sessions[user_id] = serializable_session
            
            with open(filepath, 'w') as f:
                json.dump(serializable_sessions, f, indent=2)
    
    def load_sessions(self, filepath):
        """
        Carga sesiones desde un archivo JSON.
        
        Args:
            filepath (str): Ruta del archivo desde donde cargar las sesiones
        """
        try:
            with open(filepath, 'r') as f:
                loaded_sessions = json.load(f)
            
            with self.lock:
                for user_id, session in loaded_sessions.items():
                    # Convertir strings a objetos datetime
                    session['created_at'] = datetime.fromisoformat(session['created_at'])
                    session['last_activity'] = datetime.fromisoformat(session['last_activity'])
                    
                    # Asegurar que los campos de inactividad existan
                    if 'inactivity_warning_sent' not in session:
                        session['inactivity_warning_sent'] = False
                    if 'closing_notice_sent' not in session:
                        session['closing_notice_sent'] = False
                        
                    self.sessions[user_id] = session
        except (FileNotFoundError, json.JSONDecodeError):
            # Si el archivo no existe o está malformado, iniciar con sesiones vacías
            pass