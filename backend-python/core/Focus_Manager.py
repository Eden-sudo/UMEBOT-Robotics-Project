# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Gestor Conceptual del Foco del Robot (Servicio 'Focus' NAOqi)
# Funcion Principal: Define la clase FocusManager, una implementacion conceptual
#                    y de desarrollo para interactuar con el servicio 'Focus' de
#                    NAOqi. Sirve para documentar la gestion del foco y la
#                    priorizacion de recursos, especialmente si se considera usar
#                    la Vida Autonoma del robot. Incluye introspeccion detallada
#                    del servicio y un modo de prueba.
#                    Este modulo no se utiliza normalmente en produccion cuando
#                    la Vida Autonoma esta desactivada para un control exhaustivo.
# ----------------------------------------------------------------------------------

import qi
import sys
import inspect
import time
import session_manager # Modulo para gestionar la conexion con el robot
import argparse      # Para procesar argumentos de linea de comandos

# Gestiona la adquisicion, verificacion y liberacion del foco del servicio
# 'Focus' de NAOqi. Incluye metodos para introspeccion del servicio y
# del 'handle' de foco, asi como manejo de senales de perdida de foco.
class FocusManager:
    # Inicializa el FocusManager con la aplicacion y sesion qi.
    # Intenta obtener un proxy al servicio 'Focus' y realiza una
    # introspeccion inicial del mismo.
    #
    # Args:
    #   app (qi.Application): La instancia de la aplicacion qi.
    #   session (qi.Session): La sesion qi conectada.
    #
    # Raises:
    #   ValueError: Si la aplicacion o la sesion no son validas o no estan conectadas.
    #   RuntimeError: Si no se puede obtener el servicio 'Focus'.
    def __init__(self, app, session):
        print("\n--- [FocusManager] Inicializando ---")
        if not app or not session or not session.isConnected():
            raise ValueError("Se requiere una app y una sesion conectada validas.")

        self.app = app
        self.session = session
        self.focus_service = None
        self._handle = None # Objeto devuelto por el metodo take() del servicio Focus
        self._release_signal_id = None # ID de la conexion a la senal 'released' del handle
        self._is_focused = False # Estado interno que rastrea si se tiene el foco

        try:
            print("[FocusManager] Obteniendo proxy al servicio 'Focus'...")
            self.focus_service = self.session.service("Focus")
            print("[FocusManager] Proxy a 'Focus' obtenido con exito.")

            # Realiza introspeccion inicial del servicio Focus para entender su API
            self._inspect_service()

        except RuntimeError as e:
            print(f"ERROR [FocusManager]: No se pudo obtener el servicio 'Focus': {e}")
            raise
        except Exception as e:
            print(f"ERROR [FocusManager]: Error inesperado al obtener 'Focus': {e}")
            raise

    # Realiza e imprime en consola una introspeccion basica del servicio 'Focus',
    # mostrando sus metodos y atributos disponibles mediante dir().
    def _inspect_service(self):
        if not self.focus_service:
            return
        print("\n--- [FocusManager] Introspeccion del Servicio 'Focus' ---")
        try:
            print(">>> Metodos/Atributos (dir):")
            print(dir(self.focus_service))
            # Opcional: Usar inspect.getmembers para mas detalle de los miembros del servicio.
            # print("\n>>> Miembros Detallados (inspect.getmembers):")
            # members = inspect.getmembers(self.focus_service)
            # for name, member_type in members:
            #     print(f"  {name}: {member_type}")
            print("----------------------------------------------------")
        except Exception as e:
            print(f"WARN [FocusManager]: Error durante introspeccion del servicio: {e}")

    # Realiza e imprime en consola una introspeccion del 'handle' (objeto)
    # devuelto por el metodo take() del servicio 'Focus'. Muestra el tipo
    # del handle y sus metodos/atributos disponibles mediante dir().
    def _inspect_handle(self):
        if not self._handle:
            return
        print("\n--- [FocusManager] Introspeccion del Handle del Foco ---")
        try:
            print(f">>> Tipo del Handle: {type(self._handle)}")
            print("\n>>> Metodos/Atributos del Handle (dir):")
            print(dir(self._handle))
            # Opcional: Usar inspect.getmembers para mas detalle de los miembros del handle.
            print("-------------------------------------------------")
        except Exception as e:
            print(f"WARN [FocusManager]: Error durante introspeccion del handle: {e}")

    # Callback que se ejecuta cuando se recibe la senal 'released' del handle de foco.
    # Indica que el foco ha sido perdido o liberado por otra entidad.
    # Actualiza el estado interno del FocusManager.
    def _handle_focus_lost(self, *args):
        print(f"\n*** [FocusManager] SENAL 'released' RECIBIDA! Foco perdido. Args: {args} ***")
        self._is_focused = False
        self._handle = None # Invalida el handle actual ya que el foco se perdio
        self._release_signal_id = None # Marca la senal como desconectada

    # Intenta suscribirse a la senal 'released' emitida por el 'handle' de foco.
    # Esta senal notifica la perdida del foco. La suscripcion se realiza
    # conectando el callback _handle_focus_lost a la senal.
    def _subscribe_to_release_signal(self):
        if not self._handle or self._release_signal_id is not None:
            if self._release_signal_id is not None:
                print("[FocusManager] INFO: Ya suscrito a la senal 'released'.")
            elif not self._handle:
                print("[FocusManager] WARN: No hay handle para suscribir a senal.")
            return

        print("\n--- [FocusManager] Intentando suscribirse a senal 'released' en Handle ---")
        try:
            # Basado en introspeccion, handle.released es la senal.
            # Se intenta conectar directamente a esta senal.
            print("   Probando metodo: handle.released.connect(callback)")
            # Llamada directa al metodo de conexion de la senal
            self._release_signal_id = self._handle.released.connect(self._handle_focus_lost)
            print(f"   ¡EXITO! Suscripcion a senal 'released' realizada (ID: {self._release_signal_id}).")

        except AttributeError as e:
            print(f"   ERROR [FocusManager]: AttributeError al intentar suscribir - ¿'released' o '.connect' no existen o son incorrectos? -> {e}")
            self._release_signal_id = None
        except Exception as e:
            print(f"   ERROR [FocusManager]: Fallo el intento de suscripcion a 'released': {e}")
            self._release_signal_id = None

    # Intenta desuscribirse de la senal 'released' del 'handle' de foco.
    # Utiliza el ID de conexion guardado durante la suscripcion.
    def _unsubscribe_from_release_signal(self):
        # Se usa el ID de conexion previamente guardado
        signal_id_to_disconnect = self._release_signal_id
        if not signal_id_to_disconnect:
            return

        print("\n--- [FocusManager] Intentando desuscribirse de la senal 'released' ---")
        # Marca la senal como desconectada localmente de inmediato
        self._release_signal_id = None

        # El handle es necesario para acceder al objeto senal 'released' y desconectar.
        if not self._handle:
            print("   WARN: El handle ya no es valido/existente, no se puede acceder a .released para desconectar explicitamente.")
            return

        try:
            # Llamada directa al metodo de desconexion de la senal
            print(f"   Probando metodo: handle.released.disconnect({signal_id_to_disconnect})")
            self._handle.released.disconnect(signal_id_to_disconnect)
            print("   ¡EXITO! Desuscripcion de senal 'released' realizada.")
        except AttributeError as e:
            print(f"   ERROR [FocusManager]: AttributeError al intentar desuscribir - ¿'released' o '.disconnect' no existen o son incorrectos? -> {e}")
        except Exception as e:
            print(f"   WARN [FocusManager]: Error al intentar desuscribirse de 'released': {e}")

    # Intenta adquirir el foco del servicio 'Focus' llamando a su metodo take().
    # Si la adquisicion es exitosa, realiza una introspeccion del 'handle'
    # obtenido, verifica el foco y se suscribe a la senal 'released'.
    #
    # Args:
    #   identifier (str): Un nombre identificador para este cliente de foco.
    #
    # Returns:
    #   bool: True si el foco se adquirio y verifico correctamente, False en caso contrario.
    def acquire_focus(self, identifier="PythonFocusClient"):
        if not self.focus_service:
            print("ERROR [FocusManager]: Servicio Focus no disponible.")
            return False
        if self._is_focused:
            print("WARN [FocusManager]: Ya se tiene el foco.")
            return True

        print(f"\n--- [FocusManager] Adquiriendo Foco (Identificador: '{identifier}') ---")
        try:
            print(f"   LLAMANDO: focus_service.take('{identifier}')")
            start_time = time.time()
            self._handle = self.focus_service.take(identifier) # Solicita el foco al servicio
            end_time = time.time()
            print(f"   RECIBIDO: Handle = {self._handle} (Tipo: {type(self._handle)})")
            print(f"   Tiempo de llamada take(): {end_time - start_time:.4f} seg")

            if self._handle:
                self._inspect_handle() # Inspecciona el handle recibido
                if self.check_focus(): # Verifica si el foco es valido
                    self._subscribe_to_release_signal() # Se suscribe a la senal de perdida de foco
                    self._is_focused = True # Se marca internamente que se tiene el foco
                    return True
                else:
                    print("ERROR [FocusManager]: take() devolvio un handle, pero check() fallo.")
                    self._handle = None
                    self._is_focused = False
                    return False
            else:
                print("ERROR [FocusManager]: take() no devolvio un handle valido (None).")
                self._is_focused = False
                return False

        except AttributeError as e:
            print(f"   ERROR [FocusManager]: Parece que el metodo 'take' no existe o el nombre es incorrecto: {e}")
            return False
        except Exception as e:
            print(f"   ERROR [FocusManager]: Excepcion al llamar a focus_service.take(): {e}")
            self._handle = None
            self._is_focused = False
            return False

    # Verifica si el 'handle' de foco actual (si existe) sigue siendo valido
    # llamando al metodo check() del servicio 'Focus'.
    # Actualiza el estado interno de si se tiene el foco.
    #
    # Returns:
    #   bool: True si el foco es valido, False en caso contrario.
    def check_focus(self):
        if not self.focus_service:
            print("ERROR [FocusManager]: Servicio Focus no disponible.")
            self._is_focused = False
            return False
        if not self._handle: # Si no hay handle, no hay foco que verificar
            self._is_focused = False
            return False

        try:
            is_valid = self.focus_service.check(self._handle) # Consulta al servicio si el handle es valido
            self._is_focused = bool(is_valid)
            return self._is_focused
        except AttributeError as e:
            print(f"   ERROR [FocusManager]: Parece que el metodo 'check' no existe o el nombre es incorrecto: {e}")
            self._is_focused = False
            return False
        except Exception as e:
            print(f"   ERROR [FocusManager]: Excepcion al llamar a focus_service.check(): {e}")
            self._is_focused = False
            return False

    # Intenta liberar el foco manualmente llamando al metodo release() del 'handle'.
    # Antes de liberar, se desuscribe de la senal 'released'.
    # Actualiza el estado interno independientemente del exito de la llamada remota.
    def release_focus(self):
        if not self._handle:
            # El foco ya esta liberado o nunca se adquirio
            return

        print("\n--- [FocusManager] Intentando Liberar Foco Manualmente ---")

        # Primero, intentar desuscribir la senal 'released' para evitar callbacks innecesarios o errores.
        self._unsubscribe_from_release_signal()

        # Luego, intentar la liberacion del foco via handle.release()
        try:
            method_name_on_handle = "release"
            if hasattr(self._handle, method_name_on_handle) and callable(getattr(self._handle, method_name_on_handle)):
                print(f"   Probando liberacion via handle.{method_name_on_handle}()...")
                getattr(self._handle, method_name_on_handle)() # Invoca el metodo release() del handle
                print(f"   Llamada a handle.{method_name_on_handle} realizada.")
                # La senal 'released' deberia dispararse, y el callback actualizara el estado.
            else:
                print(f"   WARN: Metodo '{method_name_on_handle}' no encontrado en el handle (Inesperado!).")
                # Si no se puede llamar a release(), se invalida el estado localmente.
                self._handle = None
                self._is_focused = False

        except Exception as e:
            print(f"   ERROR [FocusManager]: Excepcion durante el intento de liberacion manual (handle.release): {e}")
            # Invalida el estado local si la llamada remota falla.
            self._handle = None
            self._is_focused = False
        finally:
            # Asegura que el estado local refleje el intento de liberacion.
            # Nota: _handle_focus_lost (si es llamado por la senal) tambien actualiza _handle.
            # y _is_focused a False. Esta actualizacion aqui es redundante pero segura.
            if self._is_focused: # Si el callback no se ejecuto inmediatamente tras llamar a release().
                print("   Invalidando estado de foco local despues del intento de release.")
                self._is_focused = False
            if self._handle: # Asegurarse de que el handle se limpie si release() no lo hizo (o no se llamo el callback)
                self._handle = None

    # Devuelve el estado de foco conocido localmente por el FocusManager.
    #
    # Returns:
    #   bool: True si el gestor considera que tiene el foco, False en caso contrario.
    def is_focused(self):
        return self._is_focused

    # Realiza tareas de limpieza al finalizar el uso del FocusManager.
    # Principalmente, intenta liberar el foco si aun esta activo.
    def shutdown(self):
        print("\n--- [FocusManager] Realizando Shutdown ---")
        if self.is_focused():
            print("   Intentando liberar foco activo durante shutdown...")
            self.release_focus()
        else:
            print("   No hay foco activo que liberar durante shutdown.")

