#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Controlador de Hardware del Robot (Interfaz de Bajo Nivel)
# Funcion Principal: Define la clase RobotHardwareController, que actua como una
#                    interfaz de bajo nivel para controlar directamente el hardware
#                    fisico del robot a traves de los servicios NAOqi (principalmente
#                    ALMotion y ALRobotPosture). Es el componente que recibe los
#                    comandos de movimiento (ej. velocidades de la base) y gestos
#                    desde modulos de mas alto nivel (como MotionGamePad) y los
#                    ejecuta en el robot.
#                    Maneja la inicializacion fisica del robot (wakeUp, postura StandInit),
#                    la liberacion de motores (rest), y la parada de emergencia.
#                    La vision a futuro de este directorio era integrar algoritmos
#                    avanzados de navegacion con ROS2, pero la implementacion actual
#                    se centra en proveer una base solida para el control manual.
# ----------------------------------------------------------------------------------

import qi
import logging
import time
import threading
from typing import Any, List, Dict, Tuple, Union, Optional

# Configuracion del logger para este modulo
log = logging.getLogger("RobotHardwareController")

# Definiciones de tipo para claridad en la definicion de gestos
# Un paso de gesto: (lista_de_nombres_articulacion, lista_de_angulos, fraccion_de_velocidad_max)
GestureStep = Tuple[Union[str, List[str]], Union[float, List[float]], float]
# Un gesto completo: una lista de pasos de gesto a ejecutar secuencialmente
GestureDefinition = List[GestureStep]

# Tiempo en segundos para esperar a que el robot se estabilice despues de cambiar de postura.
POSTURE_STABILIZATION_TIME_SEC = 2.5

