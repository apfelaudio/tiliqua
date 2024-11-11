all: tiliqua

tiliqua:
	cd vexriscv/scala && sbt compile "runMain lunasoc.GenCoreTiliqua"
	mv vexriscv/scala/vexriscv_tiliqua.* vexriscv/
