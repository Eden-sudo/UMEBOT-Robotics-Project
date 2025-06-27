# ----------------------------------------------------------------------------------
# Titular: Cliente Asincrono para la API de Modelos GPT de OpenAI
# Funcion Principal: Define la clase GPTAPIClient para interactuar de forma
#                    asincrona con los modelos de lenguaje GPT de OpenAI.
#                    Gestiona la clave API (leyendola desde variables de entorno
#                    o argumentos), permite la seleccion del modelo a utilizar,
#                    y soporta la generacion de respuestas basadas en el formato
#                    de mensajes de chat (ChatML), incluyendo funcionalidad
#                    multimodal basica. Utiliza asyncio.to_thread para manejar
#                    llamadas bloqueantes de la libreria de OpenAI en un entorno
#                    asincrono.
# ----------------------------------------------------------------------------------

import os
import asyncio
import logging
from openai import OpenAI # Libreria oficial de OpenAI
from typing import List, Dict, Any, Optional

# Configuracion del logger para este modulo
log = logging.getLogger("GPTAPIClient")
# Es buena practica que la configuracion global de logging la realice el script principal de la aplicacion.

# Cliente asincrono para interactuar con la API de OpenAI, permitiendo
# generar respuestas de modelos GPT como gpt-4o o gpt-3.5-turbo.
class GPTAPIClient:
    # Inicializa el cliente para la API de OpenAI.
    # La clave API se puede proporcionar directamente o se intentara leer de la
    # variable de entorno OPENAI_API_KEY.
    #
    # Args:
    #   api_key (Optional[str]): La clave API de OpenAI. Si es None, se busca en ENV.
    #   default_model (str): El modelo por defecto a utilizar (ej. "gpt-4o").
    #
    # Raises:
    #   ValueError: Si la clave API no se proporciona y no se encuentra en el entorno.
    def __init__(self, api_key: str | None = None, default_model: str = "gpt-4o"):
        resolved_api_key = api_key if api_key else os.environ.get("OPENAI_API_KEY")
        if not resolved_api_key:
            log.error("API key de OpenAI no proporcionada ni encontrada en la variable de entorno OPENAI_API_KEY.")
            raise ValueError("Se requiere API key de OpenAI para inicializar GPTAPIClient.")

        try:
            # Para la version openai>=1.0.0, la clase principal es OpenAI().
            # Aunque la libreria puede no ser nativamente 'async' para todas sus operaciones de red
            # en el sentido de usar 'aiohttp' internamente por defecto, las llamadas bloqueantes
            # se gestionaran con 'asyncio.to_thread' en los metodos asincronos.
            self.client = OpenAI(api_key=resolved_api_key)
            log.info(f"Cliente OpenAI inicializado. Modelo por defecto configurado: {default_model}")
        except Exception as e:
            log.error(f"Fallo al inicializar el cliente OpenAI: {e}", exc_info=True)
            raise # Re-lanza la excepcion para que sea manejada por el codigo que crea la instancia

        self.default_model = default_model

    # Genera una respuesta del modelo GPT de OpenAI de forma asincrona.
    # La lista de mensajes debe seguir el formato ChatML esperado por la API.
    # Maneja llamadas bloqueantes a la API de OpenAI ejecutandolas en un hilo separado.
    #
    # Args:
    #   messages (List[Dict[str, Any]]): Lista de mensajes en formato ChatML. Puede incluir
    #                                    contenido multimodal (texto e imagenes URL).
    #   model_override (Optional[str]): Permite especificar un modelo diferente al por defecto.
    #   temperature (float): Controla la aleatoriedad de la respuesta (0.0 a 2.0).
    #   max_tokens (int): Numero maximo de tokens a generar en la respuesta.
    #
    # Returns:
    #   Optional[str]: El contenido de la respuesta generada por el modelo, o un mensaje
    #                  de error formateado si ocurre una excepcion, o None si el cliente
    #                  no esta inicializado.
    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 600
    ) -> str | None:
        if not self.client:
            log.error("Cliente OpenAI no inicializado. No se puede generar respuesta.")
            return None

        model_to_use = model_override if model_override else self.default_model
        log.info(f"Generando respuesta con modelo OpenAI: {model_to_use} (Temperatura: {temperature}, MaxTokens: {max_tokens})")
        if messages:
            log.debug(f"Enviando {len(messages)} mensajes a OpenAI. Primer mensaje (rol y tipo de contenido): role='{messages[0].get('role')}', content_type='{type(messages[0].get('content'))}'")
        else:
            log.warning("Lista de mensajes vacia para generar respuesta.")
            return "^runTag(confused)No tengo mensajes que procesar."

        try:
            # La llamada a self.client.chat.completions.create es bloqueante por naturaleza.
            # Para evitar bloquear el bucle de eventos de asyncio, se ejecuta en un hilo separado
            # utilizando asyncio.to_thread.
            chat_completion = await asyncio.to_thread(
                self.client.chat.completions.create, # Metodo a ejecutar en el hilo
                model=model_to_use,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            response_content = chat_completion.choices[0].message.content
            log.debug(f"Respuesta OpenAI recibida (primeros 100 caracteres): {response_content[:100] if response_content else 'None'}...")
            return response_content.strip() if response_content else None
        except Exception as e:
            log.error(f"ERROR durante la llamada a la API de OpenAI: {e}", exc_info=True)
            # Devuelve un mensaje de error formateado que otros modulos (ej. ConversationManager) puedan interpretar.
            # El tag de animacion permite una respuesta mas expresiva del robot.
            return f"^runTag(disappointment)Oops, tuve un problema tecnico con mi IA ({model_to_use}). Por favor, intenta de nuevo mas tarde."

# Bloque para pruebas directas del script.
if __name__ == '__main__':
    # Configura un logging basico si este script se ejecuta directamente.
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_test = logging.getLogger("GPTAPIClientTest") # Logger especifico para estas pruebas

    async def run_gpt_tests():
        log_test.info("Iniciando pruebas para GPTAPIClient...")
        # Verifica si la clave API esta configurada en las variables de entorno.
        if not os.getenv("OPENAI_API_KEY"):
            log_test.error("La variable de entorno OPENAI_API_KEY no esta configurada. Saltando pruebas de GPTClient.")
            return
        try:
            # Se utiliza un modelo mas rapido y economico para las pruebas.
            client = GPTAPIClient(default_model="gpt-3.5-turbo")
            test_messages = [
                {"role": "system", "content": "Eres un asistente util."},
                {"role": "user", "content": "Hola, ¿como se dice 'manzana' en ingles?"}
            ]
            log_test.info(f"Enviando mensaje de prueba: {test_messages[1]['content']}")
            response = await client.generate_response(test_messages)
            log_test.info(f"Respuesta de prueba recibida: {response}")

            # Prueba de error (ejemplo: modelo invalido, si se quisiera probar manejo de errores especificos)
            # test_messages_error = [{"role": "user", "content": "Test"}]
            # response_error = await client.generate_response(test_messages_error, model_override="modelo-que-no-existe")
            # log_test.info(f"Respuesta de prueba de error: {response_error}")

        except Exception as e:
            log_test.error(f"Error en la prueba de GPTClient: {e}", exc_info=True)

    # Ejecuta las pruebas asincronas.
    asyncio.run(run_gpt_tests())
