from fractions import Fraction

class LibavH264Encoder:
    def __init__(
        self,
        bitrate: int | None = ...,
        repeat: bool = ...,
        iperiod: int = ...,
        framerate: float | Fraction = ...,
        qp: int | None = ...,
        profile: str | None = ...,
    ) -> None: ...
