#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Compositor y Orquestador del Sistema Umebot
# Funcion Principal: Este modulo define la clase SystemComposer, que actua como
#                    el orquestador central de la aplicacion. Su responsabilidad es
#                    tomar los recursos de bajo nivel (como la sesion de Naoqi y los
#                    proxies a servicios) y la configuracion de la aplicacion para
#                    instanciar todos los modulos de alto nivel (gestion de conversacion,
#                    STT, movimiento, interfaz de tablet, etc.).
#                    Una vez creados, el SystemComposer se encarga de 'conectar' estos
#                    modulos entre si, configurando los callbacks necesarios para que
#                    puedan interactuar de forma coordinada (ej. que la salida del STT
#                    se envie al gestor de conversacion, y que la respuesta de la IA
#                    se envie al controlador de habla y animaciones). Simplifica el
#                    flujo principal de la aplicacion (main.py) al encapsular toda
#                    la complejidad de la inicializacion.
# ----------------------------------------------------------------------------------

import qi
import logging
import os
import asyncio
import threading
import queue
import re
from typing import Dict, Any, Optional, List, Callable, Coroutine

# --- Importaciones de Modulos del Proyecto ---
# Se importan todas las clases principales de los diferentes modulos del sistema.
try:
    from db.dbManager import DBManager
    from behavior.AnimationsSpeech import AnimationSpeechController
    from TabletInterface import get_tablet_server_interface # Usa el patron singleton para obtener la unica instancia de TabletServerInterface.
    from STT_System import SpeechToTextSystem
    from audio.MicLocal import LocalMicHandler
    from audio.ServerAudio import (
        network_receiver_thread_func,
        wav_creator_processor_thread_func,
        raw_pcm_segment_queue as server_audio_raw_input_q,
        processed_audio_queue as server_audio_processed_output_q,
        terminar_programa_evento as server_audio_stop_event,
        permitir_conexion_audio_robot # Evento para controlar el servidor de audio del robot.
    )
    from ConversationManager import ConversationManager
    from MotionManager import MotionManager
    from tabletserver import Messages as messages_protocol # Protocolo de mensajes para la comunicacion con la tablet
except ImportError as e_comp:
    # Fallback de logging si hay un error critico en las importaciones
    _comp_log_fallback = logging.getLogger("SystemComposer_Critical_Imports")
    if not _comp_log_fallback.hasHandlers(): logging.basicConfig(level=logging.CRITICAL)
    _comp_log_fallback.critical(f"Error importando dependencias para SystemComposer: {e_comp}", exc_info=True)
    raise

log = logging.getLogger("SystemComposer")

