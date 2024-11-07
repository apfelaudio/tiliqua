package lunasoc

import vexriscv.{VexRiscv, VexRiscvConfig, plugin}
import vexriscv.ip.{DataCacheConfig, InstructionCacheConfig}
import vexriscv.plugin._

import spinal.core._
import spinal.core.SpinalConfig
import spinal.core.internals.{
  ExpressionContainer,
  PhaseAllocateNames,
  PhaseContext
}
import spinal.lib._
import spinal.lib.sim.Phase

import scala.collection.mutable.ArrayBuffer

import vexriscv.ip.fpu.{FpuCore, FpuParameter}

object GenCoreCynthion {
  def main(args: Array[String]) {
    val outputFile = "vexriscv_cynthion"
    val spinalConfig =
      LunaSpinalConfig.copy(netlistFileName = outputFile + ".v")

    spinalConfig.generateVerilog {
      // configure plugins
      val plugins = ArrayBuffer[Plugin[VexRiscv]]()
      plugins ++= List(
        new IBusSimplePlugin(
          resetVector = null,
          prediction = STATIC,
          cmdForkOnSecondStage = false,
          cmdForkPersistence = false, //Not required as the wishbone bridge ensure it
          compressedGen = true,
          memoryTranslatorPortConfig = null
        ),
        new DBusCachedPlugin(
          dBusCmdMasterPipe = true,
          dBusCmdSlavePipe = true,
          dBusRspSlavePipe = false,
          relaxedMemoryTranslationRegister = false,
          config = new DataCacheConfig(
            cacheSize = 1024,
            bytePerLine = 32,
            wayCount = 1,
            addressWidth = 32,
            cpuDataWidth = 32,
            memDataWidth = 32,
            catchAccessError = true,
            catchIllegal = true,
            catchUnaligned = true,
            withLrSc = true, // load-reserved/store-conditional instructions (LB, LH, LW, SB, SH, SW etc.)
            withAmo = true,  // atomic memory operation instructions (AMOSWAP, AMOADD, AMOAND etc.)
            earlyWaysHits = true
          ),
          memoryTranslatorPortConfig = null,
          csrInfo = true
        ),
        new StaticMemoryTranslatorPlugin(
          ioRange = _.msb    // 0x8000_0000 -> 0xffff_ffff will not be cached
        ),
        new DecoderSimplePlugin(
          catchIllegalInstruction = true
        ),
        new RegFilePlugin(
          regFileReadyKind = plugin.SYNC,
          zeroBoot = false
        ),
        new IntAluPlugin,
        new SrcPlugin(
          separatedAddSub = false,
          executeInsertion = true
        ),
        new FullBarrelShifterPlugin,
        new HazardSimplePlugin(
          bypassExecute = true,
          bypassMemory = true,
          bypassWriteBack = true,
          bypassWriteBackBuffer = true,
          pessimisticUseSrc = false,
          pessimisticWriteRegFile = false,
          pessimisticAddressMatch = false
        ),
        new BranchPlugin(
          earlyBranch = false,
          catchAddressMisaligned = true
        ),
        new CsrPlugin(
          CsrPluginConfig.all(mtvecInit = null).copy(ebreakGen = true, xtvecModeGen = false)
        ),
        new YamlPlugin(outputFile + ".yaml"),
        new MulPlugin,
        new DivPlugin,
        new FpuPlugin(
          externalFpu = false,
          p = FpuParameter(
            withDouble = false
          )
        ),
        new ExternalInterruptArrayPlugin(
          machineMaskCsrId = 0xbc0,
          machinePendingsCsrId = 0xfc0,
          supervisorMaskCsrId = 0x9c0,
          supervisorPendingsCsrId = 0xdc0
        )
      )

      // instantiate core
      val cpu = new VexRiscv(VexRiscvConfig(plugins.toList))

      // modify CPU to use wishbone bus
      cpu.rework {
        for (plugin <- cpu.config.plugins) plugin match {
          case plugin: IBusSimplePlugin => {
            plugin.iBus.setAsDirectionLess() // clear iBus IO properties
            master(plugin.iBus.toWishbone()).setName("iBusWishbone")
          }
          case plugin: IBusCachedPlugin => {
            plugin.iBus.setAsDirectionLess()
            master(plugin.iBus.toWishbone()).setName("iBusWishbone")
          }
          case plugin: DBusSimplePlugin => {
            plugin.dBus.setAsDirectionLess()
            master(plugin.dBus.toWishbone()).setName("dBusWishbone")
          }
          case plugin: DBusCachedPlugin => {
            plugin.dBus.setAsDirectionLess()
            master(plugin.dBus.toWishbone()).setName("dBusWishbone")
          }
          case _ =>
        }
      }
      cpu
    }
  }
}
