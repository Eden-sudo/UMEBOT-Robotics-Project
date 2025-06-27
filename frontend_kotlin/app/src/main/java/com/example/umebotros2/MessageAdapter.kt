package com.example.umebotros2

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.umebotros2.R
import com.example.umebotros2.backend.ChatMessage

/** Constante para el tipo de vista de un mensaje del usuario. */
private const val VIEW_TYPE_USER_MESSAGE = 1
/** Constante para el tipo de vista de un mensaje del robot. */
private const val VIEW_TYPE_ROBOT_MESSAGE = 2
/** Constante para el tipo de vista de un mensaje del sistema. */
private const val VIEW_TYPE_SYSTEM_MESSAGE = 3

/**
 * Un adaptador para `RecyclerView` que gestiona la visualización de una lista de `ChatMessage`.
 *
 * Su característica principal es la capacidad de mostrar diferentes layouts para los mensajes
 * según quién sea el remitente (`sender`), permitiendo una interfaz de chat con mensajes
 * de usuario alineados de un lado y los del robot del otro.
 *
 * Utiliza `ListAdapter` para manejar las actualizaciones de la lista de forma eficiente.
 */
class MessageAdapter : ListAdapter<ChatMessage, MessageAdapter.MessageViewHolder>(MessageDiffCallback()) {

    /**
     * Clase base sellada (`sealed`) para todos los `ViewHolder` de mensajes.
     * El uso de una `sealed class` asegura que todos los posibles tipos de ViewHolder
     * estén definidos en este archivo, lo que mejora la seguridad y legibilidad del código.
     *
     * @param itemView La vista raíz del layout del item.
     */
    sealed class MessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        /**
         * Método abstracto que cada ViewHolder específico debe implementar para vincular
         * los datos de un `ChatMessage` a su layout correspondiente.
         * @param message El mensaje a mostrar.
         */
        abstract fun bind(message: ChatMessage)

        /** ViewHolder para los mensajes enviados por el usuario. Gestiona el layout `item_message_user.xml`. */
        class UserMessageViewHolder(itemView: View) : MessageViewHolder(itemView) {
            private val messageTextView: TextView = itemView.findViewById(R.id.message_text_view_user)

            override fun bind(message: ChatMessage) {
                messageTextView.text = message.text
            }
        }

        /** ViewHolder para los mensajes enviados por el robot. Gestiona el layout `item_message_robot.xml`. */
        class RobotMessageViewHolder(itemView: View) : MessageViewHolder(itemView) {
            private val senderTextView: TextView = itemView.findViewById(R.id.sender_text_view_robot)
            private val messageTextView: TextView = itemView.findViewById(R.id.message_text_view_robot)

            override fun bind(message: ChatMessage) {
                senderTextView.text = message.sender
                senderTextView.visibility = if (message.sender.isNotBlank()) View.VISIBLE else View.GONE
                messageTextView.text = message.text
            }
        }

        /** ViewHolder para los mensajes del sistema. Gestiona el layout `item_message_system.xml`. */
        class SystemMessageViewHolder(itemView: View) : MessageViewHolder(itemView) {
            private val messageTextView: TextView = itemView.findViewById(R.id.messageText)

            override fun bind(message: ChatMessage) {
                messageTextView.text = message.text
            }
        }
    }

    /**
     * Determina qué tipo de layout se debe usar para un elemento en una posición específica.
     * Esta función es el núcleo de la lógica de vistas múltiples.
     *
     * @param position La posición del elemento en la lista.
     * @return Un entero que representa el tipo de vista (ej. `VIEW_TYPE_USER_MESSAGE`).
     */
    override fun getItemViewType(position: Int): Int {
        val message = getItem(position)
        return when (message.sender.lowercase()) {
            "usuario", "usuario (voz)" -> VIEW_TYPE_USER_MESSAGE
            "umebot" -> VIEW_TYPE_ROBOT_MESSAGE
            "sistema" -> VIEW_TYPE_SYSTEM_MESSAGE
            else -> VIEW_TYPE_ROBOT_MESSAGE // Tipo por defecto para remitentes desconocidos
        }
    }

    /**
     * Crea una nueva instancia de `MessageViewHolder` según el `viewType`.
     * El `RecyclerView` llama a este método cuando necesita crear una nueva celda.
     *
     * @param parent El ViewGroup al que se añadirá la nueva vista.
     * @param viewType El tipo de vista devuelto por `getItemViewType`.
     * @return Una nueva instancia de `MessageViewHolder` (`UserMessageViewHolder`, `RobotMessageViewHolder`, etc.).
     */
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): MessageViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            VIEW_TYPE_USER_MESSAGE -> {
                val view = inflater.inflate(R.layout.item_message_user, parent, false)
                MessageViewHolder.UserMessageViewHolder(view)
            }
            VIEW_TYPE_ROBOT_MESSAGE -> {
                val view = inflater.inflate(R.layout.item_message_robot, parent, false)
                MessageViewHolder.RobotMessageViewHolder(view)
            }
            VIEW_TYPE_SYSTEM_MESSAGE -> {
                val view = inflater.inflate(R.layout.item_message_system, parent, false)
                MessageViewHolder.SystemMessageViewHolder(view)
            }
            else -> throw IllegalArgumentException("onCreateViewHolder: ViewType desconocido - $viewType")
        }
    }

    /**
     * Vincula los datos de un `ChatMessage` a un `MessageViewHolder` existente.
     *
     * @param holder El `MessageViewHolder` que debe ser actualizado.
     * @param position La posición del elemento en la lista.
     */
    override fun onBindViewHolder(holder: MessageViewHolder, position: Int) {
        holder.bind(getItem(position))
    }
}

/**
 * Implementación de `DiffUtil.ItemCallback` para que `ListAdapter` pueda calcular
 * de forma eficiente las diferencias entre dos listas de `ChatMessage`.
 */
class MessageDiffCallback : DiffUtil.ItemCallback<ChatMessage>() {
    /**
     * Comprueba si dos items representan el mismo objeto. Usar una combinación de
     * `timestamp`, `sender` y `text` es una estrategia razonable para obtener
     * un identificador único cuando no se dispone de un ID explícito.
     */
    override fun areItemsTheSame(oldItem: ChatMessage, newItem: ChatMessage): Boolean {
        return oldItem.timestamp == newItem.timestamp && oldItem.sender == newItem.sender && oldItem.text == newItem.text
    }

    /**
     * Comprueba si el contenido visual de dos items es el mismo.
     * Dado que `ChatMessage` es una `data class`, la comparación `==` verifica todos
     * los campos, lo que es ideal para este propósito.
     */
    override fun areContentsTheSame(oldItem: ChatMessage, newItem: ChatMessage): Boolean {
        return oldItem == newItem
    }
}
