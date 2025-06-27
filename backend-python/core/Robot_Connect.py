# ----------------------------------------------------------------------------------
# Titular: Descubrimiento Asincrono de IP del Robot via Zeroconf
# Funcion Principal: Implementa la logica para encontrar la direccion IP de un
#                    robot (u otro servicio) en la red local utilizando el protocolo
#                    Zeroconf (mDNS/DNS-SD) de manera asincrona. Utiliza la
#                    libreria zeroconf.
# ----------------------------------------------------------------------------------

import asyncio
import socket
import logging
from typing import Optional
import sys

from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser, AsyncServiceInfo # Usar versiones async de zeroconf

# Configuracion del logger para este modulo
log = logging.getLogger("RobotConnect")
# Nota: El logging global se configura generalmente en un punto de entrada principal (ej. main.py)

# Clase auxiliar (helper) para escuchar y capturar informacion de servicios Zeroconf.
# Funciona en conjunto con AsyncServiceBrowser para identificar y resolver
# el servicio objetivo especificado por su nombre de instancia.
class ServiceListener:
    # Inicializa el listener con el nombre de la instancia del servicio objetivo.
    # Prepara un evento asyncio para senalar cuando el servicio es encontrado.
    def __init__(self, target_instance_name: str):
        self.target_instance_name_lower = target_instance_name.lower() # Para comparaciones insensibles a mayusculas
        self.found_service_info: Optional[AsyncServiceInfo] = None
        self.found_event = asyncio.Event() # Evento para senalar que el servicio ha sido encontrado y resuelto

    # Verifica si el nombre del servicio descubierto contiene el nombre de la instancia objetivo.
    # Considera que Zeroconf puede devolver nombres en diferentes formatos (ej. "Instancia._tipo._tcp.local.").
    def _is_target_service(self, name: str) -> bool:
        # La comprobacion busca si el nombre de la instancia objetivo esta en el nombre del servicio anunciado.
        return self.target_instance_name_lower in name.lower()

    # Metodo llamado por AsyncServiceBrowser cuando un servicio es removido de la red.
    # Para el proposito de descubrimiento inicial, esta accion no es critica.
    def remove_service(self, zc: AsyncZeroconf, type_: str, name: str) -> None:
        log.debug(f"Servicio Zeroconf removido: {name}, tipo: {type_}")
        # No se realiza ninguna accion particular al remover, el foco esta en anadir/resolver.

    # Metodo llamado por AsyncServiceBrowser cuando un servicio es anadido o actualizado.
    # Si el servicio coincide con el objetivo y aun no ha sido encontrado,
    # crea una tarea para resolver sus detalles de forma asincrona.
    def add_service(self, zc: AsyncZeroconf, type_: str, name: str) -> None:
        log.debug(f"Servicio Zeroconf anadido/actualizado: {name}, tipo: {type_}")
        # Si es el servicio buscado y el evento 'found_event' no ha sido activado aun, intenta resolverlo.
        if self._is_target_service(name) and not self.found_event.is_set():
            asyncio.create_task(self._resolve_service(zc, type_, name))

    # Resuelve de forma asincrona los detalles de un servicio Zeroconf (IP, puerto).
    # Si la informacion del servicio se obtiene con exito y coincide con la instancia
    # objetivo, almacena la informacion y activa el evento de 'encontrado'.
    async def _resolve_service(self, zc: AsyncZeroconf, type_: str, name: str):
        log.debug(f"Resolviendo servicio: {name}...")
        info = AsyncServiceInfo(type_, name)
        # Intenta obtener la informacion del servicio con un timeout.
        if await info.async_request(zc, 3000): # Timeout de 3 segundos para obtener info
            # Extrae el nombre de la instancia del servicio desde la informacion obtenida.
            # info.name suele ser "Instancia._tipo._tcp.local."
            service_instance_name_from_info = info.name.split('.')[0]

            if self.target_instance_name_lower == service_instance_name_from_info.lower():
                log.info(f"Servicio objetivo '{self.target_instance_name_lower}' encontrado y resuelto: {name}")
                self.found_service_info = info
                self.found_event.set() # Senala que el servicio fue encontrado y resuelto
            else:
                log.debug(f"Servicio '{name}' resuelto, pero no es la instancia objetivo '{self.target_instance_name_lower}' (instancia encontrada: '{service_instance_name_from_info}'). Ignorando.")
        else:
            log.warning(f"No se pudo obtener informacion para el servicio Zeroconf: {name}")

