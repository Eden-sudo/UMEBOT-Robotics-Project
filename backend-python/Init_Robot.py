# ----------------------------------------------------------------------------------
# Titular: Inicializacion Base del Robot
# Funcion Principal: Gestiona la conexion inicial, autenticacion y obtencion de
#                    servicios basicos del robot. Es una abstraccion de alto nivel
#                    de la libreria oficial NAOqi (libqi) y utiliza modulos del
#                    directorio 'core' del proyecto.
# ----------------------------------------------------------------------------------

import qi
import sys
import time
import keyring
import getpass
import logging
import asyncio

# --- Configuracion del Logger ---
log = logging.getLogger("InitRobot")

# --- Importar modulos base desde el subdirectorio 'core' ---
try:
    from core.Robot_Connect import get_service_ip_async
    from core.Session_Manager import connect_robot
    from core.Nao_Services import get_naoqi_services
    from core.Setup_Robot import RobotSetup
except ImportError as e_core:
    print(f"ERROR [InitRobot]: No se pudieron importar modulos desde 'core/': {e_core}")
    log.critical(f"No se pudieron importar modulos desde 'core': {e_core}", exc_info=True)
    sys.exit(1)

# --- Configuracion General ---
KEYRING_SERVICE_NAME = "UmebotPepperConnection"
KEYRING_USERNAME = "nao"
ZEROCONF_INSTANCE_NAME = "Umebot"
ZEROCONF_SERVICE_TYPE = "_naoqi._tcp.local."
ZEROCONF_TIMEOUT_MS = 7000 # Timeout para Zeroconf async

# Esta funcion obtiene la contrasena del robot.
# Primero intenta leerla desde el keyring (llavero seguro del sistema).
# Si no la encuentra en el keyring, la solicita al usuario de forma segura.
# Si se ingresa una nueva contrasena y el keyring es operacional, intenta guardarla.
def _get_robot_password_from_keyring(service_name, username, robot_ip_for_prompt):
    password = None
    keyring_operational_for_get = True # Asume que el keyring funciona para 'get' inicialmente

    # Intenta obtener la contrasena del keyring
    try:
        password = keyring.get_password(service_name, username)
        if password:
            log.info(f"Contrasena para '{username}' obtenida del keyring.")
            return password
        else:
            log.info(f"No se encontro contrasena en keyring para {service_name}/{username}.")
    except Exception as e_get:
        log.warning(f"Error al intentar obtener contrasena del keyring: {e_get}. "
                    "Se solicitara la contrasena directamente para esta sesion.")
        keyring_operational_for_get = False # Marca el keyring como no operacional para 'get'

    # Si no se obtuvo del keyring, solicitarla al usuario
    prompt_text = f"--> Ingrese la CONTRASENA para el robot '{username}' en {robot_ip_for_prompt}: "
    password_input = getpass.getpass(prompt_text)

    if password_input:
        # Si se ingreso una contrasena y el keyring no fallo al obtener (o es la primera vez)
        # y no habia una contrasena previa (evita reescribir si 'get' fallo pero 'set' podria funcionar)
        if keyring_operational_for_get and password is None:
            try:
                keyring.set_password(service_name, username, password_input)
                log.info("Contrasena guardada en keyring para futuras sesiones.")
            except Exception as e_set:
                log.warning(f"No se pudo guardar la contrasena en keyring: {e_set}")
        return password_input
    else:
        log.error("No se ingreso contrasena.")
        return None

