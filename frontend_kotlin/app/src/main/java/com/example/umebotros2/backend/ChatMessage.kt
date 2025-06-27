package com.example.umebotros2.backend

/**
 * Modela un único mensaje dentro de la conversación entre el usuario y el robot.
 *
 * Es una estructura de datos inmutable (`data class`) que encapsula toda la
 * información necesaria para representar un mensaje en la interfaz de usuario,
 * incluyendo su contenido, quién lo envió y cuándo.
 *
 * @property sender El identificador de quién envía el mensaje. Típicamente será "Usuario" o "Umebot".
 * @property text El contenido textual del mensaje que se mostrará en la UI.
 * @property timestamp La marca de tiempo (en milisegundos desde la época) en que se creó el mensaje.
 * Por defecto, se asigna el tiempo actual del sistema. Es útil para ordenar
 * los mensajes cronológicamente.
 * @property source Un identificador opcional para la fuente o el tipo de mensaje (ej. "IA", "sistema", "error").
 * Permite un procesamiento o visualización diferenciada si es necesario.
 */
data class ChatMessage(
    val sender: String,
    val text: String,
    val timestamp: Long = System.currentTimeMillis(),
    val source: String? = null
)
