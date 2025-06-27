#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Gestor de Movimiento del Robot (MotionManager)
# Funcion Principal: Este modulo define la clase MotionManager, que actua como el
#                    orquestador de alto nivel para todo el movimiento del robot.
#                    Abstrae la complejidad de los modulos del directorio 'motion/',
#                    gestionando una maquina de estados para los diferentes modos de
#                    control (ej. 'idle', 'gamepad', 'emergency_stopped').
#                    Recibe los comandos desde la interfaz de usuario (a traves de
#                    los callbacks de TabletInterface) y los delega al controlador
#                    adecuado, como MotionGamePad. Es responsable de activar y
#                    desactivar los modos de control y de gestionar la logica de
#                    parada de emergencia a un nivel superior.
# ----------------------------------------------------------------------------------

import logging
from typing import Dict, Any, Optional, Callable, Coroutine
import asyncio

# Importacion de los controladores de bajo nivel
from motion.MotionGamePad import MotionGamePad
from motion.RobotHardwareController import RobotHardwareController
from behavior.AnimationsSpeech import AnimationSpeechController

# Configuracion del logger para este modulo
log = logging.getLogger("MotionManager")

# Orquesta los diferentes modos de control de movimiento del robot, actuando
# como una fachada (facade) sobre los controladores de mas bajo nivel.
class MotionManager:
    # Inicializa el MotionManager.
    # Crea instancias de los controladores de bajo nivel (RobotHardwareController
    # y MotionGamePad) y configura los manejadores (handlers) que se
    # conectaran a los callbacks de la interfaz de la tablet.
    #
    # Args:
    #   almotion_proxy (Any): Proxy a ALMotion (requerido).
    #   alrobotposture_proxy (Optional[Any]): Proxy a ALRobotPosture.
    #   animation_speech_controller (Optional[AnimationSpeechController]): Controlador de animaciones y habla.
    #   gamepad_animation_config (Optional[Dict]): Configuracion de mapeo de botones del gamepad.
    #   default_gamepad_animation_layer (int): Capa de animacion por defecto para el gamepad.
    #   initial_gamepad_speed_modifier (float): Modificador de velocidad inicial para el gamepad.
    def __init__(self,
                 almotion_proxy: Any,
                 alrobotposture_proxy: Optional[Any] = None,
                 animation_speech_controller: Optional[AnimationSpeechController] = None,
                 gamepad_animation_config: Optional[Dict[int, Dict[str, Dict[str, Any]]]] = None,
                 default_gamepad_animation_layer: int = 0,
                 initial_gamepad_speed_modifier: float = 0.5):
        
        log.info("MotionManager: Inicializando...")
        if almotion_proxy is None:
            msg = "MotionManager: El proxy a ALMotion (almotion_proxy) es requerido."; log.critical(msg); raise ValueError(msg)

        # Instancia el controlador de hardware de bajo nivel
        self.robot_hw_controller = RobotHardwareController(
            motion_proxy=almotion_proxy,
            posture_proxy=alrobotposture_proxy
        )
        # Instancia el controlador especifico para el gamepad
        self.gamepad_controller = MotionGamePad(
            robot_hardware_controller=self.robot_hw_controller,
            animation_speech_controller=animation_speech_controller,
            gamepad_animation_config=gamepad_animation_config,
            default_animation_layer=default_gamepad_animation_layer,
            initial_speed_modifier=initial_gamepad_speed_modifier
        )

        self._global_speed_factor: float = 1.0 # Factor de velocidad global (no usado activamente en este script)
        # Maquina de estados para el modo de control actual
        self._current_control_mode: Optional[str] = "idle" # Modos posibles: "idle", "gamepad", "emergency_stopped"

        # Asigna los metodos de esta clase a atributos que seran usados como callbacks por TabletInterface
        self.gamepad_payload_handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]] = self._handle_gamepad_payload_from_tablet
        # Este handler es por si la interfaz envia una senal de E-STOP especifica,
        # aunque la logica principal se maneja en el payload_handler.
        self.gamepad_emergency_stop_handler: Callable[[], Coroutine[Any, Any, None]] = self._handle_gamepad_emergency_stop_event

        log.info("MotionManager inicializado correctamente.")

    # --- Metodos Publicos de Control y Estado ---

    # Establece un factor de velocidad global.
    def set_global_speed_factor(self, factor: float):
        if 0.1 <= factor <= 1.0:
            self._global_speed_factor = factor
            # Propaga el cambio de velocidad al controlador del gamepad
            if hasattr(self.gamepad_controller, 'update_speed_factor_from_global'):
                self.gamepad_controller.update_speed_factor_from_global(self._global_speed_factor)
            log.info(f"Factor de velocidad global del MotionManager establecido a: {self._global_speed_factor:.2f}")
        else:
            log.warning(f"El factor de velocidad global '{factor}' esta fuera del rango (0.1-1.0). No fue aplicado.")

    # Obtiene el factor de velocidad global actual.
    def get_global_speed_factor(self) -> float:
        return self._global_speed_factor

    # Metodo de inicializacion del gestor (simple, preparacion real ocurre en la activacion de modos).
    async def initialize(self) -> bool:
        log.info("MotionManager: Metodo initialize() llamado. Listo para activar modos de control."); return True

    # Metodo de apagado (shutdown) para detener todos los sistemas de movimiento de forma segura.
    async def shutdown(self):
        log.info("MotionManager: Apagando todos los sistemas de movimiento...");
        current_mode_before_shutdown = self._current_control_mode

        # Desactiva el modo de control que estuviera activo
        if current_mode_before_shutdown == "gamepad":
            await self.deactivate_gamepad_control(called_by_emergency_stop=False)
        elif current_mode_before_shutdown == "emergency_stopped":
            log.info("MotionManager: El sistema estaba en 'emergency_stopped' durante el apagado.")
            await self.deactivate_gamepad_control(called_by_emergency_stop=True)

        # Como salvaguarda, se asegura de que el robot quede en estado de reposo.
        if self.robot_hw_controller and hasattr(self.robot_hw_controller, 'is_robot_initialized_physically') and self.robot_hw_controller.is_robot_initialized_physically():
            log.info(f"MotionManager Shutdown Safeguard: El robot aun estaba inicializado fisicamente. Solicitando liberacion de motores.")
            if hasattr(self.robot_hw_controller, 'release_robot'):
                await asyncio.to_thread(self.robot_hw_controller.release_robot)

        self._current_control_mode = "idle"; log.info("MotionManager: Sistemas de movimiento detenidos. Modo actual: 'idle'.")

    # Activa el modo de control por gamepad, iniciando el procesamiento de sus comandos.
    async def activate_gamepad_control(self) -> bool:
        if self._current_control_mode == "gamepad":
            log.info("MotionManager: El control por gamepad ya se encuentra activo."); return True

        log.info(f"MotionManager: Activando el modo de control por gamepad (Modo actual: {self._current_control_mode})...")
        # Limpia cualquier estado de E-STOP previo en el controlador del gamepad antes de activar.
        if hasattr(self.gamepad_controller, 'clear_emergency_stop'):
            await asyncio.to_thread(self.gamepad_controller.clear_emergency_stop)

        # Inicia el hilo de procesamiento del gamepad
        gamepad_started_successfully = await asyncio.to_thread(self.gamepad_controller.start_processing)
        if gamepad_started_successfully:
            self._current_control_mode = "gamepad"
            log.info("MotionManager: Control por gamepad activado exitosamente."); return True
        else:
            log.error("MotionManager: Fallo al activar el control por gamepad (gamepad_controller.start_processing() fallo).")
            self._current_control_mode = "idle"; return False # Asegura que el modo vuelva a idle

    # Desactiva el modo de control por gamepad.
    async def deactivate_gamepad_control(self, called_by_emergency_stop: bool = False):
        log.info(f"MotionManager: Solicitud para desactivar control por gamepad. Modo actual: {self._current_control_mode}, Invocado como parte de E-STOP: {called_by_emergency_stop}")
        # Solo desactiva si estaba en modo 'gamepad' o 'emergency_stopped'
        if self._current_control_mode in ["gamepad", "emergency_stopped"]:
            log.info(f"MotionManager: Procediendo a desactivar MotionGamePad...")
            await asyncio.to_thread(self.gamepad_controller.stop_processing, emergency_stop_invoked=called_by_emergency_stop)
            # Si la desactivacion no fue por un E-STOP, vuelve a modo idle.
            # Si fue por un E-STOP, el modo ya habra sido cambiado a "emergency_stopped".
            if not (called_by_emergency_stop and self._current_control_mode == "emergency_stopped"):
                self._current_control_mode = "idle"
            log.info(f"MotionManager: Control por gamepad desactivado. Modo actual: {self._current_control_mode}")
        else:
            log.info(f"MotionManager: No se necesita desactivar el gamepad (modo actual: {self._current_control_mode}).")

    # --- Metodos Internos para Gestion de Parada de Emergencia ---

    # Activa la secuencia de parada de emergencia, cambiando el modo interno y
    # notificando al controlador del gamepad para que detenga el hardware.
    async def _trigger_internal_emergency_stop(self):
        if self._current_control_mode != "emergency_stopped":
            log.warning("MotionManager: INICIANDO SECUENCIA DE PARADA DE EMERGENCIA INTERNA (E-STOP).")
            original_mode = self._current_control_mode
            self._current_control_mode = "emergency_stopped"
            log.info(f"MotionManager: Modo de control cambiado de '{original_mode}' a 'emergency_stopped'.")
            if hasattr(self.gamepad_controller, 'trigger_emergency_stop'):
                await asyncio.to_thread(self.gamepad_controller.trigger_emergency_stop)
            else: # Fallback si el metodo no existe
                log.error("MotionManager: MotionGamePad no tiene el metodo 'trigger_emergency_stop'. Intentando E-STOP de hardware directamente.")
                if self.robot_hw_controller and hasattr(self.robot_hw_controller, 'trigger_hardware_emergency_stop'):
                    await asyncio.to_thread(self.robot_hw_controller.trigger_hardware_emergency_stop)
            log.info("MotionManager: Procesamiento de E-STOP interno completado.")
        else:
            log.debug("MotionManager: Ya se encuentra en modo 'emergency_stopped'. Solicitud de E-STOP ignorada.")

    # Limpia el estado de parada de emergencia, permitiendo que el control por gamepad se reanude.
    async def _clear_internal_emergency_stop(self):
        if self._current_control_mode == "emergency_stopped":
            log.info("MotionManager: Limpiando el estado de PARADA DE EMERGENCIA INTERNA.")
            if hasattr(self.gamepad_controller, 'clear_emergency_stop'):
                await asyncio.to_thread(self.gamepad_controller.clear_emergency_stop)
            self._current_control_mode = "gamepad" # Vuelve al modo gamepad
            log.info("MotionManager: E-STOP interno limpiado. Modo de control cambiado a 'gamepad'.")
        else:
            log.info(f"MotionManager: Se llamo a clear_internal_emergency_stop, pero el modo actual es '{self._current_control_mode}'. No se requiere accion.")

    # --- Manejadores de Callbacks (Conexion con TabletInterface) ---

    # Metodo principal que recibe el payload del gamepad desde la TabletInterface.
    # Interpreta si se trata de una senal de E-Stop (L3/R3), si se debe limpiar
    # el E-Stop, o si es un payload normal de movimiento que debe ser reenviado
    # al MotionGamePad.
    async def _handle_gamepad_payload_from_tablet(self, payload: Dict[str, Any]):
        log.info(f"MotionManager: Recibido payload de gamepad desde TabletInterface. Modo actual: {self._current_control_mode}.")
        # Extrae el estado de los botones de los sticks (usados para E-STOP)
        stick_buttons = payload.get("stick_button_states", {})
        l3_pressed = stick_buttons.get("l3_pressed", False)
        r3_pressed = stick_buttons.get("r3_pressed", False)

        # 1. Manejo de activacion de E-STOP
        if (l3_pressed or r3_pressed) and self._current_control_mode != "emergency_stopped":
            log.warning(f"MotionManager: Boton L3/R3 presionado detectado en payload. Activando E-STOP.")
            await self._trigger_internal_emergency_stop()
            return # No procesar el resto del payload si se activa el E-STOP

        # 2. Manejo de limpieza de E-STOP
        if self._current_control_mode == "emergency_stopped" and not (l3_pressed or r3_pressed):
            log.info("MotionManager: Payload sin L3/R3 recibido en modo 'emergency_stopped'. Limpiando E-STOP.")
            await self._clear_internal_emergency_stop()
            # Despues de limpiar, el modo es 'gamepad', y se procesa el resto del payload actual.

        # 3. Procesamiento normal del payload
        if self._current_control_mode == "gamepad":
            if hasattr(self.gamepad_controller, 'update_gamepad_state'):
                log.info(f"MotionManager: Reenviando payload normal a MotionGamePad.")
                self.gamepad_controller.update_gamepad_state(payload)
            else: log.error("MotionManager: MotionGamePad no tiene el metodo 'update_gamepad_state'.")
        elif self._current_control_mode == "idle": # Si esta en modo idle, ignora el payload
            log.info(f"MotionManager: Payload de gamepad ignorado. El modo actual ({self._current_control_mode}) no esta activo para movimiento.")

    # Manejador alternativo para una senal explicita de E-Stop, por si la UI
    # la envia como un evento separado del payload normal.
    async def _handle_gamepad_emergency_stop_event(self):
        log.warning(f"MotionManager: _handle_gamepad_emergency_stop_event llamado explicitamente. Se activara el E-STOP interno.")
        await self._trigger_internal_emergency_stop()

    # Devuelve el modo de control actual ('idle', 'gamepad', 'emergency_stopped').
    def get_current_control_mode(self) -> Optional[str]:
        return self._current_control_mode
