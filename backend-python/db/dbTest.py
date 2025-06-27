# ----------------------------------------------------------------------------------
# Titular: Script de Prueba y Gestion para la Base de Datos
# Funcion Principal: Este modulo define una version de la clase DBManager disenada
#                    especificamente para pruebas interactivas y gestion de la base
#                    de datos de forma standalone (independiente). A diferencia del
#                    DBManager principal (disenado para ser thread-safe), esta version
#                    mantiene una unica conexion a la base de datos abierta durante
#                    la vida de la instancia, lo que la hace mas adecuada para scripts
#                    de prueba o tareas de administracion en un solo hilo.
#                    Cuando se ejecuta directamente, el script ofrece un menu interactivo
#                    en la consola para ver estadisticas, limpiar la base de datos y
#                    anadir datos de ejemplo.
# ----------------------------------------------------------------------------------

import sqlite3
import os
import shutil
import json # Para el ejemplo de guardar/leer JSON en la columna 'content'

# Gestor de base de datos SQLite con un diseno de conexion persistente,
# optimizado para uso en scripts de prueba y tareas de administracion.
# Implementa el protocolo de context manager para una gestion segura de la conexion.
class DBManager:
    # Inicializa el gestor, establece una conexion persistente con la base de datos
    # y asegura que el esquema de tablas este creado.
    #
    # Args:
    #   db_path (str): Ruta al archivo de la base de datos SQLite.
    #   files_dir (str): Ruta al directorio donde se guardarian archivos fisicos.
    def __init__(self, db_path='ws/db/conversations.db', files_dir='ws/db/files'):
        self.db_path = db_path
        self.files_dir = files_dir

        # Asegura que los directorios para la BD y los archivos existan
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        os.makedirs(os.path.abspath(files_dir), exist_ok=True)

        self.conn = None # La conexion se almacenara aqui durante la vida de la instancia
        try:
            self._connect() # Establece la conexion
            self._create_tables() # Crea las tablas si no existen
        except sqlite3.Error as e:
            # Imprime solo si hay un error real al inicializar
            print(f"Error Critico al inicializar DBManager con la BD '{db_path}': {e}")
            raise e # Relanza la excepcion para indicar el fallo

    # Metodo auxiliar interno que crea y almacena la conexion unica a la base de datos.
    def _connect(self):
        if self.conn is None:
            try:
                self.conn = sqlite3.connect(self.db_path)
                self.conn.row_factory = sqlite3.Row # Permite acceder a las columnas por nombre
                self.conn.execute("PRAGMA foreign_keys = ON;") # Activa el soporte para claves foraneas
                self.conn.commit()
            except sqlite3.Error as e:
                print(f"Error al conectar a la base de datos '{self.db_path}': {e}")
                self.conn = None
                raise

    # Crea el esquema completo de la base de datos (tablas, indices) si no existe,
    # utilizando la conexion persistente de la instancia.
    def _create_tables(self):
        if not self.conn:
            try:
                self._connect()
                if not self.conn:
                    raise sqlite3.Error("No se pudo establecer una conexion para crear las tablas.")
            except sqlite3.Error as e:
                raise sqlite3.Error(f"Fallo al intentar reconectar para _create_tables: {e}")

        # Definicion del esquema SQL
        sql_schema = """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_timestamp DATETIME,
                last_updated DATETIME,
                summary TEXT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                file_type TEXT,
                description TEXT NULL,
                upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS interactions (
                interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                timestamp DATETIME,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL, -- Puede almacenar texto plano o una cadena JSON
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS interaction_files (
                interaction_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                PRIMARY KEY (interaction_id, file_id),
                FOREIGN KEY (interaction_id) REFERENCES interactions(interaction_id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE RESTRICT
            );
            -- Indices para mejorar el rendimiento de las busquedas
            CREATE INDEX IF NOT EXISTS idx_interactions_conversation_id ON interactions(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_timestamp ON interactions(timestamp);
            CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path);
            CREATE INDEX IF NOT EXISTS idx_interaction_files_interaction ON interaction_files(interaction_id);
            CREATE INDEX IF NOT EXISTS idx_interaction_files_file ON interaction_files(file_id);
        """
        try:
            cursor = self.conn.cursor()
            cursor.executescript(sql_schema) # Ejecuta todas las sentencias SQL del string
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Error al crear o verificar las tablas: {e}")
            raise

    # Cierra explicitamente la conexion persistente a la base de datos.
    def close(self):
        if self.conn:
            try:
                self.conn.close()
                self.conn = None
            except sqlite3.Error as e:
                print(f"Error al cerrar la conexion con la base de datos: {e}")

    # Metodo destructivo para borrar todas las tablas y recrear el esquema.
    # Incluye una confirmacion interactiva para prevenir borrados accidentales.
    # Opcionalmente, puede borrar tambien los archivos fisicos asociados.
    def clear_all_data(self, also_delete_physical_files=False, interactive_confirm=True):
        if not self.conn:
            print("Error: No hay una conexion a la base de datos para limpiar."); return False

        if interactive_confirm:
            confirm = input(f"ADVERTENCIA: ¿Estas seguro de que quieres limpiar COMPLETAMENTE la base de datos '{self.db_path}'? (s/N): ")
            if confirm.lower() != 's':
                print("Limpieza cancelada por el usuario."); return False

        tables_to_drop = ["interaction_files", "interactions", "files", "conversations"]
        try:
            cursor = self.conn.cursor()
            print("Deshabilitando claves foraneas temporalmente para el borrado de tablas...")
            cursor.execute("PRAGMA foreign_keys=OFF;")

            for table_name in tables_to_drop:
                print(f"Borrando tabla {table_name}...")
                cursor.execute(f"DROP TABLE IF EXISTS {table_name};")

            print("Rehabilitando claves foraneas...")
            cursor.execute("PRAGMA foreign_keys=ON;")
            self.conn.commit()
            print("Todas las tablas han sido borradas.")

            print("Recreando la estructura de las tablas...")
            self._create_tables()
            print("Tablas verificadas y recreadas exitosamente.")

            # Logica opcional para borrar archivos fisicos del directorio asociado
            if also_delete_physical_files:
                print(f"ADVERTENCIA: Limpiando directorio de archivos fisicos: {self.files_dir}...")
                if interactive_confirm: # Pide una segunda confirmacion especifica para los archivos
                    confirm_files = input(f"¿Estas seguro de que quieres borrar TODOS los archivos en '{self.files_dir}'? (s/N): ")
                    if confirm_files.lower() != 's':
                        print("Limpieza de archivos fisicos cancelada."); return True

                if os.path.exists(self.files_dir):
                    # Borra y recrea el directorio para una limpieza total
                    shutil.rmtree(self.files_dir)
                    os.makedirs(self.files_dir, exist_ok=True)
                    print("Directorio de archivos fisicos limpiado y recreado.")
                else:
                    print(f"El directorio {self.files_dir} no existe, no se limpio nada.")
            return True
        except sqlite3.Error as e:
            print(f"Error al limpiar la base de datos: {e}")
            try: # Intenta reactivar las claves foraneas incluso si hubo un error
                if self.conn: self.conn.execute("PRAGMA foreign_keys=ON;"); self.conn.commit()
            except: pass
            return False

    # --- Metodos CRUD (Crear, Leer, Actualizar, Borrar) y de Gestion ---
    # Nota: Estos metodos asumen que la conexion `self.conn` ya esta establecida.

    def start_conversation(self, summary=None):
        sql = "INSERT INTO conversations (summary, last_updated, start_timestamp) VALUES (?, strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime'), strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime'))"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (summary,))
            self.conn.commit(); return cursor.lastrowid
        except sqlite3.Error as e: print(f"Error al iniciar conversacion: {e}"); return None

    def update_conversation_timestamp(self, conversation_id):
        sql = "UPDATE conversations SET last_updated = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime') WHERE conversation_id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (conversation_id,)); self.conn.commit(); return cursor.rowcount > 0
        except sqlite3.Error as e: print(f"Error al actualizar timestamp de conversacion {conversation_id}: {e}"); return False

    def get_conversation_details(self, conversation_id):
        sql = "SELECT * FROM conversations WHERE conversation_id = ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(sql, (conversation_id,)); return cursor.fetchone()
        except sqlite3.Error as e: print(f"Error al obtener detalles de conversacion {conversation_id}: {e}"); return None

    def get_recent_conversations(self, limit=20):
        sql = "SELECT conversation_id, start_timestamp, last_updated, summary FROM conversations ORDER BY last_updated DESC LIMIT ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(sql, (limit,)); return cursor.fetchall()
        except sqlite3.Error as e: print(f"Error al obtener conversaciones recientes: {e}"); return []

    def delete_conversation(self, conversation_id, interactive_confirm=False):
        if interactive_confirm:
            confirm = input(f"¿Estas seguro de que quieres eliminar la conversacion {conversation_id} y todas sus interacciones? (s/N): ")
            if confirm.lower() != 's': print("Eliminacion cancelada."); return False
        sql = "DELETE FROM conversations WHERE conversation_id = ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(sql, (conversation_id,)); self.conn.commit()
            if cursor.rowcount > 0: print(f"Conversacion {conversation_id} eliminada."); return True
            else: print(f"No se encontro la conversacion {conversation_id}."); return False
        except sqlite3.Error as e: print(f"Error al eliminar conversacion {conversation_id}: {e}"); return False

    def add_interaction(self, conversation_id, role, content):
        if role not in ('user', 'assistant', 'system'): print(f"Error: Rol invalido '{role}'."); return None
        sql = "INSERT INTO interactions (conversation_id, role, content, timestamp) VALUES (?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime'))"
        try:
            cursor = self.conn.cursor()
            content_str = json.dumps(content) if not isinstance(content, str) else content
            cursor.execute(sql, (conversation_id, role, content_str))
            interaction_id = cursor.lastrowid
            if not self.update_conversation_timestamp(conversation_id): print(f"Advertencia: No se pudo actualizar el timestamp para la conv {conversation_id}.")
            self.conn.commit(); return interaction_id
        except sqlite3.Error as e:
            print(f"Error al anadir interaccion a conv {conversation_id}: {e}")
            if self.conn:
                try: self.conn.rollback()
                except sqlite3.Error as rb_err: print(f"Error durante el rollback: {rb_err}")
            return None

    def get_interactions(self, conversation_id, limit=50):
        sql = "SELECT * FROM (SELECT * FROM interactions WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC"
        try:
            cursor = self.conn.cursor(); cursor.execute(sql, (conversation_id, limit)); return cursor.fetchall()
        except sqlite3.Error as e: print(f"Error al obtener interacciones de conv {conversation_id}: {e}"); return []

    # Se omiten los metodos de archivos y estadisticas por brevedad, pero seguirian el mismo patron.
    # ...

    # --- Metodos para el Context Manager ---
    # Permite usar la clase con la sintaxis 'with DBManager(...) as db:'
    def __enter__(self):
        # Asegura que la conexion este abierta al entrar al bloque 'with'
        if self.conn is None: self._connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Asegura que la conexion se cierre al salir del bloque 'with'
        self.close()

