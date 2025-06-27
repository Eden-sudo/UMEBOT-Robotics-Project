#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Interprete de Comandos de Gamepad para Movimiento del Robot
# Funcion Principal: Este modulo define la clase MotionGamePad, responsable de
#                    interpretar los datos de entrada recibidos de un gamepad y
#                    traducirlos en comandos de movimiento y acciones para el robot.
#                    Interactua con un RobotHardwareController para el control de la
#                    base movil y con un AnimationSpeechController (opcional) para
#                    ejecutar animaciones o dialogos asignados a los botones.
#                    El script maneja la velocidad de movimiento, el cambio entre
#                    capas de animacion y un sistema de parada de emergencia.
#                    Aunque se concibio la idea de extender este modulo para un
#                    movimiento autonomo avanzado usando ROS2, esta funcionalidad
#                    quedo en una fase conceptual debido a limitaciones de tiempo,
#                    enfocandose la implementacion actual en el control manual robusto.
# ----------------------------------------------------------------------------------

import threading
import time
import logging
from typing import Dict, Any, Optional

# Definiciones de tipo para claridad (se reemplazarian con los tipos reales si estan definidos)
RobotHardwareControllerType = Any
AnimationSpeechControllerType = Any

# Configuracion del logger para este modulo
log = logging.getLogger("MotionGamePad")

# --- Constantes de Configuracion del Gamepad ---
DEFAULT_SPEED_MODIFIER_INIT: float = 0.5   # Modificador de velocidad inicial por defecto
MIN_SPEED_MODIFIER_LIMIT: float = 0.1      # Limite minimo para el modificador de velocidad
MAX_SPEED_MODIFIER_LIMIT: float = 1.0      # Limite maximo para el modificador de velocidad
SPEED_MODIFIER_INCREMENT_STEP: float = 0.1 # Incremento/decremento para el modificador de velocidad
JOYSTICK_DEADZONE: float = 0.08            # Zona muerta para los joysticks (valores menores se ignoran)
GAMEPAD_DATA_TIMEOUT_SEC: float = 0.35     # Timeout para el "hombre muerto": si no hay datos del gamepad, el robot se detiene.

