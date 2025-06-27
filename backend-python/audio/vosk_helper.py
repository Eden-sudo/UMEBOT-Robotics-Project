# ----------------------------------------------------------------------------------
# Titular: Asistente para el Motor de Reconocimiento de Voz Vosk
# Funcion Principal: Define la clase VoskHelper, que encapsula la carga del
#                    modelo de lenguaje Vosk y la logica para el procesamiento
#                    de audio para reconocimiento de voz (STT). Permite el uso de
#                    un vocabulario especifico para mejorar la precision.
# ----------------------------------------------------------------------------------

import vosk
import json
import os
import logging
from typing import Optional, List # Para type hints

# Configuracion del logger para este modulo
log = logging.getLogger("VoskHelper")

# Clase auxiliar que simplifica la interaccion con el motor de reconocimiento
# de voz Vosk. Se encarga de cargar el modelo, configurar el reconocedor
# (opcionalmente con un vocabulario especifico) y procesar audio.
class VoskHelper:
    # Subdirectorio por defecto (relativo al directorio 'audio') donde se espera encontrar el modelo Vosk.
    DEFAULT_MODEL_SUBDIR = "models/vosk-model-es-0.42"

    # Inicializa el VoskHelper, cargando el modelo de lenguaje Vosk y
    # configurando el reconocedor KaldiRecognizer.
    #
    # Args:
    #   model_path (Optional[str]): Ruta al directorio del modelo Vosk.
    #                               Si es None, intenta una ruta por defecto.
    #   sample_rate (int): Tasa de muestreo del audio (ej. 16000 Hz).
    #   vocabulary (Optional[List[str]]): Lista opcional de palabras
    #                                   para el vocabulario especifico.
    def __init__(self, model_path: Optional[str] = None, sample_rate: int = 16000, vocabulary: Optional[List[str]] = None):
        log.info(f"Inicializando VoskHelper con SR={sample_rate}Hz...")

        actual_model_path = self._resolve_model_path(model_path)
        log.info(f"Cargando modelo Vosk desde: {os.path.abspath(actual_model_path)}")

        try:
            self.model = vosk.Model(actual_model_path)

            if vocabulary and isinstance(vocabulary, list) and len(vocabulary) > 0:
                log.info(f"Usando vocabulario especifico (palabras: {len(vocabulary)}).")
                # Convierte la lista de vocabulario a formato JSON para Vosk
                vocab_json = json.dumps(vocabulary, ensure_ascii=False)
                self.recognizer = vosk.KaldiRecognizer(self.model, sample_rate, vocab_json)
            else:
                log.info("Usando vocabulario general del modelo.")
                self.recognizer = vosk.KaldiRecognizer(self.model, sample_rate)

            self.recognizer.SetWords(True) # Configura para obtener informacion de palabras individuales
            self.recognizer.SetPartialWords(True) # Habilita la obtencion detallada de palabras parciales.
            log.info(f"Modelo Vosk cargado, reconocedor creado para {sample_rate}Hz.")
        except Exception as e:
            log.error(f"Fallo al cargar modelo Vosk o crear reconocedor desde '{actual_model_path}': {e}", exc_info=True)
            raise

    # Resuelve la ruta absoluta al directorio del modelo Vosk.
    # Intenta varias ubicaciones posibles si no se provee una ruta absoluta valida.
    #
    # Args:
    #   model_path_arg (Optional[str]): La ruta proporcionada al constructor.
    #
    # Returns:
    #   str: La ruta absoluta validada del modelo.
    #
    # Raises:
    #   ValueError: Si no se puede encontrar el directorio del modelo.
    def _resolve_model_path(self, model_path_arg: Optional[str]) -> str:
        if model_path_arg is None:
            # Si no se especifica ruta, intenta construir una ruta por defecto relativa al proyecto.
            # Asume que este script (vosk_helper.py) esta en un subdirectorio (ej. 'audio').
            # Sube un nivel para estar en el directorio padre de 'audio', luego anade 'audio/DEFAULT_MODEL_SUBDIR'.
            script_dir_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path_arg = os.path.join(script_dir_parent, "audio", self.DEFAULT_MODEL_SUBDIR)
            log.debug(f"Ruta de modelo no especificada, intentando ruta por defecto: {model_path_arg}")

        # Comprueba si la ruta proporcionada (o la por defecto) ya es absoluta y un directorio valido.
        if os.path.isabs(model_path_arg) and os.path.isdir(model_path_arg):
            log.debug(f"Ruta de modelo absoluta y valida: {model_path_arg}")
            return model_path_arg

        # Si no es absoluta, prueba varias interpretaciones relativas.
        script_dir = os.path.dirname(os.path.abspath(__file__)) # Directorio de este script
        project_root_guess = os.path.dirname(script_dir) # Suposicion de la raiz del proyecto (un nivel arriba)

        possible_paths = [
            os.path.join(script_dir, model_path_arg),       # Relativa al directorio del script
            os.path.join(project_root_guess, model_path_arg), # Relativa a la supuesta raiz del proyecto
            os.path.join(os.getcwd(), model_path_arg),      # Relativa al directorio de trabajo actual
            model_path_arg                                  # La ruta tal cual (podria ser relativa y valida)
        ]

        for path_option in possible_paths:
            abs_path_option = os.path.abspath(path_option)
            if os.path.isdir(abs_path_option):
                log.debug(f"Ruta de modelo resuelta a: {abs_path_option}")
                return abs_path_option

        log.error(f"No se pudo encontrar el directorio del modelo Vosk: '{model_path_arg}'. Opciones probadas (absolutas): {[os.path.abspath(p) for p in possible_paths]}")
        raise ValueError(f"Directorio del modelo Vosk no encontrado: '{model_path_arg}'")

    # Procesa un fragmento (chunk) de datos de audio con el reconocedor Vosk.
    #
    # Args:
    #   audio_chunk_bytes (bytes): El fragmento de audio en formato de bytes.
    #
    # Returns:
    #   bool: True si Vosk determina que el fragmento de audio completa
    #         un segmento de habla (y un resultado esta listo en Result()),
    #         False en caso contrario.
    def process_audio_chunk(self, audio_chunk_bytes: bytes) -> bool:
        if not hasattr(self, 'recognizer') or not self.recognizer:
            log.warning("Reconocedor Vosk no inicializado en process_audio_chunk.")
            return False
        if not audio_chunk_bytes: # No procesar chunks vacios
            return False
        try:
            # Envia el chunk de audio al reconocedor Vosk
            return self.recognizer.AcceptWaveform(audio_chunk_bytes)
        except Exception as e: # Captura excepciones genericas durante el procesamiento
            log.debug(f"Excepcion en AcceptWaveform: {e}")
            return False

    # Obtiene la hipotesis de reconocimiento parcial actual del motor Vosk.
    # Util para mostrar texto mientras el usuario aun esta hablando.
    #
    # Returns:
    #   str: El texto del resultado parcial, o una cadena vacia si no hay
    #        resultado parcial disponible o si ocurre un error.
    def get_partial_result(self) -> str:
        if not hasattr(self, 'recognizer') or not self.recognizer:
            return ""
        try:
            # El metodo PartialResult() de Vosk devuelve una cadena JSON
            partial_json_str = self.recognizer.PartialResult()
            partial_data = json.loads(partial_json_str)
            # Extrae el texto parcial y elimina espacios en blanco al inicio/final.
            return partial_data.get("partial", "").strip()
        except Exception: # Si hay error al parsear JSON o al obtener resultado
            return ""

    # Obtiene el texto del resultado del segmento de habla actual procesado por Vosk.
    # Esto se usa tipicamente cuando AcceptWaveform devuelve True.
    #
    # Returns:
    #   str: El texto reconocido para el segmento actual, o una cadena vacia
    #        si no hay resultado o si ocurre un error.
    def get_current_segment_text(self) -> str:
        if not hasattr(self, 'recognizer') or not self.recognizer:
            return ""
        try:
            # El metodo Result() de Vosk devuelve una cadena JSON con el texto del ultimo segmento.
            res_json_str = self.recognizer.Result()
            res_data = json.loads(res_json_str)
            return res_data.get("text", "").strip()
        except Exception as e:
            log.error(f"Error obteniendo el resultado del segmento de Vosk: {e}", exc_info=True)
            return ""

    # Obtiene el resultado final de todo el audio procesado desde el ultimo reinicio.
    # Llamar a este metodo tambien reinicia el estado interno del reconocedor Vosk.
    #
    # Returns:
    #   str: El texto final reconocido, o una cadena vacia si no hay resultado
    #        o si ocurre un error.
    def get_final_result(self) -> str:
        if not hasattr(self, 'recognizer') or not self.recognizer:
            return ""
        try:
            # El metodo FinalResult() de Vosk devuelve una cadena JSON y reinicia el reconocedor.
            res_json_str = self.recognizer.FinalResult()
            res_data = json.loads(res_json_str)
            return res_data.get("text", "").strip()
        except Exception as e:
            log.error(f"Error obteniendo el resultado final de Vosk: {e}", exc_info=True)
            return ""

    # Reinicia explicitamente el estado interno del reconocedor Vosk,
    # preparandolo para una nueva secuencia de reconocimiento de voz.
    def reset(self):
        if hasattr(self, 'recognizer') and self.recognizer:
            try:
                self.recognizer.Reset()
                log.debug("Reconocedor Vosk reiniciado.")
            except Exception as e:
                log.error(f"Error reiniciando el reconocedor Vosk: {e}", exc_info=True)