# Descubre la direccion IP de un servicio en la red local utilizando Zeroconf
# de forma asincrona.
#
# Args:
#   instance_name (str): Nombre de la instancia del servicio (ej. "Umebot").
#   service_type (str): Tipo de servicio (ej. "_naoqi._tcp.local.").
#   timeout_ms (int): Tiempo maximo de espera en milisegundos.
#
# Returns:
#   Optional[str]: La direccion IP del servicio si se encuentra, o None
#                  (ej. "192.168.1.10").
async def get_service_ip_async(instance_name: str, service_type: str, timeout_ms: int = 5000) -> Optional[str]:
    log.info(f"Buscando IP para '{instance_name}' (tipo: {service_type}) usando Zeroconf asincrono...")
    zc = AsyncZeroconf()
    listener = ServiceListener(target_instance_name=instance_name)
    # AsyncServiceBrowser busca todos los servicios del tipo especificado y notifica al listener.
    browser = AsyncServiceBrowser(zc.zeroconf, service_type, listener=listener) # type: ignore para compatibilidad

    try:
        # Espera a que el listener encuentre y resuelva el servicio, o hasta que se cumpla el timeout.
        await asyncio.wait_for(listener.found_event.wait(), timeout=timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        log.warning(f"Timeout ({timeout_ms}ms) esperando el servicio Zeroconf '{instance_name}'.")
        return None
    except Exception as e:
        log.error(f"Error inesperado durante la busqueda Zeroconf: {e}", exc_info=True)
        return None
    finally:
        log.debug("Cerrando ServiceBrowser y AsyncZeroconf...")
        # Es importante cerrar el browser antes que la instancia de zc.
        if browser: # browser podria no haberse asignado si AsyncZeroconf() falla
            await browser.async_cancel() # Detiene el browser y sus tareas internas
        await zc.async_close() # Cierra la instancia de Zeroconf y libera sockets
        log.debug("Recursos Zeroconf cerrados.")

    if listener.found_service_info and listener.found_service_info.addresses:
        # Convierte la primera direccion IPv4 encontrada a formato string.
        # 'addresses' es una lista de bytes, ej. [b'\xc0\xa8\x01\x05'] para 192.168.1.5.
        try:
            ip_address_bytes = listener.found_service_info.addresses[0]
            ip_address_str = socket.inet_ntoa(ip_address_bytes) # Convierte bytes a string IP (ej. "192.168.1.5")
            log.info(f"IP encontrada para '{instance_name}': {ip_address_str} (Puerto: {listener.found_service_info.port})")
            return ip_address_str
        except Exception as e_addr:
            log.error(f"Error convirtiendo direccion IP de Zeroconf: {e_addr}", exc_info=True)
            return None
    else:
        log.warning(f"No se encontro informacion de direccion para el servicio '{instance_name}'.")
        return None

# Bloque para ejecutar el modulo como script independiente para pruebas.
# Configura un logging basico y ejecuta una prueba de descubrimiento
# para un nombre de instancia y tipo de servicio definidos.
if __name__ == '__main__':
    # Configura un logging basico si no hay manejadores configurados (util para pruebas directas)
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)] 
        )

    TARGET_INSTANCE_NAME = "Umebot"  # Nombre de instancia del robot a buscar
    TARGET_SERVICE_TYPE = "_naoqi._tcp.local." # Tipo de servicio estandar para NAOqi
    # TARGET_SERVICE_TYPE = "_http._tcp.local." # Ejemplo para buscar un servidor HTTP local anunciado via Zeroconf

    # Funcion de prueba asincrona para el descubrimiento de servicios.
    # Llama a get_service_ip_async e informa si la IP fue encontrada.
    async def test_discovery():
        log.info(f"Iniciando prueba de descubrimiento para '{TARGET_INSTANCE_NAME}'...")
        ip = await get_service_ip_async(TARGET_INSTANCE_NAME, TARGET_SERVICE_TYPE, timeout_ms=7000)
        if ip:
            log.info(f"EXITO: IP encontrada para '{TARGET_INSTANCE_NAME}': {ip}")
        else:
            log.error(f"FALLO: No se pudo encontrar la IP para '{TARGET_INSTANCE_NAME}'.")
            log.warning("Asegurate de que el robot este encendido, en la misma red, "
                        "y que el servicio Zeroconf/Bonjour este activo en el robot y la red.")
            log.warning(f"Tambien verifica que ZEROCONF_INSTANCE_NAME ('{TARGET_INSTANCE_NAME}') "
                        f"en el script de inicializacion principal (ej. Init_Robot.py) coincida con el nombre de tu robot.")

    try:
        asyncio.run(test_discovery())
    except KeyboardInterrupt:
        log.info("Prueba de descubrimiento interrumpida por el usuario.")