# Interpreta los datos de un gamepad para controlar el movimiento y las acciones
# del robot. Opera en un hilo dedicado para procesar los comandos de forma continua.
class MotionGamePad:
    # Inicializa el manejador de movimiento por gamepad.
    #
    # Args:
    #   robot_hardware_controller (RobotHardwareControllerType): Controlador para el hardware base del robot (requerido).
    #   animation_speech_controller (Optional[AnimationSpeechControllerType]): Controlador para animaciones y habla (opcional).
    #   gamepad_animation_config (Optional[Dict]): Configuracion de mapeo de botones a animaciones/acciones por capas.
    #                                              Formato esperado: {capa_idx: {"boton_id": {"type": "tipo_accion", ...params...}}}
    #   default_animation_layer (int): Capa de animacion inicial a utilizar.
    #   initial_speed_modifier (float): Modificador de velocidad inicial (entre 0.1 y 1.0).
    #
    # Raises:
    #   ValueError: Si robot_hardware_controller no se proporciona.
    def __init__(self,
                 robot_hardware_controller: RobotHardwareControllerType,
                 animation_speech_controller: Optional[AnimationSpeechControllerType] = None,
                 gamepad_animation_config: Optional[Dict[int, Dict[str, Dict[str, Any]]]] = None,
                 default_animation_layer: int = 0,
                 initial_speed_modifier: float = DEFAULT_SPEED_MODIFIER_INIT):

        if robot_hardware_controller is None:
            raise ValueError("MotionGamePad: Se requiere una instancia de RobotHardwareController.")

        self.robot_hw = robot_hardware_controller # Controlador del hardware del robot
        self.asc = animation_speech_controller      # Controlador de animaciones y habla
        self.gamepad_anim_config = gamepad_animation_config if gamepad_animation_config is not None else {} # Config de animaciones

        # Atributos para el estado del gamepad y sincronizacion de hilos
        self._latest_gamepad_payload: Optional[Dict[str, Any]] = None # Ultimo payload de datos del gamepad recibido
        self._payload_lock = threading.Lock()               # Lock para proteger el acceso a _latest_gamepad_payload
        self._new_data_event = threading.Event()            # Evento para senalar la llegada de nuevos datos del gamepad

        # Atributos para el hilo de procesamiento
        self._processing_thread: Optional[threading.Thread] = None # Hilo donde se ejecuta _processing_loop
        self._run_processing_flag = threading.Event()       # Bandera para controlar la ejecucion del hilo de procesamiento
        self._emergency_stop_active_flag = threading.Event()# Bandera para el estado de parada de emergencia interna

        # Modificador de velocidad actual, restringido a los limites definidos
        self.current_speed_modifier: float = max(MIN_SPEED_MODIFIER_LIMIT, min(MAX_SPEED_MODIFIER_LIMIT, initial_speed_modifier))

        # Gestion de capas de animacion
        self.num_animation_layers = len(self.gamepad_anim_config)
        if self.num_animation_layers == 0:
            log.warning("MotionGamePad: No se proporciono configuracion de capas de animacion (gamepad_anim_config esta vacia).")
            self.current_animation_layer: int = 0
        else:
            # Asegura que la capa de animacion por defecto este dentro del rango valido
            self.current_animation_layer: int = default_animation_layer if 0 <= default_animation_layer < self.num_animation_layers else 0
            if not (0 <= default_animation_layer < self.num_animation_layers) and self.num_animation_layers > 0:
                log.warning(f"MotionGamePad: Capa de animacion por defecto {default_animation_layer} fuera de rango. Usando capa 0.")

        # Almacena el estado previo de los botones para detectar flancos (presion unica)
        self._prev_dpad_events: Dict[str, bool] = {}
        self._prev_action_button_events: Dict[str, bool] = {}
        # Almacena las ultimas velocidades enviadas para evitar envios redundantes
        self._last_sent_velocities: Dict[str, float] = {"vx": 0.0, "vy": 0.0, "vtheta": 0.0}

        if not self.asc:
            log.warning("MotionGamePad: AnimationSpeechController no proporcionado. Las animaciones de botones no funcionaran.")

        log.info(f"MotionGamePad inicializado. Capas de animacion: {self.num_animation_layers}, Capa actual: {self.current_animation_layer}, Mod. Velocidad: {self.current_speed_modifier:.2f}")

    # Actualiza el estado mas reciente del gamepad con un nuevo payload de datos.
    # Este metodo es llamado externamente (ej. desde un servidor WebSocket) cuando se recibe
    # informacion del gamepad. Activa un evento para notificar al hilo de procesamiento.
    #
    # Args:
    #   gamepad_payload (Dict[str, Any]): Diccionario con el estado actual de los controles del gamepad.
    def update_gamepad_state(self, gamepad_payload: Dict[str, Any]):
        log.debug(f"MotionGamePad: update_gamepad_state recibido. EStopFlag MGP: {self._emergency_stop_active_flag.is_set()}. Payload (primeros 150c): {str(gamepad_payload)[:150]}")

        # Si la parada de emergencia interna de MotionGamePad esta activa, ignora nuevos payloads.
        if self._emergency_stop_active_flag.is_set():
            log.warning("MotionGamePad: update_gamepad_state - Parada de emergencia interna (MGP) activa, ignorando payload del gamepad.")
            return

        with self._payload_lock: # Protege el acceso concurrente al payload
            self._latest_gamepad_payload = gamepad_payload
        self._new_data_event.set() # Notifica al hilo de procesamiento que hay nuevos datos

    # Activa el estado de parada de emergencia interna y notifica al RobotHardwareController.
    # Detiene inmediatamente cualquier movimiento en curso controlado por este modulo.
    def trigger_emergency_stop(self):
        log.warning("MotionGamePad: Metodo trigger_emergency_stop LLAMADO.")
        self._emergency_stop_active_flag.set() # Activa la bandera de E-STOP interna
        with self._payload_lock: self._latest_gamepad_payload = None # Limpia cualquier payload pendiente
        self._new_data_event.set() # Despierta el hilo de procesamiento para que reaccione al E-STOP

        # Reenvia la senal de E-STOP al controlador de hardware si existe el metodo
        if self.robot_hw and hasattr(self.robot_hw, 'trigger_hardware_emergency_stop'):
            log.info("MotionGamePad: Reenviando trigger_emergency_stop a RobotHardwareController.")
            self.robot_hw.trigger_hardware_emergency_stop()

        # Adicionalmente, este modulo se asegura de que las velocidades sean cero.
        self._last_sent_velocities = {"vx": 0.0, "vy": 0.0, "vtheta": 0.0}
        if self.robot_hw and hasattr(self.robot_hw, 'set_base_velocities'):
            self.robot_hw.set_base_velocities(0.0, 0.0, 0.0) # Envia velocidades cero inmediatamente

    # Limpia el estado de parada de emergencia interna, permitiendo reanudar el control.
    # Resetea el payload del gamepad para evitar procesar datos antiguos.
    def clear_emergency_stop(self):
        log.info("MotionGamePad: Metodo clear_emergency_stop LLAMADO.")
        self._emergency_stop_active_flag.clear() # Limpia la bandera de E-STOP interna
        # Resetea el payload para evitar procesar datos que pudieron llegar durante el E-STOP.
        with self._payload_lock:
            self._latest_gamepad_payload = None
        self._new_data_event.clear() # Limpia cualquier evento de datos pendiente del periodo de E-STOP
        log.info("MotionGamePad: Bandera interna de parada de emergencia (_emergency_stop_active_flag) limpiada.")

    # Actualiza el modificador de velocidad del gamepad con un valor global.
    # Asegura que el nuevo valor este dentro de los limites definidos.
    #
    # Args:
    #   global_speed_factor (float): Nuevo factor de velocidad (se espera entre 0.1 y 1.0).
    def update_speed_factor_from_global(self, global_speed_factor: float):
        self.current_speed_modifier = max(MIN_SPEED_MODIFIER_LIMIT, min(MAX_SPEED_MODIFIER_LIMIT, global_speed_factor))
        log.info(f"MotionGamePad: Modificador de velocidad actualizado por factor global a: {self.current_speed_modifier:.2f}")

    # Procesa los eventos del D-Pad (cruceta) del gamepad.
    # Arriba/Abajo: ajustan el modificador de velocidad.
    # Izquierda/Derecha: cambian la capa actual de animaciones (si estan configuradas).
    # Solo actua sobre el flanco de subida (cuando se presiona por primera vez).
    #
    # Args:
    #   dpad_events (Dict[str, bool]): Estado actual de los botones del D-Pad (ej. {"up": True}).
    def _process_dpad_events(self, dpad_events: Dict[str, bool]):
        if not dpad_events: return # No hacer nada si no hay eventos de D-Pad
        if not self._prev_dpad_events: self._prev_dpad_events = {k: False for k in dpad_events} # Inicializa estado previo si es la primera vez

        # D-Pad Arriba: Aumenta el modificador de velocidad
        if dpad_events.get("up") and not self._prev_dpad_events.get("up", False):
            self.current_speed_modifier = min(MAX_SPEED_MODIFIER_LIMIT, self.current_speed_modifier + SPEED_MODIFIER_INCREMENT_STEP)
            log.info(f"MotionGamePad D-Pad: Modificador de Velocidad AUMENTADO a: {self.current_speed_modifier:.2f}")
        # D-Pad Abajo: Disminuye el modificador de velocidad
        if dpad_events.get("down") and not self._prev_dpad_events.get("down", False):
            self.current_speed_modifier = max(MIN_SPEED_MODIFIER_LIMIT, self.current_speed_modifier - SPEED_MODIFIER_INCREMENT_STEP)
            log.info(f"MotionGamePad D-Pad: Modificador de Velocidad DISMINUIDO a: {self.current_speed_modifier:.2f}")

        # D-Pad Izquierda/Derecha: Cambian la capa de animacion, si estan configuradas
        if self.num_animation_layers > 0:
            if dpad_events.get("left") and not self._prev_dpad_events.get("left", False):
                self.current_animation_layer = (self.current_animation_layer - 1 + self.num_animation_layers) % self.num_animation_layers # Ciclico hacia atras
                log.info(f"MotionGamePad D-Pad: Capa de Animacion CAMBIADA a: {self.current_animation_layer}")
            if dpad_events.get("right") and not self._prev_dpad_events.get("right", False):
                self.current_animation_layer = (self.current_animation_layer + 1) % self.num_animation_layers # Ciclico hacia adelante
                log.info(f"MotionGamePad D-Pad: Capa de Animacion CAMBIADA a: {self.current_animation_layer}")
        elif any(dpad_events.get(k) and not self._prev_dpad_events.get(k, False) for k in ["left", "right"]):
            log.info("MotionGamePad D-Pad: Intento de cambio de capa, pero no hay capas de animacion configuradas.")

        self._prev_dpad_events = dpad_events.copy() # Guarda el estado actual para la proxima deteccion de flanco

    # Procesa los eventos de los botones de accion (ej. A, B, X, Y) del gamepad.
    # Ejecuta la animacion o accion configurada para el boton presionado en la capa actual,
    # utilizando el AnimationSpeechController si esta disponible y configurado.
    # Solo actua sobre el flanco de subida (cuando se presiona por primera vez).
    #
    # Args:
    #   action_button_events (Dict[str, bool]): Estado actual de los botones de accion.
    def _process_action_button_events(self, action_button_events: Dict[str, bool]):
        if not action_button_events: return
        if not self._prev_action_button_events: self._prev_action_button_events = {k: False for k in action_button_events}

        # Verifica si el controlador de animaciones y habla esta disponible
        if not self.asc:
            if any(action_button_events.values()): log.warning("MotionGamePad Actions: AnimationSpeechController no disponible. No se ejecutaran acciones de botones.");
            self._prev_action_button_events = action_button_events.copy(); return

        # Verifica si hay configuracion de animaciones
        if not self.gamepad_anim_config or self.num_animation_layers == 0:
            if any(action_button_events.values()): log.info("MotionGamePad Actions: Sin configuracion de animaciones para los botones.");
            self._prev_action_button_events = action_button_events.copy(); return

        # Obtiene la configuracion para la capa actual
        current_layer_config = self.gamepad_anim_config.get(self.current_animation_layer)
        if not current_layer_config:
            if any(action_button_events.values()): log.warning(f"MotionGamePad Actions: No hay configuracion de animaciones para la capa {self.current_animation_layer}.")
            self._prev_action_button_events = action_button_events.copy(); return

        # Itera sobre los botones de accion definidos (ej. a, b, x, y)
        for btn_key in ["a", "b", "x", "y"]: # Se pueden anadir otros botones aqui si es necesario
            # Detecta flanco de subida (boton presionado ahora pero no antes)
            if action_button_events.get(btn_key) and not self._prev_action_button_events.get(btn_key, False):
                action_config = current_layer_config.get(btn_key)
                if action_config and isinstance(action_config, dict) and action_config.get("type") != "none":
                    action_type = action_config.get("type")
                    log.info(f"MotionGamePad Actions: Boton '{btn_key}' (Capa {self.current_animation_layer}) PRESIONADO. Config: {action_config}")
                    # Ejecuta la accion segun el tipo configurado
                    if action_type == "local_anim" and hasattr(self.asc, 'play_local_animation_by_category'):
                        cat = action_config.get("category"); name = action_config.get("name")
                        if cat: log.info(f"   -> ASC: Ejecutando local_anim: Categoria='{cat}', Nombre='{name if name else '[ALEATORIO]'}'"); self.asc.play_local_animation_by_category(cat, name, wait=False)
                        else: log.warning(f"   -> ASC: Falta 'category' para la accion local_anim del boton '{btn_key}'")
                    elif action_type == "standard_tag" and hasattr(self.asc, 'play_standard_animation_by_tag'):
                        tag = action_config.get("tag")
                        if tag: log.info(f"   -> ASC: Ejecutando standard_tag: '{tag}'"); self.asc.play_standard_animation_by_tag(tag, wait=False)
                        else: log.warning(f"   -> ASC: Falta 'tag' para la accion standard_tag del boton '{btn_key}'")
                    elif action_type == "speak_annotated" and hasattr(self.asc, 'say_text_with_embedded_standard_animations'):
                        text_to_say = action_config.get("text")
                        if text_to_say: log.info(f"   -> ASC: Ejecutando speak_annotated: '{text_to_say[:30]}...'"); self.asc.say_text_with_embedded_standard_animations(text_to_say, wait_for_speech=False)
                        else: log.warning(f"   -> ASC: Falta 'text' para la accion speak_annotated del boton '{btn_key}'")
                    else: # Tipo de accion desconocido o no manejable
                        log.warning(f"   -> Accion del boton '{btn_key}': Tipo '{action_type}' desconocido o no manejable por AnimationSpeechController.")
                elif action_config and action_config.get("type") == "none": # Accion configurada como "ninguna"
                    log.info(f"MotionGamePad Actions: Boton '{btn_key}' (Capa {self.current_animation_layer}) configurado como 'none', no se ejecuta accion.")
        self._prev_action_button_events = action_button_events.copy() # Guarda estado actual para proximo flanco

    # Bucle principal ejecutado en un hilo dedicado para el procesamiento continuo de los datos del gamepad.
    # Espera nuevos datos del gamepad y, si se reciben, procesa los joysticks para movimiento
    # (aplicando deadzone y modificador de velocidad), y los eventos de D-Pad y botones de accion.
    # Si no se reciben datos del gamepad dentro de un timeout (GAMEPAD_DATA_TIMEOUT_SEC),
    # detiene al robot (mecanismo de "hombre muerto").
    # Tambien maneja el estado de parada de emergencia.
    def _processing_loop(self):
        log.info(f"MotionGamePad Loop: Hilo de procesamiento '{threading.current_thread().name}' INICIADO.")
        while self._run_processing_flag.is_set(): # El bucle se ejecuta mientras la bandera este activa
            try:
                # Si la parada de emergencia interna esta activa, detener el robot y esperar
                if self._emergency_stop_active_flag.is_set():
                    # Solo envia velocidades cero si el robot se estaba moviendo
                    if any(abs(v) > 0.001 for v in self._last_sent_velocities.values()):
                        if self.robot_hw and hasattr(self.robot_hw, 'set_base_velocities'):
                            log.info("MotionGamePad Loop: E-STOP MGP activo - Enviando velocidades cero a RobotHardwareController.")
                            self.robot_hw.set_base_velocities(0.0, 0.0, 0.0)
                        self._last_sent_velocities = {"vx": 0.0, "vy": 0.0, "vtheta": 0.0}
                    time.sleep(0.1) # Espera un poco antes de volver a verificar las banderas
                    continue # Vuelve al inicio del bucle

                # Espera nuevos datos del gamepad, con un timeout
                event_is_set = self._new_data_event.wait(timeout=GAMEPAD_DATA_TIMEOUT_SEC)

                current_payload: Optional[Dict[str, Any]] = None
                if event_is_set: # Si se recibieron nuevos datos
                    self._new_data_event.clear() # Limpia el evento para la proxima senal
                    with self._payload_lock: # Acceso seguro al payload
                        current_payload = self._latest_gamepad_payload
                        self._latest_gamepad_payload = None # Consume el payload (procesado una vez)

                if current_payload: # Si hay un payload valido para procesar
                    # Procesa eventos de D-Pad (velocidad, capas de animacion)
                    dpad = current_payload.get("dpad_events", {})
                    self._process_dpad_events(dpad)

                    # Procesa eventos de botones de accion (animaciones, habla)
                    actions = current_payload.get("action_button_events", {})
                    self._process_action_button_events(actions)

                    # Obtiene valores de los joysticks del payload
                    left_stick = current_payload.get("left_stick", {"x": 0.0, "y": 0.0})
                    right_stick = current_payload.get("right_stick", {"x": 0.0, "y": 0.0})

                    raw_ly = left_stick.get("y", 0.0)  # Y del joystick izquierdo
                    raw_lx = left_stick.get("x", 0.0)  # X del joystick izquierdo
                    raw_rx = right_stick.get("x", 0.0) # X del joystick derecho (usado para rotacion)

                    # --- IMPORTANTE: AJUSTE DE SIGNOS PARA VELOCIDADES DEL ROBOT ---
                    # Esta seccion es CRUCIAL para que el robot se mueva en la direccion esperada.
                    # El comportamiento de ALMotion es:
                    #   vx POSITIVO = Robot se mueve HACIA ADELANTE
                    #   vy POSITIVO = Robot se desplaza lateralmente HACIA LA IZQUIERDA (strafe)
                    #   vtheta POSITIVO = Robot rota HACIA LA IZQUIERDA (antihorario)
                    # Debes VERIFICAR los valores que tu gamepad envia para cada eje (raw_ly, raw_lx, raw_rx)
                    # y ajustar los signos de vx_in, vy_in, vtheta_in segun corresponda.
                    # Los logs de advertencia te ayudaran a depurar esto.

                    # Movimiento Adelante/Atras (vx) - Mapeado a raw_ly (eje Y del joystick izquierdo)
                    # ASUNCION ACTUAL: Joystick ARRIBA produce raw_ly POSITIVO. Para ADELANTE (vx+), vx_in = raw_ly.
                    # Si tu joystick ARRIBA produce raw_ly NEGATIVO, entonces vx_in = -raw_ly.
                    vx_in = raw_ly  # ¡¡¡VERIFICAR ESTO CON TU GAMEPAD ESPECIFICO!!!
                    log.debug(f"MotionGamePad: raw_ly={raw_ly:.3f} -> vx_in(pre-deadzone)={vx_in:.3f}. Si el robot no avanza/retrocede correctamente, ajusta el signo de vx_in.")

                    # Desplazamiento Lateral (vy) - Mapeado a raw_lx (eje X del joystick izquierdo)
                    # ASUNCION ACTUAL: Joystick DERECHA produce raw_lx POSITIVO. Para STRAFE DERECHA (vy-), vy_in = -raw_lx.
                    # Si tu joystick DERECHA produce raw_lx NEGATIVO, entonces vy_in = raw_lx.
                    vy_in = -raw_lx # ¡¡¡VERIFICAR ESTO CON TU GAMEPAD ESPECIFICO!!!
                    log.debug(f"MotionGamePad: raw_lx={raw_lx:.3f} -> vy_in(pre-deadzone)={vy_in:.3f}. Si el robot no se desplaza lateralmente bien, ajusta el signo de vy_in.")

                    # Rotacion (vtheta) - Mapeado a raw_rx (eje X del joystick derecho)
                    # ASUNCION ACTUAL: Joystick DERECHA produce raw_rx POSITIVO. Para ROTAR DERECHA (vtheta-), vtheta_in = -raw_rx.
                    # Si tu joystick DERECHA produce raw_rx NEGATIVO, entonces vtheta_in = raw_rx.
                    vtheta_in = -raw_rx # ¡¡¡VERIFICAR ESTO CON TU GAMEPAD ESPECIFICO!!!
                    log.debug(f"MotionGamePad: raw_rx={raw_rx:.3f} -> vtheta_in(pre-deadzone)={vtheta_in:.3f}. Si el robot no rota correctamente, ajusta el signo de vtheta_in.")
                    # --- FIN DE AJUSTE DE SIGNOS ---

                    # Aplica zona muerta a los valores de entrada, usando el valor raw original del eje correspondiente
                    vx_applied = vx_in if abs(raw_ly) > JOYSTICK_DEADZONE else 0.0
                    vy_applied = vy_in if abs(raw_lx) > JOYSTICK_DEADZONE else 0.0
                    vtheta_applied = vtheta_in if abs(raw_rx) > JOYSTICK_DEADZONE else 0.0

                    # Aplica el modificador de velocidad actual
                    vx_t = vx_applied * self.current_speed_modifier
                    vy_t = vy_applied * self.current_speed_modifier
                    vtheta_t = vtheta_applied * self.current_speed_modifier

                    # Determina si las velocidades calculadas deben enviarse (si han cambiado o si el robot estaba parado y ahora se mueve)
                    should_send_velocities = False
                    if abs(vx_t - self._last_sent_velocities["vx"]) > 0.001 or \
                       abs(vy_t - self._last_sent_velocities["vy"]) > 0.001 or \
                       abs(vtheta_t - self._last_sent_velocities["vtheta"]) > 0.001:
                        should_send_velocities = True
                    elif (abs(vx_t) > 0.001 or abs(vy_t) > 0.001 or abs(vtheta_t) > 0.001) and \
                         all(abs(v) < 0.001 for v in self._last_sent_velocities.values()): # Si estaba parado y ahora se mueve
                        should_send_velocities = True

                    # Envia las velocidades al controlador de hardware si es necesario
                    if self.robot_hw and hasattr(self.robot_hw, 'set_base_velocities'):
                        if should_send_velocities:
                            log.info(f"MotionGamePad -> RHC: LLAMANDO set_base_velocities(vx={vx_t:.2f}, vy={vy_t:.2f}, vtheta={vtheta_t:.2f})")
                            self.robot_hw.set_base_velocities(vx_t, vy_t, vtheta_t)
                            self._last_sent_velocities = {"vx": vx_t, "vy": vy_t, "vtheta": vtheta_t} # Actualiza ultimas velocidades enviadas
                else: # Timeout del evento (no hay nuevos datos del gamepad) - Mecanismo de "hombre muerto"
                    if self.robot_hw and hasattr(self.robot_hw, 'set_base_velocities') and not self._emergency_stop_active_flag.is_set():
                        # Si el robot se estaba moviendo, detenerlo
                        if any(abs(v) > 0.001 for v in self._last_sent_velocities.values()):
                            log.info("MotionGamePad: Timeout de datos del gamepad (mecanismo de 'hombre muerto'). Enviando velocidades cero.")
                            self.robot_hw.set_base_velocities(0.0, 0.0, 0.0)
                            self._last_sent_velocities = {"vx": 0.0, "vy": 0.0, "vtheta": 0.0}
            except Exception as e_loop: # Captura cualquier excepcion no manejada en el bucle
                log.error(f"MotionGamePad Loop: EXCEPCION NO MANEJADA EN EL BUCLE DE PROCESAMIENTO: {e_loop}", exc_info=True)
                self._run_processing_flag.clear() # Detiene el bucle en caso de error grave para evitar repeticion
                break # Sale del bucle while

        log.info(f"MotionGamePad Loop: Hilo de procesamiento '{threading.current_thread().name}' FINALIZANDO.")
        # Asegura que el robot se detenga si el hilo termina por alguna razon (excepto si E-STOP ya lo manejo)
        if self.robot_hw and hasattr(self.robot_hw, 'set_base_velocities') and not self._emergency_stop_active_flag.is_set():
            log.info("MotionGamePad Loop: Asegurando detencion del robot al finalizar el hilo de procesamiento.")
            self.robot_hw.set_base_velocities(0.0, 0.0, 0.0)

    # Inicia el procesamiento de los comandos del gamepad.
    # Realiza una inicializacion del hardware del robot a traves del RobotHardwareController
    # y luego inicia el hilo del bucle de procesamiento (_processing_loop).
    #
    # Returns:
    #   bool: True si el hilo de procesamiento se inicio correctamente, False en caso contrario.
    def start_processing(self) -> bool:
        if self._processing_thread and self._processing_thread.is_alive():
            log.info("MotionGamePad: Hilo de procesamiento ya se encuentra activo."); return True

        if not self.robot_hw: # Verificacion critica
            log.error("MotionGamePad: RobotHardwareController no esta asignado. No se puede iniciar el procesamiento."); return False

        log.info("MotionGamePad: Solicitando inicializacion fisica del robot a RobotHardwareController...")
        # Intenta inicializar el hardware del robot a traves del controlador inyectado
        if hasattr(self.robot_hw, 'initialize_robot'):
            if not self.robot_hw.initialize_robot():
                log.error("MotionGamePad: Fallo al inicializar RobotHardwareController. No se iniciara el hilo de procesamiento."); return False
        else: # Si el RHC no tiene el metodo esperado
            log.error("MotionGamePad: RobotHardwareController no tiene el metodo 'initialize_robot'. No se iniciara el hilo."); return False
        log.info("MotionGamePad: RobotHardwareController inicializado correctamente.")

        self._run_processing_flag.set() # Habilita la ejecucion del bucle de procesamiento
        self._emergency_stop_active_flag.clear() # Asegura que no este en E-STOP al iniciar
        self._new_data_event.clear() # Limpia eventos de datos pendientes
        with self._payload_lock: self._latest_gamepad_payload = None # Limpia cualquier payload antiguo

        # Crea e inicia el hilo de procesamiento
        self._processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._processing_thread.name = "MotionGamePadProcThread" # Nombre descriptivo para el hilo
        self._processing_thread.start()
        log.info(f"MotionGamePad: Hilo de procesamiento '{self._processing_thread.name}' iniciado con exito.")
        return True

    # Detiene el hilo de procesamiento de comandos del gamepad de forma ordenada.
    # Senala al hilo que debe terminar, espera su finalizacion y, opcionalmente,
    # solicita la liberacion de los recursos del robot a traves del RobotHardwareController.
    #
    # Args:
    #   emergency_stop_invoked (bool): Indica si esta detencion es parte de una parada de emergencia global.
    #                                  Si es True, podria omitirse la liberacion de hardware aqui,
    #                                  ya que otro modulo (ej. MotionManager) podria gestionarlo.
    def stop_processing(self, emergency_stop_invoked: bool = False):
        log.info(f"MotionGamePad: Solicitando detencion del procesamiento (Invocado como parte de E-STOP global: {emergency_stop_invoked})...")
        self._run_processing_flag.clear() # Indica al hilo de procesamiento que debe detenerse
        self._new_data_event.set()  # Despierta el hilo si esta esperando en _new_data_event.wait()

        # Espera a que el hilo de procesamiento termine
        if self._processing_thread and self._processing_thread.is_alive():
            log.debug("MotionGamePad: Esperando finalizacion del hilo de procesamiento...")
            self._processing_thread.join(timeout=GAMEPAD_DATA_TIMEOUT_SEC + 0.5) # Espera un tiempo prudencial
            if self._processing_thread.is_alive(): log.warning("MotionGamePad: Hilo de procesamiento no termino a tiempo.")
        self._processing_thread = None # Limpia la referencia al hilo

        # La detencion final del robot (set_base_velocities(0,0,0)) se maneja al final del _processing_loop,
        # o por trigger_emergency_stop, o por el timeout del gamepad.

        # Si no es una parada de emergencia global, se puede liberar el hardware del robot.
        if not emergency_stop_invoked:
            if self.robot_hw and hasattr(self.robot_hw, 'release_robot'):
                log.info("MotionGamePad: Detencion normal. Solicitando liberacion de los recursos del robot a RobotHardwareController...")
                self.robot_hw.release_robot()
        else: # Si es parte de un E-STOP global
            log.info("MotionGamePad: Procesamiento detenido como parte de una parada de emergencia global. No se solicitara 'release_robot' aqui (MotionManager podria gestionarlo).")

        log.info("MotionGamePad: Procesamiento detenido.")
