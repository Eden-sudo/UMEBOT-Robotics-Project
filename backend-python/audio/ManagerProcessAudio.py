# ----------------------------------------------------------------------------------
# Titular: Gestor de Procesamiento de Audio para STT con Vosk
# Funcion Principal: Define la clase AudioProcessor, que abstrae la complejidad
#                    del procesamiento de chunks de audio para el reconocimiento de
#                    voz (STT) utilizando el motor Vosk a traves de VoskHelper.
#                    Maneja callbacks para resultados parciales y finales.
# ----------------------------------------------------------------------------------

import logging
from typing import Optional, Callable, List

# Importacion del modulo VoskHelper desde el mismo paquete (directorio)
from .vosk_helper import VoskHelper

# Configuracion del logger para este modulo
log = logging.getLogger("AudioProcessor")

# Procesa flujos de audio por chunks para realizar reconocimiento de voz (STT)
# utilizando Vosk. Proporciona resultados parciales y finales a traves de
# funciones callback configurables.
class AudioProcessor:
    # Inicializa el AudioProcessor y su instancia de VoskHelper.
    #
    # Args:
    #   vosk_model_path (str): Ruta al directorio del modelo Vosk.
    #   text_recognized_callback (Optional[Callable[[str], None]]): Callback para
    #                               cuando se reconoce un segmento final de texto.
    #   sample_rate (int): Tasa de muestreo del audio (ej. 16000 Hz).
    #   vocabulary (Optional[List[str]]): Lista opcional de palabras para
    #                                   restringir/guiar el reconocimiento.
    #   partial_text_callback (Optional[Callable[[str], None]]): Callback para
    #                                 cuando hay resultados parciales de texto.
    def __init__(self,
                 vosk_model_path: str,
                 text_recognized_callback: Optional[Callable[[str], None]],
                 sample_rate: int,
                 vocabulary: Optional[List[str]] = None,
                 partial_text_callback: Optional[Callable[[str], None]] = None):

        log.info(f"[AudioProcessor] Inicializando con SR={sample_rate}Hz...")
        try:
            # Crea una instancia de VoskHelper, pasando la configuracion necesaria
            self.vosk_helper = VoskHelper(model_path=vosk_model_path, sample_rate=sample_rate, vocabulary=vocabulary)
            log.debug("[AudioProcessor] Instancia de VoskHelper creada.")
        except Exception as e:
            log.critical(f"[AudioProcessor] Fallo al crear VoskHelper: {e}", exc_info=True)
            raise

        self.final_text_segment_callback = text_recognized_callback # Callback para resultados de segmentos "finales" de Vosk
        self.partial_text_callback = partial_text_callback      # Callback para resultados parciales durante el reconocimiento
        self._last_sent_partial = "" # Almacena el ultimo parcial enviado para evitar repeticiones
        log.info("[AudioProcessor] VoskHelper creado y listo.")

    # Procesa un fragmento (chunk) de datos de audio utilizando VoskHelper.
    # Invoca las funciones callback correspondientes para los resultados
    # de reconocimiento parciales y/o finales obtenidos del chunk.
    def process_chunk(self, chunk: bytes):
        if not self.vosk_helper:
            log.warning("[AudioProcessor] VoskHelper no inicializado, no se puede procesar chunk.")
            return

        if not chunk:
            # No procesar chunks vacios o None
            return

        # Procesa el chunk de audio con Vosk y determina si es un segmento final
        is_final_segment_from_vosk = self.vosk_helper.process_audio_chunk(chunk)

        # --- Logica para manejar y llamar al callback de resultados parciales ---
        if self.partial_text_callback:
            current_partial = self.vosk_helper.get_partial_result()

            # Log de depuracion para observar el resultado parcial de Vosk
            log.debug(f"[AudioProcessor] DEBUG_PARTIAL: VoskHelper.get_partial_result() = '{current_partial}' (Ultimo enviado: '{self._last_sent_partial}')")

            # Condicion para enviar el parcial: si es nuevo, diferente, o si se borra un parcial previo
            if (current_partial and current_partial != self._last_sent_partial) or \
               (not current_partial and self._last_sent_partial):
                try:
                    self.partial_text_callback(current_partial if current_partial else "")
                    self._last_sent_partial = current_partial if current_partial else ""
                except Exception as e:
                    log.error(f"[AudioProcessor] Error en partial_text_callback: {e}", exc_info=True)
        # --- Fin de la logica para resultados parciales ---

        if is_final_segment_from_vosk:
            final_text_segment = self.vosk_helper.get_current_segment_text()

            log.debug(f"[AudioProcessor] Texto de SEGMENTO STT (por AcceptWaveform=True): '{final_text_segment}'")
            if final_text_segment and self.final_text_segment_callback:
                try:
                    self.final_text_segment_callback(final_text_segment)
                except Exception as e:
                    log.error(f"[AudioProcessor] Error en final_text_segment_callback (segmento): {e}", exc_info=True)
            # Resetea el ultimo parcial enviado despues de un segmento final
            self._last_sent_partial = ""

    # Finaliza el proceso de reconocimiento actual y obtiene el resultado
    # de texto definitivo de toda la elocucion procesada hasta el momento.
    # Llama al callback de texto final si hay un resultado.
    def finalize_recognition(self):
        log.debug("[AudioProcessor] Finalizando reconocimiento...")
        if not self.vosk_helper:
            log.warning("[AudioProcessor] VoskHelper no inicializado, no se puede finalizar.")
            return

        final_text = self.vosk_helper.get_final_result()
        log.info(f"[AudioProcessor] Texto FINAL STT (por finalize_recognition): '{final_text}'")
        if self.final_text_segment_callback:
            if final_text: # Solo llamar si hay texto final
                try:
                    self.final_text_segment_callback(final_text)
                except Exception as e:
                    log.error(f"[AudioProcessor] Error en final_text_segment_callback (finalize): {e}", exc_info=True)
        # Resetea el ultimo parcial enviado despues de finalizar el reconocimiento completo
        self._last_sent_partial = ""

    # Reinicia el estado interno del reconocedor Vosk a traves de VoskHelper,
    # permitiendo iniciar un nuevo reconocimiento desde cero.
    def reset_recognizer(self):
        log.debug("[AudioProcessor] Reseteando reconocedor...")
        if self.vosk_helper:
            self.vosk_helper.reset()
            # Resetea el ultimo parcial enviado al reiniciar el reconocedor
            self._last_sent_partial = ""
