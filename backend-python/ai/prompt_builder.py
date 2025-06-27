# ----------------------------------------------------------------------------------
# Titular: Constructor de Prompts para Modelos de Lenguaje Grandes (LLM)
# Funcion Principal: Este modulo define la clase PromptBuilder y funciones auxiliares
#                    para construir prompts estructurados y contextualizados para
#                    interactuar con LLMs. Incorpora una personalidad base para el
#                    robot, contexto recuperado de una base de conocimientos (RAG simple
#                    desde JSONL), historial de conversacion (obtenido de una base de
#                    datos), e informacion de archivos.
#                    El modulo fue parte de una extensa experimentacion con diferentes
#                    modelos y enfoques de prompting. Dada la capacidad limitada del
#                    hardware disponible para el proyecto, algunas implementaciones,
#                    especialmente para formatos de LLMs locales, son conceptuales y
#                    requeririan adaptacion para modelos especificos.
# ----------------------------------------------------------------------------------

import json
import datetime
import re
import os
import logging
from typing import Union, Optional, List, Dict, Any

# Configuracion del logger para este modulo
log = logging.getLogger("PromptBuilder")
# Configuracion de logging basica si no hay manejadores (util para ejecucion directa o pruebas del modulo)
if not log.hasHandlers() and __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s')

# --- Carga de Base de Conocimientos (para RAG) ---
_PROMPT_BUILDER_DIR = os.path.dirname(os.path.abspath(__file__)) # Directorio actual del script
# Ruta por defecto al archivo JSONL que actua como base de conocimientos para RAG.
DEFAULT_KNOWLEDGE_BASE_PATH = os.path.join(_PROMPT_BUILDER_DIR, "data_JSONL", "umebot_tuning_data.jsonl")

# Carga pares de pregunta y respuesta (Q&A) desde un archivo JSONL
# para ser utilizados como una base de conocimientos simple en un sistema RAG
# (Retrieval Augmented Generation). Maneja diferentes formatos de entrada JSONL.
#
# Args:
#   filepath (str): Ruta al archivo JSONL de la base de conocimientos.
#
# Returns:
#   List[Dict[str, str]]: Lista de diccionarios, cada uno con "question" y "answer".
def load_knowledge_base_from_jsonl(filepath: str = DEFAULT_KNOWLEDGE_BASE_PATH) -> List[Dict[str, str]]:
    knowledge: List[Dict[str, str]] = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    entry = json.loads(line)
                    # Adaptado para aceptar varios formatos de entrada JSONL (ej. 'messages' o 'question'/'answer')
                    q, a = None, None
                    if "messages" in entry and isinstance(entry["messages"], list) and len(entry["messages"]) >= 2:
                        q = entry["messages"][0].get("content")
                        a = entry["messages"][1].get("content")
                    elif "question" in entry and "answer" in entry:
                        q = entry["question"]; a = entry["answer"]

                    if q and a: knowledge.append({"question": str(q), "answer": str(a)})
                    else: log.warning(f"Entrada JSONL invalida en '{filepath}' linea {line_num}: {line.strip()}")
                except json.JSONDecodeError:
                    log.warning(f"Linea JSONL malformada en '{filepath}' linea {line_num}: {line.strip()}")
        log.info(f"Base de conocimientos JSONL cargada desde '{filepath}' ({len(knowledge)} entradas).")
    except FileNotFoundError:
        log.error(f"Archivo de base de conocimientos JSONL no encontrado: {filepath}. El RAG general podria no funcionar.")
    except Exception as e:
        log.error(f"Error cargando base de conocimientos desde '{filepath}': {e}", exc_info=True)
    return knowledge

# La base de conocimientos se carga globalmente al importar este modulo.
KNOWLEDGE_BASE_JSONL: List[Dict[str, str]] = load_knowledge_base_from_jsonl()