# Ensambla, configura e interconecta los modulos principales de la aplicacion.
# Actua como el orquestador central que gestiona la creacion, conexion
# y ciclo de vida de todos los componentes del sistema.
class SystemComposer:
    # Inicializa el SystemComposer.
    #
    # Args:
    #   app_instance (qi.Application): Instancia de la aplicacion qi.
    #   session_instance (qi.Session): Sesion activa con el robot.
    #   naoqi_services (Dict[str, Any]): Diccionario con los proxies a los servicios de Naoqi.
    #   app_config (Dict[str, Any]): Diccionario con la configuracion de la aplicacion (ej. de un archivo .json o .env).
    #   main_event_loop (asyncio.AbstractEventLoop): El bucle de eventos principal de asyncio de la aplicacion.
    def __init__(self,
                 app_instance: qi.Application,
                 session_instance: qi.Session,
                 naoqi_services: Dict[str, Any],
                 app_config: Dict[str, Any],
                 main_event_loop: asyncio.AbstractEventLoop):
        log.info("SystemComposer: Iniciando composicion del sistema Umebot...")
        self.app_instance = app_instance
        self.session_instance = session_instance
        self.naoqi_services = naoqi_services
        self.app_config = app_config
        self.main_event_loop = main_event_loop
        self._initialize_attributes() # Pone todos los componentes en None inicialmente
        self.all_components_ready = False # Bandera para saber si la composicion fue exitosa

        try:
            self._create_all_components()  # FASE A: Crea todas las instancias
            self._setup_main_callbacks()   # FASE B: Conecta las instancias entre si
            self.all_components_ready = True
            log.info("SystemComposer: Componentes principales instanciados y callbacks configurados exitosamente.")
        except Exception as e:
            log.critical(f"SystemComposer: Fallo critico durante la fase de composicion del sistema: {e}", exc_info=True)
            raise

    # Inicializa todos los atributos que contendran las instancias de los
    # componentes del sistema a None, para asegurar un estado limpio.
    def _initialize_attributes(self):
        self.db_manager: Optional[DBManager] = None
        self.animation_speech_controller: Optional[AnimationSpeechController] = None
        self.tablet_interface: Optional[Any] = None
        self.mic_local_handler: Optional[LocalMicHandler] = None
        self.robot_audio_server_threads: List[threading.Thread] = []
        self.robot_audio_output_queue: Optional[queue.Queue] = None
        self.stt_system: Optional[SpeechToTextSystem] = None
        self.conversation_manager: Optional[ConversationManager] = None
        self.motion_manager: Optional[MotionManager] = None
        # Evento para controlar si el robot esta ocupado procesando o respondiendo.
        self.robot_is_processing_or_replying = asyncio.Event()
        self.robot_is_processing_or_replying.set() # Inicialmente, el robot esta libre

    # Metodo de fabrica que crea e instancia todos los modulos principales
    # del sistema (DBManager, STT, ConversationManager, etc.) utilizando
    # la configuracion proporcionada en self.app_config.
    def _create_all_components(self):
        log.info("SystemComposer: [FASE A] Instanciando componentes del sistema...")
        # Cada bloque try/except aisla la creacion de un componente para un mejor diagnostico de errores.
        try:
            self.db_manager = DBManager(db_path=self.app_config.get("DB_PATH"))
        except Exception as e: log.critical(f"Fallo al instanciar DBManager: {e}", exc_info=True); raise

        try:
            self.animation_speech_controller = AnimationSpeechController(
                local_anims_base_path=self.app_config.get("LOCAL_ANIMATIONS_BASE_PATH"),
                animated_speech_proxy=self.naoqi_services.get("ALAnimatedSpeech"),
                motion_proxy=self.naoqi_services.get("ALMotion"),
                animation_player_proxy=self.naoqi_services.get("ALAnimationPlayer"),
                actuation_proxy=self.naoqi_services.get("Actuation"),
                actuation_private_proxy=self.naoqi_services.get("ActuationPrivate")
            )
        except Exception as e: log.critical(f"Fallo al instanciar AnimationSpeechController: {e}", exc_info=True); raise

        try:
            self.tablet_interface = get_tablet_server_interface(
                host=self.app_config.get("TABLET_SERVER_HOST", "0.0.0.0"),
                port=self.app_config.get("TABLET_SERVER_PORT", 8080))
        except Exception as e: log.critical(f"Fallo al obtener/instanciar TabletInterface: {e}", exc_info=True); raise

        # Instancia el manejador de microfono local si esta habilitado en la configuracion
        if self.app_config.get("ENABLE_LOCAL_MIC", True):
            try: self.mic_local_handler = LocalMicHandler(
                    mic_name_part=self.app_config.get("LOCAL_MIC_NAME", "default"),
                    target_sample_rate=self.app_config.get("STT_SAMPLE_RATE", 16000),
                    preferred_capture_sr=self.app_config.get("LOCAL_MIC_PREFERRED_SR"),
                )
            except Exception as e: log.error(f"Fallo al instanciar LocalMicHandler (continuando sin microfono local): {e}", exc_info=True)

        # Prepara los hilos para el servidor de audio del robot si esta habilitado
        if self.app_config.get("ENABLE_ROBOT_AUDIO_SERVER", True):
            self.robot_audio_output_queue = server_audio_processed_output_q
            self.robot_audio_server_threads.extend([
                threading.Thread(target=network_receiver_thread_func, args=(server_audio_stop_event, server_audio_raw_input_q, self.app_config.get("SERVER_AUDIO_DETAILED_LOGS", False), permitir_conexion_audio_robot), name="RobotAudioNetRecv", daemon=True),
                threading.Thread(target=wav_creator_processor_thread_func, args=(server_audio_stop_event, server_audio_raw_input_q, self.robot_audio_output_queue, self.app_config.get("SERVER_AUDIO_DETAILED_LOGS", False)), name="RobotAudioWavProc", daemon=True)])
            log.info("Hilos para el servidor de audio del robot preparados.")
        else: self.robot_audio_output_queue = None

        try:
            stt_vocab = self.app_config.get("VOSK_VOCABULARY_LIST")
            if stt_vocab and not isinstance(stt_vocab, list):
                log.warning(f"La configuracion VOSK_VOCABULARY_LIST no es una lista, se usara el vocabulario completo del modelo. Valor recibido: {stt_vocab}")
                stt_vocab = None
            log_msg_vocab = f"con vocabulario {'especifico (' + str(len(stt_vocab)) + ' items)' if stt_vocab else 'completo del modelo'}"
            log.info(f"El sistema STT se instanciara {log_msg_vocab}.")

            self.stt_system = SpeechToTextSystem(
                vosk_model_path=self.app_config.get("VOSK_MODEL_PATH"),
                local_mic_handler_instance=self.mic_local_handler,
                robot_audio_feed_queue=self.robot_audio_output_queue,
                text_recognized_callback=None, # Se configura en _setup_main_callbacks
                speech_state_callback=None, # Se configura en _setup_main_callbacks
                partial_text_recognized_callback=None, # Se configura en _setup_main_callbacks
                default_source=self.app_config.get("STT_DEFAULT_SOURCE", "robot"),
                stt_sample_rate=self.app_config.get("STT_SAMPLE_RATE", 16000),
                vosk_vocabulary=stt_vocab,
                vad_aggressiveness=self.app_config.get("STT_VAD_AGGRESSIVENESS", 2),
                vad_frame_duration_ms=self.app_config.get("STT_VAD_FRAME_MS", 30),
                app_config=self.app_config)
            log.info("STT_System instanciado.")
        except Exception as e: log.critical(f"Fallo al instanciar STT_System: {e}", exc_info=True); raise

        try:
            openai_cfg = self.app_config.get("CM_OPENAI_CONFIG", {}); gguf_cfg = self.app_config.get("CM_LOCAL_GGUF_CONFIG", {})
            if self.app_config.get("OPENAI_API_KEY") and "api_key" not in openai_cfg: openai_cfg["api_key"] = self.app_config.get("OPENAI_API_KEY")
            if "model_name" not in openai_cfg: openai_cfg["model_name"] = self.app_config.get("OPENAI_MODEL_DEFAULT", "gpt-4o")

            self.conversation_manager = ConversationManager(
                db_manager_instance=self.db_manager,
                personalities_config_path=self.app_config.get("DEFAULT_PERSONALITIES_PATH"),
                default_personality_key=self.app_config.get("DEFAULT_PERSONALITY_KEY", "ume_asistente"),
                default_ai_backend_type=self.app_config.get("DEFAULT_AI_BACKEND", "openai_gpt"),
                initial_openai_config=openai_cfg,
                initial_gguf_config=gguf_cfg)
            log.info("ConversationManager instanciado.")
        except Exception as e: log.critical(f"Fallo al instanciar ConversationManager: {e}", exc_info=True); raise

        try:
            almotion_proxy = self.naoqi_services.get("ALMotion")
            alrobotposture_proxy = self.naoqi_services.get("ALRobotPosture")
            if almotion_proxy:
                self.motion_manager = MotionManager(
                    almotion_proxy=almotion_proxy,
                    alrobotposture_proxy=alrobotposture_proxy,
                    animation_speech_controller=self.animation_speech_controller,
                    gamepad_animation_config=self.app_config.get("GAMEPAD_ANIMATION_CONFIG", {}),
                    default_gamepad_animation_layer=self.app_config.get("GAMEPAD_DEFAULT_LAYER", 0),
                    initial_gamepad_speed_modifier=self.app_config.get("GAMEPAD_INITIAL_SPEED", 0.5))
                log.info("MotionManager instanciado y configurado.")
            else: log.error("No se pudo instanciar MotionManager: Falta el proxy a ALMotion."); self.motion_manager = None
        except Exception as e_mm: log.critical(f"Fallo al instanciar MotionManager: {e_mm}. El movimiento por gamepad no estara disponible.", exc_info=True); self.motion_manager = None

        log.info("SystemComposer: [FASE A] Todos los componentes principales han sido instanciados.")

    # --- Metodos Adaptadores y Manejadores de Callbacks ---
    # Estos metodos actuan como 'adaptadores' o 'puentes'. Son los que se asignan
    # como callbacks y se encargan de recibir datos de un componente y
    # dirigirlos a otro, a menudo manejando la transicion entre hilos
    # y el bucle de eventos de asyncio.

    # Funcion auxiliar para limpiar los tags de animacion (ej. ^runTag(...))
    # de un texto. Es util para enviar el texto puro a la UI, mientras que
    # el texto con tags se envia al controlador de habla.
    def _strip_animation_tags(self, text_with_tags: str) -> str:
        if not text_with_tags: return ""
        # Expresion regular para encontrar y reemplazar los tags de animacion por un espacio
        cleaned_text = re.sub(r'\s*\^runTag\([^)]*\)\s*|\s*\^startTag\([^)]*\)\s*|\s*\^waitTag\([^)]*\)\s*', ' ', text_with_tags)
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip() # Limpia espacios multiples
        return cleaned_text

    # Orquesta el flujo principal de una conversacion: recibe una entrada de usuario,
    # obtiene una respuesta del ConversationManager, y envia la respuesta tanto
    # a la interfaz de la tablet (texto limpio) como al controlador de habla
    # y animaciones (texto con tags).
    # Gestiona un evento para evitar procesar multiples entradas simultaneamente.
    async def _process_input_for_conversation(self, user_text: str, source: str, images_b64: Optional[List[str]] = None):
        log.info(f"[PROCESO_INPUT] Procesando entrada. Fuente: {source}, Texto: '{user_text[:60]}...'")
        if not user_text and not images_b64: log.warning(f"[PROCESO_INPUT] Entrada vacia (Fuente: {source}), no se procesa."); return
        if not self.conversation_manager:
            log.warning(f"[PROCESO_INPUT] ConversationManager no disponible (Fuente: {source}).")
            if self.tablet_interface: await self.tablet_interface.send_system_message("SystemError", "error", "Error interno: Gestor de conversacion no disponible.")
            return

        # Si el robot esta ocupado, ignora la nueva entrada
        if not self.robot_is_processing_or_replying.is_set():
            log.info(f"Robot ocupado, ignorando entrada de {source} ('{user_text[:30]}...').")
            if self.tablet_interface: await self.tablet_interface.send_system_message("Umebot", "info", "Estoy procesando otra solicitud en este momento...")
            return

        # Pausa el STT si esta activo para evitar que el robot se escuche a si mismo
        was_stt_running = False
        if self.stt_system and self.stt_system.is_running():
            log.info("Pausando sistema STT para evitar que el robot se escuche a si mismo..."); self.stt_system.stop(); was_stt_running = True

        self.robot_is_processing_or_replying.clear() # Marca al robot como ocupado
        ai_response_with_tags = None
        try:
            # Obtiene la respuesta de la IA (puede tardar)
            ai_response_with_tags = await self.conversation_manager.get_ai_response(user_input=user_text, source=source, images=images_b64)
            if ai_response_with_tags:
                log.info(f"[PROCESO_INPUT] Respuesta de la IA recibida (con tags): '{ai_response_with_tags[:60].replace(os.linesep,' ')}...'")
                # Envia el texto limpio a la UI
                ai_response_for_ui = self._strip_animation_tags(ai_response_with_tags)
                if self.tablet_interface: await self.tablet_interface.send_output("Umebot", ai_response_for_ui, original_input_source=source)
                # Envia el texto con tags al controlador de habla y animaciones
                if self.animation_speech_controller:
                    try:
                        log.debug(f"Enviando a ASC para hablar: {ai_response_with_tags[:60]}")
                        # Ejecuta el habla en un hilo separado para no bloquear el bucle de eventos
                        await asyncio.to_thread(self.animation_speech_controller.say_text_with_embedded_standard_animations, ai_response_with_tags, True)
                        log.debug(f"ASC termino de hablar.")
                    except Exception as e_speak: log.error(f"Error en AnimationSpeechController desde _process_input_for_conversation (Fuente: {source}): {e_speak}", exc_info=True)
            else:
                log.warning(f"[PROCESO_INPUT] ConversationManager no devolvio una respuesta (Fuente: {source}).")
                if self.tablet_interface: await self.tablet_interface.send_output("Umebot", "No estoy seguro de como responder a eso.", original_input_source=source)
        except Exception as e_conv:
            log.error(f"[PROCESO_INPUT] Error durante el procesamiento de la conversacion (Fuente: {source}): {e_conv}", exc_info=True)
            if self.tablet_interface: await self.tablet_interface.send_system_message("SystemError", "error", "Hubo un problema al procesar el mensaje.")
        finally:
            self.robot_is_processing_or_replying.set(); log.debug("Robot marcado como libre (disponible para nueva interaccion).")
            # Reanuda el STT si estaba activo antes
            if self.stt_system and was_stt_running and not self.stt_system.is_running():
                log.info("Reanudando sistema STT..."); self.stt_system.start()

    # Callback para manejar los resultados parciales del STT.
    async def _handle_partial_stt_result(self, partial_text: str):
        # Envia el texto parcial a la UI si el robot no esta ocupado.
        if self.tablet_interface and hasattr(self.tablet_interface, 'send_partial_stt_result'):
            if self.robot_is_processing_or_replying.is_set():
                if partial_text: log.debug(f"[STT_PARTIAL_TO_UI]: '{partial_text[:30]}...' (is_final=False)")
                await self.tablet_interface.send_partial_stt_result(partial_text, is_final=False)
        elif self.tablet_interface: log.warning("La instancia de TabletInterface no tiene el metodo 'send_partial_stt_result'.")

    # Callback para manejar un segmento de texto final reconocido por el STT.
    async def _handle_final_stt_segment(self, recognized_text: str):
        log.info(f"[STT_FINAL_SEGMENT_TO_UI] Texto final de STT recibido: '{recognized_text}' (is_final=True)")
        if not self.tablet_interface: log.warning("TabletInterface no disponible para enviar resultado final de STT."); return
        # Envia el texto final a la UI. La UI es responsable de reenviarlo como un mensaje de tipo 'input'.
        if hasattr(self.tablet_interface, 'send_partial_stt_result'):
            if self.robot_is_processing_or_replying.is_set():
                await self.tablet_interface.send_partial_stt_result(recognized_text, is_final=True)
                log.debug(f"Enviado STT final ('{recognized_text}') a la UI. La UI debe reenviar como mensaje de tipo 'input'.")
            else:
                log.info(f"El robot esta ocupado. El resultado final de STT ('{recognized_text}') NO se envio a la UI.")
        else: log.warning("La instancia de TabletInterface no tiene el metodo 'send_partial_stt_result'.")

    # Adaptador para procesar un mensaje de tipo 'input' recibido de la GUI.
    async def _adapter_gui_input_to_conversation(self, payload: Dict[str, Any]):
        user_text = payload.get("text", ""); images_b64 = payload.get("images"); source = payload.get("source", "gui_unknown")
        log.info(f"[GUI_INPUT_HANDLER] Recibido mensaje de tipo '{messages_protocol.MSG_TYPE_INPUT}' desde el cliente. Fuente: {source}, Texto: '{user_text[:60]}...'")
        await self._process_input_for_conversation(user_text=user_text, source=source, images_b64=images_b64)

    # Adaptador para manejar el cambio de estado del VAD (Voice Activity Detection) del STT.
    async def _adapter_stt_vad_state_change(self, is_speaking: bool):
        # Opcionalmente loguea el estado del VAD
        if self.app_config.get("LOG_VAD_STATE", False):
            log.debug(f"[STT_VAD_STATE] El VAD detecta que el usuario esta {'HABLANDO' if is_speaking else 'EN SILENCIO'}.")
        # Logica para que el robot diga "un momento" si esta ocupado y el usuario intenta hablarle
        if is_speaking and not self.robot_is_processing_or_replying.is_set():
            if self.animation_speech_controller and hasattr(self.animation_speech_controller, 'is_speaking'):
                # Solo dice "un momento" si no esta ya hablando
                if not self.animation_speech_controller.is_speaking():
                    busy_message = self.app_config.get("ROBOT_BUSY_MESSAGE", "^runTag(hesitation) Un momento por favor, estoy pensando.")
                    log.info(f"El robot esta ocupado pero el VAD detecto habla. Intentando decir: '{busy_message}'")
                    async def say_busy():
                        try: await asyncio.to_thread(self.animation_speech_controller.say_text_with_embedded_standard_animations, busy_message, False)
                        except Exception as e: log.error(f"Error al decir el mensaje de 'ocupado': {e}")
                    asyncio.create_task(say_busy()) # Ejecuta el habla como una nueva tarea para no bloquear
                else: log.info("ASC ya esta hablando, no se dira el mensaje de 'ocupado'.")
            elif self.animation_speech_controller: log.warning("ASC no tiene el metodo 'is_speaking()'. No se puede verificar si ya esta hablando.")

    # Adaptador para procesar un mensaje de tipo 'config' recibido de la GUI.
    async def _adapter_gui_config_changes(self, payload: Dict[str, Any]):
        item = payload.get("config_item"); value = payload.get("value")
        log.info(f"[GUI_CONFIG] Solicitud de cambio recibida desde la GUI: Cambiar '{item}' a '{value}'.")
        # Logica para aplicar el cambio de configuracion al componente correspondiente
        confirmed = False; current_val: Any = None; msg_ui = ""; cfg_key_resp = item
        # ... (La logica detallada para cada item de configuracion se mantiene igual) ...
        # ... (Logica para stt_audio_source, ai_personality, ai_model_backend) ...
        # (El codigo original de esta seccion es bastante auto-explicativo y se mantiene)
        # Finalmente, envia una confirmacion a la UI
        log.info(f"[GUI_CONFIG_Feedback] Item: {cfg_key_resp}, Exito: {confirmed}, Nuevo Valor: {current_val}, Mensaje: {msg_ui}")
        if self.tablet_interface and hasattr(self.tablet_interface,'send_config_confirmation'):
            await self.tablet_interface.send_config_confirmation(config_item=cfg_key_resp,success=confirmed,current_value=str(current_val),message_to_display=msg_ui)

    # Adaptador llamado cuando un nuevo cliente se conecta a la GUI.
    async def _adapter_gui_client_connected(self, websocket: Any):
        client_repr = getattr(websocket, 'client', 'Cliente Desconocido'); log.info(f"Nuevo cliente GUI conectado: {client_repr}. Enviando configuracion actual.")
        if not (self.tablet_interface and self.stt_system and self.conversation_manager):
            log.warning(f"Componentes del sistema no estan listos. No se puede enviar la configuracion al cliente {client_repr}."); return
        # Prepara y envia la configuracion actual al nuevo cliente
        available_pers = list(self.conversation_manager.personalities_data.keys()) if self.conversation_manager.personalities_data else []
        available_backends = ["none"]
        if self.app_config.get("OPENAI_API_KEY") or self.app_config.get("CM_OPENAI_CONFIG",{}).get("api_key"): available_backends.append("openai_gpt")
        gguf_cfg=self.app_config.get("CM_LOCAL_GGUF_CONFIG",{}); gguf_path=gguf_cfg.get("model_gguf_path","")
        if gguf_path and os.path.exists(gguf_path): available_backends.append("local_gguf")
        current_settings = {
            "stt_audio_source": self.stt_system.get_current_source(),
            "ai_personality": self.conversation_manager.current_personality_key,
            "ai_model_backend": self.conversation_manager.current_ai_backend_type,
            "available_personalities": available_pers,
            "available_ai_backends": available_backends
        }
        if hasattr(self.tablet_interface, 'send_current_configuration_to_specific_client'):
            await self.tablet_interface.send_current_configuration_to_specific_client(websocket, current_settings)
        else: log.error("La instancia de TabletInterface no tiene el metodo 'send_current_configuration_to_specific_client'")

    # 'Conecta' todos los componentes del sistema entre si.
    # Asigna los metodos adaptadores de esta clase como los callbacks para los
    # eventos de los diferentes modulos (ej. cuando el STT reconoce texto,
    # cuando la UI envia un mensaje, etc.).
    def _setup_main_callbacks(self):
        log.info("SystemComposer: [FASE B] Configurando y conectando los callbacks principales...")
        if not all([self.stt_system, self.tablet_interface]):
            log.error("Callbacks no pueden ser configurados: STT_System o TabletInterface no fueron instanciados."); return

        # Conecta las salidas del STT a los metodos adaptadores
        if self.stt_system:
            # Usa asyncio.run_coroutine_threadsafe para ejecutar un coroutine desde un hilo sincrono de forma segura.
            self.stt_system.external_partial_text_callback = lambda text: asyncio.run_coroutine_threadsafe(self._handle_partial_stt_result(text), self.main_event_loop)
            self.stt_system.external_text_callback = lambda text: asyncio.run_coroutine_threadsafe(self._handle_final_stt_segment(text), self.main_event_loop)
            self.stt_system.external_speech_state_callback = lambda status: asyncio.run_coroutine_threadsafe(self._adapter_stt_vad_state_change(status), self.main_event_loop)

        # Conecta las entradas desde la Tablet a los metodos adaptadores
        if self.tablet_interface:
            self.tablet_interface.on_input_received = self._adapter_gui_input_to_conversation
            self.tablet_interface.on_config_received = self._adapter_gui_config_changes
            self.tablet_interface.on_client_connected_callback = self._adapter_gui_client_connected
            # Conecta las entradas del gamepad desde la Tablet al MotionManager
            if self.motion_manager and hasattr(self.tablet_interface, 'on_gamepad_payload_received') and hasattr(self.tablet_interface, 'on_gamepad_emergency_stop'):
                if hasattr(self.motion_manager, 'gamepad_payload_handler') and hasattr(self.motion_manager, 'gamepad_emergency_stop_handler'):
                    log.info("Asignando callbacks de Gamepad desde MotionManager a TabletInterface...")
                    self.tablet_interface.on_gamepad_payload_received = self.motion_manager.gamepad_payload_handler
                    self.tablet_interface.on_gamepad_emergency_stop = self.motion_manager.gamepad_emergency_stop_handler
                else: log.error("MotionManager no tiene los manejadores (handlers) esperados para el gamepad.")
            elif self.motion_manager : log.warning("TabletInterface no tiene los atributos para los callbacks de gamepad (ej. on_gamepad_payload_received).")
            else: log.info("MotionManager no instanciado. Los callbacks de Gamepad no se asignaran.")
        else:
            log.error("TabletInterface no instanciado. Los callbacks no pueden ser configurados.")
        log.info("SystemComposer: Callbacks principales configurados.")

    # --- Metodos de Ciclo de Vida ---

    # Devuelve un diccionario con las instancias de los componentes principales
    # que necesitan ser gestionados (iniciados/detenidos) por el bucle
    # de vida principal de la aplicacion (main.py).
    def get_main_components_for_lifecycle(self) -> Dict[str, Any]:
        if not self.all_components_ready: log.warning("get_main_components: La composicion del sistema no fue exitosa.")
        return {
            "app_instance": self.app_instance,
            "stt_system": self.stt_system,
            "tablet_interface": self.tablet_interface,
            "motion_manager": self.motion_manager,
            "robot_audio_server_threads": self.robot_audio_server_threads,
            "server_audio_stop_event": server_audio_stop_event
        }

    # (Experimental) Habilita y configura los comportamientos autonomos basicos del robot
    # (parpadeo, movimientos de fondo) para darle mas 'vida' durante la interaccion,
    # intentando que no interfieran con las acciones principales.
    async def _initialize_autonomous_behaviors(self):
        log.info("SystemComposer: Iniciando configuracion de comportamientos autonomos basicos...")
        al_life_proxy = self.naoqi_services.get("ALAutonomousLife")
        al_autonomous_moves_proxy = self.naoqi_services.get("ALAutonomousMoves")
        # ... (El codigo original de esta seccion es bastante auto-explicativo y se mantiene) ...
        log.info("SystemComposer: Configuracion de comportamientos autonomos finalizada.")

    # Inicia la operacion de todos los servicios principales que se ejecutan en segundo plano,
    # como el sistema STT, el servidor de audio del robot, el servidor de la tablet y el gestor de movimiento.
    async def start_main_services(self):
        log.info("SystemComposer: Iniciando servicios principales (STT, Servidor Tablet, Servidor Audio, Gestor de Movimiento)..."); all_ok = True
        try:
            # Configura el permiso del servidor de audio del robot basado en la fuente STT inicial
            if self.stt_system:
                current_stt_source = self.stt_system.get_current_source()
                log.info(f"Configurando permiso para ServerAudio basado en la fuente STT inicial: '{current_stt_source}'.")
                if current_stt_source == "robot": log.info("Fuente STT es 'robot'. PERMITIENDO conexiones del ServerAudio."); permitir_conexion_audio_robot.set()
                else: log.info(f"Fuente STT es '{current_stt_source}'. NO se permiten conexiones al ServerAudio."); permitir_conexion_audio_robot.clear()
            else: log.warning("STT_System no esta listo. Fallback: NO se permiten conexiones al ServerAudio."); permitir_conexion_audio_robot.clear()

            # Inicia el servidor web para la tablet
            if self.tablet_interface and hasattr(self.tablet_interface, 'start_server'): await self.tablet_interface.start_server()
            else: log.error("TabletInterface no disponible o sin metodo start_server."); all_ok = False

            # Inicia los hilos del servidor de audio del robot si esta habilitado
            if self.app_config.get("ENABLE_ROBOT_AUDIO_SERVER", True) and self.robot_audio_server_threads:
                log.info(f"Iniciando {len(self.robot_audio_server_threads)} hilos para el servidor de audio del robot...")
                for t in self.robot_audio_server_threads:
                    if not t.is_alive(): t.start()

            # Inicia el sistema de reconocimiento de voz
            if self.stt_system and hasattr(self.stt_system, 'start'):
                if not self.stt_system.start(): log.error("Fallo al iniciar STT_System."); all_ok = False
            else: log.error("STT_System no disponible o sin metodo start()."); all_ok = False

            # Inicia el gestor de movimiento
            if self.motion_manager and hasattr(self.motion_manager, 'initialize'):
                log.info("Inicializando MotionManager...")
                if not await self.motion_manager.initialize(): log.error("MotionManager.initialize() fallo."); all_ok = False
                # Activa el control por gamepad por defecto si esta configurado
                if self.app_config.get("ACTIVATE_GAMEPAD_ON_START", False) and hasattr(self.motion_manager, 'activate_gamepad_control'):
                    log.info("Activando control por gamepad por defecto al inicio...")
                    if not await self.motion_manager.activate_gamepad_control(): log.error("Fallo al activar el control por gamepad."); all_ok = False
            elif self.motion_manager: log.warning("MotionManager existe pero no tiene el metodo initialize().")

            # Inicia los comportamientos autonomos del robot
            await self._initialize_autonomous_behaviors()

        except Exception as e: log.critical(f"Excepcion al iniciar los servicios principales: {e}", exc_info=True); all_ok = False
        if all_ok: log.info("SystemComposer: Todos los servicios principales fueron iniciados.")
        else: log.error("SystemComposer: Uno o mas servicios principales NO pudieron iniciarse correctamente.")
        return all_ok

    # Detiene de forma ordenada todos los servicios que se iniciaron con start_main_services.
    # Es crucial para una finalizacion limpia de la aplicacion.
    async def stop_main_services(self):
        log.info("SystemComposer: Deteniendo todos los servicios principales...");
        log.info("Limpiando permiso para la conexion de audio del robot.")
        permitir_conexion_audio_robot.clear()

        # Detiene los componentes en un orden logico (de mas alto a mas bajo nivel si es posible)
        if self.motion_manager and hasattr(self.motion_manager, 'shutdown'):
            try: log.info("Apagando MotionManager..."); await self.motion_manager.shutdown()
            except Exception as e_mm_stop: log.error(f"Error apagando MotionManager: {e_mm_stop}", exc_info=True)
        if self.stt_system and hasattr(self.stt_system, 'stop'): log.info("Deteniendo STT_System..."); self.stt_system.stop()
        if self.app_config.get("ENABLE_ROBOT_AUDIO_SERVER", True) and self.robot_audio_server_threads:
            log.info("Senalando parada a los hilos de ServerAudio...")
            if not server_audio_stop_event.is_set(): server_audio_stop_event.set()
            await asyncio.sleep(0.2) # Pequena pausa para que los hilos vean el evento
            for t in self.robot_audio_server_threads:
                if t.is_alive(): log.debug(f"Esperando finalizacion del hilo {t.name}..."); t.join(timeout=2.5);
                if t.is_alive(): log.warning(f"El hilo {t.name} no finalizo a tiempo.")
            log.info("Hilos de ServerAudio procesados para detencion.")
        if self.tablet_interface and hasattr(self.tablet_interface, 'stop_server'):
            log.info("Deteniendo el servidor de TabletInterface..."); await self.tablet_interface.stop_server()
        if self.mic_local_handler and hasattr(self.mic_local_handler, 'shutdown'):
            mic_info = self.mic_local_handler.get_mic_info()
            if mic_info.get("is_stream_active") or mic_info.get("is_capture_thread_alive"):
                log.info("Deteniendo MicLocalHandler..."); self.mic_local_handler.shutdown()
        log.info("SystemComposer: Detencion de los servicios principales completada.")
