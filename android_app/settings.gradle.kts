// fork 本地改动：适配这台构建机的 ~/.gradle/init.gradle。
//
// 那台机器的 init.gradle 给 allprojects 注入仓库，和这里的 FAIL_ON_PROJECT_REPOS
// 冲突，Gradle 直接报错：
//   "Build was configured to prefer settings repositories over project repositories
//    but repository 'maven' was added by initialization script"
// 因此改成 PREFER_SETTINGS，并把 aliyun 镜像写在 settings 里（镜像只在 settings 里
// 才生效）。
pluginManagement {
    repositories {
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
        maven { url = uri("https://maven.aliyun.com/repository/public") }
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.PREFER_SETTINGS)
    repositories {
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/public") }
        google()
        mavenCentral()
    }
}

rootProject.name = "OpenCodeNative"
include(":app")