# Extrae palabras clave significativas de un texto dado.
# Procesa el texto convirtiendolo a minusculas, eliminando puntuacion
# y excluyendo una lista personalizable de "stop words" (palabras comunes).
#
# Args:
#   text (str): El texto del cual extraer palabras clave.
#
# Returns:
#   set[str]: Un conjunto de palabras clave unicas.
def get_keywords(text: str) -> set[str]:
    text_processed = re.sub(r'[^\w\s]', '', str(text).lower()) # Limpia puntuacion y convierte a minusculas
    # Lista personalizable de "stop words" (palabras comunes a ignorar)
    stop_words = {
        "de", "la", "el", "en", "y", "a", "los", "las", "un", "una", "es", "ser", "estar",
        "mi", "me", "que", "cual", "quien", "como", "cuando", "donde", "para", "por",
        "hola", "gracias", "ayuda", "ume", "umebot", "universidad", "umecit", "robot"
        # Se pueden anadir mas stop words comunes o especificas del dominio aqui.
    }
    words = [word for word in text_processed.split() if word not in stop_words and len(word) > 2] # Filtra stop words y palabras cortas
    return set(words)

# Recupera los N fragmentos mas relevantes de la base de conocimientos (formato JSONL)
# basandose en la coincidencia de palabras clave con la consulta del usuario.
# Implementa una estrategia RAG simple basada en conteo de keywords comunes.
#
# Args:
#   user_query (str): La pregunta o entrada del usuario.
#   knowledge_base (List[Dict[str, str]]): La base de conocimientos cargada.
#   top_n (int): El numero de fragmentos mas relevantes a devolver.
#
# Returns:
#   List[str]: Una lista de strings, cada uno representando un contexto relevante formateado.
def retrieve_relevant_context_from_jsonl(user_query: str,
                                         knowledge_base: List[Dict[str, str]],
                                         top_n: int = 1) -> List[str]:
    if not knowledge_base or not user_query or top_n <= 0: return []
    user_keywords = get_keywords(user_query)
    if not user_keywords: return [] # Si no hay keywords en la consulta, no se puede buscar

    scored_entries: List[Dict[str, Any]] = []
    for entry in knowledge_base:
        q_text = entry.get("question", "")
        entry_keywords = get_keywords(q_text)
        common_keywords = user_keywords.intersection(entry_keywords) # Palabras clave comunes
        score = len(common_keywords)
        if score > 0: # Solo considera entradas con alguna coincidencia de palabras clave.
            # Ponderacion simple del puntaje basada en la proporcion de keywords coincidentes.
            adjusted_score = score * (len(common_keywords) / len(user_keywords) if user_keywords else 1.0)
            scored_entries.append({"score": adjusted_score, "entry": entry})

    scored_entries.sort(key=lambda x: x["score"], reverse=True) # Ordena por puntaje descendente

    context_strings: List[str] = []
    for item in scored_entries[:top_n]: # Toma los N mejores
        q = item['entry'].get('question', 'Info relacionada')
        a = item['entry'].get('answer', 'No disponible')
        context_strings.append(f"Contexto Relevante (Pregunta similar: \"{q}\" Respuesta Conocida: \"{a}\")")
    return context_strings

