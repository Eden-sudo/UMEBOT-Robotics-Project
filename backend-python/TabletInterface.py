# ----------------------------------------------------------------------------------
# Titular: Interfaz del Servidor de la Tablet (Gestor del Servidor Web)
# Funcion Principal: Este modulo define la clase TabletServerInterface, que actua
#                    como el puente de comunicacion de alto nivel entre el backend
#                    y el frontend (la UI de la tablet). Su funcion es encapsular
#                    y gestionar el ciclo de vida del servidor web FastAPI/Uvicorn.
#                    Implementa un patron Singleton para asegurar que solo exista una
#                    instancia de esta interfaz en toda la aplicacion.
#                    Provee metodos para enviar mensajes estandarizados a la UI
#                    y un sistema de callbacks para que un orquestador (como
#                    Init_System.py) pueda conectar los mensajes recibidos de la
#                    UI con la logica de negocio correspondiente (ej. procesar
#                    entradas de chat, cambios de configuracion o datos del gamepad).
# ----------------------------------------------------------------------------------

import asyncio
import uvicorn
import logging
import os
from typing import Callable, Any, Dict, Optional, Set, Literal, Coroutine, List

# Configuracion de logging basica si no hay un manejador configurado.
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger("TabletInterface")

# Importacion de modulos del subdirectorio 'tabletserver'.
try:
    from tabletserver import Messages as messages # Modulo con el protocolo de mensajes.
    from tabletserver.ServerWeb import app as fastapi_app # Instancia de la aplicacion FastAPI.
except ImportError as e:
    log.critical(f"Error importando desde 'tabletserver'. Asegurate que TabletInterface.py este en el directorio padre y que PYTHONPATH sea correcto. Error: {e}", exc_info=True)
    raise

