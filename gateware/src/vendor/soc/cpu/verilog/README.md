## Dependencies

The VexRiscv core is implemented using SpinalHDL which requires the
`sbt` build tool for Scala projects to be installed.

On macOS:

    brew install sbt


## Rebuild cores

You can rebuild the verilog for all cores using:

    make all


## JTAG support

There is a [documented issue](https://github.com/SpinalHDL/VexRiscv/issues/381) with
the verilog synthesis of Vexriscv JTAG support.

The `tap_fsm_state` register is not assigned a reset state resulting
in the TDO line being kept in bypass mode.

Until this can be tracked down you'll need to manually edit the
`vexriscv_cynthion+jtag.v` after a rebuild as follows:

    reg        [1:0]    logic_jtagLogic_dmiStat_value_aheadValue;
    wire       [3:0]    tap_fsm_stateNext;
    reg        [3:0]    tap_fsm_state = 0;  <--------------
    wire       [3:0]    _zz_tap_fsm_stateNext;
    wire       [3:0]    _zz_tap_fsm_stateNext_1;