# --- Bloque Principal para ejecutar como script de prueba interactivo ---
if __name__ == "__main__":
    main_db_path = 'ws/db/conversations.db' # Define la BD a gestionar
    main_files_dir = 'ws/db/files/'
    print(f"--- Herramienta de Gestion para la Base de Datos: {main_db_path} ---")

    try:
        # Usa el context manager para asegurar que la conexion se cierre al final
        with DBManager(db_path=main_db_path, files_dir=main_files_dir) as db:
            print("DBManager inicializado para la base de datos principal.")

            # Bucle del menu interactivo
            while True:
                print("\n--- MENU DE PRUEBA ---")
                print("1. Mostrar Estadisticas")
                print("2. Limpiar TODA la Base de Datos")
                print("3. Anadir Conversacion de Ejemplo")
                print("4. Salir")
                choice = input("Selecciona una opcion: ")

                if choice == '1':
                    print("\n--- ESTADISTICAS ACTUALES ---")
                    # (La implementacion completa de metodos de estadisticas seria necesaria aqui)
                    print("Funcionalidad de estadisticas no implementada en esta version del script de prueba.")
                elif choice == '2':
                    # Llama al metodo de limpieza con confirmacion interactiva
                    db.clear_all_data(also_delete_physical_files=True, interactive_confirm=True)
                elif choice == '3':
                    print("\n--- Anadiendo Conversacion de Ejemplo ---")
                    # Logica de ejemplo para anadir datos
                    conv_id_ex = db.start_conversation(summary="Ejemplo desde dbTest.py")
                    if conv_id_ex:
                        print(f"Conversacion de ejemplo creada con ID: {conv_id_ex}")
                        db.add_interaction(conv_id_ex, 'user', json.dumps({"text": "Este es un mensaje de prueba."}))
                        db.add_interaction(conv_id_ex, 'assistant', json.dumps({"text": "Esta es una respuesta de prueba."}))
                        print("Interacciones de ejemplo anadidas.")
                        historial = db.get_interactions(conv_id_ex)
                        print(f"Historial recuperado ({len(historial)} mensajes).")
                    else:
                        print("Fallo al crear la conversacion de ejemplo.")
                elif choice == '4':
                    print("Saliendo de la herramienta de prueba.")
                    break
                else:
                    print("Opcion no valida. Por favor, intenta de nuevo.")
    except Exception as e:
        print(f"Error inesperado en el script de prueba: {e}")
        import traceback
        traceback.print_exc()
