plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.gms.google-services")
}

val aigalgameCompileSdk: Int = (findProperty("aigalgame.compileSdkOverride") as? String)?.toInt()
    ?: (findProperty("PROP_COMPILE_SDK_VERSION") as? String)?.toInt()
    ?: 35
val aigalgameTargetSdk: Int = (findProperty("aigalgame.targetSdkOverride") as? String)?.toInt()
    ?: (findProperty("PROP_TARGET_SDK_VERSION") as? String)?.toInt()
    ?: 35
val sherpaVersion = "1.13.2"
val sherpaAar = layout.projectDirectory.file("libs/sherpa-onnx-static-link-onnxruntime-$sherpaVersion.aar")

android {
    namespace = "com.aigalgame.demo"
    compileSdk = aigalgameCompileSdk

    defaultConfig {
        applicationId = "com.aigalgame.demo"
        minSdk = 26
        targetSdk = aigalgameTargetSdk
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        getByName("release") {
            signingConfig = signingConfigs.getByName("debug")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    androidResources {
        noCompress += setOf("onnx", "txt")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

val officialLive2DRequiredFiles = listOf(
    "libs/Live2DCubismCore.aar",
    "src/main/java/com/live2d/sdk/cubism/framework/CubismFramework.java",
    "src/main/java/com/live2d/sdk/cubism/framework/rendering/android/CubismRendererAndroid.java",
    "src/main/assets/com/live2d/sdk/cubism/framework/shaders/standardES/VertShaderSrc.vert",
    "src/main/assets/com/live2d/sdk/cubism/framework/shaders/standardES/FragShaderSrc.frag",
    "src/main/assets/live2d/models/neko/neko.model3.json",
    "src/main/assets/live2d/models/neko/neko.moc3",
    "src/main/assets/live2d/models/neko/neko.physics3.json",
    "src/main/assets/live2d/models/neko/neko.cdi3.json",
    "src/main/assets/live2d/models/neko/neko.4096/texture_00.png",
    "src/main/assets/live2d/models/neko/neko.4096/texture_01.png",
    "src/main/assets/live2d/models/neko/neko.4096/texture_02.png",
    "src/main/assets/live2d/models/neko/neko.4096/texture_03.png",
    "src/main/assets/live2d/models/neko/neko.4096/texture_04.png",
    "src/main/assets/live2d/models/neko/neko.4096/texture_05.png",
    "src/main/res/drawable-nodpi/character_atri_idle.png",
    "src/main/res/drawable-nodpi/character_murasame_idle.png"
)

val obsoleteLive2DPaths = listOf(
    "src/main/assets/live2d-web",
    "src/main/assets/live2d/models/Haru",
    "src/main/assets/live2d-web/vendor/pixi.min.js",
    "src/main/assets/live2d-web/vendor/cubism4.min.js"
)

tasks.register("verifyOfficialLive2DAssets") {
    group = "verification"
    description = "Checks the official Android Live2D runtime, NEKO model, and static PNG fallback assets."
    doLast {
        val missing = officialLive2DRequiredFiles.filterNot { layout.projectDirectory.file(it).asFile.exists() }
        if (missing.isNotEmpty()) {
            throw GradleException("Missing official Live2D assets: ${missing.joinToString()}")
        }
        val obsolete = obsoleteLive2DPaths.filter { layout.projectDirectory.file(it).asFile.exists() }
        if (obsolete.isNotEmpty()) {
            throw GradleException("Remove obsolete Web/Pixi/Haru Live2D assets: ${obsolete.joinToString()}")
        }
        val stageKt = layout.projectDirectory.file("src/main/java/com/aigalgame/demo/Live2DStage.kt").asFile.readText()
        val configKt = layout.projectDirectory.file("src/main/java/com/aigalgame/demo/Live2DConfig.kt").asFile.readText()
        val forbiddenStageTokens = listOf("WebView", "android.webkit", "live2d-web", "pixi", "Pixi", "Haru")
        val offenders = forbiddenStageTokens.filter { it in stageKt || it in configKt }
        if (offenders.isNotEmpty()) {
            throw GradleException("Official renderer source still references obsolete Live2D Web/Haru tokens: ${offenders.joinToString()}")
        }
        if ("OfficialLive2DView" !in stageKt || "CharacterStandee(" !in stageKt) {
            throw GradleException("Live2DStage must use the official Android view and keep the static PNG fallback.")
        }
        if ("Live2DBootLoadingScreen" !in layout.projectDirectory.file("src/main/java/com/aigalgame/demo/MainActivity.kt").asFile.readText()) {
            throw GradleException("App startup must keep the Live2D boot loading screen.")
        }
        if ("PersistentLive2DEngine" !in layout.projectDirectory.file("src/main/java/com/aigalgame/demo/live2d/OfficialLive2DView.java").asFile.readText()) {
            throw GradleException("OfficialLive2DView must attach to the persistent Live2D engine.")
        }
        if ("releaseRenderer()" in stageKt) {
            throw GradleException("Live2DStage must not release the persistent renderer when Compose disposes.")
        }
    }
}

tasks.register("verifyLocalAsrAssets") {
    group = "verification"
    description = "Checks sherpa-onnx local ASR runtime and model assets."
    val required = listOf(
        "libs/sherpa-onnx-static-link-onnxruntime-$sherpaVersion.aar",
        "src/main/assets/sherpa-onnx-streaming-paraformer-bilingual-zh-en/encoder.int8.onnx",
        "src/main/assets/sherpa-onnx-streaming-paraformer-bilingual-zh-en/decoder.int8.onnx",
        "src/main/assets/sherpa-onnx-streaming-paraformer-bilingual-zh-en/tokens.txt",
        "src/main/assets/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/model.int8.onnx",
        "src/main/assets/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/tokens.txt",
    )
    doLast {
        val missing = required.filterNot { layout.projectDirectory.file(it).asFile.exists() }
        if (missing.isNotEmpty()) {
            throw GradleException(
                "Missing Android local ASR assets: ${missing.joinToString()}. " +
                    "Run from repo root: powershell -ExecutionPolicy Bypass -File tools/fetch_android_asr_assets.ps1 -Download " +
                    "or pass -SourceProject to copy an existing local ASR setup."
            )
        }
    }
}

tasks.named("preBuild") {
    dependsOn("verifyOfficialLive2DAssets")
    dependsOn("verifyLocalAsrAssets")
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation(platform("com.google.firebase:firebase-bom:34.0.0"))
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
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("com.google.firebase:firebase-messaging")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation(files("libs/Live2DCubismCore.aar"))
    implementation(files(sherpaAar.asFile))

    testImplementation("junit:junit:4.13.2")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
