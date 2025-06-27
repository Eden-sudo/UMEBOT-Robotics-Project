package com.example.umebotros2.gamepad

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.umebotros2.R

/**
 * Un adaptador para `RecyclerView` que gestiona de forma eficiente la visualización
 * de una lista de dispositivos gamepad (`GamepadInfo`).
 *
 * Utiliza `ListAdapter` y `DiffUtil` para un rendimiento óptimo, calculando
 * automáticamente las diferencias en la lista y animando los cambios de forma eficiente.
 *
 * @param onGamepadClicked Una función lambda que se invoca cuando el usuario hace clic en un
 * elemento de la lista, pasando el `GamepadInfo` correspondiente a ese elemento.
 */
class GamepadListAdapter(
    private val onGamepadClicked: (GamepadInfo) -> Unit
) : ListAdapter<GamepadInfo, GamepadListAdapter.GamepadViewHolder>(GamepadDiffCallback()) {

    /**
     * Crea una nueva instancia de `GamepadViewHolder` inflando el layout del item.
     * Es invocado por el `LayoutManager` del RecyclerView cuando necesita una nueva vista.
     */
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): GamepadViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_gamepad_device, parent, false)
        return GamepadViewHolder(view, onGamepadClicked)
    }

    /**
     * Vincula los datos de un `GamepadInfo` específico a un `GamepadViewHolder`.
     * Es invocado por el `LayoutManager` para mostrar los datos en una posición concreta.
     */
    override fun onBindViewHolder(holder: GamepadViewHolder, position: Int) {
        val gamepad = getItem(position)
        holder.bind(gamepad)
    }

    /**
     * Representa y gestiona la vista de un único elemento (`item_gamepad_device.xml`) en la lista.
     * Contiene las referencias a las vistas internas (TextViews) y la lógica para poblarlas.
     *
     * @param itemView La vista raíz del layout del item.
     * @param onGamepadClicked La misma función lambda del adaptador, pasada para gestionar el evento de clic.
     */
    class GamepadViewHolder(
        itemView: View,
        private val onGamepadClicked: (GamepadInfo) -> Unit
    ) : RecyclerView.ViewHolder(itemView) {

        private val nameTextView: TextView = itemView.findViewById(R.id.textview_gamepad_name)
        private val addressTextView: TextView = itemView.findViewById(R.id.textview_gamepad_address)

        /**
         * Vincula (binds) los datos de un objeto `GamepadInfo` a los componentes de la vista
         * (TextViews) de este ViewHolder y configura el listener de clic.
         *
         * @param gamepadInfo El objeto de datos que se debe mostrar.
         */
        fun bind(gamepadInfo: GamepadInfo) {
            nameTextView.text = gamepadInfo.name ?: "Nombre Desconocido"
            addressTextView.text = gamepadInfo.address
            itemView.setOnClickListener {
                onGamepadClicked(gamepadInfo)
            }
        }
    }

    /**
     * Implementación de `DiffUtil.ItemCallback` para que `ListAdapter` pueda calcular
     * de forma eficiente las diferencias entre dos listas de `GamepadInfo`.
     * Esto permite al `RecyclerView` realizar animaciones optimizadas y precisas.
     */
    private class GamepadDiffCallback : DiffUtil.ItemCallback<GamepadInfo>() {
        /**
         * Comprueba si dos items representan el mismo objeto. La dirección MAC es un identificador único perfecto.
         */
        override fun areItemsTheSame(oldItem: GamepadInfo, newItem: GamepadInfo): Boolean {
            return oldItem.address == newItem.address
        }

        /**
         * Comprueba si el contenido visual de dos items es el mismo.
         * Como `GamepadInfo` es una `data class`, la comparación `==` verifica todos los campos,
         * lo cual es exactamente lo que se necesita.
         */
        override fun areContentsTheSame(oldItem: GamepadInfo, newItem: GamepadInfo): Boolean {
            return oldItem == newItem
        }
    }
}
