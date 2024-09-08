val spinalVersion = "1.10.2a"

lazy val vexRiscv = RootProject(uri("https://github.com/SpinalHDL/VexRiscv.git#master"))

lazy val root = (project in file(".")).
  settings(
    inThisBuild(List(
      organization := "com.github.spinalhdl",
      scalaVersion := "2.12.18",
      version      := "2.0.0"
    )),
    name := "VexRiscvOnWishbone",
    libraryDependencies ++= Seq(
        "com.github.spinalhdl" %% "spinalhdl-core" % spinalVersion,
        "com.github.spinalhdl" %% "spinalhdl-lib" % spinalVersion,
        compilerPlugin("com.github.spinalhdl" %% "spinalhdl-idsl-plugin" % spinalVersion)
    ),
    scalacOptions += s"-Xplugin-require:idsl-plugin"
  ).dependsOn(vexRiscv)

fork := true
