import time
import threading
import json
from datetime import datetime, timedelta


class SessionManager:
    """
    Gestor de sesiones para el chatbot de WhatsApp.
    Mantiene el estado de las conversaciones con los usuarios y maneja la expiración de sesiones.
    """
    
    def __init__(self, session_timeout=180):  # 3 minutos por defecto
        """
        Inicializa el gestor de sesiones.
        
        Args:
            session_timeout (int): Tiempo en segundos antes de que una sesión expire por inactividad
        """
        self.sessions = {}
        self.session_timeout = session_timeout
        self.lock = threading.RLock()  # Para operaciones thread-safe
        
        # Iniciar thread de limpieza en segundo plano
        self.cleanup_thread = threading.Thread(target=self._cleanup_expired_sessions, daemon=True)
        self.cleanup_thread.start()
    
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
                    'message_history': []
                }
            else:
                # Actualizar timestamp de última actividad
                self.sessions[user_id]['last_activity'] = datetime.now()
            
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
        Thread en segundo plano que limpia periódicamente las sesiones expiradas.
        """
        while True:
            time.sleep(60)  # Verificar cada minuto
            with self.lock:
                current_time = datetime.now()
                expired_sessions = []
                
                for user_id, session in self.sessions.items():
                    expiration_time = session['last_activity'] + timedelta(seconds=self.session_timeout)
                    if current_time > expiration_time:
                        expired_sessions.append(user_id)
                
                # Eliminar sesiones expiradas
                for user_id in expired_sessions:
                    # Opcionalmente, enviar mensaje de despedida aquí si se requiere
                    print(f"Sesión expirada para el usuario {user_id}")
                    del self.sessions[user_id]
    
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
                    self.sessions[user_id] = session
        except (FileNotFoundError, json.JSONDecodeError):
            # Si el archivo no existe o está malformado, iniciar con sesiones vacías
            pass