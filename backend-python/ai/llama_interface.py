# ----------------------------------------------------------------------------------
# Titular: Interfaz para Modelos de Lenguaje Locales (GGUF via llama-cpp-python)
# Funcion Principal: Define la clase LocalLlamaClient, una interfaz para cargar y
#                    ejecutar inferencias con modelos de lenguaje grandes (LLM) en
#                    formato GGUF de forma local, utilizando la libreria llama-cpp-python.
#                    Aunque inicialmente se consideraron modelos Llama, la experimentacion
#                    favorecio el uso de Phi-3 Mini (en formato GGUF) para la generacion
#                    de respuestas y emulacion del rol de Umebot.
#                    Este script es funcional y permite un control mas granular sobre el
#                    modelo, aunque su rendimiento es mas lento que las APIs en la nube.
#                    La capacidad limitada de hardware impidio exploraciones extensas
#                    de ajuste fino (fine-tuning). El nombre del archivo podria ser
#                    mas preciso como 'gguf_interface.py' dado su uso actual con
#                    modelos como Phi-3.
# ----------------------------------------------------------------------------------

import os
import asyncio
import logging
from llama_cpp import Llama, LlamaGrammar # Importa Llama y LlamaGrammar de llama-cpp-python
from typing import List, Dict, Any, Optional

# Configuracion del logger para este modulo.
log = logging.getLogger("LocalLlamaClient")
# Evita anadir multiples manejadores si el logging ya esta configurado globalmente.
if not log.hasHandlers():
    # Configuracion basica de logging. Idealmente, se configura en el punto de entrada principal de la aplicacion.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Interfaz para interactuar con modelos de lenguaje en formato GGUF
