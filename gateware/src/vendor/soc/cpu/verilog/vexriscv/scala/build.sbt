val spinalVersion = "1.6.0"

lazy val vexRiscv = RootProject(uri("https://github.com/SpinalHDL/VexRiscv.git#master"))

lazy val root = (project in file(".")).
  settings(
    inThisBuild(List(
      organization := "com.github.spinalhdl",
      scalaVersion := "2.11.12",
      version      := "0.1.0-SNAPSHOT"
    )),
    name := "VexRiscvOnWishbone",
    libraryDependencies ++= Seq(
        compilerPlugin("com.github.spinalhdl" % "spinalhdl-idsl-plugin_2.11" % spinalVersion)
    ),
    scalacOptions += s"-Xplugin-require:idsl-plugin"
  ).dependsOn(vexRiscv)

fork := true
