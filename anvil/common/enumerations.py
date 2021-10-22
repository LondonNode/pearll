from enum import Enum


class NetworkType(Enum):
    MLP = "mlp"
    PARAMETER = "parameter"


class TrajectoryType(Enum):
    NUMPY = "numpy"
    TORCH = "torch"


class TrainFrequencyType(Enum):
    EPISODE = "episode"
    STEP = "step"


class HERGoalStrategy(Enum):
    FINAL = "final"
    FUTURE = "future"
    EPISODE = "episode"
    RANDOM = "random"
