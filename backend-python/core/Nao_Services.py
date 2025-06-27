# ----------------------------------------------------------------------------------
# Titular: Obtencion de Servicios NAOqi
# Funcion Principal: Provee una funcion para solicitar y obtener proxies a un
#                    conjunto predefinido de servicios del robot NAOqi, dada una
#                    sesion activa. Devuelve un diccionario con los servicios
#                    obtenidos.
# ----------------------------------------------------------------------------------

import qi
import logging
import sys

# Configuracion del logger para este modulo
log = logging.getLogger("NaoServices")

# Lista de nombres de servicios NAOqi comunes que se intentaran obtener.
# Otros modulos pueden depender de que estos servicios esten disponibles.
# Se puede anadir aqui otros servicios que se usen frecuentemente.
COMMON_SERVICES_REQUEST_LIST = [
    "ALMotion",
    "ALTextToSpeech",
    "ALAnimatedSpeech",
    "ALMemory",
    "ALAutonomousLife",
    "ALRobotPosture",
    "ALAnimationPlayer",
    "ALAudioDevice",
    "ALBattery",
    "ALDialog",
    "ALMood",
    "ALRobotMood",
    "ALLeds",
    "Actuation",
    "ActuationPrivate"
    # Considera anadir aqui otros servicios que tu aplicacion utilice de forma generalizada o usar servicios propios (deben iniciarse antes).
]

# Obtiene proxies para una lista predefinida de servicios NAOqi utilizando
# una sesion de 'qi' activa y conectada.
# Intenta obtener cada servicio de la lista COMMON_SERVICES_REQUEST_LIST.
# Registra (log) el exito o fallo para cada servicio.
# Ademas, verifica la presencia de servicios considerados criticos.
#
# Args:
#   session (qi.Session): La sesion 'qi' activa y conectada al robot.
#
# Returns:
#   dict | None: Un diccionario donde las claves son los nombres de los
#                servicios y los valores son los proxies a dichos servicios.
#                Los servicios que no pudieron ser obtenidos tendran un valor de None.
#                Retorna None si la sesion no es valida o no esta conectada.
def get_naoqi_services(session: qi.Session) -> dict | None:
    if not session or not session.isConnected():
        log.error("Se requiere una sesion qi conectada y valida para obtener servicios.")
        return None

    log.info(f"Obteniendo proxies para {len(COMMON_SERVICES_REQUEST_LIST)} servicios comunes...")
    service_proxies = {}
    services_obtained_count = 0
    services_failed_count = 0

    for service_name in COMMON_SERVICES_REQUEST_LIST:
        proxy = None # Inicializa el proxy como None para cada servicio
        try:
            proxy = session.service(service_name)
            log.info(f"Servicio '{service_name}' obtenido.") # Log individual por exito
            services_obtained_count += 1
        except RuntimeError as e:
            log.warning(f"No se pudo obtener el servicio '{service_name}': {e}")
            services_failed_count += 1
        except Exception as e_general:
            log.error(f"Error inesperado obteniendo '{service_name}': {e_general}", exc_info=True)
            services_failed_count += 1
        finally:
            # Almacena el proxy (o None si fallo) en el diccionario
            service_proxies[service_name] = proxy

    log.info(f"Intento de obtencion de servicios finalizado. Obtenidos: {services_obtained_count}, Fallidos/No disponibles: {services_failed_count}.")

    # Verifica si los servicios criticos para el controlador de hardware del robot (RHC) y otros estan presentes
    required_for_rhc = ["ALMotion", "ALRobotPosture", "ALAutonomousLife", "ALBasicAwareness"]
    missing_critical = [s for s in required_for_rhc if s not in service_proxies or service_proxies[s] is None]
    if missing_critical:
        log.error(f"Faltan servicios criticos: {missing_critical}. Algunas funcionalidades podrian estar afectadas.")
        # Decision de diseno: actualmente se devuelve lo que se haya podido obtener,
        # incluso si faltan servicios criticos. Considerar un manejo mas estricto si es necesario.
    return service_proxies
