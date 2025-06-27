# ----------------------------------------------------------------------------------
# Titular: Gestor de Base de Datos SQLite para Conversaciones y Archivos
# Funcion Principal: Este modulo define la clase DBManager, una interfaz para
#                    gestionar una base de datos SQLite disenada para almacenar el
#                    historial de conversaciones, interacciones y, en un futuro,
#                    archivos adjuntos.
#                    La implementacion es de naturaleza experimental y esta disenada
#                    para ser segura en entornos multihilo: en lugar de compartir una
#                    unica conexion, cada operacion abre y cierra su propia conexion
#                    a la base de datos para evitar problemas de concurrencia con SQLite.
#                    Provee metodos para operaciones CRUD (Crear, Leer, Actualizar, Borrar)
#                    sobre las conversaciones y sus interacciones.
# ----------------------------------------------------------------------------------

import sqlite3
import os
from datetime import datetime, timezone
import logging
from typing import List, Dict, Any, Optional
import sys # Importado para el bloque __main__

# Configuracion del logger para este modulo.
log = logging.getLogger("DBManager")
# Si este script se ejecuta directamente, se configura un logging basico.
if not log.hasHandlers() and __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout)])

# Gestiona todas las operaciones con la base de datos SQLite.
# Su diseno de abrir/cerrar conexiones por operacion la hace segura para
# ser usada en aplicaciones con multiples hilos (multi-threaded).
class DBManager:
    # Inicializa el gestor, guardando la ruta a la base de datos y asegurando
    # que el esquema de tablas este creado.
    # La conexion a la base de datos no se mantiene abierta, se establece por metodo.
    #
    # Args:
    #   db_path (str): Ruta al archivo de la base de datos SQLite.
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path) # Guarda la ruta absoluta a la BD
        # Asegura que el directorio para el archivo de la BD exista
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        try:
            # Llama al metodo para crear las tablas si no existen. Usa una conexion temporal.
            self._initialize_schema()
            log.info(f"DBManager inicializado. La base de datos se encuentra en: {self.db_path}")
        except sqlite3.Error as e:
            log.critical(f"Error critico al inicializar el esquema de DBManager para '{self.db_path}': {e}", exc_info=True)
            raise # Re-lanza la excepcion para indicar un fallo en la inicializacion del gestor

    # Metodo auxiliar interno para establecer y devolver una NUEVA conexion a la BD.
    # Cada conexion se configura para devolver filas como diccionarios (sqlite3.Row)
    # y para que las claves foraneas (foreign keys) esten activadas.
    def _get_connection(self) -> sqlite3.Connection:
        try:
            # El timeout es util si la BD esta bloqueada por otra operacion.
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row # Permite acceder a las columnas por nombre
            conn.execute("PRAGMA foreign_keys = ON;") # Habilita el soporte para claves foraneas
            return conn
        except sqlite3.Error as e:
            log.error(f"Error al conectar con la base de datos '{self.db_path}': {e}", exc_info=True)
            raise # Re-lanza la excepcion para que el metodo que la llamo pueda manejarla

    # Crea el esquema completo de la base de datos (tablas e indices) si no existe.
    # Se ejecuta una unica vez al inicializar la primera instancia de DBManager.
    def _initialize_schema(self):
        # Esquema SQL para las tablas de la base de datos
        sql_schema = """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default_user',
                start_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                summary TEXT NULL
            );
            CREATE TABLE IF NOT EXISTS files ( /* Para gestion futura de archivos */
                file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                file_type TEXT,
                description TEXT NULL,
                upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS interactions (
                interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL, /* Se espera un string JSON con el payload y metadatos */
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS interaction_files ( /* Tabla de union para enlazar archivos a interacciones */
                interaction_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                PRIMARY KEY (interaction_id, file_id),
                FOREIGN KEY (interaction_id) REFERENCES interactions(interaction_id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE RESTRICT
            );
            -- Creacion de indices para acelerar las consultas comunes
            CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_conversation_id ON interactions(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_timestamp ON interactions(timestamp);
        """
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.executescript(sql_schema) # executescript permite ejecutar multiples sentencias SQL
            conn.commit() # Guarda los cambios
            log.info("Tablas de la base de datos verificadas/creadas exitosamente.")
        except sqlite3.Error as e:
            log.error(f"Error al crear o verificar las tablas de la base de datos: {e}", exc_info=True)
            if conn: conn.rollback() # Revierte los cambios si hubo un error
            raise # Re-lanza la excepcion
        finally:
            if conn: conn.close() # Siempre cierra la conexion

    # --- Metodos para la gestion de Conversaciones ---

    # Inicia una nueva conversacion en la base de datos para un usuario especifico.
    #
    # Returns:
    #   Optional[int]: El ID de la nueva conversacion, o None si ocurre un error.
    def start_conversation(self, summary: Optional[str] = None, user_id: str = "default_user") -> Optional[int]:
        sql = "INSERT INTO conversations (summary, user_id) VALUES (?, ?)"
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, (summary, user_id))
            conn.commit()
            last_id = cursor.lastrowid # Obtiene el ID de la fila insertada
            log.info(f"Nueva conversacion (ID: {last_id}) iniciada para el usuario '{user_id}'.")
            return last_id
        except sqlite3.Error as e:
            log.error(f"Error al iniciar una nueva conversacion para el usuario '{user_id}': {e}", exc_info=True)
            if conn: conn.rollback()
            return None
        finally:
            if conn: conn.close()

    # Actualiza la marca de tiempo 'last_updated' de una conversacion especifica.
    # Util para saber cuando fue la ultima actividad en una conversacion.
    #
    # Returns:
    #   bool: True si la actualizacion fue exitosa, False en caso contrario.
    def update_conversation_timestamp(self, conversation_id: int) -> bool:
        sql = "UPDATE conversations SET last_updated = ? WHERE conversation_id = ?"
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            current_utc_iso = datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z' # Timestamp UTC actual
            cursor.execute(sql, (current_utc_iso, conversation_id))
            conn.commit()
            return cursor.rowcount > 0 # Devuelve True si se actualizo al menos una fila
        except sqlite3.Error as e:
            log.error(f"Error al actualizar la marca de tiempo de la conversacion {conversation_id}: {e}", exc_info=True)
            if conn: conn.rollback()
            return False
        finally:
            if conn: conn.close()

    # Obtiene todos los detalles de una conversacion especifica por su ID.
    #
    # Returns:
    #   Optional[Dict[str, Any]]: Un diccionario con los datos de la conversacion, o None si no se encuentra.
    def get_conversation_details(self, conversation_id: int) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM conversations WHERE conversation_id = ?"
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, (conversation_id,))
            row = cursor.fetchone() # Obtiene la primera fila del resultado
            return dict(row) if row else None # Convierte la fila a diccionario si existe
        except sqlite3.Error as e:
            log.error(f"Error al obtener los detalles de la conversacion {conversation_id}: {e}", exc_info=True)
            return None
        finally:
            if conn: conn.close()

    # Verifica de forma rapida si una conversacion con un ID dado existe en la base de datos.
    #
    # Returns:
    #   bool: True si la conversacion existe, False en caso contrario.
    def conversation_exists(self, conversation_id: int) -> bool:
        return self.get_conversation_details(conversation_id) is not None

    # --- Metodos para la gestion de Interacciones ---

    # Anade una nueva interaccion (un mensaje de 'user', 'assistant' o 'system')
    # a una conversacion existente. Esta operacion es atomica y tambien actualiza
    # la marca de tiempo de la conversacion.
    #
    # Returns:
    #   Optional[int]: El ID de la nueva interaccion, o None si ocurre un error.
    def add_interaction(self, conversation_id: int, role: str, content_json_str: str) -> Optional[int]:
        if role not in ('user', 'assistant', 'system'):
            log.error(f"Rol invalido '{role}' proporcionado. Debe ser 'user', 'assistant', o 'system'."); return None

        sql_interaction = "INSERT INTO interactions (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)"
        sql_update_conv = "UPDATE conversations SET last_updated = ? WHERE conversation_id = ?"
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            interaction_ts_utc_iso = datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z'
            # Ejecuta ambas operaciones dentro de una misma transaccion
            cursor.execute(sql_interaction, (conversation_id, role, content_json_str, interaction_ts_utc_iso))
            interaction_id = cursor.lastrowid
            cursor.execute(sql_update_conv, (interaction_ts_utc_iso, conversation_id)) # Actualiza con el mismo timestamp
            conn.commit()
            log.debug(f"Interaccion (ID:{interaction_id}) del rol '{role}' anadida a la conversacion {conversation_id}.")
            return interaction_id
        except sqlite3.Error as e:
            log.error(f"Error al anadir interaccion a la conversacion {conversation_id}: {e}", exc_info=True)
            if conn: conn.rollback() # Revierte toda la transaccion si algo falla
            return None
        finally:
            if conn: conn.close()

    # Obtiene las ultimas N interacciones de una conversacion, ordenadas cronologicamente.
    # La consulta SQL esta optimizada para obtener eficientemente las mas recientes.
    #
    # Returns:
    #   List[Dict[str, Any]]: Una lista de diccionarios, donde cada uno es una interaccion.
    def get_interactions(self, conversation_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        # La subconsulta obtiene las 'limit' mas recientes, y la consulta externa las reordena cronologicamente.
        sql = """
            SELECT * FROM
                (SELECT interaction_id, conversation_id, timestamp, role, content
                 FROM interactions
                 WHERE conversation_id = ?
                 ORDER BY timestamp DESC
                 LIMIT ?)
            ORDER BY timestamp ASC;
        """
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, (conversation_id, limit))
            rows = cursor.fetchall()
            return [dict(row) for row in rows] # Devuelve una lista de diccionarios
        except sqlite3.Error as e:
            log.error(f"Error al obtener las interacciones de la conversacion {conversation_id}: {e}", exc_info=True)
            return []
        finally:
            if conn: conn.close()

    # --- Metodos para la gestion de Archivos (Funcionalidad Experimental/Futura) ---

    # (Experimental) Registra un nuevo archivo en la base de datos.
    # Parte de una funcionalidad futura para gestionar archivos adjuntos en las conversaciones.
    #
    # Returns:
    #   Optional[int]: El ID del nuevo archivo, o None si ocurre un error (ej. ruta duplicada).
    def add_file(self, file_path: str, file_type: str, description: Optional[str] = None) -> Optional[int]:
        sql = "INSERT INTO files (file_path, file_type, description) VALUES (?, ?, ?)"
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, (file_path, file_type, description))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError as e: # Error especifico si se viola una restriccion (ej. UNIQUE)
            log.warning(f"Error de integridad al anadir archivo (¿posible ruta duplicada? '{file_path}'): {e}")
            return None
        except sqlite3.Error as e:
            log.error(f"Error al anadir el archivo '{file_path}' a la base de datos: {e}", exc_info=True)
            return None
        finally:
            if conn: conn.close()

    # Otros metodos para gestionar archivos (get_file_details, link_file_to_interaction, etc.)
    # seguirian este mismo patron de abrir/cerrar conexion por operacion para ser thread-safe.

