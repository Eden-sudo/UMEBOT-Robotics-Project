# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Configuracion y Verificacion del Estado del Robot
# Funcion Principal: Define la clase RobotSetup, responsable de verificar el
#                    estado inicial del robot (ej. ALife, motores) y prepararlo
#                    para la operacion. Utiliza proxies de servicio inyectados
#                    para interactuar con el robot.
# ----------------------------------------------------------------------------------

import qi
import time

# Clase para verificar servicios esenciales y preparar el estado inicial
# del robot (ej. desactivar ALife, activar motores).
# Utiliza inyeccion de dependencias para recibir los proxies de servicio.
class RobotSetup:
    # Inicializa el verificador con los proxies de servicio NAOqi necesarios.
    # Verifica que todos los proxies requeridos sean validos al momento de la creacion.
    #
    # Args:
    #   alife_proxy: Proxy al servicio ALAutonomousLife.
    #   motion_proxy: Proxy al servicio ALMotion.
    #   posture_proxy: Proxy al servicio ALRobotPosture.
    #   tts_proxy: Proxy al servicio ALTextToSpeech.
    #
    # Raises:
    #   ValueError: Si alguno de los proxies requeridos es None.
    def __init__(self, alife_proxy, motion_proxy, posture_proxy, tts_proxy):
        print("[SetupRobot] Inicializando con proxies inyectados...")
        if not all([alife_proxy, motion_proxy, posture_proxy, tts_proxy]):
            # Imprime cuales proxies faltan para facilitar la depuracion
            missing = [name for name, proxy in [('ALife', alife_proxy), ('Motion', motion_proxy), ('Posture', posture_proxy), ('TTS', tts_proxy)] if not proxy]
            raise ValueError(f"Se requieren proxies validos. Faltan o son None: {missing}")

        # Guardar los proxies recibidos como atributos de la instancia
        self.alife = alife_proxy
        self.motion = motion_proxy
        self.posture = posture_proxy
        self.tts = tts_proxy

        self.status_report = {} # Diccionario para almacenar los resultados de las verificaciones
        self.is_ready = False   # Estado general final de preparacion del robot
        print("[SetupRobot] Proxies recibidos correctamente.")

    # Verifica el estado de ALAutonomousLife, intenta ponerlo en 'disabled'
    # y asegura que los motores esten activos (wakeUp), usando los proxies inyectados.
    # Actualiza el reporte de estado (self.status_report).
    # Devuelve True si la preparacion es exitosa, False si falla.
    def _check_alife_and_prepare_motors(self):
        print("[SetupRobot] Verificando y preparando ALAutonomousLife y Motores...")
        alife_ok = False
        motors_ok = False

        try:
            # Verificar y establecer estado de ALAutonomousLife
            initial_state = self.alife.getState()
            self.status_report['alife_estado_inicial'] = initial_state
            print(f"   - Estado inicial de ALife: {initial_state}")

            if initial_state != "disabled":
                print("   - Intentando desactivar ALife...")
                self.alife.setState("disabled")
                time.sleep(1) # Dar tiempo para que la transicion de estado se complete
                final_state = self.alife.getState()
                if final_state == "disabled":
                    print("   - ALife desactivado correctamente.")
                    self.status_report['alife_estado_final'] = "disabled (OK)"
                    alife_ok = True
                else:
                    print(f"   - ERROR: No se pudo desactivar ALife. Estado actual: {final_state}")
                    self.status_report['alife_estado_final'] = f"ERROR: No desactivado ({final_state})"
                    return False # Fallo critico, no continuar
            else:
                print("   - ALife ya estaba desactivado.")
                self.status_report['alife_estado_final'] = "disabled (OK)"
                alife_ok = True

            # Verificar y activar motores usando ALMotion
            print("   - Verificando y activando motores (wakeUp)...")
            if not self.motion.robotIsWakeUp():
                self.motion.wakeUp()
                time.sleep(1) # Dar tiempo para que los motores se activen

            if self.motion.robotIsWakeUp():
                print("   - Motores activados correctamente.")
                self.status_report['estado_motores'] = "Activos (Wake - OK)"
                motors_ok = True
            else:
                print("   - ERROR: No se pudieron activar los motores despues de wakeUp.")
                self.status_report['estado_motores'] = "ERROR: No activados"
                return False # Fallo critico, no continuar

        except Exception as e:
            print(f"   - ERROR durante verificacion/preparacion de ALife/Motores: {e}")
            self.status_report['alife_estado_final'] = f"ERROR ({e})"
            self.status_report['estado_motores'] = f"ERROR ({e})"
            return False

        return alife_ok and motors_ok

    # Realiza verificaciones adicionales en otros componentes del robot
    # utilizando los proxies de servicio inyectados (ej. postura, TTS).
    # Actualiza el reporte de estado (self.status_report).
    # Devuelve True si las verificaciones basicas pasan, False si alguna falla.
    def _check_other_components(self):
        print("[SetupRobot] Verificando otros componentes...")
        posture_ok = False
        tts_ok = False

        # Verificar ALRobotPosture (obteniendo postura actual)
        try:
            current_posture = self.posture.getPosture()
            print(f"   - Postura actual (via getPosture): {current_posture}")
            self.status_report['verificacion_postura'] = f"OK ({current_posture})"
            posture_ok = True
        except Exception as e:
            print(f"   - ERROR al obtener postura: {e}")
            self.status_report['verificacion_postura'] = f"ERROR ({e})"

        # Verificar ALTextToSpeech (obteniendo idioma actual)
        try:
            lang = self.tts.getLanguage()
            print(f"   - Idioma TTS actual: {lang}")
            self.status_report['verificacion_tts'] = f"OK ({lang})"
            tts_ok = True
        except Exception as e:
            print(f"   - ERROR al verificar TTS: {e}")
            self.status_report['verificacion_tts'] = f"ERROR ({e})"

        # Ejemplo de verificacion adicional (bateria):
        # try:
        #     # Nota: Para usar ALMemory aqui, necesitaria ser inyectado en __init__
        #     # memory_proxy = self.memory # Suponiendo que self.memory fue inyectado
        #     # level = memory_proxy.getData("Device/SubDeviceList/Battery/Charge/Sensor/Value")
        #     # if level < 0.15: # Ejemplo: minimo 15% de bateria
        #     #     print(f"   - ADVERTENCIA: Nivel de bateria bajo ({level*100:.0f}%)")
        #     #     self.status_report['verificacion_bateria'] = f"BAJA ({level*100:.0f}%)"
        #     #     # return False # Podria hacerse critico si se desea
        #     # else:
        #     #     self.status_report['verificacion_bateria'] = f"OK ({level*100:.0f}%)"
        # except Exception as e:
        #     # self.status_report['verificacion_bateria'] = f"ERROR ({e})"
        #     pass # Si ALMemory no esta disponible o falla, no lo consideramos critico aqui

        # Se considera que estas verificaciones adicionales pasan si postura y TTS estan OK
        return posture_ok and tts_ok

    # Metodo principal que orquesta todas las verificaciones y preparaciones del robot.
    # Llama a los metodos internos para verificar ALife, motores y otros componentes.
    # Actualiza el estado general 'is_ready' y el reporte de estado.
    #
    # Returns:
    #   bool: True si el robot esta verificado y preparado correctamente, False en caso contrario.
    def check_and_prepare(self):
        print("\n===== INICIANDO VERIFICACION Y PREPARACION DEL ROBOT =====")
        self.status_report.clear() # Limpiar reporte de una ejecucion anterior
        self.is_ready = False      # Reiniciar estado de preparacion

        # 1. Verificar que los proxies inyectados son validos
        # Esta verificacion ya se realiza en __init__, pero una comprobacion adicional no dana.
        if not all([self.alife, self.motion, self.posture, self.tts]):
            print("ERROR CRITICO: Faltan proxies de servicio esenciales para RobotSetup.")
            self.status_report['proxies_inyectados'] = "ERROR: Faltantes"
            return False
        self.status_report['proxies_inyectados'] = "OK"

        # 2. Verificar y establecer estado de ALAutonomousLife y Motores
        alife_motors_ok = self._check_alife_and_prepare_motors()
        if not alife_motors_ok:
            print("ERROR CRITICO: Fallo la preparacion de ALife o Motores.")
            self._print_status_report() # Imprimir reporte antes de salir
            return False # No continuar si el robot no esta en el estado base deseado

        # 3. Verificar otros componentes (opcional pero recomendado)
        other_components_ok = self._check_other_components()
        if not other_components_ok:
            print("ADVERTENCIA: Fallo la verificacion de algunos componentes secundarios (Postura/TTS).")
            # Se puede decidir si esto es un fallo critico o solo una advertencia.
            # Por ahora, se permite continuar pero se registra.

        # Si todas las verificaciones criticas pasaron
        print("===== VERIFICACION Y PREPARACION COMPLETADA =====")
        self.is_ready = True # Marcar el robot como listo
        self._print_status_report() # Mostrar el resumen final del estado
        return True

    # Imprime en consola el reporte de estado acumulado de las verificaciones
    # y preparaciones realizadas, de forma organizada.
    def _print_status_report(self):
        print("\n----- Reporte de Estado del Robot (Setup) -----")
        # Estado de los proxies inyectados
        print("   Estado de Servicios (Proxies Inyectados):")
        print(f"     - ALAutonomousLife        : {'OK' if self.alife else 'ERROR: No recibido'}")
        print(f"     - ALMotion                  : {'OK' if self.motion else 'ERROR: No recibido'}") # Podria anadirse nota sobre Robot Setup.exe
        print(f"     - ALRobotPosture            : {'OK' if self.posture else 'ERROR: No recibido'}")
        print(f"     - ALTextToSpeech            : {'OK' if self.tts else 'ERROR: No recibido'}")

        # Estado de la preparacion de componentes
        print("\n   Estado de Preparacion:")
        print(f"     - Estado ALife Inicial        : {self.status_report.get('alife_estado_inicial', 'No verificado')}")
        print(f"     - Estado ALife Final          : {self.status_report.get('alife_estado_final', 'No verificado')}")
        print(f"     - Estado Motores              : {self.status_report.get('estado_motores', 'No verificado')}")
        print(f"     - Verificacion Postura        : {self.status_report.get('verificacion_postura', 'No verificado')}")
        print(f"     - Verificacion TTS            : {self.status_report.get('verificacion_tts', 'No verificado')}")
        # Si se implementa la verificacion de bateria, descomentar:
        # print(f"     - Verificacion Bateria        : {self.status_report.get('verificacion_bateria', 'No verificado')}")

        print("---------------------------------------------")
        print(f"   => Estado General Listo: {self.is_ready}")
        print("---------------------------------------------")

    # Devuelve el diccionario que contiene los resultados detallados
    # de las verificaciones y el estado de los componentes.
    def get_status_report_dict(self):
        return self.status_report

    # Devuelve el estado general de preparacion del robot.
    # Este estado se establece despues de ejecutar check_and_prepare().
    def is_robot_ready(self):
        return self.is_ready


