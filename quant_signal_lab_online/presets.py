"""Five saved strategy configurations. Defaults match the existing spot workbench."""
from dataclasses import asdict, dataclass


WEIGHT_FIELDS = ("w_trend", "w_momentum", "w_volume", "w_pattern", "w_risk")
STYLE_BY_NAME = {
    "稳健": "trend",
    "均衡": "trend",
    "激进": "trend",
    "趋势跟踪": "trend",
    "均值回归": "mean_reversion",
}
PRESET_ORDER = tuple(STYLE_BY_NAME)


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    style: str
    threshold: int
    strong: int
    minimum_rr: float
    capital: float
    risk_pct: float
    fee_pct: float
    slip_pct: float
    ema: tuple
    boll: int
    rsi: int
    macd: tuple
    atr: int
    volume: int
    structure: int
    weights: tuple
    mr_rsi_low: float
    mr_rsi_high: float
    mr_atr_stop: float

    def to_dict(self):
        raw = asdict(self)
        raw["ema"] = list(self.ema)
        raw["macd"] = list(self.macd)
        raw["weights"] = list(self.weights)
        return raw

    @classmethod
    def from_dict(cls, raw):
        data = dict(raw)
        data["ema"] = tuple(int(value) for value in data["ema"])
        data["macd"] = tuple(int(value) for value in data["macd"])
        data["weights"] = tuple(int(value) for value in data["weights"])
        data["style"] = STYLE_BY_NAME[data["name"]]
        return cls(**data)

    def session_values(self):
        return {
            "rule_threshold": int(self.threshold),
            "rule_strong": int(self.strong),
            "rule_rr": float(self.minimum_rr),
            "cost_capital": float(self.capital),
            "cost_risk": float(self.risk_pct),
            "cost_fee": float(self.fee_pct),
            "cost_slip": float(self.slip_pct),
            "ema_7": int(self.ema[0]),
            "ema_20": int(self.ema[1]),
            "ema_50": int(self.ema[2]),
            "ema_200": int(self.ema[3]),
            "boll_n": int(self.boll),
            "rsi_n": int(self.rsi),
            "macd_fast": int(self.macd[0]),
            "macd_slow": int(self.macd[1]),
            "macd_signal": int(self.macd[2]),
            "atr_n": int(self.atr),
            "volume_n": int(self.volume),
            "structure_n": int(self.structure),
            "w_trend": int(self.weights[0]),
            "w_momentum": int(self.weights[1]),
            "w_volume": int(self.weights[2]),
            "w_pattern": int(self.weights[3]),
            "w_risk": int(self.weights[4]),
            "mr_rsi_low": float(self.mr_rsi_low),
            "mr_rsi_high": float(self.mr_rsi_high),
            "mr_atr_stop": float(self.mr_atr_stop),
        }

    def errors(self):
        problems = []
        if self.name not in STYLE_BY_NAME:
            problems.append("只能保存五套固定策略名称")
        if self.macd[0] >= self.macd[1]:
            problems.append("MACD 快线周期须小于慢线")
        if self.threshold > self.strong:
            problems.append("候选阈值须低于或等于强候选阈值")
        if len(self.weights) != 5 or any(value < 0 for value in self.weights) or sum(self.weights) != 100:
            problems.append("五项权重必须非负且合计为 100")
        if not self.mr_rsi_low < self.mr_rsi_high:
            problems.append("均值回归的 RSI 低阈值须小于高阈值")
        if self.mr_atr_stop <= 0:
            problems.append("均值回归止损倍数须大于 0")
        return problems


def _config(name, **changes):
    base = dict(
        name=name, style=STYLE_BY_NAME[name], threshold=65, strong=80, minimum_rr=1.5,
        capital=1000.0, risk_pct=0.5, fee_pct=0.06, slip_pct=0.03,
        ema=(7, 20, 50, 200), boll=20, rsi=14, macd=(12, 26, 9), atr=14, volume=20,
        structure=20, weights=(30, 20, 20, 15, 15), mr_rsi_low=30.0, mr_rsi_high=70.0,
        mr_atr_stop=1.5,
    )
    base.update(changes)
    return StrategyConfig(**base)


DEFAULTS = {
    "稳健": _config("稳健", threshold=75, strong=88, minimum_rr=2.0, risk_pct=0.3, structure=30),
    "均衡": _config("均衡"),
    "激进": _config("激进", threshold=55, strong=70, minimum_rr=1.2, risk_pct=1.0),
    "趋势跟踪": _config("趋势跟踪", threshold=60, strong=78, weights=(40, 25, 15, 10, 10)),
    "均值回归": _config("均值回归", minimum_rr=1.2),
}


def from_session(name, state):
    return StrategyConfig(
        name=name, style=STYLE_BY_NAME[name],
        threshold=int(state["rule_threshold"]), strong=int(state["rule_strong"]),
        minimum_rr=float(state["rule_rr"]), capital=float(state["cost_capital"]),
        risk_pct=float(state["cost_risk"]), fee_pct=float(state["cost_fee"]),
        slip_pct=float(state["cost_slip"]),
        ema=(int(state["ema_7"]), int(state["ema_20"]), int(state["ema_50"]), int(state["ema_200"])),
        boll=int(state["boll_n"]), rsi=int(state["rsi_n"]),
        macd=(int(state["macd_fast"]), int(state["macd_slow"]), int(state["macd_signal"])),
        atr=int(state["atr_n"]), volume=int(state["volume_n"]), structure=int(state["structure_n"]),
        weights=tuple(int(state[key]) for key in WEIGHT_FIELDS),
        mr_rsi_low=float(state["mr_rsi_low"]), mr_rsi_high=float(state["mr_rsi_high"]),
        mr_atr_stop=float(state["mr_atr_stop"]),
    )
