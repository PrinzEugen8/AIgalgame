plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.aigalgame.demo"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.aigalgame.demo"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

val requiredLive2DAssets = listOf(
    "src/main/assets/live2d-web/index.html",
    "src/main/assets/live2d-web/stage.js",
    "src/main/assets/live2d-web/vendor/live2dcubismcore.min.js",
    "src/main/assets/live2d-web/vendor/pixi.min.js",
    "src/main/assets/live2d-web/vendor/cubism4.min.js",
    "src/main/assets/live2d/models/Haru/Haru.model3.json",
    "src/main/assets/live2d/models/Haru/Haru.moc3",
    "src/main/assets/live2d/models/Haru/Haru.physics3.json",
    "src/main/assets/live2d/models/Haru/Haru.pose3.json",
    "src/main/assets/live2d/models/Haru/Haru.2048/texture_00.png",
    "src/main/assets/live2d/models/Haru/Haru.2048/texture_01.png",
    "src/main/assets/live2d/models/Haru/expressions/F01.exp3.json",
    "src/main/assets/live2d/models/Haru/motions/haru_g_idle.motion3.json",
    "src/main/assets/live2d/models/Haru/motions/haru_g_m26.motion3.json"
)

val forbiddenLive2DAssets = listOf(
    "src/main/assets/live2d-web/official/index.html",
    "src/main/assets/live2d-web/official/official-stage.js",
    "src/main/assets/live2d/sdk/live2dcubismcore.min.js"
)

tasks.register("verifyLive2DAssets") {
    group = "verification"
    description = "Checks that the offline Live2D Web runtime and fallback model are packaged."
    doLast {
        val missing = requiredLive2DAssets.filterNot { layout.projectDirectory.file(it).asFile.exists() }
        if (missing.isNotEmpty()) {
            throw GradleException("Missing Live2D assets: ${missing.joinToString()}")
        }
        val forbidden = forbiddenLive2DAssets.filter { layout.projectDirectory.file(it).asFile.exists() }
        if (forbidden.isNotEmpty()) {
            throw GradleException("Remove obsolete Live2D renderer assets: ${forbidden.joinToString()}")
        }
        val stageJs = layout.projectDirectory.file("src/main/assets/live2d-web/stage.js").asFile.readText()
        if ("live2d/samples/" in stageJs) {
            throw GradleException("Live2D stage.js must default to live2d/models, not live2d/samples")
        }
    }
}

tasks.named("preBuild") {
    dependsOn("verifyLive2DAssets")
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.datastore:datastore-preferences:1.1.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.media3:media3-datasource-okhttp:1.5.1")
    implementation("androidx.media3:media3-exoplayer:1.5.1")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