# Esta funcion realiza la inicializacion de bajo nivel del robot de forma asincrona.
# Descubre la IP del robot, obtiene la contrasena, establece una conexion segura libqi,
# obtiene los proxies a los servicios NAOqi necesarios y prepara el estado fisico
# inicial del robot (motores, ALife, postura mediante wakeUp).
async def initialize_robot_base_async():
    log.info("===== [InitRobot] INICIANDO CONEXION Y SETUP BASE DEL ROBOT (ASYNC) =====")
    app_instance, session_instance, service_proxies = None, None, None
    robot_is_fully_ready = False
    discovered_ip, robot_pass = None, None

    try:
        log.info(f"[InitRobot] Buscando robot '{ZEROCONF_INSTANCE_NAME}' (async)...")
        # Llamada asincrona para el descubrimiento de IP del robot via Zeroconf
        discovered_ip = await get_service_ip_async(
            ZEROCONF_INSTANCE_NAME, ZEROCONF_SERVICE_TYPE, ZEROCONF_TIMEOUT_MS
        )
        if not discovered_ip: raise RuntimeError(f"No se pudo encontrar el robot '{ZEROCONF_INSTANCE_NAME}'.")
        log.info(f"[InitRobot] Robot encontrado en IP: {discovered_ip}")

        # Obtiene la contrasena del robot, usando keyring o solicitandola al usuario
        robot_pass = _get_robot_password_from_keyring(KEYRING_SERVICE_NAME, KEYRING_USERNAME, discovered_ip)
        if not robot_pass: raise ValueError("No se pudo obtener la contrasena del robot.")

        log.info(f"[InitRobot] Conectando a {discovered_ip}...")
        # Nota: connect_robot y get_naoqi_services son funciones sincronas.
        # Su llamado desde una funcion async es aceptable aqui si su bloqueo
        # es corto o no es critico en esta fase de inicializacion.
        app_instance, session_instance = connect_robot(discovered_ip, password=robot_pass)
        if not app_instance or not session_instance: raise RuntimeError("Fallo la conexion con el robot.")
        log.info("[InitRobot] Conexion al robot establecida.")

        log.info("[InitRobot] Obteniendo proxies de servicio NAOqi...")
        service_proxies = get_naoqi_services(session_instance)
        if not service_proxies: raise RuntimeError("No se pudo obtener el diccionario de servicios NAOqi.")
        
        # Verifica la existencia de servicios NAOqi criticos para el setup del robot
        critical_for_robot_setup = ["ALAutonomousLife", "ALMotion", "ALRobotPosture", "ALTextToSpeech"]
        if not all(service_proxies.get(name) for name in critical_for_robot_setup):
            missing = [name for name in critical_for_robot_setup if not service_proxies.get(name)]
            raise RuntimeError(f"Faltan servicios NAOqi esenciales para el setup del robot: {missing}")
        log.info("[InitRobot] Proxies NAOqi necesarios para setup obtenidos.")

        log.info("[InitRobot] Preparando estado del robot (ALife, postura con wakeUp, colisiones)...")
        # Instancia el manejador para la configuracion fisica del robot
        setup_checker = RobotSetup(
            alife_proxy=service_proxies.get("ALAutonomousLife"),
            motion_proxy=service_proxies.get("ALMotion"),
            posture_proxy=service_proxies.get("ALRobotPosture"),
            tts_proxy=service_proxies.get("ALTextToSpeech")
        )
        # Ejecuta la preparacion y verificacion del estado del robot (funcion sincrona)
        is_setup_ok = setup_checker.check_and_prepare()
            
        if not is_setup_ok:
            log.error("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            log.error("!!! [InitRobot] ¡ALERTA DE MOVIMIENTO/SETUP!                               !!!")
            log.error("!!! El robot NO pudo ser preparado correctamente por RobotSetup.         !!!")
            log.error("!!! Causas posibles:                                                     !!!")
            log.error("!!!   - Robot en un estado fisico inesperado (ej. sentado sin soporte).  !!!")
            log.error("!!!   - Fallo al intentar ejecutar wakeUp() o setExternalCollisionProtectionEnabled(). !!!")
            log.error("!!!   - Problemas con los motores o sensores.                              !!!")
            log.error("!!! ACCION REQUERIDA:                                                    !!!")
            log.error("!!!   - Revise los logs detallados de Setup_Robot.py.                    !!!")
            log.error("!!!   - Verifique el estado fisico del robot y su entorno.               !!!")
            log.error("!!!   - Considere reiniciar el robot si el problema persiste.             !!!")
            log.error("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            raise RuntimeError("La preparacion del robot por RobotSetup fallo. Revisa la ALERTA anterior y los logs de Setup_Robot.")
            
        log.info("[InitRobot] Robot preparado fisicamente.")

        robot_is_fully_ready = True
        log.info("===== [InitRobot] INICIALIZACION BASE DEL ROBOT COMPLETADA =====")
        return app_instance, session_instance, service_proxies, robot_is_fully_ready

    except Exception as e:
        log.critical(f"[InitRobot] ERROR CRITICO durante la inicializacion base: {e}", exc_info=True)
        # Intenta detener la aplicacion qi si se creo una instancia, en caso de error
        if app_instance:
            try: app_instance.stop()
            except: pass # Ignora errores al detener, el objetivo es limpiar lo posible
        log.error("===== [InitRobot] INICIALIZACION BASE DEL ROBOT FALLIDA =====")
        return None, None, None, False
