# ----------------------------------------------------------------------------------
# Titular: Gestor de Sesion y Conexion con el Robot (libqi)
# Funcion Principal: Provee las herramientas para establecer y gestionar una
#                    conexion segura (tcps) con un robot NAOqi. Incluye clases
#                    de autenticacion y una funcion principal 'connect_robot'.
#                    Permite la ejecucion como script independiente para pruebas
#                    de conexion.
# ----------------------------------------------------------------------------------

import qi
import sys
import time
import argparse

# Clase simple para manejar las credenciales de usuario y contrasena.
class Authenticator(object):
    # Inicializa el autenticador con el nombre de usuario y la contrasena.
    def __init__(self, username, password):
        self.username = username
        self.password = password

    # Devuelve un diccionario con los datos de autenticacion inicial (
    # 'user' y 'token'), como lo requiere la libreria libqi.
    def initialAuthData(self):
        return {'user': self.username, 'token': self.password}

# Fabrica (factory) que crea instancias de la clase Authenticator.
class AuthenticatorFactory(object):
    # Inicializa la fabrica con el nombre de usuario y la contrasena
    # que se usaran para crear nuevos autenticadores.
    def __init__(self, username, password):
        self.username = username
        self.password = password

    # Crea y devuelve una nueva instancia de Authenticator cuando la libreria
    # libqi necesita autenticar la sesion.
    def newAuthenticator(self):
        return Authenticator(self.username, self.password)

# Establece una conexion segura (tcps) con el robot NAOqi.
# Utiliza la libreria libqi para crear una aplicacion, configurar la
# autenticacion y iniciar una sesion con el robot.
# Devuelve la instancia de la aplicacion y la sesion si tiene exito,
# o (None, None) en caso de error.
def connect_robot(robot_ip, port=9503, username="nao", password="nao"):
    connection_url = f"tcps://{robot_ip}:{port}"
    print(f"INFO: Intentando conectar a: {connection_url}...")
    app_args = sys.argv
    # Si no hay argumentos de sistema (ej. cuando se importa como modulo),
    # se provee un nombre por defecto para la aplicacion qi.
    if not sys.argv or len(sys.argv) == 0:
        app_args = ["RobotConnectorApp"]

    app = None
    session = None
    try:
        # Inicializa la aplicacion qi con la URL de conexion
        app = qi.Application(app_args, url=connection_url)
        logins = (username, password)
        # Crea y establece la fabrica de autenticadores para la sesion
        factory = AuthenticatorFactory(*logins)
        app.session.setClientAuthenticatorFactory(factory)
        # Inicia la aplicacion y la conexion
        app.start()
        session = app.session
        print(f"INFO: ¡Conexion exitosa a {robot_ip}:{port}!")
        return app, session
    except RuntimeError as e:
        print(f"ERROR: No se pudo conectar a {connection_url}. Detalles: {e}")
        # Intenta detener la aplicacion si se creo antes de fallar
        if app:
            try: app.stop()
            except Exception as stop_err: print(f"WARN: Error adicional al intentar detener app: {stop_err}")
        return None, None
    except Exception as e:
        print(f"ERROR inesperado durante la conexion: {e}")
        if app:
            try: app.stop()
            except Exception as stop_err: print(f"WARN: Error adicional al intentar detener app: {stop_err}")
        return None, None

# Bloque de codigo para ejecutar este modulo como un script independiente.
# Permite probar la funcionalidad de conexion al robot directamente
# desde la linea de comandos, proporcionando IP y contrasena.
if __name__ == "__main__":
    print("-----------------------------------------------------")
    print("Ejecutando Session_Manager.py como script principal...")
    print("-----------------------------------------------------")

    # Configura el parser para los argumentos de linea de comandos
    parser = argparse.ArgumentParser(description='Conecta a un robot Pepper via Python/qi.')
    parser.add_argument('--ip', type=str, required=True,
                        help='Direccion IP del robot Pepper.')
    parser.add_argument('--password', type=str, required=True,
                        help='Contrasena del robot Pepper (usuario nao).')
    parser.add_argument('--port', type=int, default=9503,
                        help='Puerto de conexion seguro (por defecto: 9503).')
    parser.add_argument('--username', type=str, default='nao',
                        help='Nombre de usuario del robot (por defecto: nao).')

    # Procesa los argumentos proporcionados por el usuario
    args = parser.parse_args()

    print(f"-> Intentando conectar usando argumentos: IP={args.ip}, Puerto={args.port}, Usuario={args.username}")

    # Llama a la funcion de conexion con los argumentos parseados
    app_instance, session_instance = connect_robot(
        robot_ip=args.ip,
        port=args.port,
        username=args.username,
        password=args.password
    )

    # Verifica el resultado de la conexion y realiza pruebas basicas si fue exitosa
    if app_instance and session_instance:
        print("\n-> Prueba de conexion del modulo completada con exito.")
        try:
            print("   Intentando obtener version del sistema...")
            system_service = session_instance.service("ALSystem")
            version = system_service.systemVersion()
            print(f"   Version del sistema obtenida: {version}")

            print("   Intentando usar TTS...")
            tts_service = session_instance.service("ALTextToSpeech")
            tts_service.say("Prueba de conexion desde linea de comandos exitosa.")
            print("   Prueba de TTS completada.")

        except Exception as e:
            print(f"   ERROR durante la prueba de servicios post-conexion: {e}")
        finally:
            # Siempre intenta detener la aplicacion al finalizar las pruebas
            print("-> Deteniendo la aplicacion de prueba...")
            app_instance.stop()
            print("-> Aplicacion de prueba detenida.")
    else:
        print("\n-> La prueba de conexion del modulo fallo.")
        sys.exit(1)
