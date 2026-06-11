plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val aigalgameCompileSdk: Int = (findProperty("aigalgame.compileSdkOverride") as? String)?.toInt() ?: 36
val aigalgameTargetSdk: Int = (findProperty("aigalgame.targetSdkOverride") as? String)?.toInt() ?: 36

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
    }
}

tasks.named("preBuild") {
    dependsOn("verifyOfficialLive2DAssets")
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

    testImplementation("junit:junit:4.13.2")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
