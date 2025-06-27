plugins {
    // Plugin principal para construir una aplicación Android.
    alias(libs.plugins.android.application)
    // Plugin para habilitar el soporte de Kotlin en el proyecto.
    alias(libs.plugins.kotlin.android)
    // Plugin para habilitar el soporte del framework de UI Jetpack Compose (aunque no lo estés usando activamente para toda la UI).
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.example.umebotros2"
    compileSdk = 35 // La versión del SDK de Android contra la que se compila la app.

    defaultConfig {
        applicationId = "com.example.umebotros2"
        // La versión mínima del sistema operativo requerida. API 23 corresponde a Android 6.0 (Marshmallow),
        // necesario para la tablet del robot Pepper.
        minSdk = 23
        targetSdk = 35 // La versión del SDK para la que la app fue probada.
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false // Desactiva la ofuscación y reducción de código para la build de release.
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }
    
    // Configuración para usar una cadena de herramientas de Java más moderna (JDK 17),
    // a menudo requerida por las versiones recientes del plugin de Android Gradle.
    java {
        toolchain {
            languageVersion.set(JavaLanguageVersion.of(17))
        }
    }

    buildFeatures {
        // Habilita el View Binding para acceder a las vistas XML de forma segura.
        viewBinding = true
    }
}

dependencies {
    // ---- Fundamentales de AndroidX y Kotlin ----
    implementation(libs.androidx.core.ktx) // Funciones de extensión de Kotlin para el framework de Android.
    implementation(libs.androidx.appcompat) // Clases de compatibilidad para la UI (ej. AppCompatActivity).

    // ---- Componentes de UI (Vistas XML) ----
    implementation(libs.material) // Componentes de Material Design (botones, switches, etc.).
    implementation(libs.androidx.constraintlayout) // Para construir layouts complejos y flexibles.
    implementation(libs.androidx.drawerlayout) // Para el panel de navegación lateral.
    implementation(libs.androidx.recyclerview) // Para mostrar listas eficientemente (historial de chat, lista de gamepads).

    // ---- Arquitectura (MVVM) y Ciclo de Vida ----
    implementation(libs.androidx.activity.ktx) // Para el delegate 'by viewModels'.
    implementation(libs.androidx.lifecycle.runtime.ktx) // Para lifecycleScope (corutinas seguras).
    implementation(libs.androidx.lifecycle.viewmodel.ktx) // Para la clase ViewModel.

    // ---- Corutinas ----
    implementation(libs.kotlinx.coroutines.core) // Soporte fundamental para corutinas.
    implementation(libs.kotlinx.coroutines.android) // Dispatchers para el hilo principal de Android.

    // ---- Redes ----
    implementation(libs.okhttp) // Cliente HTTP y WebSocket para la comunicación con el backend.

    // ---- Dependencias de Prueba ----
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
}