# Bloque de codigo para ejecutar este modulo como un script independiente para pruebas.
if __name__ == "__main__":
    print("*****************************************************")
    print("Ejecutando focus_manager.py como script principal...")
    print("*****************************************************")

    # Configuracion de los argumentos de linea de comandos para la prueba
    parser = argparse.ArgumentParser(description='Prueba FocusManager conectandose a Pepper.')
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

    print(f"\n[Main Script] Conectando al robot ({args.ip}:{args.port})...")
    # Establece la conexion utilizando el modulo session_manager
    app_instance, session_instance = session_manager.connect_robot(
        robot_ip=args.ip,
        port=args.port,
        username=args.username,
        password=args.password
    )

    focus_manager_instance = None
    if app_instance and session_instance:
        try:
            print("\n[Main Script] Creando instancia de FocusManager...")
            focus_manager_instance = FocusManager(app_instance, session_instance)

            print("\n[Main Script] Solicitando adquirir foco...")
            if focus_manager_instance.acquire_focus("TestFocusClient_CmdLine"):
                print("[Main Script] Adquisicion de foco reportada como exitosa.")

                print("\n[Main Script] Manteniendo el foco por 5 segundos (prueba corta)...")
                # Bucle para mantener el foco y verificar si se pierde por senal
                start_wait = time.time()
                while time.time() - start_wait < 5: # Tiempo reducido para prueba rapida
                    if not focus_manager_instance.is_focused():
                        print("\n[Main Script] ¡Detectado que el foco se perdio durante la espera (via senal)!")
                        break
                    time.sleep(0.5)
                if focus_manager_instance.is_focused(): # Verifica si aun se tiene el foco despues de la espera
                    print("[Main Script] Foco mantenido durante la espera.")
            else:
                print("[Main Script] Adquisicion de foco fallo.")

            if focus_manager_instance: # Asegurarse que la instancia se creo antes de intentar liberar
                print("\n[Main Script] Solicitando liberacion de foco...")
                focus_manager_instance.release_focus()

        except Exception as e:
            print(f"\nERROR [Main Script]: Ocurrio un error en la prueba: {e}")
            import traceback
            traceback.print_exc() # Imprime el traceback completo para facilitar la depuracion
        finally:
            if focus_manager_instance:
                print("\n[Main Script] Llamando a shutdown de FocusManager...")
                focus_manager_instance.shutdown() # Asegura la liberacion del foco si aun se tenia

            if app_instance:
                print("\n[Main Script] Deteniendo la aplicacion qi...")
                app_instance.stop()
                print("[Main Script] Aplicacion qi detenida.")
    else:
        print("\n[Main Script] Fallo la conexion inicial. No se puede continuar.")
        sys.exit(1)

    print("\n*****************************************************")
    print("Script de prueba focus_manager.py finalizado.")
    print("*****************************************************")
