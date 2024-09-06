package lunasoc

import spinal.core._
import spinal.core.internals.{
  ExpressionContainer,
  PhaseAllocateNames,
  PhaseContext
}

object LunaSpinalConfig
    extends spinal.core.SpinalConfig(
      defaultConfigForClockDomains = ClockDomainConfig(
        resetKind = spinal.core.SYNC
      )
    ) {
  // disable these to let the toolchain infer block memories
  /*phasesInserters += { (array) =>
    array.insert(
      array.indexWhere(_.isInstanceOf[PhaseAllocateNames]) + 1,
      new ForceRamBlockPhase
    )
  }
  phasesInserters += { (array) =>
    array.insert(
      array.indexWhere(_.isInstanceOf[PhaseAllocateNames]) + 1,
      new NoRwCheckPhase
    )
  }*/
}

class ForceRamBlockPhase() extends spinal.core.internals.Phase {
  override def impl(pc: PhaseContext): Unit = {
    pc.walkBaseNodes {
      case mem: Mem[_] => {
        var asyncRead = false
        mem.dlcForeach[MemPortStatement] {
          case _: MemReadAsync => asyncRead = true
          case _               =>
        }
        if (!asyncRead) mem.addAttribute("ram_style", "block")
      }
      case _ =>
    }
  }
  override def hasNetlistImpact: Boolean = false
}

class NoRwCheckPhase() extends spinal.core.internals.Phase {
  override def impl(pc: PhaseContext): Unit = {
    pc.walkBaseNodes {
      case mem: Mem[_] => {
        var doit = false
        mem.dlcForeach[MemPortStatement] {
          case _: MemReadSync => doit = true
          case _              =>
        }
        mem.dlcForeach[MemPortStatement] {
          case p: MemReadSync if p.readUnderWrite != dontCare => doit = false
          case _                                              =>
        }
        if (doit) mem.addAttribute("no_rw_check")
      }
      case _ =>
    }
  }
  override def hasNetlistImpact: Boolean = false
}
