# ----------------------------------------------------------------------------------
# Titular: Definicion de Endpoints (Rutas) de la API del Servidor
# Funcion Principal: Este modulo define todos los endpoints (rutas) de la API
#                    para el servidor FastAPI. Utiliza un APIRouter de FastAPI para
#                    mantener las rutas organizadas y separadas del archivo principal
#                    del servidor (ServerWeb.py). Los endpoints gestionan la
#                    comunicacion con el frontend (la tablet).
#                    El diseno se basa en delegar la logica de negocio a una
#                    instancia de 'TabletServerInterface' que se inyecta a traves
#                    del estado de la aplicacion (app.state), manteniendo los
#                    endpoints limpios y enfocados en la gestion de la comunicacion.
# ----------------------------------------------------------------------------------

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.requests import Request
import logging

# Importacion relativa del modulo Messages, que contiene helpers para el formato de los mensajes JSON.
from . import Messages as messages
from typing import Any # Para el type hint de la interfaz del servidor

# Configuracion del logger para este modulo
log = logging.getLogger(__name__)

# Crea una instancia de APIRouter para agrupar los endpoints de esta seccion de la API.
# Este router sera luego incluido en la aplicacion principal de FastAPI en ServerWeb.py.
router = APIRouter()

# Endpoint HTTP GET para verificar el estado y la salud del servidor.
# Proporciona informacion basica como si la interfaz principal esta cargada
# y el numero de clientes WebSocket activos.
# Es util para diagnostico y monitoreo.
@router.get("/status", tags=["General"])
async def get_status(request: Request):
    log.info("Solicitud recibida en el endpoint /status")
    # Accede a la instancia de la interfaz principal a traves del estado de la aplicacion
    interface_instance = getattr(request.app.state, 'tablet_server_interface', None)
    interface_available = interface_instance is not None
    active_connections_count = 0
    # Si la interfaz existe, obtiene el numero de clientes conectados
    if interface_available and hasattr(interface_instance, '_active_connections'):
        active_connections_count = len(interface_instance._active_connections)
    return {
        "status_api": "Endpoints de UmebotLogics activos",
        "service_port": getattr(request.app.state, 'actual_server_port', 'N/A'),
        "tablet_interface_loaded": interface_available,
        "active_websocket_clients": active_connections_count,
    }

# Endpoint WebSocket para la comunicacion bidireccional en tiempo real con los clientes (frontend).
# Gestiona el ciclo de vida de la conexion de cada cliente:
# 1. Acepta la conexion y registra al cliente en la interfaz principal.
# 2. Entra en un bucle para recibir mensajes de texto del cliente.
# 3. Deserializa cada mensaje y delega su procesamiento a la interfaz principal.
# 4. Maneja la desconexion y los errores de forma limpia, desregistrando al cliente.
@router.websocket("/ws_bidirectional")
async def websocket_bidirectional_endpoint(websocket: WebSocket):
    server_interface: Any
    try:
        # Obtiene la instancia de la interfaz principal del servidor desde el estado de la app FastAPI.
        # Esta instancia contiene la logica principal y es inyectada por el lanzador de la aplicacion (ej. TabletInterface.py).
        server_interface = websocket.app.state.tablet_server_interface
        if server_interface is None: raise AttributeError("TabletServerInterface no encontrada en el estado de la aplicacion.")
    except AttributeError:
        # Si la interfaz no esta configurada, es un error critico del servidor.
        log.error("CRITICO: TabletServerInterface no configurada en app.state. La funcionalidad del WebSocket estara deshabilitada.")
        await websocket.accept() # Acepta la conexion para poder enviar un mensaje de error
        await websocket.send_text(messages.craft_system_message("Servidor", "error", "Error interno critico del servidor de logica. No se pueden procesar mensajes."))
        await websocket.close(code=1011) # Cierra la conexion con un codigo de error interno
        return

    await websocket.accept() # Acepta la conexion del cliente WebSocket
    log.info(f"Cliente WebSocket CONECTADO: {websocket.client.host}:{websocket.client.port} al endpoint /ws_bidirectional")
    # Notifica a la interfaz principal que un nuevo cliente se ha conectado, para que pueda gestionarlo.
    await server_interface.register_client_connection(websocket)

    try:
        # Bucle infinito para recibir mensajes mientras la conexion este activa
        while True:
            # Espera a recibir un mensaje de texto del cliente
            data_from_client_text = await websocket.receive_text()
            log.debug(f"WS_RAW_RECV ({websocket.client}): '{data_from_client_text}'")
            try:
                # Utiliza el modulo Messages para deserializar el string JSON recibido en un diccionario Python.
                client_msg_dict = messages.deserialize_client_message(data_from_client_text)
                # Delega el manejo del mensaje deserializado a la interfaz principal.
                # Pasa el tipo, el payload y la instancia del websocket para que la interfaz sepa que hacer y a quien responder si es necesario.
                await server_interface.handle_client_message(
                    client_msg_dict.get("type"),
                    client_msg_dict.get("payload"),
                    websocket
                )
            except ValueError as e_val: # Error si el mensaje no es un JSON valido o no tiene el formato esperado
                log.error(f"Mensaje invalido recibido de {websocket.client}: {e_val}")
                error_msg = messages.craft_system_message("Servidor", "error", f"Mensaje con formato invalido: {e_val}")
                await websocket.send_text(error_msg) # Envia un mensaje de error al cliente
            except Exception as e_proc: # Cualquier otro error durante el procesamiento del mensaje
                log.error(f"Error procesando mensaje de {websocket.client}: {e_proc}", exc_info=True)
                error_msg = messages.craft_system_message("Servidor", "error", "Error interno del servidor al procesar el mensaje.")
                await websocket.send_text(error_msg)
    except WebSocketDisconnect: # Se activa cuando el cliente cierra la conexion de forma normal
        log.info(f"Cliente WebSocket {websocket.client} DESCONECTADO (cierre normal).")
    except Exception as e_ws: # Cualquier otra excepcion inesperada en la conexion WebSocket
        log.error(f"Excepcion en la conexion WebSocket con {websocket.client}: {e_ws}", exc_info=True)
    finally:
        # Este bloque se ejecuta siempre, ya sea por desconexion normal o por error.
        # Asegura que el cliente sea desregistrado de la interfaz principal para evitar conexiones "fantasma".
        log.info(f"Desregistrando cliente WebSocket {websocket.client} del manejador (bloque finally).")
        await server_interface.unregister_client_connection(websocket)

log.info("Modulo Endpoints.py cargado. El router con los endpoints /status y /ws_bidirectional esta definido y listo para ser incluido.")