# Provee una interfaz directa para controlar el hardware del robot, como
# la base movil y las articulaciones, utilizando los proxies de NAOqi.
class RobotHardwareController:
    # Inicializa el controlador de hardware.
    #
    # Args:
    #   motion_proxy (Any): Proxy al servicio ALMotion de NAOqi (requerido).
    #   posture_proxy (Optional[Any]): Proxy al servicio ALRobotPosture de NAOqi (opcional, pero recomendado).
    #
    # Raises:
    #   ValueError: Si motion_proxy no se proporciona.
    def __init__(self,
                 motion_proxy: Any,
                 posture_proxy: Optional[Any] = None):
        log.info("RHC: Inicializando controlador de hardware...")
        if not motion_proxy:
            msg = "RobotHardwareController: Se requiere un proxy valido para ALMotion (motion_proxy)."
            log.critical(msg)
            raise ValueError(msg)

        self.motion = motion_proxy
        self.posture = posture_proxy
        self.is_initialized_physically = False # Bandera que indica si el robot esta listo para operacion
        self._gesture_thread: Optional[threading.Thread] = None # Hilo para ejecucion de gestos no bloqueantes
        self._stop_gesture_flag = threading.Event() # Evento para detener un gesto en curso
        # Diccionario que contiene una coleccion de gestos predefinidos.
        # Cada gesto es una secuencia de pasos a ejecutar.
        self.scripted_gestures: Dict[str, GestureDefinition] = {
            "saludo_simple": [
                (["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw"],
                 [-0.5,             -0.3,            1.0,         1.0,          0.0], 0.2), # Mover brazo a posicion de saludo
                (["RWristYaw"], [0.5], 0.1), (["RWristYaw"], [-0.5], 0.1), (["RWristYaw"], [0.0], 0.1), # Agitar muneca
                (["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw"],
                 [1.2,              -0.2,            0.0,         0.0,          0.0], 0.2) # Bajar brazo
            ],
            "asentir_cabeza": [
                (["HeadPitch"], [0.2], 0.1), (["HeadPitch"], [-0.1], 0.1), (["HeadPitch"], [0.0], 0.1)
            ],
            "negar_cabeza": [
                (["HeadYaw"], [0.3], 0.15), (["HeadYaw"], [-0.3], 0.3), (["HeadYaw"], [0.0], 0.15)
            ]
        }
        log.info(f"RHC: {len(self.scripted_gestures)} gestos programados definidos. Proxies de hardware asignados.")

    # Prepara el robot para la operacion, asegurando un estado fisico seguro y predecible.
    # Realiza una secuencia de acciones: activa los motores (wakeUp), desactiva la vida
    # autonoma (ALAutonomousLife), detiene cualquier movimiento previo, establece la
    # postura 'StandInit', y habilita la proteccion de colisiones.
    #
    # Returns:
    #   bool: True si el robot fue inicializado correctamente, False en caso contrario.
    def initialize_robot(self) -> bool:
        if self.is_initialized_physically:
            log.info("RHC.init: El robot ya esta inicializado fisicamente."); return True

        log.info("RHC.init: Iniciando secuencia de preparacion fisica del robot...");
        try:
            # Paso 1: Activar motores si el robot no esta "despierto"
            if not self.motion.robotIsWakeUp():
                log.info(f"RHC.init: El estado 'isWakeUp' es Falso. Ejecutando wakeUp()...");
                self.motion.wakeUp()
                time.sleep(1.5) # Pausa para dar tiempo a que los motores se activen completamente.
                if not self.motion.robotIsWakeUp(): # Doble verificacion despues de la pausa
                    log.error("RHC.init: Fallo critico - wakeUp() fue llamado pero el robot aun no esta despierto.")
                    return False
                log.info(f"RHC.init: wakeUp() completado. Estado 'isWakeUp' ahora es Verdadero.")
            else:
                log.info("RHC.init: El robot ya se encontraba en estado 'wakeUp'.")

            # Paso 2: Desactivar la Vida Autonoma para permitir control externo completo
            if hasattr(self.motion, 'getAutonomousLifeState') and hasattr(self.motion, 'setAutonomousLifeState'):
                alife_state = self.motion.getAutonomousLifeState()
                if alife_state != "disabled":
                    log.info(f"RHC.init: El estado de ALife es '{alife_state}'. Desactivando...")
                    self.motion.setAutonomousLifeState("disabled")
                    time.sleep(0.5) # Pausa para la transicion de estado de ALife
                    new_alife_state = self.motion.getAutonomousLifeState()
                    log.info(f"RHC.init: Nuevo estado de ALife: {new_alife_state}")
                    if new_alife_state != "disabled":
                        log.warning(f"RHC.init: No se pudo desactivar ALife, estado actual: {new_alife_state}. Esto podria interferir con el control manual.")
                else:
                    log.info("RHC.init: ALife ya se encontraba en estado 'disabled'.")
            else:
                log.warning("RHC.init: No se pudo verificar o configurar el estado de ALAutonomousLife (metodos no encontrados en proxy).")

            # Paso 3: Detener cualquier movimiento de la base que pudiera estar activo
            if self.motion.moveIsActive():
                log.info("RHC.init: Movimiento de la base estaba activo, deteniendolo...");
                self.motion.stopMove()
                time.sleep(0.5) # Pausa para asegurar la detencion

            # Paso 4: Establecer una postura inicial estandar y segura ('StandInit')
            posture_considered_ok = False
            if self.posture and hasattr(self.posture, 'goToPosture') and hasattr(self.motion, 'getRobotPosture'):
                current_posture = "Unknown"
                try: current_posture = self.motion.getRobotPosture()
                except Exception as e_getpost_initial: log.warning(f"RHC.init: Excepcion al obtener postura inicial: {e_getpost_initial}")
                log.info(f"RHC.init: Postura actual del robot: {current_posture}")

                if current_posture == "StandInit":
                    log.info("RHC.init: El robot ya esta en la postura 'StandInit'.")
                    posture_considered_ok = True
                else: # Si no esta en la postura correcta, intentar moverlo
                    log.info(f"RHC.init: Robot no esta en 'StandInit'. Intentando goToPosture('StandInit')...")
                    success_goto = False
                    try: success_goto = self.posture.goToPosture("StandInit", 0.8) # 0.8 es la fraccion de velocidad
                    except Exception as e_gotoposture: log.error(f"RHC.init: Excepcion DURANTE goToPosture('StandInit'): {e_gotoposture}", exc_info=True)

                    if success_goto:
                        log.info(f"RHC.init: goToPosture('StandInit') fue exitoso. Esperando {POSTURE_STABILIZATION_TIME_SEC}s para estabilizacion...")
                        time.sleep(POSTURE_STABILIZATION_TIME_SEC) # Pausa para que el robot se estabilice fisicamente
                        posture_considered_ok = True
                    else:
                        log.error(f"RHC.init: goToPosture('StandInit') fallo. No se pudo establecer la postura inicial.")
                        posture_considered_ok = False
            else: # Si no se puede gestionar la postura, se continua con una advertencia
                log.warning("RHC.init: ALRobotPosture no disponible o metodos necesarios no encontrados. No se puede gestionar/verificar 'StandInit'.")
                posture_considered_ok = True # Se asume OK para permitir continuar, pero es un riesgo.

            # Paso 5: Verificacion final y activacion de proteccion de colisiones
            if self.motion.robotIsWakeUp() and posture_considered_ok:
                log.info("RHC.init: El robot esta despierto y la postura es correcta. Considerado LISTO para operacion.")
                self.is_initialized_physically = True # Marcar como listo
                if hasattr(self.motion, 'setExternalCollisionProtectionEnabled'):
                    self.motion.setExternalCollisionProtectionEnabled("All", True) # Activa proteccion de colisiones
                    log.info("RHC.init: Proteccion de colision externa HABILITADA para 'All'.")
                return True
            else: # Si alguna verificacion final fallo
                log.error(f"RHC.init: FALLO final en la inicializacion fisica. Estado WakeUp: {self.motion.robotIsWakeUp()}, Estado Postura OK: {posture_considered_ok}.")
                return False
        except Exception as e:
            log.error(f"RHC.init: Error GENERAL durante la inicializacion fisica del robot: {e}", exc_info=True)
            # Intento de recuperacion minimo
            try:
                if self.motion and not self.motion.robotIsWakeUp(): self.motion.wakeUp()
            except Exception as e_wu_final: log.error(f"RHC.init: Error en el ultimo intento de wakeUp tras excepcion general: {e_wu_final}")
            return False

    # Libera los motores del robot, poniendolo en un estado de reposo seguro.
    # Llama al metodo 'rest()' de ALMotion.
    def release_robot(self):
        log.info("RHC: Solicitando al robot que descanse (llamando a ALMotion.rest())...")
        try:
            if self.motion.robotIsWakeUp():
                if self.motion.moveIsActive(): self.motion.stopMove(); log.info("RHC: Movimiento base detenido antes de llamar a 'rest'.")
                self.motion.rest()
                self.is_initialized_physically = False
                log.info("RHC: El robot ha sido puesto en estado de reposo (motores sin rigidez).")
            else:
                log.info("RHC: El robot ya no estaba despierto. No se llamo a rest().")
                self.is_initialized_physically = False
        except Exception as e: log.error(f"RHC: Error al poner el robot en estado de reposo: {e}", exc_info=True)

    # Envia un comando de velocidad a la base movil del robot.
    # Este es el metodo principal para el control de movimiento continuo.
    # Utiliza el metodo 'moveToward()' de ALMotion.
    #
    # Args:
    #   vx (float): Velocidad hacia adelante (positivo) / atras (negativo).
    #   vy (float): Velocidad de desplazamiento lateral (strafe): izquierda (positivo) / derecha (negativo).
    #   vtheta (float): Velocidad de rotacion: izquierda (positivo, antihorario) / derecha (negativo, horario).
    def set_base_velocities(self, vx: float, vy: float, vtheta: float):
        log.info(f"RHC: Llamado a set_base_velocities con vx={vx:.3f}, vy={vy:.3f}, vtheta={vtheta:.3f}. Robot inicializado: {self.is_initialized_physically}")
        if not self.motion:
            log.error("RHC.set_base_velocities: El proxy a ALMotion no esta disponible. No se puede mover."); return
        if not self.is_initialized_physically:
            log.warning("RHC.set_base_velocities: El robot no ha sido inicializado fisicamente. El movimiento podria no funcionar correctamente.")
            if not self.motion.robotIsWakeUp():
                log.error("RHC.set_base_velocities: El robot no esta en estado 'wakeUp'. No se enviara el comando moveToward."); return
        try:
            # Logs de diagnostico antes de enviar el comando de movimiento
            alife_state = "N/A"; is_wakeup = "N/A"; all_collision_status = "N/A"; move_collision_status = "N/A"
            if hasattr(self.motion, 'getAutonomousLifeState'): alife_state = self.motion.getAutonomousLifeState()
            if hasattr(self.motion, 'robotIsWakeUp'): is_wakeup = self.motion.robotIsWakeUp()
            if hasattr(self.motion, "getExternalCollisionProtectionEnabled"):
                all_collision_status = self.motion.getExternalCollisionProtectionEnabled("All")
                move_collision_status = self.motion.getExternalCollisionProtectionEnabled("Move")
            log.info(f"RHC Pre-MoveToward: ALife='{alife_state}', WakeUp='{is_wakeup}', CollProtect All='{all_collision_status}', CollProtect Move='{move_collision_status}'")

            # Envia el comando de movimiento al robot
            log.info(f"RHC: Intentando llamar a self.motion.moveToward({vx:.3f}, {vy:.3f}, {vtheta:.3f})")
            self.motion.moveToward(vx, vy, vtheta)
            log.debug(f"RHC: La llamada a self.motion.moveToward se completo sin una excepcion en Python.")
        except RuntimeError as e_rt:
            log.error(f"RHC.set_base_velocities: RuntimeError en ALMotion.moveToward: {e_rt}", exc_info=True)
        except Exception as e_gen:
            log.error(f"RHC.set_base_velocities: Excepcion general en ALMotion.moveToward: {e_gen}", exc_info=True)

    # Ejecuta una parada de emergencia a nivel de hardware.
    # Llama inmediatamente a 'stopMove()' de ALMotion para detener todo movimiento de la base
    # y senala la detencion de cualquier gesto en curso.
    def trigger_hardware_emergency_stop(self):
        log.warning("RHC: trigger_hardware_emergency_stop LLAMADO!")
        if not self.motion:
            log.error("RHC: El proxy a ALMotion no esta disponible, no se puede ejecutar la parada de emergencia de hardware."); return
        try:
            log.info("RHC: Ejecutando ALMotion.stopMove() para una parada de emergencia inmediata...")
            self.motion.stopMove()
            log.info("RHC: ALMotion.stopMove() ejecutado exitosamente.")
            # Si hay un gesto en ejecucion, tambien se le senala que se detenga.
            if self._gesture_thread and self._gesture_thread.is_alive():
                log.info("RHC: E-STOP: Senalando detencion a gesto en curso.")
                self._stop_gesture_flag.set()
        except RuntimeError as e_rt:
            log.error(f"RHC: RuntimeError durante parada de emergencia (stopMove): {e_rt}", exc_info=True)
        except Exception as e_gen:
            log.error(f"RHC: Excepcion general en trigger_hardware_emergency_stop: {e_gen}", exc_info=True)

    # Devuelve si el robot ha sido inicializado fisicamente con exito.
    #
    # Returns:
    #   bool: True si initialize_robot() fue exitoso, False en caso contrario.
    def is_robot_initialized_physically(self) -> bool:
        return self.is_initialized_physically

    # Funcion objetivo para el hilo de ejecucion de gestos.
    # Itera a traves de los pasos de un gesto predefinido y los ejecuta secuencialmente
    # usando 'setAngles()'. Puede ser interrumpido por la bandera _stop_gesture_flag.
    def _execute_gesture_thread_target(self, gesture_steps: GestureDefinition, gesture_name: str):
        log.info(f"RHC Gesto Hilo: Iniciando ejecucion del gesto '{gesture_name}'...")
        self._stop_gesture_flag.clear() # Limpia la bandera de parada al inicio
        try:
            for i, (joints, angles, speed_fraction) in enumerate(gesture_steps):
                if self._stop_gesture_flag.is_set(): # Verifica si se debe interrumpir
                    log.info(f"RHC Gesto Hilo: Gesto '{gesture_name}' interrumpido por senal de parada."); break
                log.debug(f"RHC Gesto '{gesture_name}', paso {i+1}: J={joints}, A={angles}, S={speed_fraction}")
                self.motion.setAngles(joints, angles, speed_fraction) # Envia comando de angulo a las articulaciones
                time.sleep(0.3 + (1.0 - speed_fraction) * 0.5) # Pausa calculada para dar tiempo al movimiento
            if not self._stop_gesture_flag.is_set(): log.info(f"RHC Gesto Hilo: Gesto '{gesture_name}' completado.")
        except Exception as e: log.error(f"RHC Gesto Hilo: Error ejecutando el gesto '{gesture_name}': {e}", exc_info=True)
        finally: log.info(f"RHC Gesto Hilo: Hilo para el gesto '{gesture_name}' ha finalizado.")

    # Ejecuta un gesto pre-programado de la lista 'scripted_gestures'.
    # Puede ejecutar el gesto de forma bloqueante (espera a que termine)
    # o no bloqueante (en un nuevo hilo).
    #
    # Args:
    #   gesture_name (str): El nombre del gesto a ejecutar (debe existir en scripted_gestures).
    #   wait (bool): Si es True, la funcion espera a que el gesto termine.
    #                Si es False, se ejecuta en un hilo y la funcion retorna inmediatamente.
    #
    # Returns:
    #   bool: True si el gesto se inicio correctamente, False en caso contrario.
    def execute_scripted_gesture(self, gesture_name: str, wait: bool = False) -> bool:
        if not self.is_initialized_physically:
            log.warning(f"RHC: El robot no esta inicializado. No se puede ejecutar el gesto '{gesture_name}'."); return False
        if gesture_name not in self.scripted_gestures:
            log.error(f"RHC: Gesto con nombre '{gesture_name}' no esta definido en scripted_gestures."); return False
        # Evita ejecutar un nuevo gesto si otro ya esta en curso
        if self._gesture_thread and self._gesture_thread.is_alive():
            log.warning(f"RHC: Ya hay un gesto en ejecucion. El nuevo gesto '{gesture_name}' no fue iniciado."); return False

        gesture_steps = self.scripted_gestures[gesture_name] # Obtiene los pasos del gesto
        log.info(f"RHC: Solicitando ejecucion del gesto '{gesture_name}' (Bloqueante: {wait}).")
        if wait: # Ejecucion bloqueante
            self._execute_gesture_thread_target(gesture_steps, gesture_name)
            return True
        else: # Ejecucion no bloqueante (en un nuevo hilo)
            self._gesture_thread = threading.Thread(target=self._execute_gesture_thread_target, args=(gesture_steps, gesture_name), daemon=True)
            self._gesture_thread.name = f"GestureThread-{gesture_name}"
            self._gesture_thread.start()
            return True
