#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Activador Experimental de Cliente de Audio en Robot via SSH
# Funcion Principal: Este script define la clase ActivationClientAudio, disenada
#                    como un mecanismo experimental para activar automaticamente un
#                    script cliente de audio en el robot. La idea era descubrir la
#                    IP del robot (usando Zeroconf para servicios SSH), conectar
#                    via SSH (Paramiko), y luego ejecutar/gestionar el script cliente
#                    en el robot, pasandole la IP del servidor.
#                    Este enfoque fue explorado durante fases de experimentacion pero
#                    no se integro en el flujo principal del sistema. Se prefirio el
#                    uso de microfonos locales/externos conectados al PC servidor por
#                    parte de los usuarios finales, especialmente en entornos ruidosos,
#                    para mejorar la calidad del STT, lo que redujo la necesidad de
#                    este tipo de activacion remota para el audio del robot.
#                    El script sirve como documentacion de dicha exploracion.
# ----------------------------------------------------------------------------------

import paramiko # Para conexiones SSH
import time
import logging
import json     # Importado pero no usado activamente; se mantiene por si se retoma alguna idea.
import socket
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # Para descubrimiento de servicios en red

# Configuracion basica de logging.
# Cambiar a logging.DEBUG para ver mas detalles de Zeroconf.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(module)s.%(funcName)s:%(lineno)d] - %(message)s')

# --- CONSTANTES DE CONFIGURACION ---
SSH_USERNAME = "nao"
SSH_PASSWORD = "nao" # Considerar el uso de claves SSH para mayor seguridad en un entorno real.

# --- Configuracion de ZeroConf para descubrir el robot ---
ZEROCONF_SERVICE_TYPE = "_ssh._tcp.local." # Tipo de servicio SSH anunciado por Zeroconf
# IMPORTANTE: Ajustar al nombre de host real del robot como se anuncia en la red.
# Si es None o vacio, el script intentara usar el primer servicio SSH encontrado, lo cual es menos fiable.
ZEROCONF_ROBOT_HOSTNAME_FILTER = "Umebot" # EJEMPLO: ¡AJUSTAR ESTO AL NOMBRE DE HOST DE TU ROBOT!
DEVICE_DISCOVERY_TIMEOUT = 20  # Segundos maximos para esperar a encontrar el servicio del robot via Zeroconf

# --- Configuracion del Script Cliente de Audio en el Robot ---
PATH_TO_AUDIO_SCRIPT_ON_ROBOT = '/home/nao/ros2_pepper_ws/umebot_core/audio/ClientAudio.py' # Ruta al script cliente
PYTHON_EXECUTABLE_IN_VENV_ON_ROBOT = '/home/nao/myenv/bin/python' # Python dentro del entorno virtual del robot
VENV_ACTIVATION_COMMAND_ON_ROBOT = 'source /home/nao/myenv/bin/activate' # Comando para activar el venv
AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT = "ClientAudio.py" # Nombre del proceso para buscar/terminar con pgrep/pkill

