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
    "src/main/assets/live2d-web/fallbacks/haru-stage.png",
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

val requiredLive2DNativeAssets = listOf(
    "src/main/res/drawable-nodpi/live2d_haru_fallback.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_00.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_01.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_02.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_03.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_04.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_05.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_06.png",
    "src/main/res/drawable-nodpi/live2d_haru_fallback_07.png"
)

val forbiddenLive2DAssets = listOf(
    "src/main/assets/live2d-web/official/index.html",
    "src/main/assets/live2d-web/official/official-stage.js",
    "src/main/assets/live2d/sdk/live2dcubismcore.min.js",
    "src/main/assets/live2d/samples"
)

tasks.register("verifyLive2DAssets") {
    group = "verification"
    description = "Checks that the offline Live2D Web runtime and fallback model are packaged."
    doLast {
        val missing = requiredLive2DAssets.filterNot { layout.projectDirectory.file(it).asFile.exists() }
        val missingNative = requiredLive2DNativeAssets.filterNot { layout.projectDirectory.file(it).asFile.exists() }
        if (missing.isNotEmpty() || missingNative.isNotEmpty()) {
            throw GradleException("Missing Live2D assets: ${(missing + missingNative).joinToString()}")
        }
        val forbidden = forbiddenLive2DAssets.filter { layout.projectDirectory.file(it).asFile.exists() }
        if (forbidden.isNotEmpty()) {
            throw GradleException("Remove obsolete Live2D renderer assets: ${forbidden.joinToString()}")
        }
        val stageJs = layout.projectDirectory.file("src/main/assets/live2d-web/stage.js").asFile.readText()
        if ("live2d/samples/" in stageJs) {
            throw GradleException("Live2D stage.js must default to live2d/models, not live2d/samples")
        }
        if ("getLocalBounds" !in stageJs || "pivot.set" !in stageJs) {
            throw GradleException("Live2D stage.js must use bounds-based pivot fitting")
        }
        if ("modelBaseHeight" in stageJs || "modelBaseWidth" in stageJs) {
            throw GradleException("Live2D stage.js must not use the obsolete modelBase width/height fit")
        }
        if (
            "transparent: true" !in stageJs ||
            "backgroundAlpha: 0" !in stageJs ||
            "app.renderer.backgroundAlpha = 0" !in stageJs ||
            "webBackgroundDisabled: true" !in stageJs
        ) {
            throw GradleException("Live2D stage.js must keep the WebView/WebGL surface transparent and let Compose own the background")
        }
        if ("preferWebGLVersion: 1" !in stageJs || "PIXI.settings.PREFER_ENV" !in stageJs) {
            throw GradleException("Live2D stage.js must force Pixi onto the WebGL1 path for Android WebView")
        }
        if ("ENABLE_DOM_PRESENTER = true" !in stageJs || "notifyPresented(\"dom-presenter\")" !in stageJs) {
            throw GradleException("Live2D stage.js must present verified model frames through the guarded DOM presenter")
        }
        if ("MODEL_PIXEL_THRESHOLD" !in stageJs || "waiting-model-pixels" !in stageJs || "modelPixelReady" !in stageJs) {
            throw GradleException("Live2D stage.js must gate presentation on real model pixels, not opaque background alpha")
        }
        if ("onPresented" !in stageJs) {
            throw GradleException("Live2D stage.js must notify Android when WebGL is presented")
        }
        val indexHtml = layout.projectDirectory.file("src/main/assets/live2d-web/index.html").asFile.readText()
        if ("present-canvas" !in indexHtml || "present-image" !in indexHtml) {
            throw GradleException("Live2D index.html must keep presenter elements for diagnostics")
        }
        val hiddenPresenterPattern = Regex(
            "#present-(canvas|image)\\s*\\{[^}]*display\\s*:\\s*none",
            setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)
        )
        if ("#present-canvas" !in indexHtml || "#present-image" !in indexHtml || hiddenPresenterPattern.containsMatchIn(indexHtml)) {
            throw GradleException("Live2D presenter layers must stay displayable and hide only with visibility/opacity")
        }
        if ("fallback-image" !in indexHtml || "src=\"./fallbacks/haru-stage.png\"" !in indexHtml) {
            throw GradleException("Live2D Web stage must expose the packaged Haru DOM fallback while waiting for verified model frames")
        }
        if ("presentCanvas.style.display = \"block\"" !in stageJs || "presentImage.style.display = \"block\"" !in stageJs) {
            throw GradleException("Live2D presenter JS must restore display:block when exposing verified model frames")
        }
        if ("setFallbackVisible(false)" in stageJs || "setFallbackVisible(!visible)" in stageJs) {
            throw GradleException("Live2D presenter must not hide the Haru fallback from an internal pixel-ready false positive")
        }
        if ("model-pixels" !in indexHtml) {
            throw GradleException("Live2D self-test must expose model pixel diagnostics")
        }
        val stageKt = layout.projectDirectory.file("src/main/java/com/aigalgame/demo/Live2DStage.kt").asFile.readText()
        if (
            "val useNativeVisibilityGuard = stageMode == \"home\"" !in stageKt ||
            "val showNativeFallback = !useWebStage || !webStagePresented || useNativeVisibilityGuard" !in stageKt ||
            "baseHeight * nativePlacement.scale" !in stageKt
        ) {
            throw GradleException("Home must keep a placement-aware native Haru guard above the WebView until Android composition is visibly reliable")
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
