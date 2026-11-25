"""Translate native stage counters into one job-wide timeline, including PDF export."""
import time


# Weights approximate wall time of the managed 7B pipeline. Native parsing stages
# otherwise occupy ~1/3 of the bar before a single paragraph has been translated.
NORMAL = {
    "scan": 1, "model": 1, "Parse PDF and Create Intermediate Representation": 1,
    "DetectScannedFile": .2, "Parse Page Layout": 1, "Parse Table": .3,
    "Parse Paragraphs": .3, "Parse Formulas and Styles": .7,
    "Automatic Term Extraction": 15, "Translate Paragraphs": 70,
    "Typesetting": 1, "Add Fonts": .2, "Generate drawing instructions": .7,
    "Subset font": .5, "Save PDF": 1, "figure_read": .5, "figure_text": 6,
    "publish": .6,
}
# Replaying human-approved text does not run term extraction or model inference.
REPLAY = {key: weight for key, weight in NORMAL.items()
          if key not in {"scan", "model", "Automatic Term Extraction", "figure_read", "figure_text"}}
REPLAY["Translate Paragraphs"] = .5
INFERENCE = {"Automatic Term Extraction", "Translate Paragraphs"}
UNITS = {"Translate Paragraphs": "段", "figure_read": "页", "figure_text": "处"}


class JobProgress:
    def __init__(self, figures=True, replay=False, clock=time.monotonic, history=None):
        self.clock = clock
        self.started = self.stage_started = self.last_advance = clock()
        self.ended = None
        self.weights = dict(REPLAY if replay else NORMAL)
        if not figures:
            self.weights.pop("figure_read", None)
            self.weights.pop("figure_text", None)
        self.replay = replay
        self.stage = None
        self.fraction = self.percent = 0.0
        self.count = self.total = self.advances = 0
        self.inference_rate = None
        self.quick_fraction = 0.0
        self.history = dict(history or {})
        self.measured = {}

    def update(self, stage, fraction=0.0, count=0, total=0):
        if self.ended is not None or stage not in self.weights:
            return
        now = self.clock()
        weights = list(self.weights)
        if self.stage != stage:
            if self.stage and weights.index(stage) < weights.index(self.stage):
                return
            if self.stage in INFERENCE and self.fraction >= .75:
                seconds = now - self.stage_started
                if seconds > 2:
                    self.inference_rate = seconds / (self.weights[self.stage] * self.fraction)
                    if self.total > 0 and seconds > 8:
                        self.measured[self.stage] = (seconds, self.total)
            self.stage = stage
            self.stage_started = self.last_advance = now
            self.fraction = self.quick_fraction = 0
            self.advances = 0
        fraction = min(1.0, max(0.0, fraction))
        # BabelDOC immediately credits skipped cells (200/897 at the first
        # tick of a clinical PDF). These units did not consume inference time.
        if stage in INFERENCE and now - self.stage_started < 1 and fraction < 1:
            self.quick_fraction = max(self.quick_fraction, fraction)
        fraction = max(0, fraction-self.quick_fraction) / (1-self.quick_fraction)
        if fraction < self.fraction:
            return
        if fraction > self.fraction:
            self.last_advance = now
            self.advances += 1
        self.fraction = fraction
        self.count, self.total = count, total
        completed = sum(self.weights[k] for k in weights[:weights.index(stage)])
        value = (completed + fraction * self.weights[stage]) * 100 / sum(self.weights.values())
        self.percent = min(99.9, max(self.percent, value))

    def finish(self, success):
        if self.ended is None:
            self.ended = self.clock()
        if success:
            self.percent = 100

    def snapshot(self):
        now = self.ended if self.ended is not None else self.clock()
        elapsed = max(0, now - self.started)
        eta = None
        if self.ended is None and self.stage:
            duration = now - self.stage_started
            f = self.fraction
            keys = list(self.weights)
            future = sum(self.weights[k] for k in keys[keys.index(self.stage)+1:])
            prior = self.inference_rate
            sample = self.history.get(self.stage)
            if sample and self.total and .33 <= self.total / sample[1] <= 3:
                prior = sample[0] * self.total / sample[1] / self.weights[self.stage]
            eligible = self.replay or self.stage in INFERENCE or self.stage == "figure_text"
            observed = None
            if eligible and self.advances >= 2 and duration >= 4 and f >= .04:
                observed = duration / (f * self.weights[self.stage])
            # The first completed batch can contain a few very long paragraphs,
            # or dozens of trivial cells. Blend it with the previous inference
            # stage until this stage has processed a representative workload.
            if observed and prior:
                influence = min(1, f / .35)
                observed = prior * (1-influence) + observed * influence
            rate = observed or prior
            if rate and eligible:
                stale = now - self.last_advance > max(60, 4 * duration / max(1, self.advances))
                if not stale:
                    eta = max(1, round(rate * (self.weights[self.stage] * (1-f) + future)))
        unit = UNITS.get(self.stage, "项")
        counter = f"{self.count} / {self.total} {unit}" if self.total > 0 else ""
        # Report a wider interval early in an inference stage. Keep the estimate
        # separate from elapsed time and only publish 100% after both PDFs exist.
        spread = .45 if self.fraction < .35 else .25
        return dict(percent=round(self.percent, 1), elapsedSeconds=int(elapsed),
                    remainingSeconds=eta, remainingLow=max(1, int(eta*(1-spread))) if eta else None,
                    remainingHigh=max(1, round(eta*(1+spread))) if eta else None, stageCount=counter)