# Clase auxiliar para Zeroconf, escucha y filtra servicios SSH en la red
# para descubrir la direccion IP del robot objetivo.
class RobotServiceListener:
    # Inicializa el listener de servicios Zeroconf.
    #
    # Args:
    #   target_hostname_filter (Optional[str]): Parte del nombre de host del robot
    #                                     a filtrar (no sensible a mayusculas).
    def __init__(self, target_hostname_filter=None):
        self.discovered_robot_ip = None # IP del robot objetivo, una vez encontrado
        self.all_ssh_services_found = {} # Almacena todos los servicios SSH detectados: { 'nombre_anunciado.local.' : 'ip_address' }
        self.target_hostname_raw_filter = target_hostname_filter # Filtro original (sensible a mayusculas)
        self.target_hostname_filter = target_hostname_filter.lower() if target_hostname_filter else None # Filtro en minusculas

        logging.info(f"Inicializando RobotServiceListener para servicios '{ZEROCONF_SERVICE_TYPE}'.")
        if self.target_hostname_filter:
            logging.info(f"Se aplicara filtro por nombre de host que contenga: '{self.target_hostname_raw_filter}' (busqueda no sensible a mayusculas).")
        else:
            logging.warning("No se ha especificado ZEROCONF_ROBOT_HOSTNAME_FILTER. Se intentara usar el primer servicio SSH encontrado (esto es menos fiable).")

    # Metodo llamado por Zeroconf cuando un servicio SSH es eliminado de la red.
    # Actualmente no implementa una logica compleja de actualizacion de estado si el IP objetivo se pierde.
    def remove_service(self, zeroconf, type, name):
        # 'name' es el nombre completo del servicio, ej: "Umebot._ssh._tcp.local."
        logging.debug(f"Servicio ZeroConf eliminado por el browser: {name} (tipo: {type})")
        # La logica principal de re-descubrimiento en caso de perdida de IP esta en el bucle de _find_robot_ip_zeroconf.
        pass

    # Metodo llamado por Zeroconf cuando un nuevo servicio SSH es detectado o actualizado.
    # Intenta obtener la informacion del servicio (IP, nombre de host) y,
    # si coincide con el filtro (si existe), establece la IP del robot descubierto.
    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name) # Obtiene detalles del servicio
        logging.debug(f"Intento de agregar/actualizar servicio: Name='{name}', Type='{type}', Info='{info}'")

        if info and info.addresses and info.server:
            # info.server es a menudo "nombredelhost.local."
            service_hostname_simple = info.server.split('.')[0].lower() # Obtiene "nombredelhost" en minusculas
            ip_address = socket.inet_ntoa(info.addresses[0]) # Convierte la primera direccion IP a string

            # Almacena todos los servicios SSH encontrados para depuracion y referencia
            if info.server not in self.all_ssh_services_found or self.all_ssh_services_found[info.server] != ip_address:
                self.all_ssh_services_found[info.server] = ip_address
                logging.info(f"Servicio SSH detectado/actualizado: Nombre Anunciado='{info.server}', Host Simple='{service_hostname_simple}', IP='{ip_address}'")

            # Si aun no hemos encontrado nuestro robot objetivo
            if not self.discovered_robot_ip:
                if self.target_hostname_filter: # Si se esta aplicando un filtro por nombre
                    if self.target_hostname_filter in service_hostname_simple: # El nombre simple del host coincide con el filtro
                        self.discovered_robot_ip = ip_address
                        logging.info(f"*** ROBOT OBJETIVO ENCONTRADO (filtro aplicado) ***")
                        logging.info(f"     Nombre de host simple: '{service_hostname_simple}' (coincide con filtro '{self.target_hostname_raw_filter}')")
                        logging.info(f"     Nombre anunciado completo: '{info.server}'")
                        logging.info(f"     IP asignada: {self.discovered_robot_ip}")
                    else: # El servicio encontrado no coincide con el filtro
                        logging.debug(f"Servicio '{info.server}' (host simple '{service_hostname_simple}') no coincide con el filtro '{self.target_hostname_raw_filter}'. Ignorando para seleccion.")
                else: # No hay filtro, se toma el primer servicio SSH encontrado
                    self.discovered_robot_ip = ip_address
                    logging.warning(f"*** ROBOT SELECCIONADO (sin filtro de nombre de host - primer servicio SSH) ***")
                    logging.warning(f"     Nombre anunciado: '{info.server}'")
                    logging.warning(f"     IP asignada: {self.discovered_robot_ip}")
        elif info: # Si se obtuvo info, pero no direcciones o nombre de servidor
            logging.debug(f"Servicio '{name}' (tipo: {type}) no tiene direcciones IP o nombre de servidor en la info recuperada: {info}")
        else: # Si no se pudo obtener ServiceInfo
            logging.debug(f"No se pudo obtener ServiceInfo para '{name}' (tipo: {type}).")

    # Metodo llamado por Zeroconf cuando la informacion de un servicio existente cambia.
    # Delega el procesamiento a add_service para re-evaluar la informacion.
    def update_service(self, zeroconf, type, name):
        logging.debug(f"Servicio ZeroConf actualizado: {name} (tipo: {type}). Re-procesando como add_service.")
        self.add_service(zeroconf, type, name)

