#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Sistema de Reconocimiento de Voz (Speech-To-Text)
# Funcion Principal: Este modulo define la clase STT_System, que actua como un
#                    orquestador de alto nivel para la funcionalidad de
#                    reconocimiento de voz. Sus responsabilidades clave incluyen:
#                    - Gestionar multiples fuentes de audio: puede procesar audio
#                      tanto de un microfono local (a traves de MicLocalHandler) como
#                      de un flujo de red proveniente del robot (a traves de una cola).
#                    - Permitir el cambio dinamico entre estas fuentes de audio.
#                    - Utilizar el motor de reconocimiento Vosk (a traves de AudioProcessor)
#                      para convertir el audio en texto.
#                    - Opcionalmente, usar Deteccion de Actividad de Voz (VAD) con
#                      webrtcvad para detectar cuando un usuario esta hablando y
#                      finalizar el reconocimiento automaticamente tras un silencio.
#                    - Comunicar los resultados (parciales y finales) y el estado del
#                      VAD al resto del sistema mediante funciones callback.
# ----------------------------------------------------------------------------------

import sys
import os
import time
import threading
import queue
import traceback
import logging
from typing import Optional, Callable, List, Union, Dict, Any

# --- Importaciones de Modulos del Proyecto ---
try:
    from audio.MicLocal import LocalMicHandler
    from audio.ManagerProcessAudio import AudioProcessor