# Clase principal para construir prompts para Modelos de Lenguaje Grandes (LLMs).
# Permite incorporar una personalidad base, contexto de RAG, contexto de archivos,
# historial de conversacion y la entrada actual del usuario para generar prompts
# en formatos compatibles con APIs de chat (como OpenAI) o para LLMs locales.
class PromptBuilder:
    # Inicializa el PromptBuilder.
    #
    # Args:
    #   predefined_system_prompt (str): El prompt de sistema base que define
    #                                     la personalidad y comportamiento general del robot.
    #
    # Raises:
    #   ValueError: Si predefined_system_prompt no es valido.
    def __init__(self, predefined_system_prompt: str):
        if not predefined_system_prompt or not isinstance(predefined_system_prompt, str):
            raise ValueError("Se requiere un 'predefined_system_prompt' valido (string no vacio).")

        self.system_prompt_core: str = predefined_system_prompt # Personalidad base del robot
        # Plantilla para anadir informacion dinamica y directivas comunes al final del prompt de sistema.
        self.system_prompt_end_template: str = (
            "\n\nConsidera la conversacion previa Y la INFORMACION ADICIONAL CONTEXTUAL (si se proporciona) "
            "para generar tu respuesta. Debes usar tags de animacion como ^runTag(nombre_animacion) "
            "intercalados en tu texto donde sea naturalmente apropiado para anadir expresividad. "
            "Responde siempre como {robot_name}."
            "\nFecha y hora actual: {current_date} {current_time}"
        )
        self.knowledge_base_jsonl: List[Dict[str, str]] = KNOWLEDGE_BASE_JSONL # Utiliza la base cargada globalmente
        self.robot_name: str = "Umebot" # Nombre del robot, puede ser configurable si es necesario.
        log.debug(f"PromptBuilder inicializado. System prompt core: '{predefined_system_prompt[:80]}...'")

    # Construye el contenido completo del mensaje de sistema ('system' role).
    # Combina el prompt de sistema base con contexto RAG, contexto de archivos
    # y una plantilla final que incluye fecha, hora e instrucciones adicionales.
    #
    # Args:
    #   rag_context (Optional[List[str]]): Lista de cadenas de contexto RAG.
    #   file_context (Optional[str]): Cadena con contexto de archivos adjuntos.
    #
    # Returns:
    #   str: El contenido completo y formateado para el mensaje de sistema.
    def _get_current_system_prompt_content(self,
                                           rag_context: Optional[List[str]] = None,
                                           file_context: Optional[str] = None) -> str:
        parts = [self.system_prompt_core] # Comienza con la personalidad base
        if rag_context: # Anade contexto RAG si esta disponible
            parts.append("\n\n[INFORMACION DE CONTEXTO ADICIONAL DE UMECIT (Usar si es relevante)]:\n" + "\n".join(rag_context))
        if file_context: # Anade contexto de archivos adjuntos en la conversacion actual, si existe.
            parts.append(f"\n\n[INFORMACION DE ARCHIVOS ADJUNTOS EN ESTA CONVERSACION]:\n{file_context}")

        now = datetime.now()
        # Formatea y anade la parte final del prompt de sistema (fecha, hora, directivas)
        final_system_additions = self.system_prompt_end_template.format(
            robot_name=self.robot_name, # Asegura que {robot_name} se formatee correctamente en la plantilla.
            current_date=now.strftime('%Y-%m-%d'),
            current_time=now.strftime('%H:%M:%S')
        )
        parts.append(final_system_additions)
        return "\n".join(parts).strip() # Une todas las partes en un solo string

    # Construye una lista de mensajes en el formato esperado por APIs de chat
    # como la de OpenAI (ej. [{"role": "system", "content": "..."}, ...]).
    # Incorpora el prompt de sistema, contexto RAG, historial de la base de datos
    # y la entrada actual del usuario (que puede ser texto o multimodal).
    #
    # Args:
    #   user_input_content (Union[str, List[Dict[str, Any]]]): La entrada del usuario (texto o multimodal).
    #   db_manager_instance (Optional[Any]): Instancia para acceder al historial de conversacion.
    #   current_conversation_id (Optional[int]): ID de la conversacion actual.
    #   file_context_from_db (Optional[str]): Contexto de archivos obtenido de la DB.
    #   max_history_interactions (int): Maximos pares de interacciones (user/assistant) a incluir del historial.
    #   num_rag_chunks (int): Numero de fragmentos RAG a recuperar e incluir.
    #
    # Returns:
    #   List[Dict[str, Any]]: Lista de mensajes formateados para la API de chat.
    def build_messages_for_chat_api(
        self,
        user_input_content: Union[str, List[Dict[str, Any]]],
        db_manager_instance: Optional[Any], # Idealmente, aqui iria el tipo 'DBManager'
        current_conversation_id: Optional[int],
        file_context_from_db: Optional[str] = None,
        max_history_interactions: int = 5,
        num_rag_chunks: int = 1
    ) -> List[Dict[str, Any]]:

        # Extrae el componente de texto de la entrada del usuario para la busqueda RAG,
        # incluso si la entrada es multimodal (ej. imagen + texto).
        text_for_rag_lookup = ""
        if isinstance(user_input_content, str):
            text_for_rag_lookup = user_input_content
        elif isinstance(user_input_content, list): # Manejo de entrada multimodal
            for part in user_input_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_for_rag_lookup = part.get("text", "")
                    break # Usa el primer texto encontrado

        # Recupera contexto RAG si es aplicable
        rag_context_list = retrieve_relevant_context_from_jsonl(
            text_for_rag_lookup, self.knowledge_base_jsonl, top_n=num_rag_chunks
        ) if num_rag_chunks > 0 else None

        # Construye el mensaje de sistema con el contexto RAG y de archivos
        system_content = self._get_current_system_prompt_content(rag_context_list, file_context_from_db)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]

        # Anade historial de la conversacion desde la base de datos.
        # NOTA: La logica de truncado sofisticada (por tokens/longitud) no esta implementada aqui.
        if current_conversation_id and db_manager_instance and max_history_interactions > 0:
            # Aqui iria la logica especifica para obtener y formatear 'raw_interactions' desde la DB.
            # Se asume un formato especifico para las interacciones devueltas por la DB.
            raw_interactions = db_manager_instance.get_interactions(current_conversation_id, limit=max_history_interactions * 2)
            for interaction in raw_interactions: # Asume que `interaction` tiene 'role' y 'content_text'
                role = interaction.get("role")
                # Asume que el texto del DBManager ('content_text') esta limpio y listo para usar.
                content = interaction.get("content_text")
                if role in ["user", "assistant"] and content:
                    messages.append({"role": role, "content": content})
            log.debug(f"Historial de {len(raw_interactions)} interacciones (aprox.) anadido al prompt.")

        # Anade la entrada actual del usuario al final de la lista de mensajes
        messages.append({"role": "user", "content": user_input_content})

        log.info(f"PromptBuilder: {len(messages)} mensajes preparados para API de Chat (OpenAI u compatible).")
        return messages

    # Convierte una lista de mensajes (formato API de chat OpenAI) en una unica
    # cadena de texto (string) formateada para ser usada con LLMs locales.
    # NOTA: Esta es una implementacion de EJEMPLO y conceptual. El formato
    # exacto (ej. uso de tokens especiales como <|system|>, <s>, etc.)
    # debe ajustarse al modelo local especifico que se este utilizando.
    #
    # Args:
    #   chat_api_messages (List[Dict[str, Any]]): Lista de mensajes en formato API de chat.
    #
    # Returns:
    #   str: Una unica cadena de texto representando el prompt completo.
    def build_single_string_for_local_llm(
        self,
        chat_api_messages: List[Dict[str, Any]] # Toma la salida de build_messages_for_chat_api
    ) -> str:
        prompt_parts = []
        for message in chat_api_messages:
            role = message.get("role", "system") # Asume 'system' como rol por defecto si no se especifica.
            content = message.get("content")

            # Ejemplo de tokens de formato para algunos modelos locales. Esto varia mucho.
            if role == "system":
                prompt_parts.append(f"<|system|>\n{content}</s>")
            elif role == "user":
                # Manejo basico de contenido multimodal para LLMs locales (ej. extrayendo solo texto).
                user_text_content = ""
                if isinstance(content, str):
                    user_text_content = content
                elif isinstance(content, list): # Si es multimodal (lista de partes)
                    for part in content:
                        if part.get("type") == "text":
                            user_text_content = part.get("text","")
                            break # Usa el primer texto encontrado
                prompt_parts.append(f"<|user|>\n{user_text_content}</s>")
            elif role == "assistant":
                prompt_parts.append(f"<|assistant|>\n{content}</s>")

        # Anade el token/indicador para que el modelo asistente comience su respuesta.
        prompt_parts.append("<|assistant|>") # Importante para "guiar" al modelo a generar la respuesta del asistente

        full_prompt_string = "\n".join(prompt_parts)
        log.debug(f"PromptBuilder: String unico para LLM local generado (longitud: {len(full_prompt_string)}).")
        return full_prompt_string