# Gestiona el proceso de descubrir el robot, conectar via SSH y activar/gestionar
# un script cliente de audio en el robot de forma remota.
class ActivationClientAudio:
    # Inicializa el activador del cliente de audio.
    def __init__(self):
        self.robot_ip = None # IP del robot descubierta
        self.laptop_ip_for_robot = None # IP de esta maquina (servidor) que el robot usara para conectar
        self.ssh_client = None # Cliente Paramiko SSH
        logging.info("ActivationClientAudio inicializado.")

    # Intenta descubrir la direccion IP del robot en la red local utilizando Zeroconf
    # para buscar servicios SSH. Utiliza RobotServiceListener.
    #
    # Returns:
    #   bool: True si se encontro la IP del robot objetivo, False en caso contrario.
    def _find_robot_ip_zeroconf(self):
        logging.info(f"Iniciando busqueda ZeroConf para servicios tipo '{ZEROCONF_SERVICE_TYPE}' durante {DEVICE_DISCOVERY_TIMEOUT}s.")
        zeroconf = Zeroconf()
        listener = RobotServiceListener(target_hostname_filter=ZEROCONF_ROBOT_HOSTNAME_FILTER)
        browser = ServiceBrowser(zeroconf, ZEROCONF_SERVICE_TYPE, listener) # Inicia el "escaneo" de servicios

        start_time = time.time()
        try:
            # Espera hasta que se descubra la IP o se alcance el timeout
            while listener.discovered_robot_ip is None and (time.time() - start_time) < DEVICE_DISCOVERY_TIMEOUT:
                time.sleep(0.5)

            # Muestra todos los servicios SSH detectados al final de la busqueda para depuracion
            if listener.all_ssh_services_found:
                logging.info("--- Resumen de todos los servicios SSH detectados durante la busqueda ---")
                for name, ip_addr in listener.all_ssh_services_found.items():
                    logging.info(f"   - {name} en {ip_addr}")
                logging.info("--------------------------------------------------------------------")
            else:
                logging.info(f"No se detectaron servicios SSH (tipo '{ZEROCONF_SERVICE_TYPE}') durante la busqueda.")

            if listener.discovered_robot_ip:
                self.robot_ip = listener.discovered_robot_ip
                logging.info(f"BUSQUEDA FINALIZADA: IP del robot objetivo seleccionada: {self.robot_ip}")
                return True
            else: # No se encontro el robot objetivo
                logging.error("BUSQUEDA FINALIZADA: No se pudo seleccionar un robot objetivo segun los criterios.")
                if ZEROCONF_ROBOT_HOSTNAME_FILTER and not listener.all_ssh_services_found:
                    logging.warning(f"   Asegurate de que el robot este en la red y anunciando un servicio SSH (tipo '{ZEROCONF_SERVICE_TYPE}').")
                elif ZEROCONF_ROBOT_HOSTNAME_FILTER and listener.all_ssh_services_found:
                    logging.warning(f"   Se encontraron servicios SSH, pero ninguno coincidio con el filtro por nombre de host '{ZEROCONF_ROBOT_HOSTNAME_FILTER}'. Verifica el nombre y el filtro.")
                elif not ZEROCONF_ROBOT_HOSTNAME_FILTER and listener.all_ssh_services_found:
                    # Esta condicion es inusual si se encontraron servicios y no habia filtro.
                    logging.warning("   Se encontraron servicios SSH, pero la logica para seleccionar el primero (sin filtro) fallo. Esto es inusual.")
                return False
        finally: # Asegura la liberacion de recursos de Zeroconf
            browser.cancel()
            zeroconf.close()
            logging.debug("Recursos de ZeroConf liberados.")

    # Intenta determinar la direccion IP de esta maquina (laptop/servidor)
    # que sea accesible desde la red del robot. Esta IP se pasara
    # al script cliente en el robot para que sepa a donde conectarse.
    #
    # Returns:
    #   bool: True si se pudo determinar una IP, False en caso contrario.
    def _get_laptop_ip_for_robot(self):
        if not self.robot_ip:
            logging.error("Se necesita la IP del robot para determinar la IP de la laptop (para la interfaz de red correcta).")
            return False
        try:
            # Intenta conectar a la IP del robot (no se envian datos) para obtener la IP local de esa interfaz
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1) # Evita bloqueo indefinido
            s.connect((self.robot_ip, 1)) # Conectar a un puerto arbitrario (ej. 1)
            self.laptop_ip_for_robot = s.getsockname()[0] # Obtiene la IP local de la interfaz usada
            s.close()
            logging.info(f"IP de la laptop determinada para el robot ({self.robot_ip}): {self.laptop_ip_for_robot}")
            return True
        except Exception as e:
            logging.error(f"No se pudo determinar la IP de la laptop para el robot ({self.robot_ip}) usando connect: {e}")
            # Fallback: intentar obtener IP por nombre de host (puede no ser la correcta para el robot)
            try:
                hostname = socket.gethostname()
                self.laptop_ip_for_robot = socket.gethostbyname(hostname)
                logging.warning(f"Fallback: IP de la laptop por nombre de host: {self.laptop_ip_for_robot} (esta IP podria no ser accesible por el robot si hay multiples interfaces de red).")
                return True
            except socket.gaierror: # Error resolviendo nombre de host
                logging.error("Fallback fallido: No se pudo obtener IP por nombre de host.")
                self.laptop_ip_for_robot = "127.0.0.1" # Ultimo recurso, probablemente no util para el robot
                return False

    # Establece una conexion SSH con el robot utilizando Paramiko.
    # Utiliza las credenciales y la IP del robot previamente descubierta.
    #
    # Returns:
    #   bool: True si la conexion SSH fue exitosa, False en caso contrario.
    def _connect_ssh(self):
        if not self.robot_ip:
            logging.error("No se ha descubierto la IP del robot. No se puede conectar via SSH.")
            return False
        # Si ya hay una conexion activa, no hacer nada
        if self.ssh_client and self.ssh_client.get_transport() and self.ssh_client.get_transport().is_active():
            return True
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy()) # Acepta automaticamente la clave del host
            logging.info(f"Conectando via SSH a {self.robot_ip} como usuario '{SSH_USERNAME}'...")
            self.ssh_client.connect(self.robot_ip, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=10)
            logging.info("Conexion SSH establecida con el robot.")
            return True
        except paramiko.AuthenticationException:
            logging.error(f"Error de autenticacion SSH para {self.robot_ip} con usuario '{SSH_USERNAME}'. Verifica las credenciales.")
            self.ssh_client = None
            return False
        except Exception as e: # Otros errores de conexion (timeout, red, etc.)
            logging.error(f"Error al conectar via SSH a {self.robot_ip}: {e}")
            self.ssh_client = None
            return False

    # Cierra la conexion SSH activa con el robot si existe.
    def _disconnect_ssh(self):
        if self.ssh_client:
            try:
                self.ssh_client.close()
                logging.info("Conexion SSH cerrada.")
            except Exception as e:
                logging.error(f"Error al cerrar la conexion SSH: {e}")
            finally:
                self.ssh_client = None # Asegura limpiar la referencia

    # Ejecuta un comando en el robot a traves de la conexion SSH activa.
    # Opcionalmente, registra la salida y error estandar del comando.
    #
    # Args:
    #   command (str): El comando a ejecutar en el robot.
    #   log_output (bool): Si es True, registra stdout y stderr del comando.
    #
    # Returns:
    #   Tuple[Optional[paramiko.ChannelFile], ...]: Streams stdin, stdout, stderr,
    #                                               o (None, None, None) si hay error
    #                                               o la conexion no esta activa.
    def _execute_ssh_command(self, command, log_output=True):
        # Verifica si hay una conexion SSH activa; si no, intenta conectar.
        if not self.ssh_client or not self.ssh_client.get_transport() or not self.ssh_client.get_transport().is_active():
            logging.error("No hay conexion SSH activa para ejecutar el comando. Intentando reconectar...")
            if not self._connect_ssh(): return None, None, None # Falla si no se puede reconectar

        logging.info(f"Ejecutando comando SSH en el robot: {command}")
        try:
            # Ejecuta el comando
            stdin, stdout, stderr = self.ssh_client.exec_command(command, timeout=20)
            if log_output: # Si se deben registrar las salidas
                out = stdout.read().decode(errors='ignore').strip()
                err = stderr.read().decode(errors='ignore').strip()
                if out: logging.info(f"Salida del comando (stdout):\n{out}")
                if err: logging.warning(f"Errores del comando (stderr):\n{err}") # stderr no siempre es un error fatal
            return stdin, stdout, stderr
        except Exception as e:
            logging.error(f"Error al ejecutar comando SSH '{command}': {e}")
            return None, None, None

    # Verifica si el script cliente de audio ya se esta ejecutando en el robot.
    # Utiliza el comando 'pgrep' via SSH.
    #
    # Returns:
    #   bool: True si el script parece estar ejecutandose, False en caso contrario.
    def is_script_running_on_robot(self):
        if not self._connect_ssh(): return False # Requiere conexion SSH
        command = f"pgrep -f {AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}" # Comando para buscar el proceso
        logging.debug(f"Verificando si el script '{AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}' esta corriendo en el robot...")
        stdin, stdout, stderr = self._execute_ssh_command(command, log_output=False) # No loguear salida de pgrep por defecto

        output = ""
        err_output = ""
        if stdout: output = stdout.read().decode(errors='ignore').strip()
        if stderr: err_output = stderr.read().decode(errors='ignore').strip()

        if output: # Si pgrep devuelve PIDs, el script esta corriendo
            logging.info(f"Script '{AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}' parece estar corriendo (PIDs: {output}).")
            return True
        # Si no hay PIDs Y no hay error en stderr (pgrep devuelve salida vacia y codigo 0 si no encuentra nada, pero algunos pgrep devuelven error)
        # O si hay error en stderr (pgrep devuelve codigo 1 si no encuentra, que puede ir a stderr)
        logging.info(f"Script '{AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}' no encontrado en ejecucion (pgrep stdout: '{output}', stderr: '{err_output}').")
        return False

    # Intenta detener el script cliente de audio en el robot.
    # Utiliza el comando 'pkill' via SSH.
    #
    # Returns:
    #   bool: True si el script se detuvo (o ya no estaba corriendo),
    #         False si no se pudo confirmar la detencion.
    def stop_script_on_robot(self):
        if not self._connect_ssh(): return False # Requiere conexion SSH
        command = f"pkill -f {AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}" # Comando para terminar el proceso
        logging.info(f"Intentando detener el script '{AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}' en el robot...")
        self._execute_ssh_command(command) # Ejecuta pkill
        time.sleep(2) # Espera un momento para que el proceso termine
        if not self.is_script_running_on_robot(): # Verifica si el script se detuvo
            logging.info(f"Script '{AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}' detenido exitosamente en el robot.")
            return True
        else:
            logging.warning(f"No se pudo confirmar la detencion del script '{AUDIO_SCRIPT_PROCESS_NAME_ON_ROBOT}' en el robot.")
            return False

    # Inicia el script cliente de audio en el robot via SSH.
    # Activa un entorno virtual (venv), luego ejecuta el script con 'nohup'
    # para que continue incluso si la sesion SSH se cierra, y le pasa la IP
    # del servidor (esta maquina) como argumento.
    #
    # Returns:
    #   bool: True si el script parece haberse iniciado correctamente,
    #         False en caso contrario.
    def start_audio_script_on_robot(self):
        if not self._connect_ssh(): return False # Requiere conexion SSH
        if not self.laptop_ip_for_robot: # IP del servidor (esta maquina) es necesaria
            logging.error("IP de la laptop (servidor) no disponible. No se puede iniciar el script de audio en el robot.")
            return False

        # Comando para ejecutar el script cliente, pasandole la IP del servidor
        audio_script_run_cmd = f"{PYTHON_EXECUTABLE_IN_VENV_ON_ROBOT} {PATH_TO_AUDIO_SCRIPT_ON_ROBOT} --ip {self.laptop_ip_for_robot}"
        # Comando completo, incluyendo la activacion del entorno virtual
        full_command_on_robot = f"{VENV_ACTIVATION_COMMAND_ON_ROBOT} && {audio_script_run_cmd}"
        # Ejecuta con nohup para que el script siga corriendo en segundo plano y redirige salida
        nohup_command = f"nohup {full_command_on_robot} > /tmp/client_audio.log 2>&1 &"

        logging.info(f"Iniciando script de audio en el robot (segundo plano): {nohup_command}")
        self._execute_ssh_command(nohup_command, log_output=False) # No se espera salida inmediata de nohup
        time.sleep(3) # Espera un momento para que el script inicie

        if self.is_script_running_on_robot(): # Verifica si el script inicio correctamente
            logging.info(f"Script de audio iniciado y parece estar corriendo en el robot. Logs en el robot en /tmp/client_audio.log")
            return True
        else:
            logging.error("El script de audio no parece estar corriendo despues del intento de inicio. Revisar /tmp/client_audio.log en el robot.")
            return False

    # Metodo principal para gestionar el script de audio en el robot.
    # Descubre la IP del robot y la IP de esta maquina, establece conexion SSH,
    # y luego verifica si el script cliente esta corriendo. Si no lo esta,
    # lo inicia. Si 'force_restart' es True, lo detiene y lo reinicia.
    #
    # Args:
    #   force_restart (bool): Si es True, detiene el script si esta corriendo y lo reinicia.
    #
    # Returns:
    #   bool: True si la gestion fue exitosa (script corriendo al final),
    #         False si ocurrio algun error.
    def manage_audio_script(self, force_restart=False):
        # Paso 1: Descubrir IPs si no estan disponibles
        if not self.robot_ip:
            if not self._find_robot_ip_zeroconf():
                logging.error("Fallo al descubrir IP del robot. Abortando gestion de script.")
                return False
        if not self.laptop_ip_for_robot:
            if not self._get_laptop_ip_for_robot():
                logging.error("Fallo al obtener IP de la laptop (servidor). Abortando gestion de script.")
                return False

        # Paso 2: Conectar via SSH (se reintentara dentro de los metodos si es necesario)
        if not self._connect_ssh(): # Intenta una conexion inicial
            logging.error("No se pudo establecer la conexion SSH inicial. Abortando gestion de script.")
            return False

        success = False
        try:
            script_is_running = self.is_script_running_on_robot()
            if script_is_running:
                if force_restart: # Si se debe forzar el reinicio
                    logging.info("Script de audio del robot esta corriendo. Forzando reinicio...")
                    self.stop_script_on_robot()
                    time.sleep(1) # Pausa antes de reiniciar
                    success = self.start_audio_script_on_robot()
                else: # Si esta corriendo y no se fuerza reinicio, no hacer nada
                    logging.info("Script de audio del robot ya esta corriendo. No se requiere accion.")
                    success = True
            else: # Si el script no esta corriendo, iniciarlo
                logging.info("Script de audio del robot no esta corriendo. Iniciando...")
                success = self.start_audio_script_on_robot()
        except Exception as e:
            logging.error(f"Excepcion durante la gestion del script de audio en el robot: {e}", exc_info=True)
            success = False
        finally:
            self._disconnect_ssh() # Siempre cierra la conexion SSH al finalizar
        return success

# Bloque de ejecucion principal para probar la clase ActivationClientAudio.
if __name__ == '__main__':
    activator = ActivationClientAudio()
    logging.info("--- Iniciando/Asegurando que el script de audio del cliente este corriendo en el robot ---")

    # Bloque comentado para depuracion especifica de ZeroConf y obtencion de IPs:
    # if activator._find_robot_ip_zeroconf():
    #     logging.info(f"Robot encontrado en: {activator.robot_ip}")
    #     if activator._get_laptop_ip_for_robot():
    #         logging.info(f"IP de laptop (servidor) para el robot: {activator.laptop_ip_for_robot}")
    # else:
    #     logging.error("No se pudo encontrar el robot o determinar las IPs necesarias.")

    # Intenta gestionar el script de audio; force_restart=False por defecto.
    # Cambiar a True para forzar un reinicio del script en el robot en la primera ejecucion de esta prueba.
    if activator.manage_audio_script(force_restart=False):
        logging.info("Gestion del script de audio en el robot completada exitosamente.")
    else:
        logging.error("Fallo la gestion del script de audio en el robot.")