# ejecutados localmente mediante la libreria llama-cpp-python.
# Permite cargar modelos y generar respuestas de manera asincrona.
class LocalLlamaClient:
    # Inicializa el cliente y carga el modelo GGUF especificado.
    #
    # Args:
    #   model_gguf_path (str): Ruta completa al archivo .gguf del modelo.
    #   n_ctx (int): Tamano del contexto del modelo (ej. 4096 para Phi-3 Mini).
    #   n_gpu_layers (int): Numero de capas a descargar en la GPU (0 para CPU).
    #   chat_format (str): Formato de chat que el modelo espera (ej. "chatml").
    #                      Es crucial para la correcta interpretacion de los prompts.
    #   n_threads (Optional[int]): Numero de hilos de CPU a usar para la inferencia.
    #                              Si es None, llama.cpp decide.
    #   n_batch (int): Tamano del lote para el procesamiento de prompts.
    #   verbose (bool): Si es True, llama.cpp emitira logs detallados.
    #
    # Raises:
    #   FileNotFoundError: Si no se encuentra el archivo del modelo GGUF.
    #   Exception: Si ocurre un error durante la carga del modelo.
    def __init__(self,
                 model_gguf_path: str,
                 n_ctx: int = 4096,         # Tamano de contexto por defecto, adecuado para Phi-3 Mini 4k.
                 n_gpu_layers: int = 0,     # Por defecto, se usa solo CPU (n_gpu_layers=0 segun los requisitos).
                 chat_format: str = "chatml", # Formato de chat por defecto, compatible con Phi-3 Mini.
                 n_threads: Optional[int] = None, # None permite a llama.cpp determinar el numero optimo de hilos.
                 n_batch: int = 512,        # Tamano de lote comun para el procesamiento de prompts.
                 verbose: bool = False):    # Controla la verbosidad de los logs internos de llama.cpp.

        self.model_path = model_gguf_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.chat_format = chat_format
        self.n_threads = n_threads
        self.n_batch = n_batch
        self.verbose_llama = verbose # Se renombro para claridad interna.

        self.llm: Optional[Llama] = None # Almacenara la instancia del modelo Llama cargado.
        self._load_model() # El modelo se carga automaticamente durante la inicializacion.

    # Carga el modelo GGUF utilizando los parametros especificados en la instancia.
    # Este metodo es llamado durante la inicializacion (__init__).
    # Si se necesitan cambiar parametros fundamentales del modelo (ej. ruta, n_ctx),
    # se debe crear una nueva instancia de LocalLlamaClient.
    def _load_model(self):
        if not os.path.isfile(self.model_path):
            log.error(f"Archivo de modelo GGUF no encontrado en la ruta: {self.model_path}")
            raise FileNotFoundError(f"No se encontro el archivo del modelo GGUF: {self.model_path}")

        log.info(
            f"Cargando modelo GGUF: {os.path.basename(self.model_path)} con los siguientes parametros:\n"
            f"   n_ctx: {self.n_ctx}\n"
            f"   n_gpu_layers: {self.n_gpu_layers}\n"
            f"   chat_format: '{self.chat_format}'\n"
            f"   n_threads: {self.n_threads if self.n_threads is not None else 'default de llama.cpp'}\n"
            f"   n_batch: {self.n_batch}\n"
            f"   verbose (llama.cpp): {self.verbose_llama}"
        )
        try:
            self.llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                chat_format=self.chat_format,
                n_threads=self.n_threads,
                n_batch=self.n_batch,
                verbose=self.verbose_llama # Pasa el flag de verbosidad a llama.cpp
            )
            log.info(f"Modelo GGUF '{os.path.basename(self.model_path)}' cargado exitosamente.")
        except Exception as e:
            log.error(f"ERROR FATAL al cargar el modelo GGUF '{self.model_path}': {e}", exc_info=True)
            self.llm = None # Se asegura que self.llm sea None si la carga del modelo falla.
            # Re-lanza la excepcion para que el codigo que instancia la clase pueda manejar el fallo.
            raise

    # Genera una respuesta de forma asincrona utilizando el modelo GGUF local cargado.
    # Ejecuta la inferencia del modelo en un hilo separado para no bloquear
    # el bucle de eventos de asyncio.
    #
    # Args:
    #   messages (List[Dict[str, Any]]): Lista de mensajes en formato de chat
    #                                    (generalmente preparada por PromptBuilder).
    #   temperature (float): Controla la aleatoriedad de la generacion.
    #                        Valores mas bajos para respuestas mas deterministas.
    #   max_tokens (int): Numero maximo de tokens a generar en la respuesta.
    #   stop (Optional[List[str]]): Lista de secuencias de texto que, si se generan,
    #                                 detendran la generacion prematuramente.
    #   grammar (Optional[LlamaGrammar]): Objeto LlamaGrammar para restringir la salida
    #                                       del modelo a un formato especifico (ej. JSON).
    #
    # Returns:
    #   Optional[str]: La respuesta de texto generada por el modelo, o un mensaje de
    #                  error formateado, o None si el modelo no esta cargado o falla la generacion.
    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3, # Temperatura baja para respuestas mas enfocadas
        max_tokens: int = 512,    # Limite maximo de tokens para la respuesta generada
        stop: Optional[List[str]] = None, # Secuencias de texto que detendran la generacion
        grammar: Optional[LlamaGrammar] = None # Gramatica GBNF para forzar la salida a un formato
    ) -> str | None:
        if self.llm is None:
            log.error("El modelo GGUF no esta cargado en LocalLlamaClient. No se puede generar respuesta.")
            # Devuelve un mensaje de error formateado para Umebot si el modelo no esta cargado.
            return "^runTag(disappointment)Lo siento, mi motor de procesamiento local no esta disponible en este momento."

        try:
            log.debug(
                f"Generando respuesta con GGUF (modelo: {os.path.basename(self.model_path)}, "
                f"chat_format: {self.chat_format}). Temperatura: {temperature}, Max Tokens: {max_tokens}"
            )
            if grammar:
                log.debug("Usando gramatica GBNF para la generacion.")

            # La llamada a self.llm.create_chat_completion es sincrona (bloqueante).
            # Se ejecuta en un hilo separado usando asyncio.to_thread para no bloquear el bucle de eventos de asyncio.
            completion_task = asyncio.to_thread(
                self.llm.create_chat_completion, # Metodo de la instancia de Llama que se ejecutara en el hilo.
                messages=messages,               # Argumentos que se pasaran al metodo create_chat_completion.
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                grammar=grammar                  # Se pasa la gramatica GBNF si fue proporcionada.
            )
            output = await completion_task # Espera a que la tarea en el hilo separado complete su ejecucion.

            # Procesa la salida del modelo
            if output and 'choices' in output and len(output['choices']) > 0 and \
               'message' in output['choices'][0] and 'content' in output['choices'][0]['message']:
                response_text = output['choices'][0]['message']['content']
                log.debug(f"Respuesta cruda de GGUF recibida (primeros 100 caracteres): {response_text[:100] if response_text else 'None'}...")
                return response_text.strip() if response_text else None
            else:
                log.error(f"La estructura de la salida del modelo GGUF no fue la esperada. Output: {output}")
                return None # O devuelve un mensaje de error formateado si la estructura es inesperada.
        except Exception as e:
            log.error(f"ERROR durante la generacion de respuesta con el modelo GGUF '{self.model_path}': {e}", exc_info=True)
            # Devuelve un mensaje de error con formato para Umebot.
            return "^runTag(disappointment)Oops, tuve un problema generando mi respuesta localmente. ¿Podrias intentarlo de nuevo?"

