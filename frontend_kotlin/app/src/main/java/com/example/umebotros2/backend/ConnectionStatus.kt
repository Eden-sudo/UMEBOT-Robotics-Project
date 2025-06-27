package com.example.umebotros2.backend

/**
 * Define los distintos estados posibles del ciclo de vida de la conexión gestionada por `BackendConnector`.
 *
 * Representa una máquina de estados finitos que permite a la interfaz de usuario (UI)
 * reaccionar de forma declarativa a los cambios en el proceso de conexión,
 * mostrando indicadores de carga, mensajes de error o estados de éxito.
 */
enum class ConnectionStatus {
    /**
     * El conector está inactivo, ya sea en su estado inicial o después de haber sido detenido explícitamente.
     * No hay actividad de red en curso.
     */
    IDLE,

    /**
     * El conector está buscando activamente el servicio del backend en la red local mediante NSD (Network Service Discovery).
     */
    DISCOVERING,

    /**
     * Se ha encontrado un servicio compatible en la red. El siguiente paso es resolver su dirección IP y puerto.
     */
    SERVICE_FOUND,

    /**
     * La dirección IP y el puerto del servicio se han obtenido con éxito. El conector está listo para iniciar la conexión WebSocket.
     */
    RESOLVED,

    /**
     * Se está intentando establecer la conexión WebSocket con el servidor.
     */
    CONNECTING,

    /**
     * La conexión WebSocket está establecida y activa. La comunicación bidireccional es posible.
     */
    CONNECTED,

    /**
     * Se perdió la conexión previamente establecida y el conector está intentando restablecerla automáticamente.
     */
    RECONNECTING,

    /**
_    * El conector no está conectado. Este estado puede ser el resultado de un fallo de conexión,
_    * una desconexión por parte del servidor, o un cierre manual.
     */
    DISCONNECTED
}