except ImportError as e_imp:
    _critical_log_stt_imp = logging.getLogger("STT_System_Import_Critical")
    if not _critical_log_stt_imp.hasHandlers():
        logging.basicConfig(level=logging.CRITICAL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    _critical_log_stt_imp.critical(f"ERROR CRITICO [STT_System]: Importacion fallida de modulos 'audio.*': {e_imp}. "
                                   "Asegurate que STT_System.py este en el directorio raiz y que los modulos "
                                   "se encuentren en la carpeta 'audio/'.", exc_info=True)
    sys.exit(1)

# Importacion opcional de webrtcvad para Deteccion de Actividad de Voz
try:
    import webrtcvad
    logging.getLogger("STT_System.VAD_Import").info("webrtcvad encontrado e importado. VAD disponible.")
except ImportError:
    logging.getLogger("STT_System.VAD_Import").warning("webrtcvad no encontrado. La Deteccion de Actividad de Voz (VAD) estara desactivada.")
    webrtcvad = None

log = logging.getLogger("STT_System")

# Orquesta el proceso de Speech-To-Text (STT) desde multiples fuentes de audio,
# utilizando Vosk para el reconocimiento y, opcionalmente, VAD para la deteccion de habla.
class SpeechToTextSystem:
    # Inicializa el sistema de STT.
    #
    # Args:
    #   vosk_model_path (str): Ruta al directorio del modelo Vosk.
    #   text_recognized_callback (Optional[Callable]): Callback para resultados de texto finales.
    #   local_mic_handler_instance (Optional[LocalMicHandler]): Instancia para capturar audio local.
    #   robot_audio_feed_queue (Optional[queue.Queue]): Cola para recibir audio del robot.
    #   speech_state_callback (Optional[Callable]): Callback para cambios de estado del VAD (hablando/silencio).
    #   partial_text_recognized_callback (Optional[Callable]): Callback para resultados de texto parciales.
    #   default_source (str): Fuente de audio por defecto ('local', 'robot', o 'none').
    #   stt_sample_rate (int): Tasa de muestreo requerida por el modelo STT (ej. 16000 Hz).
    #   vad_aggressiveness (int): Nivel de agresividad del VAD (0-3, mas alto es mas agresivo).
    #   vad_frame_duration_ms (int): Duracion en ms de cada frame de audio para el VAD (10, 20 o 30).
    #   vosk_vocabulary (Optional[List[str]]): Vocabulario especifico para guiar a Vosk y mejorar la precision.
    #   app_config (Optional[Dict]): Diccionario de configuracion de la aplicacion para parametros adicionales.
    def __init__(self,
                 vosk_model_path: str,
                 text_recognized_callback: Optional[Callable[[str], None]],
                 local_mic_handler_instance: Optional[LocalMicHandler] = None,
                 robot_audio_feed_queue: Optional[queue.Queue] = None,
                 speech_state_callback: Optional[Callable[[bool], None]] = None,
                 partial_text_recognized_callback: Optional[Callable[[str], None]] = None,
                 default_source: str = "robot",
                 stt_sample_rate: int = 16000,
                 vad_aggressiveness: int = 2,
                 vad_frame_duration_ms: int = 30,
                 vosk_vocabulary: Optional[List[str]] = None,
                 app_config: Optional[Dict[str, Any]] = None
                 ):
        log.info(f"Iniciando STT_System (Vosk SR: {stt_sample_rate}Hz, VAD Agresividad: {vad_aggressiveness}, Fuente por Defecto: '{default_source}')...")
        self.app_config = app_config if app_config is not None else {}

        # Validacion de los callbacks proporcionados
        if text_recognized_callback and not callable(text_recognized_callback): raise TypeError("text_recognized_callback debe ser una funcion o None.")
        if speech_state_callback and not callable(speech_state_callback): raise TypeError("speech_state_callback debe ser una funcion o None.")
        if partial_text_recognized_callback and not callable(partial_text_recognized_callback): raise TypeError("partial_text_recognized_callback debe ser una funcion o None.")

        # Asignacion de las fuentes de audio y callbacks externos
        self.local_mic_handler = local_mic_handler_instance
        self.robot_audio_queue = robot_audio_feed_queue
        self.external_text_callback = text_recognized_callback
        self.external_speech_state_callback = speech_state_callback
        self.external_partial_text_callback = partial_text_recognized_callback
        self.stt_sample_rate = stt_sample_rate

        # Instanciacion del procesador de audio Vosk
        self.audio_processor: Optional[AudioProcessor] = None
        try:
            log.debug(f"Creando AudioProcessor (Vocabulario: {'Si, con ' + str(len(vosk_vocabulary)) + ' terminos' if vosk_vocabulary else 'No - Modelo completo'})")
            self.audio_processor = AudioProcessor(
                vosk_model_path=vosk_model_path,
                text_recognized_callback=self._internal_text_callback,
                sample_rate=self.stt_sample_rate,
                vocabulary=vosk_vocabulary,
                partial_text_callback=self._internal_partial_text_callback
            )
        except Exception as e_ap: log.critical(f"Fallo al crear AudioProcessor: {e_ap}", exc_info=True); raise

        # Instanciacion del VAD si la libreria esta disponible
        self.vad: Optional[webrtcvad.Vad] = None
        self.vad_frame_bytes: int = 0
        if webrtcvad:
            if self.stt_sample_rate not in [8000, 16000, 32000, 48000]:
                log.warning(f"La tasa de muestreo STT ({self.stt_sample_rate}Hz) no es estandar para VAD WebRTC. Podria no funcionar correctamente.")
            try:
                self.vad = webrtcvad.Vad(vad_aggressiveness)
                # Calcula el numero de bytes por frame para el VAD (16-bit PCM)
                self.vad_frame_bytes = int(self.stt_sample_rate * (vad_frame_duration_ms / 1000.0) * 2)
                log.info(f"VAD inicializado (Agresividad: {vad_aggressiveness}, Frame: {vad_frame_duration_ms}ms, {self.vad_frame_bytes} bytes por frame)")
            except Exception as e_vad: log.error(f"Fallo al inicializar VAD: {e_vad}. VAD estara desactivado.", exc_info=True); self.vad = None
        else:
            log.info("VAD no esta disponible (webrtcvad no fue importado).")

        # Atributos de estado interno y control de hilos
        self._active_source: str = "none"
        self._is_running: bool = False
        self._stop_event: threading.Event = threading.Event()
        self._processing_thread: Optional[threading.Thread] = None
        self._is_currently_speaking_vad: bool = False # Estado actual del VAD (hablando/silencio)
        self._vad_buffer: bytes = b'' # Buffer para acumular audio para el VAD
        self._lock: threading.Lock = threading.Lock() # Lock para proteger el cambio de fuente

        # Configura la fuente de audio inicial sin activarla
        self._set_initial_source(default_source)
        log.info(f"STT_System inicializado. Fuente configurada inicial: '{self._active_source}' (aun no activa).")

    # --- Callbacks Internos (Puente hacia el Exterior) ---
    # Estos metodos son llamados por AudioProcessor y a su vez llaman a los callbacks externos
    # proporcionados al STT_System, desacoplando los componentes.

    # Recibe el texto parcial de AudioProcessor y lo reenvia al callback externo.
    def _internal_partial_text_callback(self, partial_text: str):
        if self.external_partial_text_callback:
            try: self.external_partial_text_callback(partial_text)
            except Exception as e_cb_partial: log.error(f"Error ejecutando el callback externo de texto parcial: {e_cb_partial}", exc_info=True)

    # Recibe el texto final de AudioProcessor y lo reenvia al callback externo.
    def _internal_text_callback(self, recognized_text: str):
        if not recognized_text or not isinstance(recognized_text, str): return
        final_text = recognized_text.strip()
        if not final_text: return
        log.info(f"STT: Texto Final/Segmento Reconocido -> '{final_text}'")
        if self.external_text_callback:
            try: self.external_text_callback(final_text)
            except Exception as e_cb_final: log.error(f"Error ejecutando el callback externo de texto final: {e_cb_final}", exc_info=True)
        else: log.warning("STT: Callback externo para texto final no esta configurado. El texto reconocido se perdera.")

    # --- Metodos de Gestion de Fuentes de Audio ---

    # Configura la fuente de audio inicial al crear la instancia, sin activarla.
    def _set_initial_source(self, source_name: str):
        with self._lock:
            if source_name == "robot":
                if self.robot_audio_queue: self._active_source = "robot"
                else: log.warning("No se puede establecer 'robot' como fuente inicial: la cola de audio del robot no fue proporcionada."); self._active_source = "none"
            elif source_name == "local":
                if self.local_mic_handler: self._active_source = "local"
                else: log.warning("No se puede establecer 'local' como fuente inicial: MicLocalHandler no fue proporcionado."); self._active_source = "none"
            else:
                if source_name != "none": log.warning(f"Fuente de audio inicial '{source_name}' no reconocida. Se usara 'none'.")
                self._active_source = "none"
            log.debug(f"Fuente de audio para STT configurada internamente a: '{self._active_source}' (aun no esta activa).")

    # Activa la fuente de audio actual si es necesario (ej. inicia la captura del microfono local).
    def _activate_current_source_if_needed(self) -> bool:
        source_activated_ok = False
        if self._active_source == "local":
            if self.local_mic_handler:
                mic_info = self.local_mic_handler.get_mic_info()
                if not mic_info.get("is_capture_thread_alive"): # Si el microfono no esta capturando
                    log.info("Intentando activar la fuente de audio 'local' (MicLocalHandler)...")
                    if self.local_mic_handler.start_capture():
                        time.sleep(0.5); current_mic_info = self.local_mic_handler.get_mic_info()
                        source_activated_ok = current_mic_info.get("is_stream_active", False) # Verifica que el stream este activo
                        if source_activated_ok: log.info("MicLocalHandler activado y transmitiendo audio.")
                        else: log.error("Fallo al confirmar que MicLocalHandler esta activo despues de llamar a start_capture().")
                    else: log.error("El metodo local_mic_handler.start_capture() devolvio False.")
                else: # Si ya estaba activo
                    log.debug("La fuente 'local' (MicLocalHandler) ya estaba activa o intentando activarse."); source_activated_ok = mic_info.get("is_stream_active", False)
            else: log.error("No se puede activar la fuente 'local': la instancia de LocalMicHandler no esta disponible.")
        elif self._active_source == "robot":
            if self.robot_audio_queue: log.info("Fuente 'robot' seleccionada. STT comenzara a consumir de la cola de audio del robot."); source_activated_ok = True
            else: log.error("No se puede usar la fuente 'robot': la cola de audio del robot no esta disponible.")
        elif self._active_source == "none":
            log.info("La fuente de audio para STT es 'none'. No se activara ninguna fuente de audio."); source_activated_ok = True
        return source_activated_ok

    # Desactiva la fuente de audio actual (ej. detiene la captura del microfono local).
    def _deactivate_current_source_if_needed(self):
        if self._active_source == "local" and self.local_mic_handler:
            mic_info = self.local_mic_handler.get_mic_info()
            if mic_info.get("is_capture_thread_alive") or mic_info.get("is_stream_active"):
                log.info("Desactivando la fuente de audio 'local' (deteniendo MicLocalHandler)..."); self.local_mic_handler.stop_capture()
                log.info("MicLocalHandler detenido (o senal de detencion enviada).")
        elif self._active_source == "robot":
            log.info("Fuente 'robot' estaba activa. Su desactivacion es manejada por el productor de la cola o al cambiar de fuente.")

    # Metodo publico para cambiar la fuente de audio activa en tiempo de ejecucion ('local', 'robot', 'none').
    def set_audio_source(self, new_source_name: str):
        if new_source_name not in ["local", "robot", "none"]:
            log.error(f"Intento de establecer una fuente de audio desconocida: '{new_source_name}'."); return
        with self._lock: # Lock para evitar condiciones de carrera al cambiar de fuente
            if new_source_name == self._active_source: log.info(f"La fuente de audio para STT ya es '{new_source_name}'. No se realiza ningun cambio."); return
            log.info(f"Solicitud para cambiar la fuente de audio de STT: de '{self._active_source}' a '{new_source_name}'")

            # Verifica si la nueva fuente es viable (si sus dependencias existen)
            can_switch = False
            if new_source_name=="local": can_switch=bool(self.local_mic_handler)
            elif new_source_name=="robot": can_switch=bool(self.robot_audio_queue)
            elif new_source_name=="none": can_switch=True
            if not can_switch: log.warning(f"No se pudo cambiar a la fuente '{new_source_name}' porque su dependencia no esta disponible."); return

            was_running = self._is_running
            # Si el sistema estaba corriendo, desactiva la fuente antigua primero
            if was_running : self._deactivate_current_source_if_needed()

            # Resetea el estado del VAD y del procesador de audio para un cambio limpio
            self._vad_buffer=b'';
            if self._is_currently_speaking_vad:
                if self.external_speech_state_callback: self.external_speech_state_callback(False)
                self._is_currently_speaking_vad=False
            if self.audio_processor: self.audio_processor.finalize_recognition(); self.audio_processor.reset_recognizer()

            old_src=self._active_source; self._active_source=new_source_name

            # Activa la nueva fuente si el sistema estaba corriendo
            activated_ok=True
            if was_running and self._active_source!="none":
                activated_ok=self._activate_current_source_if_needed()

            if activated_ok: log.info(f"Fuente de audio para STT cambiada de '{old_src}' a '{self._active_source}'.")
            else: log.error(f"Fallo al activar la nueva fuente '{self._active_source}'. Se establecera a 'none' como medida de seguridad."); self._active_source="none"

    # --- Metodos de Ciclo de Vida del Sistema STT ---

    # Inicia el sistema STT. Activa la fuente de audio actual e inicia el hilo de procesamiento principal.
    def start(self) -> bool:
        with self._lock:
            if self._is_running: log.warning("STT_System.start() llamado, pero ya esta corriendo."); return True
            if not self.audio_processor: log.error("AudioProcessor no esta inicializado. No se puede iniciar STT_System."); return False

            log.info(f"Iniciando STT_System con la fuente configurada: '{self._active_source}'...")
            if self._active_source != "none":
                if not self._activate_current_source_if_needed():
                    log.error(f"Fallo al activar la fuente de audio '{self._active_source}'. STT_System no se iniciara."); return False
            else:
                log.info("STT_System se iniciara con la fuente 'none'. No se procesara audio hasta que se cambie la fuente.")

            self._is_running = True; self._stop_event.clear()
            self._processing_thread = threading.Thread(target=self._stt_loop, name="STTProcessingLoop"); self._processing_thread.daemon = True
            self._processing_thread.start()
            log.info(f"STT_System iniciado. Hilo de procesamiento activo. Fuente actual: '{self._active_source}'.")
        return True

    # Detiene el sistema STT de forma ordenada. Para el hilo de procesamiento y desactiva la fuente de audio.
    def stop(self):
        with self._lock:
            if not self._is_running: log.info("STT_System.stop() llamado, pero no estaba corriendo."); return
            log.info("Deteniendo STT_System..."); self._is_running = False; self._stop_event.set()

            # Espera a que el hilo de procesamiento termine
            if self._processing_thread and self._processing_thread.is_alive():
                log.debug("Esperando la finalizacion del hilo de procesamiento de STT..."); self._processing_thread.join(timeout=3.0)
                if self._processing_thread.is_alive(): log.warning("El hilo de procesamiento de STT no finalizo a tiempo.")
            self._processing_thread = None; log.info("Hilo de procesamiento de STT detenido.")

            self._deactivate_current_source_if_needed() # Desactiva la fuente de audio
            if self.audio_processor: log.debug("Finalizando cualquier reconocimiento pendiente en AudioProcessor..."); self.audio_processor.finalize_recognition()
            log.info("STT_System detenido completamente.")

    # Bucle principal del sistema STT, ejecutado en un hilo dedicado.
    # Se encarga de obtener continuamente fragmentos de audio de la fuente activa,
    # pasarlos al VAD (si esta habilitado) para detectar actividad de voz, y enviarlos
    # al AudioProcessor para el reconocimiento. Tambien maneja el timeout de silencio del VAD.
    def _stt_loop(self):
        thread_name = threading.current_thread().name
        log.info(f"[{thread_name}] Hilo de procesamiento STT iniciado y entrando en el bucle principal.")
        last_vad_activity_time = time.monotonic()
        VAD_SILENCE_TIMEOUT_SEC = self.app_config.get("STT_VAD_SILENCE_TIMEOUT_SEC", 2.0)

        while self._is_running and not self._stop_event.is_set():
            current_source_locked: str
            with self._lock: current_source_locked = self._active_source

            audio_chunk_for_stt: Optional[bytes] = None

            # Obtiene un chunk de audio de la fuente activa
            if current_source_locked == "robot":
                if self.robot_audio_queue:
                    try:
                        queue_item = self.robot_audio_queue.get(block=True, timeout=0.05)
                        if isinstance(queue_item, tuple): audio_chunk_for_stt = queue_item[0] # Asume (bytes, sr)
                        elif isinstance(queue_item, bytes): audio_chunk_for_stt = queue_item
                        elif queue_item is None: audio_chunk_for_stt = b'' # Fin de stream de un cliente
                    except queue.Empty: pass # Timeout es normal
                else: log.error("Fuente 'robot' activa pero la cola no esta disponible."); time.sleep(0.2); continue
            elif current_source_locked == "local":
                if self.local_mic_handler:
                    audio_chunk_for_stt = self.local_mic_handler.get_mono_chunk(block=True, timeout=0.05)
            elif current_source_locked == "none":
                time.sleep(0.1); continue # Si no hay fuente, espera

            # Si se obtuvo un chunk, se procesa
            if audio_chunk_for_stt is not None:
                if self.audio_processor:
                    self.audio_processor.process_chunk(audio_chunk_for_stt) # Envia al procesador Vosk
                if not self.vad and len(audio_chunk_for_stt) > 0:
                    last_vad_activity_time = time.monotonic() # Si no hay VAD, resetea el timer de silencio si hay audio

                # Logica del VAD: procesa el audio en frames y detecta si hay voz
                if self.vad and self.vad_frame_bytes > 0:
                    self._vad_buffer += audio_chunk_for_stt
                    while len(self._vad_buffer) >= self.vad_frame_bytes:
                        frame_to_vad = self._vad_buffer[:self.vad_frame_bytes]; self._vad_buffer = self._vad_buffer[self.vad_frame_bytes:]
                        try:
                            is_speech = self.vad.is_speech(frame_to_vad, self.stt_sample_rate)
                            if is_speech != self._is_currently_speaking_vad: # Si el estado del VAD cambia
                                self._is_currently_speaking_vad = is_speech; last_vad_activity_time = time.monotonic()
                                if self.external_speech_state_callback: self.external_speech_state_callback(self._is_currently_speaking_vad)
                            elif is_speech: # Si sigue habiendo voz
                                last_vad_activity_time = time.monotonic() # Resetea el timer de silencio
                        except Exception: pass # Ignora errores de VAD en un frame

            # Logica de finalizacion por silencio (timeout de VAD)
            is_timeout_condition = (audio_chunk_for_stt is None) or (audio_chunk_for_stt == b"")
            if is_timeout_condition and current_source_locked != "none":
                should_finalize_due_to_silence = \
                    (self.vad and self._is_currently_speaking_vad and (time.monotonic() - last_vad_activity_time > VAD_SILENCE_TIMEOUT_SEC)) or \
                    (not self.vad and (time.monotonic() - last_vad_activity_time > VAD_SILENCE_TIMEOUT_SEC * 1.5))

                if should_finalize_due_to_silence:
                    log.info(f"Timeout de silencio detectado. Finalizando el reconocimiento actual.")
                    if self.audio_processor: self.audio_processor.finalize_recognition()
                    if self._is_currently_speaking_vad: # Notifica que ya no se esta hablando
                        if self.external_speech_state_callback: self.external_speech_state_callback(False)
                        self._is_currently_speaking_vad = False
                    self._vad_buffer = b''; last_vad_activity_time = time.monotonic()

            if not audio_chunk_for_stt : time.sleep(0.01) # Pequena pausa si no hubo audio para no sobrecargar CPU

        log.info(f"[{thread_name}] Hilo de procesamiento STT ha finalizado.")
        # Limpieza final al salir del bucle
        if self.audio_processor: self.audio_processor.finalize_recognition()
        if self._is_currently_speaking_vad:
            if self.external_speech_state_callback: self.external_speech_state_callback(False)
            self._is_currently_speaking_vad = False
        self._vad_buffer = b''

    # --- Metodos de Estado ---
    def get_current_source(self) -> str:
        with self._lock: return self._active_source
    def is_running(self) -> bool: return self._is_running


# --- Bloque de Prueba Standalone ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.StreamHandler(sys.stdout)])
    test_log = logging.getLogger("STT_System_StandaloneTest")
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    VOSK_MODEL_PATH_FOR_TEST = os.path.join(current_script_dir, "audio", "models", "vosk-model-small-es-0.42")
    if not os.path.isdir(VOSK_MODEL_PATH_FOR_TEST): test_log.error(f"Ruta modelo Vosk NO VALIDA: '{VOSK_MODEL_PATH_FOR_TEST}'"); sys.exit(1)
    test_log.info(f"Usando modelo Vosk para prueba desde: {VOSK_MODEL_PATH_FOR_TEST}")
    
    final_recognized_texts_standalone = []
    last_partial_text_standalone = ""

    def standalone_final_text_cb(text: str):
        global last_partial_text_standalone
        test_log.info(f"===> TEXTO FINAL/SEGMENTO (Prueba): '{text}'")
        final_recognized_texts_standalone.append(text)
        last_partial_text_standalone = ""

    def standalone_partial_text_cb(partial_text: str):
        global last_partial_text_standalone
        if partial_text != last_partial_text_standalone:
            test_log.info(f"===> PARCIAL (Prueba): '{partial_text}'")
            last_partial_text_standalone = partial_text

    def standalone_vad_cb(is_speaking: bool):
        test_log.info(f"===> ESTADO VAD (Prueba): {'HABLANDO' if is_speaking else 'SILENCIO'}")

    stt_system_instance_test = None
    local_mic_handler_test_instance = None
    
    # Simular una app_config para la prueba standalone
    test_app_config = {"STT_VAD_SILENCE_TIMEOUT_SEC": 2.5}

    try:
        test_log.info("\n--- PRUEBA STANDALONE STT_System (MIC LOCAL) ---")
        MIC_NAME_FOR_TEST = "default"
        test_log.info(f"Intentando usar micrófono para prueba: '{MIC_NAME_FOR_TEST}'")
        
        local_mic_handler_test_instance = LocalMicHandler(mic_name_part=MIC_NAME_FOR_TEST, target_sample_rate=16000)
        test_log.info("LocalMicHandler para prueba instanciado.")
        
        stt_system_instance_test = SpeechToTextSystem(
            vosk_model_path=VOSK_MODEL_PATH_FOR_TEST,
            text_recognized_callback=standalone_final_text_cb,
            speech_state_callback=standalone_vad_cb,
            partial_text_recognized_callback=standalone_partial_text_cb,
            local_mic_handler_instance=local_mic_handler_test_instance,
            default_source="local",
            stt_sample_rate=16000,
            app_config=test_app_config # Pasar la config de prueba
        )
        test_log.info("STT_System para prueba local instanciado.")
        
        if stt_system_instance_test.start():
            mic_info = local_mic_handler_test_instance.get_mic_info()
            test_log.info(f"Mic info post-start: {mic_info}")
            if not mic_info.get("is_stream_active"): test_log.warning("MicLocalHandler no pudo activar stream.")
            test_log.info("STT (Local Mic) iniciado. Habla por 20 seg..."); time.sleep(20)
            test_log.info("Tiempo de prueba finalizado.")
        else:
            test_log.error("Fallo al iniciar STT_System para prueba local.")
            
    except Exception as e_test:
        test_log.critical(f"Error durante prueba STT_System: {e_test}", exc_info=True)
    finally:
        if stt_system_instance_test:
            test_log.info("Deteniendo STT_System..."); stt_system_instance_test.stop()
            test_log.info("STT_System detenido.")
        test_log.info(f"\nTextos FINALES Reconocidos: {final_recognized_texts_standalone}")
        test_log.info("--- PRUEBA STANDALONE STT_System (MIC LOCAL) FINALIZADA ---")