# --- Bloque de prueba para llama_interface.py (ejecutar python ai/llama_interface.py directamente) ---
# Este bloque se ejecuta solo si el script es llamado directamente.
# Sirve para probar la funcionalidad de LocalLlamaClient.
if __name__ == '__main__':
    # Configura un logging detallado para la prueba si no esta ya configurado globalmente.
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')

    log_test = logging.getLogger("LocalLlamaClientTest") # Logger especifico para esta prueba
    log_test.info("Probando LocalLlamaClient de forma standalone...")

    # --- CONFIGURACION PARA LA PRUEBA ---
    # Asumiendo que este script (llama_interface.py) esta en 'umebot_core/ai/'
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    MODELS_DIR = os.path.join(SCRIPT_DIR, "models") # Directorio 'models' esperado dentro de la misma carpeta 'ai'.

    # Modelo GGUF a probar (ej. Phi-3 Mini). Asegurarse de que el archivo exista en MODELS_DIR.
    phi3_model_filename = "Phi-3-mini-4k-instruct-q4.gguf" # Ejemplo de nombre de archivo
    model_to_test_path = os.path.join(MODELS_DIR, phi3_model_filename)

    phi3_n_ctx = 4096 # Tamano de contexto para Phi-3 Mini
    phi3_chat_format = "chatml" # Formato de chat crucial para la compatibilidad con el modelo Phi-3.

    if not os.path.exists(model_to_test_path):
        log_test.error(f"Modelo de prueba GGUF '{phi3_model_filename}' no encontrado en: {model_to_test_path}.")
        log_test.error("Por favor, descarga el modelo o ajusta la ruta 'model_to_test_path' para ejecutar la prueba.")
    else:
        async def run_gguf_tests():
            try:
                num_cpu_threads = os.cpu_count() # Intenta obtener el numero de CPUs disponibles
                if num_cpu_threads is None: num_cpu_threads = 4 # Fallback si no se puede determinar
                log_test.info(f"Usando n_threads={num_cpu_threads} para la prueba con llama.cpp.")

                # Crear instancia del cliente con los parametros para Phi-3 Mini
                local_client = LocalLlamaClient(
                    model_gguf_path=model_to_test_path,
                    n_ctx=phi3_n_ctx,
                    chat_format=phi3_chat_format,
                    n_threads=num_cpu_threads,
                    n_batch=512,          # Valor comun para n_batch en llama.cpp.
                    n_gpu_layers=0,       # Configuracion para usar solo CPU.
                    verbose=False         # Cambiar a True para obtener logs mas detallados de la libreria llama.cpp.
                )

                if local_client.llm is None: # Verifica si el modelo se cargo correctamente
                    log_test.error("Fallo en la carga del modelo GGUF en LocalLlamaClient. Abortando pruebas.")
                    return

                # Prueba 1: Interaccion de texto simple
                log_test.info("\n--- Prueba 1 GGUF: Texto Simple con Modelo Local (ej. Phi-3 Mini) ---")
                simple_messages = [
                    {"role": "system", "content": "Eres un asistente Umebot. Responde brevemente y amistosamente."},
                    {"role": "user", "content": "Hola Umebot, ¿quien eres?"}
                ]
                response1 = await local_client.generate_response(simple_messages, temperature=0.5, max_tokens=80)
                if response1:
                    log_test.info(f"Respuesta GGUF (Texto Simple): '{response1}'")
                else:
                    log_test.error("Fallo al obtener respuesta GGUF de texto simple.")

                # Prueba 2: Prompt mas complejo simulando Umebot con formato de tags de animacion
                log_test.info("\n--- Prueba 2 GGUF: Prompt Umebot con Tags de Animacion ---")
                umebot_system_prompt_gguf = """Eres Umebot, un robot asistente de UMECIT. Tu objetivo es ser util y expresivo.
Debes usar tags de animacion en tu respuesta cuando sea apropiado. El formato es ^runTag(nombre_tag) seguido del texto.
Tags de animacion disponibles: ^runTag(joy), ^runTag(interrogative), ^runTag(affirmative_context).
Ejemplo de uso: ^runTag(joy)¡Hola! ^runTag(interrogative)¿Como puedo ayudarte hoy?"""

                messages_umebot_prompt = [
                    {"role": "system", "content": umebot_system_prompt_gguf},
                    {"role": "user", "content": "Cuentame algo interesante sobre la universidad UMECIT de forma alegre."}
                ]
                response2 = await local_client.generate_response(
                    messages_umebot_prompt,
                    temperature=0.6, # Temperatura ligeramente mas alta para fomentar creatividad y uso de tags.
                    max_tokens=150
                )
                if response2:
                    log_test.info(f"Respuesta GGUF (Prompt Umebot con Tags): '{response2}'")
                else:
                    log_test.error("Fallo al obtener respuesta GGUF con prompt Umebot.")

            except FileNotFoundError as fnf_err: # Captura especificamente si el modelo no se encuentra
                log_test.error(f"Error de archivo no encontrado en prueba LocalLlamaClient: {fnf_err}")
            except ValueError as ve: # Por si hay algun error en los parametros pasados a Llama
                log_test.error(f"Error de valor/configuracion al probar LocalLlamaClient: {ve}")
            except Exception as e: # Otros errores inesperados
                log_test.error(f"Error inesperado probando LocalLlamaClient: {e}", exc_info=True)

        asyncio.run(run_gguf_tests()) # Ejecuta las pruebas asincronas
