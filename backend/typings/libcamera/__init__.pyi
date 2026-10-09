from enum import IntEnum

class controls:
    class AfModeEnum(IntEnum):
        Manual = 0
        Auto = 1
        Continuous = 2

    class AeExposureModeEnum(IntEnum):
        Normal = 0
        Short = 1
        Long = 2
        Custom = 3
