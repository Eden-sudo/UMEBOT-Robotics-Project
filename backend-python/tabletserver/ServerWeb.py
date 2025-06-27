#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Servidor Web Principal (FastAPI) para Comunicacion Frontend-Backend
# Funcion Principal: Este modulo crea y configura la instancia principal de la
#                    aplicacion web FastAPI, que actua como el backend para la
#                    comunicacion con el frontend (ej. la interfaz grafica en la
#                    tablet). Define el ciclo de vida de la aplicacion (lifespan)
#                    para registrarse en la red local mediante Zeroconf al iniciar
#                    (permitiendo que el frontend lo descubra automaticamente) y
#                    desregistrarse al apagar.
#                    El servidor no define los endpoints directamente, sino que los
#                    carga dinamicamente desde el modulo 'Endpoints.py', manteniendo
#                    una estructura de codigo modular y organizada.
# ----------------------------------------------------------------------------------

import asyncio
import logging
import socket
import os
import httpx # Cliente HTTP asincrono, para futuras comunicaciones con otros servicios
from fastapi import FastAPI
from contextlib import asynccontextmanager
from typing import Union, Optional
from zeroconf.asyncio import AsyncZeroconf, ServiceInfo # Para descubrimiento de servicios en red

# Configuracion del logger para este modulo
log = logging.getLogger(__name__)

# --- Funciones Auxiliares para Red y Descubrimiento ---

# Obtiene la direccion IP local de la maquina donde se ejecuta el servidor.
# Intenta conectarse a una direccion externa para determinar la IP de la
# interfaz de red correcta; si falla, recurre a metodos de fallback.
# Es crucial para el registro en Zeroconf.
#
# Returns:
#   str: La direccion IP local detectada.
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.1)
    try:
        # Tecnica para obtener la IP preferida conectando a una IP externa (no se envian datos)
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        # Fallback si el primer metodo falla
        try:
            IP = socket.gethostbyname(socket.gethostname())
        except Exception:
            IP = '127.0.0.1' # Ultimo recurso
    finally:
        s.close()
    log.debug(f"IP local detectada para Zeroconf: {IP}")
    return IP

# Obtiene el puerto real en el que Uvicorn esta ejecutando el servidor.
# Este valor se espera que sea inyectado en el estado de la app (app.state.actual_server_port)
# por el script que lanza el servidor (ej. TabletInterface.py).
def get_actual_server_port_for_zeroconf(app_instance: FastAPI) -> int:
    default_port = int(os.getenv("SERVER_PORT_DEFAULT_ZEROCONF", 8080)) # Puerto por defecto de fallback
    actual_port = getattr(app_instance.state, 'actual_server_port', default_port)
    log.debug(f"Zeroconf usara el puerto del servidor: {actual_port}")
    return actual_port

# Registra este servidor como un servicio en la red local usando Zeroconf.
# Esto permite que los clientes (frontend) encuentren el servidor
# automaticamente sin necesidad de configurar la IP y el puerto manualmente.
async def register_service(zc: AsyncZeroconf, app_for_zeroconf: FastAPI) -> Union[ServiceInfo, None]:
    ip_address = get_local_ip()
    hostname = socket.gethostname()
    port_to_register = get_actual_server_port_for_zeroconf(app_for_zeroconf)
    # Lee la configuracion del servicio desde variables de entorno para flexibilidad
    service_type_env = os.getenv("ZEROCONF_SERVICE_TYPE", "_umebotlogics._tcp.local.")
    service_name_base_env = os.getenv("ZEROCONF_SERVICE_NAME", "UmebotLogicsWebSocket")
    service_name_full = f"{service_name_base_env}.{service_type_env}"
    # Crea la informacion del servicio que se anunciara en la red
    info = ServiceInfo(
        type_=service_type_env, name=service_name_full,
        addresses=[socket.inet_aton(ip_address)], port=port_to_register,
        properties={}, server=f"{hostname}.local.",
    )
    log.info(f"Intentando registrar servicio Zeroconf: Nombre='{service_name_full}', IP='{ip_address}', Puerto='{port_to_register}'")
    try:
        await zc.async_register_service(info) # Registra el servicio de forma asincrona
        log.info(f"Servicio Zeroconf '{service_name_full}' registrado exitosamente.")
        return info
    except Exception as e:
        log.error(f"No se pudo registrar el servicio Zeroconf '{service_name_full}': {e}", exc_info=True)
        return None