# Gestiona el servidor web y actua como la interfaz principal para la comunicacion
# con los clientes (UI de la tablet). Implementada como un Singleton.
class TabletServerInterface:
    _instance = None # Atributo de clase para almacenar la unica instancia (Singleton)

    # Implementacion del patron Singleton para asegurar una unica instancia.
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(TabletServerInterface, cls).__new__(cls)
        return cls._instance

    # Inicializa la interfaz del servidor.
    # Es seguro llamar al constructor multiples veces, ya que solo se inicializara una vez.
    # Inyecta su propia instancia en el estado de la aplicacion FastAPI para que
    # los endpoints puedan acceder a sus metodos.
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        # Evita la re-inicializacion si la instancia ya fue creada
        if hasattr(self, '_initialized') and self._initialized:
            if self.host != host or self.port != port:
                log.warning(f"La interfaz ya fue inicializada con {self.host}:{self.port}. Se ignoraran los nuevos parametros {host}:{port}.")
            return

        self.host = host
        self.port = port
        self.app = fastapi_app # Referencia a la instancia de la aplicacion FastAPI

        # Inyecta datos en el estado de la aplicacion FastAPI para que sean accesibles desde los endpoints.
        self.app.state.actual_server_host = self.host
        self.app.state.actual_server_port = self.port
        self.app.state.tablet_server_interface = self # La mas importante: los endpoints acceden a esta misma instancia.

        self._active_connections: Set[Any] = set() # Conjunto de clientes WebSocket activos
        self._connections_lock = asyncio.Lock()    # Lock para gestionar el acceso concurrente a las conexiones
        self._server_task: Optional[asyncio.Task] = None # Tarea asincrona donde se ejecuta el servidor
        self._running = False # Bandera para indicar si el servidor esta activo

        # Callbacks que el orquestador principal (ej. Init_System) configurara para conectar la logica.
        self.on_input_received: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None
        self.on_config_received: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None
        self.on_client_connected_callback: Optional[Callable[[Any], Coroutine[Any, Any, None]]] = None
        self.on_client_disconnected_callback: Optional[Callable[[Any], Coroutine[Any, Any, None]]] = None
        # Callbacks especificos para los datos del gamepad.
        self.on_gamepad_payload_received: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None
        self.on_gamepad_emergency_stop: Optional[Callable[[], Coroutine[Any, Any, None]]] = None

        # Almacenamiento local del estado de la configuracion actual (opcional, para referencia).
        self.current_app_settings: Dict[str, Any] = {
            "stt_audio_source": "robot", "ai_personality": "ume_asistente", "ai_model_backend": "openai_gpt"
        }
        self._initialized = True
        log.info(f"TabletServerInterface inicializada y lista para escuchar en http://{self.host}:{self.port}")

    # --- Metodos de Gestion de Conexiones de Clientes ---

    # Registra una nueva conexion de cliente WebSocket y ejecuta el callback on_client_connected.
    async def register_client_connection(self, websocket: Any):
        async with self._connections_lock:
            self._active_connections.add(websocket)
        client_repr = getattr(websocket, 'client', 'Cliente Desconocido')
        log.info(f"Cliente WebSocket conectado: {client_repr}. Total de conexiones activas: {len(self._active_connections)}")
        # Notifica al orquestador que un nuevo cliente se ha conectado.
        if self.on_client_connected_callback:
            asyncio.create_task(self.on_client_connected_callback(websocket))

    # Desregistra una conexion de cliente WebSocket cuando se cierra.
    async def unregister_client_connection(self, websocket: Any):
        async with self._connections_lock:
            self._active_connections.discard(websocket)
        client_repr = getattr(websocket, 'client', 'Cliente Desconocido')
        log.info(f"Cliente WebSocket desconectado: {client_repr}. Total de conexiones activas: {len(self._active_connections)}")
        # Notifica al orquestador que un cliente se ha desconectado.
        if self.on_client_disconnected_callback:
            asyncio.create_task(self.on_client_disconnected_callback(websocket))

    # --- Metodos para Enviar Mensajes a los Clientes (Backend -> Frontend) ---

    # Metodo auxiliar interno para enviar un mensaje a todos los clientes WebSocket conectados de forma concurrente.
    async def _broadcast_message(self, message_str: str):
        if not self._active_connections: return

        async with self._connections_lock:
            current_connections = list(self._active_connections) # Crea una copia para evitar problemas de concurrencia

        if not current_connections: return

        # Envia el mensaje a todos los clientes simultaneamente y maneja excepciones
        results = await asyncio.gather(*(ws.send_text(message_str) for ws in current_connections), return_exceptions=True)

        # Limpia las conexiones que fallaron durante el envio
        disconnected_during_send = [current_connections[i] for i, result in enumerate(results) if isinstance(result, Exception)]
        if disconnected_during_send:
            async with self._connections_lock:
                for ws_to_remove in disconnected_during_send:
                    self._active_connections.discard(ws_to_remove)
            log.info(f"Clientes eliminados por error en broadcast: {len(disconnected_during_send)}. Total activos ahora: {len(self._active_connections)}")

    # Construye y envia un mensaje de tipo 'input' a todos los clientes.
    async def send_active_input_display(self, text: str, source: Literal["gui_manual", "stt_auto"]):
        await self._broadcast_message(messages.craft_input_echo_message(text, source))
        log.info(f"Enviado mensaje 'input' a clientes (historial): (Fuente: {source}, Texto: '{text[:30]}...')")

    # Construye y envia un mensaje de tipo 'output' a todos los clientes.
    async def send_output(self, sender_name: str, text: str, original_input_source: Literal["gui_manual", "stt_auto", "unknown"] = "unknown"):
        await self._broadcast_message(messages.craft_output_message(sender_name, text, original_input_source))
        log.info(f"Enviado mensaje 'output' a clientes: (Remitente: {sender_name}, Texto: '{text[:30]}...')")

    # Construye y envia un mensaje de tipo 'system' a todos los clientes.
    async def send_system_message(self, sender_name: str, level: Literal["info", "warning", "error"], text: str, detail: Optional[Dict] = None):
        await self._broadcast_message(messages.craft_system_message(sender_name, level, text, detail))
        log.info(f"Enviado 'system_message' a clientes: (Nivel: {level}, Texto: '{text[:30]}...')")

    # Envia la configuracion actual a un cliente especifico (usualmente, al que acaba de conectar).
    async def send_current_configuration_to_specific_client(self, websocket: Any, config_settings: Dict[str, Any]):
        client_repr = getattr(websocket, 'client', 'Cliente Desconocido')
        log.debug(f"Enviando configuracion {config_settings} al cliente especifico {client_repr}")
        try:
            await websocket.send_text(messages.craft_current_configuration_message(config_settings))
            log.info(f"Configuracion actual del sistema enviada al cliente {client_repr}.")
        except Exception as e:
            log.error(f"Error enviando la configuracion actual al cliente {client_repr}: {e}", exc_info=True)

    # Construye y envia una confirmacion de cambio de configuracion a todos los clientes.
    async def send_config_confirmation(self, config_item: str, success: bool, current_value: Any, message_to_display: str):
        await self._broadcast_message(messages.craft_config_confirmation_message(config_item, success, current_value, message_to_display))
        log.info(f"Enviado 'config_confirmation': (Item: {config_item}, Exito: {success}, ValorActual: {current_value})")

    # Construye y envia un resultado parcial o final de STT a todos los clientes.
    async def send_partial_stt_result(self, partial_text: str, is_final: bool):
        await self._broadcast_message(messages.craft_partial_stt_result_message(partial_text, is_final))
        log.debug(f"Enviado 'partial_stt_result' a clientes: (Texto: '{partial_text[:30]}...', EsFinal: {is_final})")

    # --- Metodo para Manejar Mensajes Entrantes (Frontend -> Backend) ---

    # Metodo central para manejar los mensajes entrantes de los clientes.
    # Actua como un despachador (dispatcher): segun el tipo de mensaje,
    # invoca el callback correspondiente que fue configurado por el orquestador principal.
    async def handle_client_message(self, message_type: str, payload: Dict[str, Any], websocket: Any):
        client_repr = getattr(websocket, 'client', 'Cliente Desconocido')
        log.info(f"Recibido de {client_repr}: Tipo='{message_type}', Payload='{str(payload)[:100]}...'")

        if message_type == messages.MSG_TYPE_INPUT:
            if self.on_input_received: asyncio.create_task(self.on_input_received(payload))
            else: log.warning("Callback 'on_input_received' no esta configurado en TabletInterface.")
        elif message_type == messages.MSG_TYPE_CONFIG:
            if self.on_config_received: asyncio.create_task(self.on_config_received(payload))
            else: log.warning("Callback 'on_config_received' no esta configurado en TabletInterface.")
        elif message_type == messages.MSG_TYPE_GAMEPAD_STATE:
            # Prioridad 1: Parada de Emergencia
            stick_buttons = payload.get("stick_button_states", {})
            if stick_buttons.get("l3_pressed", False) or stick_buttons.get("r3_pressed", False):
                log.warning(f"¡PARADA DE EMERGENCIA (L3/R3) solicitada por el cliente {client_repr}!")
                if self.on_gamepad_emergency_stop: asyncio.create_task(self.on_gamepad_emergency_stop())
                else: log.error("Callback 'on_gamepad_emergency_stop' no configurado. ¡NO SE PUEDE EJECUTAR EL E-STOP!")
            else: # Si no es E-STOP, pasa el payload completo al manejador de gamepad
                if self.on_gamepad_payload_received: asyncio.create_task(self.on_gamepad_payload_received(payload))
                else: log.warning("Callback 'on_gamepad_payload_received' no configurado. Los datos del gamepad seran ignorados.")
        else:
            log.warning(f"Tipo de mensaje '{message_type}' desconocido o no manejable recibido de {client_repr}.")

    # --- Metodos de Ciclo de Vida del Servidor ---

    # Inicia el servidor Uvicorn de forma asincrona en una tarea de segundo plano.
    async def start_server(self):
        if self._running:
            log.info("El servidor de TabletInterface ya esta corriendo."); return
        uvicorn_log_level = logging.getLevelName(log.getEffectiveLevel()).lower()
        if uvicorn_log_level in ["notset", "debug"]: uvicorn_log_level_actual_for_uvicorn = "info"
        else: uvicorn_log_level_actual_for_uvicorn = uvicorn_log_level
        # Crea la configuracion para el servidor Uvicorn
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level=uvicorn_log_level_actual_for_uvicorn)
        # Guarda la instancia del servidor para poder llamarle should_exit mas tarde
        self.server_instance_uvicorn = uvicorn.Server(config)
        log.info(f"Iniciando servidor Uvicorn para TabletInterface en http://{self.host}:{self.port} (Log Uvicorn: '{uvicorn_log_level_actual_for_uvicorn}')...")
        # Crea y ejecuta la tarea del servidor en el bucle de eventos de asyncio
        self._server_task = asyncio.create_task(self.server_instance_uvicorn.serve())
        self._running = True
        log.info("Servidor Uvicorn (TabletInterface) iniciado y corriendo en segundo plano.")

    # Detiene el servidor Uvicorn de forma ordenada.
    async def stop_server(self):
        if not self._running or not self._server_task:
            log.info("El servidor de TabletInterface no esta corriendo o la tarea no existe."); return
        log.info("Intentando detener el servidor Uvicorn (TabletInterface)...")
        # Pide a Uvicorn que salga de su bucle principal de forma ordenada
        if hasattr(self, 'server_instance_uvicorn') and self.server_instance_uvicorn:
            self.server_instance_uvicorn.should_exit = True
        else: # Fallback por si la instancia del servidor no se guardo
            self.server_task.cancel()
        try:
            await asyncio.wait_for(self._server_task, timeout=5.0) # Espera a que la tarea termine
        except asyncio.CancelledError: log.info("La tarea del servidor Uvicorn (TabletInterface) fue cancelada limpiamente.")
        except asyncio.TimeoutError: log.warning("Timeout esperando la detencion del servidor Uvicorn. Puede que no haya cerrado completamente.")
        except Exception as e: log.error(f"Excepcion durante la espera de la detencion del servidor Uvicorn: {e}", exc_info=True)
        finally:
            self._running = False; self._server_task = None
            log.info("Proceso de detencion del servidor Uvicorn (TabletInterface) completado.")

