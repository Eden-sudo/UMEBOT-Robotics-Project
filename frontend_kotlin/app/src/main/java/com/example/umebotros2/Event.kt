package com.example.umebotros2

/**
 * Wrapper para datos que representan un evento de un solo consumo.
 * util para LiveData/StateFlow donde un cambio de configuración podría re-emitir el último valor,
 * pero la acción del evento (ej. mostrar un Snackbar) solo debe ocurrir una vez.
 *
 * @param T El tipo del contenido del evento.
 * @property content El contenido real del evento.
 */
open class Event<out T>(private val content: T) {

    var hasBeenHandled = false
        private set // Solo se puede modificar internamente (dentro de esta clase)

    /**
     * Devuelve el contenido [content] si no ha sido manejado previamente,
     * y lo marca como manejado.
     *
     * @return El contenido, o null si ya fue manejado.
     */
    fun getContentIfNotHandled(): T? {
        return if (hasBeenHandled) {
            null
        } else {
            hasBeenHandled = true
            content
        }
    }

    /**
     * Devuelve el contenido [content] sin marcarlo como manejado.
     * util para previsualizar el valor sin "consumir" el evento.
     */
    @Suppress("unused") // Para evitar advertencias si no se usa en todos los proyectos
    fun peekContent(): T = content

