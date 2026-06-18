pluginManagement {
  repositories {
    maven { url = uri("https://maven.aliyun.com/repository/google") }
    maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
    maven { url = uri("https://maven.aliyun.com/repository/public") }
    mavenCentral()
    google()
    gradlePluginPortal()
  }
}

plugins { id("org.gradle.toolchains.foojay-resolver-convention").version("0.5.0") }

include(
    ":react-native-gradle-plugin",
    ":settings-plugin",
    ":shared",
    ":shared-testutil",
)

rootProject.name = "gradle-plugin-root"