# --- Funcion de Fabrica para el Singleton ---
# Esta funcion asegura que solo se cree una unica instancia de TabletServerInterface en toda la aplicacion.
_tablet_server_interface_instance: Optional[TabletServerInterface] = None
def get_tablet_server_interface(host: str = "0.0.0.0", port: int = 8080) -> TabletServerInterface:
    global _tablet_server_interface_instance
    if _tablet_server_interface_instance is None:
        log.debug(f"Creando la unica instancia de TabletServerInterface ({host}:{port}) a traves de get_tablet_server_interface.")
        _tablet_server_interface_instance = TabletServerInterface(host=host, port=port)
    return _tablet_server_interface_instance

# --- Bloque de prueba standalone ---
if __name__ == "__main__":
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s')

    log_main_demo = logging.getLogger("TabletInterfaceDemo")
    log_main_demo.info("--- Iniciando DEMO Standalone de TabletServerInterface ---")

    demo_host = os.getenv("TABLET_IF_DEMO_HOST", "0.0.0.0")
    demo_port = int(os.getenv("TABLET_IF_DEMO_PORT", 8089))

    server_if = get_tablet_server_interface(host=demo_host, port=demo_port)

    # Callbacks de ejemplo para la demo
    async def demo_on_connect(ws: Any):
        client_repr = getattr(ws, 'client', 'Cliente Desconocido')
        log_main_demo.info(f"[DEMO CB] Cliente {client_repr} CONECTADO. Enviando config inicial.")
        await server_if.send_current_configuration_to_specific_client(ws, server_if.current_app_settings)
        await server_if.send_system_message("DemoServidor", "info", f"¡Bienvenido {client_repr} al servidor demo!")

    async def demo_on_input(payload: Dict[str, Any]):
        txt = payload.get("text", "Texto no encontrado en payload")
        src = payload.get("source", "desconocida")
        log_main_demo.info(f"[DEMO CB] Input GUI recibido: (Fuente: {src}, Texto: '{txt}'). Haciendo eco y simulando respuesta.")
        await server_if.send_active_input_display(txt, "gui_manual") # type: ignore
        await asyncio.sleep(0.2)
        await server_if.send_output("UmebotDemo", f"He procesado tu input desde '{src}': '{txt}'", original_input_source=src) # type: ignore

    async def demo_on_config(payload: Dict[str, Any]):
        item = payload.get("config_item", "N/A")
        val = payload.get("value", "N/A")
        log_main_demo.info(f"[DEMO CB] Config GUI recibida: Cambiar '{item}' a '{val}'. Actualizando y confirmando.")
        if item in server_if.current_app_settings:
            server_if.current_app_settings[item] = val
        await server_if.send_config_confirmation(
            config_item=item, success=True, current_value=val,
            message_to_display=f"Configuración DEMO '{item}' actualizada a '{val}' en el servidor."
        )
    
    # --- Callbacks de demo para gamepad ---
    async def demo_on_gamepad_payload(payload: Dict[str, Any]):
        log_main_demo.info(f"[DEMO CB - GAMEPAD] Payload de gamepad recibido: {str(payload)[:200]}...")
        # Aquí, en una implementación real, este payload se encolaría en MotionGamePad
        # Para la demo, solo hacemos un log.
        # Podríamos enviar una confirmación simple de vuelta si quisiéramos.
        # await server_if.send_system_message("DemoServidor", "info", "Payload de gamepad recibido para procesamiento normal.")


    async def demo_on_gamepad_estop():
        log_main_demo.warning("[DEMO CB - GAMEPAD] ¡PARADA DE EMERGENCIA DE GAMEPAD SOLICITADA!")
        # En una implementación real, esto detendría el robot.
        # await server_if.send_system_message("DemoServidor", "error", "¡PARADA DE EMERGENCIA ACTIVADA!")

    # Asignar callbacks a la instancia del servidor
    server_if.on_client_connected_callback = demo_on_connect
    server_if.on_input_received = demo_on_input
    server_if.on_config_received = demo_on_config
    server_if.on_gamepad_payload_received = demo_on_gamepad_payload # <--- Asignar nuevo callback
    server_if.on_gamepad_emergency_stop = demo_on_gamepad_estop   # <--- Asignar nuevo callback

    async def demo_runner():
        await server_if.start_server()
        while server_if._running:
            await asyncio.sleep(1)

    try:
        asyncio.run(demo_runner())
    except KeyboardInterrupt:
        log_main_demo.info("[DEMO] Interrupción por teclado. Deteniendo servidor demo...")
    except Exception as e_demo_run_main:
        log_main_demo.critical(f"Error fatal en el bucle principal de la demo: {e_demo_run_main}", exc_info=True)
    finally:
        if server_if._running:
            log_main_demo.info("[DEMO] Asegurando detención del servidor en finally...")
            loop = asyncio.get_event_loop()
            if loop.is_running():
                async def stop_in_new_loop_if_needed(): # Helper para el finally
                    await server_if.stop_server()

                try:
                    # Si el loop principal ya no está, esto podia fallar o no hacer nada.
                    # El stop_server en sí mismo espera a una tarea.
                    asyncio.create_task(stop_in_new_loop_if_needed()) # Intentar detener
                except RuntimeError: # "cannot schedule new futures after shutdown"
                    log_main_demo.warning("No se pudo programar stop_server en finally (loop de eventos posiblemente cerrado).")


        log_main_demo.info("--- DEMO Standalone de TabletServerInterface Finalizada ---")