# Desregistra el servicio Zeroconf de forma ordenada cuando el servidor se apaga.
async def unregister_service(zc: AsyncZeroconf, info: Union[ServiceInfo, None]):
    if info:
        log.info(f"Desregistrando servicio Zeroconf: {info.name}")
        try:
            await zc.async_unregister_service(info)
            log.info(f"Servicio Zeroconf '{info.name}' desregistrado exitosamente.")
        except Exception as e:
            log.error(f"Error desregistrando el servicio Zeroconf '{info.name}': {e}", exc_info=True)

# --- Gestor de Ciclo de Vida de FastAPI ---

# Gestor de ciclo de vida para la aplicacion FastAPI.
# Se ejecuta al iniciar y al apagar el servidor.
# - Al iniciar: Crea un cliente HTTP asincrono (httpx) y registra
#   el servidor en la red con Zeroconf.
# - Al apagar: Desregistra el servicio Zeroconf y cierra los clientes
#   (Zeroconf, httpx) para una finalizacion limpia.
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # --- Codigo de INICIO del servidor ---
    port = getattr(app_instance.state, 'actual_server_port', 'N/A')
    log.info(f"Lifespan: Iniciando servidor (Puerto Uvicorn configurado: {port})...")
    # Crea instancias de clientes y servicios que estaran disponibles durante la vida de la app
    app_instance.state.http_client = httpx.AsyncClient(timeout=30.0) # Cliente HTTP para futuras llamadas
    zc = AsyncZeroconf()
    app_instance.state.zeroconf_instance = zc # Guarda la instancia de Zeroconf
    app_instance.state.zeroconf_info = await register_service(zc, app_instance) # Registra el servicio
    log.info("Lifespan: Tareas de inicio completadas.")

    yield # La aplicacion se ejecuta aqui

    # --- Codigo de APAGADO del servidor ---
    log.info("Lifespan: Servidor apagando...")
    # Desregistra el servicio Zeroconf
    if hasattr(app_instance.state, 'zeroconf_info') and app_instance.state.zeroconf_info:
        if hasattr(app_instance.state, 'zeroconf_instance'):
            await unregister_service(app_instance.state.zeroconf_instance, app_instance.state.zeroconf_info)
    # Cierra la instancia de Zeroconf
    if hasattr(app_instance.state, 'zeroconf_instance'):
        await app_instance.state.zeroconf_instance.async_close()
        log.info("Lifespan: Instancia de Zeroconf cerrada.")
    # Cierra el cliente HTTP
    if hasattr(app_instance.state, 'http_client'):
        await app_instance.state.http_client.aclose()
        log.info("Lifespan: Cliente HTTPX cerrado.")
    log.info("Lifespan: Apagado del servidor limpio y completado.")

# --- Creacion de la Instancia de FastAPI ---
app = FastAPI(
    title="UmebotLogics Server (FastAPI)",
    description="Servidor FastAPI para gestionar la logica y comunicacion de Umebot.",
    version="1.0.0",
    lifespan=lifespan # Asigna el gestor de ciclo de vida
)
log.info(f"Instancia de FastAPI '{app.title}' creada. El puerto de ejecucion sera definido por el lanzador (ej. TabletInterface).")

# --- Inclusion de Rutas (Endpoints) ---
# Carga dinamicamente las rutas (endpoints) de la API desde el modulo Endpoints.py.
# Esto mantiene el codigo del servidor principal limpio y modular.
log.info(">>> [ServerWeb.py] Intentando importar y configurar rutas desde 'Endpoints.py'...")
try:
    # Importacion relativa, correcta para una estructura de paquete Python.
    from . import Endpoints as umebot_endpoints_module
    log.info(f"[ServerWeb.py] EXITO importando 'Endpoints.py' relativamente. Modulo: {umebot_endpoints_module}")
    # Incluye el router definido en Endpoints.py en la aplicacion principal
    if hasattr(umebot_endpoints_module, 'router'):
        app.include_router(umebot_endpoints_module.router, tags=["Umebot Core API"])
        log.info("[ServerWeb.py] El router de 'Endpoints.py' ha sido incluido exitosamente en la aplicacion.")
    else:
        log.error("[ServerWeb.py] El modulo 'Endpoints.py' fue importado pero NO contiene un atributo 'router'.")
except ImportError as e:
    log.critical(f"[ServerWeb.py] FALLO CRITICO al importar '.Endpoints': {e}", exc_info=True)
    log.critical("[ServerWeb.py] Asegurate de que Endpoints.py exista en 'tabletserver/' y que no haya errores de importacion, como dependencias ciclicas o errores de sintaxis dentro de Endpoints.py.")
except Exception as e_gen:
    log.critical(f"[ServerWeb.py] FALLO CRITICO GENERAL al importar o configurar Endpoints: {e_gen}", exc_info=True)