# --- Bloque de Ejemplo de Uso (Para probar dbManager.py directamente) ---
if __name__ == '__main__':
    log.info("Ejecutando ejemplo de uso de DBManager...")

    # Usa una base de datos de prueba especifica para esta ejecucion directa.
    test_db_main_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db_manager_direct_test.db')
    if os.path.exists(test_db_main_path):
        os.remove(test_db_main_path) # Limpia la BD de pruebas anterior para un inicio limpio
        log.info(f"Base de datos de prueba anterior borrada: {test_db_main_path}")

    try:
        db_test_instance = DBManager(db_path=test_db_main_path)

        log.info("\n--- Probando: Iniciar Conversacion ---")
        conv_id = db_test_instance.start_conversation(summary="Prueba directa de DBManager", user_id="test_user_db")
        if not conv_id: raise Exception("No se pudo iniciar la conversacion")
        log.info(f"Conversacion iniciada exitosamente con ID: {conv_id}")

        log.info("\n--- Probando: Anadir Interacciones ---")
        user_msg1_content = json.dumps({"text": "Hola Umebot", "source":"test_console"})
        user_int_id1 = db_test_instance.add_interaction(conv_id, 'user', user_msg1_content)
        if not user_int_id1: raise Exception("No se pudo anadir la interaccion del usuario 1")
        log.info(f"Interaccion de Usuario 1 anadida (ID {user_int_id1}): {user_msg1_content}")

        assistant_msg1_content = json.dumps({"text": "¡Hola! ¿En que puedo ayudarte?", "model_used":"test_model"})
        assist_int_id1 = db_test_instance.add_interaction(conv_id, 'assistant', assistant_msg1_content)
        if not assist_int_id1: raise Exception("No se pudo anadir la interaccion del asistente 1")
        log.info(f"Interaccion de Asistente 1 anadida (ID {assist_int_id1}): {assistant_msg1_content}")

        log.info("\n--- Probando: Obtener Historial de la Conversacion ---")
        interactions = db_test_instance.get_interactions(conv_id, limit=10)
        log.info(f"Historial de la conversacion {conv_id} recuperado (total: {len(interactions)} interacciones):")
        for inter in interactions:
            log.info(f"   [{inter['timestamp']}] {inter['role']}: {inter['content']} (ID: {inter['interaction_id']})")

        log.info("\n--- Probando: Verificar si una conversacion existe ---")
        log.info(f"¿Existe la conversacion con ID {conv_id}? {db_test_instance.conversation_exists(conv_id)}")
        log.info(f"¿Existe la conversacion con ID 999? {db_test_instance.conversation_exists(999)}")

        log.info("\n--- Ejemplo de uso de DBManager finalizado exitosamente ---")
    except Exception as e_main_test:
        log.critical(f"\n--- ERROR en el ejemplo de uso de DBManager: {e_main_test} ---", exc_info=True)
