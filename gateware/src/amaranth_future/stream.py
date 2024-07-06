# from https://github.com/amaranth-lang/amaranth/pull/1266
# slightly modified to work out-of-tree with Amaranth ~= 0.4

from amaranth.hdl import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

def final(cls):
    def init_subclass():
        raise TypeError("Subclassing {}.{} is not supported"
                        .format(cls.__module__, cls.__name__))
    cls.__init_subclass__ = init_subclass
    return cls

@final
class Signature(wiring.Signature):
    def __init__(self, payload_shape, *, always_valid=False, always_ready=False):
        Shape.cast(payload_shape)
        self._payload_shape = payload_shape
        self._always_valid = bool(always_valid)
        self._always_ready = bool(always_ready)

        super().__init__({
            "payload": Out(payload_shape),
            "valid": Out(1),
            "ready": In(1)
        })

    # payload_shape intentionally not introspectable (for now)

    @property
    def always_valid(self):
        return self._always_valid

    @property
    def always_ready(self):
        return self._always_ready

    def __eq__(self, other):
        return (type(other) is type(self) and
            other._payload_shape == self._payload_shape and
            other.always_valid == self.always_valid and
            other.always_ready == self.always_ready)

    def create(self, *, path=None, src_loc_at=0):
        return Interface(self, path=path, src_loc_at=1 + src_loc_at)

    def __repr__(self):
        always_valid_repr = "" if not self._always_valid else ", always_valid=True"
        always_ready_repr = "" if not self._always_ready else ", always_ready=True"
        return f"stream.Signature({self._payload_shape!r}{always_valid_repr}{always_ready_repr})"


@final
class Interface:
    payload: Signal
    valid: 'Signal | Const'
    ready: 'Signal | Const'

    def __init__(self, signature: Signature, *, path=None, src_loc_at=0):
        if not isinstance(signature, Signature):
            raise TypeError(f"Signature of stream.Interface must be a stream.Signature, not "
                            f"{signature!r}")
        self._signature = signature
        self.__dict__.update(signature.members.create(path=path, src_loc_at=1 + src_loc_at))
        if signature.always_valid:
            self.valid = Const(1)
        if signature.always_ready:
            self.ready = Const(1)

    @property
    def signature(self):
        return self._signature

    @property
    def p(self):
        return self.payload

    def __repr__(self):
        return f"stream.Interface(payload={self.payload!r}, valid={self.valid!r}, ready={self.ready!r})"

def fifo_w_stream(fifo):
    w_stream = Signature(fifo.width).create()
    w_stream.payload = fifo.w_data
    w_stream.valid = fifo.w_en
    w_stream.ready = fifo.w_rdy
    return w_stream

def fifo_r_stream(fifo):
    r_stream = Signature(fifo.width).create()
    r_stream.payload = fifo.r_data
    r_stream.valid = fifo.r_rdy
    r_stream.ready = fifo.r_en
    return r_stream

